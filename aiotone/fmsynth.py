#!/usr/bin/env python3
"""See the docstring to main()."""

from __future__ import annotations
from typing import *

from array import array
import asyncio
from collections import defaultdict
import configparser
from dataclasses import dataclass, field
from pathlib import Path
import time

import click
import miniaudio
import uvloop

from . import profiling
from .midi import (
    MidiOut,
    NOTE_OFF,
    NOTE_ON,
    CLOCK,
    START,
    STOP,
    SONG_POSITION,
    CONTROL_CHANGE,
    MOD_WHEEL,
    MOD_WHEEL_LSB,
    EXPRESSION_PEDAL,
    EXPRESSION_PEDAL_LSB,
    SUSTAIN_PEDAL,
    PORTAMENTO,
    PORTAMENTO_TIME,
    PITCH_BEND,
    ALL_NOTES_OFF,
    STRIP_CHANNEL,
    get_ports,
    silence,
)
from .notes import note_to_freq
from .fm import calculate_panning, filter_array, saturate, Envelope, Operator
from .waves import sine_array, sine12_array, saw_array


# We want this to be symmetrical on the + and the - side.
INT16_MAXVALUE = 32767
MAX_BUFFER = 2400  # 5 ms at 48000 Hz
CURRENT_DIR = Path(__file__).parent
DEBUG = False


if TYPE_CHECKING:
    Audio = Generator[array[int], int, None]
    FMAudio = Generator[array[int], array[int], None]
    EventDelta = float  # in seconds
    TimeStamp = float  # time.time()
    MidiPacket = List[int]
    MidiMessage = Tuple[MidiPacket, EventDelta, TimeStamp]


# For clarity we're aliasing `next` because we are using it as an initializer of
# stateful generators to execute until (and including) its first `yield` expression
# to stop right before assigning a value sent to the generator.  Now the generator
# is ready to accept `.send(value)`.
# Note: due to this initialization, the first yield in Audio generators returns an
# empty array.
init = next


def panning(mono: Audio, pan: float = 0.0) -> Audio:
    result = init(mono)
    want_frames = yield result

    out_buffer = array("h", [0] * (2 * MAX_BUFFER))
    while True:
        mono_buffer = mono.send(want_frames)
        calculate_panning(pan, mono_buffer, out_buffer, want_frames)
        want_frames = yield out_buffer[: 2 * want_frames]


def auto_pan(mono: Audio, panner: Audio) -> Audio:
    result = init(mono)
    result = init(panner)
    want_frames = yield result

    out_buffer = array("h", [0] * (2 * MAX_BUFFER))
    while True:
        mono_buffer = mono.send(want_frames)
        panning = panner.send(want_frames)
        for i in range(want_frames):
            pan = panning[i] / INT16_MAXVALUE
            out_buffer[2 * i] = int((-pan + 1) / 2 * mono_buffer[i])
            out_buffer[2 * i + 1] = int((pan + 1) / 2 * mono_buffer[i])
        want_frames = yield out_buffer[: 2 * want_frames]


@dataclass
class Synthesizer:
    polyphony: int
    sample_rate: int
    panning: List[float] = field(init=False)
    voices: List[PhaseModulator] = field(init=False)
    _voices_lru: List[int] = field(init=False)  # list of `voices` indexes
    _sustain: int = field(init=False)
    _released_on_sustain: Set[float] = field(init=False)

    def __post_init__(self) -> None:
        self.reset_voices()

    def reset_voices(self) -> None:
        polyphony = self.polyphony
        self.panning = [(2 * i / (polyphony - 1) - 1) for i in range(polyphony)]
        self.voices = [
            PhaseModulator(
                wave1=filter_array(saw_array(2048), 256),
                wave2=sine12_array(2048),
                wave3=sine_array(2048),
                wave4=sine_array(2048),
                sample_rate=self.sample_rate,
            )
            for i in range(polyphony)
        ]
        self._voices_lru = [i for i in range(polyphony)]
        self._sustain = 0
        self._released_on_sustain = set()

    def stereo_out(self) -> Audio:
        """A resettable stereo mixer."""

        want_frames = 0
        while True:
            try:
                yield from self._stereo_out(want_frames)
            except EOFError as eof:
                print(eof.args[0])
                want_frames = eof.args[1]

    def _stereo_out(self, want_frames: int = 0) -> Audio:
        voices = [
            panning(self.voices[i].mono_out(), self.panning[i])
            for i in range(self.polyphony)
        ]

        mix_down = 1 / self.polyphony
        stereo = [init(v) for v in voices]
        if want_frames == 0:
            want_frames = yield stereo[0]
        id_voices = id(self.voices)

        out_buffer = array("h", [0] * (2 * MAX_BUFFER))
        with profiling.maybe(DEBUG):
            while True:
                if id(self.voices) != id_voices:
                    raise EOFError("Voices have been reset", want_frames)
                stereo = [v.send(want_frames) for v in voices]
                for i in range(0, 2 * want_frames):
                    out_buffer[i] = int(sum([mix_down * s[i] for s in stereo]))
                want_frames = yield out_buffer[: 2 * want_frames]

    # MIDI support

    async def clock(self) -> None:
        ...

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def note_on(self, note: int, velocity: int) -> None:
        try:
            pitch = note_to_freq[note]
        except KeyError:
            return

        volume = velocity / 127
        voices = self.voices
        lru = self._voices_lru
        v: PhaseModulator
        first_released = None
        for vli, vi in enumerate(lru):
            v = voices[vi]
            if v.is_silent():
                lru.append(lru.pop(vli))
                break
            elif first_released is None and v.is_released():
                first_released = vli
        else:
            if first_released is not None:
                # The first released voice is the most likely to be the least disrupted
                # by cutting it short.
                vli = first_released
            else:
                # If no voices were unused nor released, just take the least recently
                # used one.
                vli = 0
            vi = lru.pop(vli)
            v = voices[vi]
            lru.append(vi)
        v.note_on(pitch, volume)
        if self._sustain > 32:
            self._released_on_sustain.discard(pitch)
            return

    async def note_off(self, note: int, velocity: int) -> None:
        try:
            pitch = note_to_freq[note]
        except KeyError:
            return

        if self._sustain > 32:
            self._released_on_sustain.add(pitch)
            return

        volume = velocity / 127
        for v in self.voices:
            v.note_off(pitch, volume)

    async def pitch_bend(self, value: int) -> None:
        """Value range: 0 - 16384"""
        ...

    async def mod_wheel(self, value: int) -> None:
        """Value range: 0 - 16384"""
        ...

    async def expression(self, value: int) -> None:
        """Value range: 0 - 16384"""
        ...

    async def sustain(self, value: int) -> None:
        if self._sustain > 32 and value < 32:
            for pitch in self._released_on_sustain:
                for v in self.voices:
                    v.note_off(pitch, 0)
            self._released_on_sustain.clear()
        self._sustain = value

    async def all_notes_off(self, value: int) -> None:
        self.reset_voices()


@dataclass
class PhaseModulator:
    r"""A 4-operator modulator. Algorithms:

      4
      |        3     4      3               4
      3        |__ __|      |             __|__
      |           |         2     4      2     3     2   3   4
      2           2         |__ __|      |__ __|     |___|___|
      |           |            |            |            |
     <1>         <1>          <1>          <1>          <1>

    alg.0       alg.1        alg.2        alg.3        alg.4

                          4
            4             |
            |             3
            3           __|__           3     4
            |          |     |          |     |
     <1> + <2>        <1> + <2>        <1> + <2>

       alg.5            alg.6            alg.7


            4                       4
       _____|_____                __|__                     4
      |     |     |              |     |                    |
     <1> + <2> + <3>      <1> + <2> + <3>      <1> + <2> + <3>

          alg.8                alg.9                alg.10


    <1> + <2> + <3> + <4>

           alg.11
    """

    wave1: array[int]
    wave2: array[int]
    wave3: array[int]
    wave4: array[int]
    sample_rate: int
    algorithm: int = 10
    rate1: float = 0.999  # detune by adding/removing cents
    rate2: float = 1.003
    rate3: float = 1.0
    rate4: float = 19.0
    op1: Operator = field(init=False)
    op2: Operator = field(init=False)
    op3: Operator = field(init=False)
    op4: Operator = field(init=False)
    last_pitch_played: float = field(init=False)

    def __post_init__(self) -> None:
        self.reset_operators()

    def reset_operators(self) -> None:
        self.op1 = Operator(
            wave=self.wave1,
            sample_rate=self.sample_rate,
            envelope=Envelope(
                a=int(0.1 * self.sample_rate),
                d=2 * self.sample_rate,
                s=0.0,
                r=int(0.33 * self.sample_rate),
            ),
            volume=0.052 * 0.6,
        )
        self.op2 = Operator(
            wave=self.wave2,
            sample_rate=self.sample_rate,
            envelope=Envelope(
                a=48,
                d=3 * self.sample_rate,
                s=0.0,
                r=int(0.25 * self.sample_rate),
            ),
            volume=0.75 * 0.6,
        )
        self.op3 = Operator(
            wave=self.wave3,
            sample_rate=self.sample_rate,
            envelope=Envelope(
                a=48,
                d=4 * self.sample_rate,
                s=0.0,
                r=int(0.5 * self.sample_rate),
            ),
            volume=0.54 * 0.6,
        )
        self.op4 = Operator(
            wave=self.wave4,
            sample_rate=self.sample_rate,
            envelope=Envelope(
                a=48,
                d=int(self.sample_rate / 9),
                s=0.0,
                r=int(self.sample_rate / 9),
            ),
            volume=0.56 * 0.4,
        )
        self.last_pitch_played = 0.0

    def is_silent(self) -> bool:
        algo = self.algorithm
        if algo == 0 or algo == 1:
            return self.op1.is_silent()

        if algo == 2 or algo == 3:
            return self.op1.is_silent() and self.op2.is_silent()

        return self.op1.is_silent() and self.op2.is_silent() and self.op3.is_silent()

    def note_on(self, pitch: float, volume: float) -> None:
        self.last_pitch_played = pitch
        self.op1.note_on(pitch * self.rate1, volume)
        self.op2.note_on(pitch * self.rate2, volume)
        self.op3.note_on(pitch * self.rate3, volume)
        self.op4.note_on(pitch * self.rate4, volume)

    def note_off(self, pitch: float, volume: float) -> None:
        if pitch != self.last_pitch_played:
            return

        self.op1.note_off(pitch * self.rate1, volume)
        self.op2.note_off(pitch * self.rate2, volume)
        self.op3.note_off(pitch * self.rate3, volume)
        self.op4.note_off(pitch * self.rate4, volume)
        self.last_pitch_played = 0.0

    def mono_out(self) -> Audio:
        out_buffer = array("h", [0] * MAX_BUFFER)
        zero_buffer = array("h", [0] * MAX_BUFFER)

        op1 = self.op1.mono_out()
        op2 = self.op2.mono_out()
        op3 = self.op3.mono_out()
        op4 = self.op4.mono_out()
        init(op1)
        init(op2)
        init(op3)
        init(op4)
        want_frames = yield out_buffer

        while True:
            algo = self.algorithm
            out4 = op4.send(zero_buffer[:want_frames])
            if algo == 0:
                out3 = op3.send(out4)
                out2 = op2.send(out3)
                out1 = op1.send(out2)
                want_frames = yield out1
            elif algo == 1:
                out3 = op3.send(zero_buffer[:want_frames])
                for i in range(want_frames):
                    out_buffer[i] = saturate(out3[i] + out4[i])
                out2 = op2.send(out_buffer[:want_frames])
                out1 = op1.send(out2)
                want_frames = yield out1
            elif algo == 2:
                out3 = op3.send(zero_buffer[:want_frames])
                out2 = op2.send(out3)
                for i in range(want_frames):
                    out_buffer[i] = saturate(out2[i] + out4[i])
                out1 = op1.send(out2)
                want_frames = yield out1
            elif algo == 3:
                out3 = op3.send(out4)
                out2 = op2.send(out4)
                for i in range(want_frames):
                    out_buffer[i] = saturate(out2[i] + out3[i])
                out1 = op1.send(out2)
                want_frames = yield out1
            elif algo == 4:
                out3 = op3.send(zero_buffer[:want_frames])
                out2 = op2.send(zero_buffer[:want_frames])
                for i in range(want_frames):
                    out_buffer[i] = saturate(out2[i] + out3[i] + out4[i])
                out1 = op1.send(out_buffer[:want_frames])
                want_frames = yield out1
            elif algo == 5:
                out3 = op3.send(out4)
                out2 = op2.send(out3)
                out1 = op1.send(zero_buffer[:want_frames])
                for i in range(want_frames):
                    out_buffer[i] = saturate(out1[i] + out2[i])
                want_frames = yield out_buffer[:want_frames]
            elif algo == 6:
                out3 = op3.send(out4)
                out2 = op2.send(out3)
                out1 = op1.send(out3)
                for i in range(want_frames):
                    out_buffer[i] = saturate(out1[i] + out2[i])
                want_frames = yield out_buffer[:want_frames]
            elif algo == 7:
                out3 = op3.send(zero_buffer[:want_frames])
                out2 = op2.send(out4)
                out1 = op1.send(out3)
                for i in range(want_frames):
                    out_buffer[i] = saturate(out1[i] + out2[i])
                want_frames = yield out_buffer[:want_frames]
            elif algo == 8:
                out3 = op3.send(out4)
                out2 = op2.send(out4)
                out1 = op1.send(out4)
                for i in range(want_frames):
                    out_buffer[i] = saturate(out1[i] + out2[i] + out3[i])
                want_frames = yield out_buffer[:want_frames]
            elif algo == 9:
                out3 = op3.send(out4)
                out2 = op2.send(out4)
                out1 = op1.send(zero_buffer[:want_frames])
                for i in range(want_frames):
                    out_buffer[i] = saturate(out1[i] + out2[i] + out3[i])
                want_frames = yield out_buffer[:want_frames]
            elif algo == 10:
                out3 = op3.send(out4)
                out2 = op2.send(zero_buffer[:want_frames])
                out1 = op1.send(zero_buffer[:want_frames])
                for i in range(want_frames):
                    out_buffer[i] = saturate(out1[i] + out2[i] + out3[i])
                want_frames = yield out_buffer[:want_frames]
            else:
                out3 = op3.send(zero_buffer[:want_frames])
                out2 = op2.send(zero_buffer[:want_frames])
                out1 = op1.send(zero_buffer[:want_frames])
                for i in range(want_frames):
                    out_buffer[i] = saturate(out1[i] + out2[i] + out3[i] + out4[i])
                want_frames = yield out_buffer[:want_frames]

    def is_released(self) -> bool:
        return self.last_pitch_played == 0.0


async def async_main(synth: Synthesizer, cfg: Mapping[str, str]) -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    if int(cfg.get("channel", "")) != 1:
        raise click.UsageError("midi-in channel must be 1, sorry")

    try:
        midi_in, midi_out = get_ports(cfg["port-name"], clock_source=True)
    except ValueError as port:
        raise click.UsageError(f"midi-in port {port} not connected")

    def midi_callback(msg, data=None):
        sent_time = time.time()
        midi_message, event_delta = msg
        try:
            loop.call_soon_threadsafe(
                queue.put_nowait, (midi_message, event_delta, sent_time)
            )
        except BaseException as be:
            click.secho(f"callback exc: {type(be)} {be}", fg="red", err=True)

    midi_in.set_callback(midi_callback)
    midi_out.close_port()  # we won't be using that one now

    try:
        await midi_consumer(queue, synth)
    except asyncio.CancelledError:
        midi_in.cancel_callback()


async def midi_consumer(queue: asyncio.Queue[MidiMessage], synth: Synthesizer) -> None:
    click.echo("Waiting for MIDI messages...")
    system_realtime = {START, STOP, SONG_POSITION}
    notes = {NOTE_ON, NOTE_OFF}
    handled_types = system_realtime | notes | {CONTROL_CHANGE}
    last: Dict[int, int] = defaultdict(int)  # last CC value
    while True:
        msg, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        # Note hack below. We are matching the default which is channel 1 only.
        # This is what we want.
        t = msg[0]
        if t == CLOCK:
            await synth.clock()
        else:
            st = t & STRIP_CHANNEL
            if st == STRIP_CHANNEL:  # system realtime message didn't have a channel
                st = t
            if __debug__ and st == t:
                fg = "white"
                if t in system_realtime:
                    fg = "blue"
                elif t == CONTROL_CHANGE:
                    fg = "green"
                click.secho(
                    f"{msg}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}", fg=fg
                )
            if t == START:
                await synth.start()
            elif t == STOP:
                await synth.stop()
            elif t == NOTE_ON:
                if msg[2] == 0:  # velocity of zero means "note off"
                    await synth.note_off(msg[1], msg[2])
                else:
                    await synth.note_on(msg[1], msg[2])
            elif t == NOTE_OFF:
                await synth.note_off(msg[1], msg[2])
            elif t == CONTROL_CHANGE:
                last[msg[1]] = msg[2]
                if msg[1] == MOD_WHEEL:
                    await synth.mod_wheel(128 * msg[2] + last[MOD_WHEEL_LSB])
                elif msg[1] == MOD_WHEEL_LSB:
                    await synth.mod_wheel(128 * last[MOD_WHEEL] + msg[2])
                elif msg[1] == EXPRESSION_PEDAL:
                    await synth.expression(128 * msg[2] + last[EXPRESSION_PEDAL_LSB])
                elif msg[1] == EXPRESSION_PEDAL_LSB:
                    await synth.expression(128 * last[EXPRESSION_PEDAL] + msg[2])
                elif msg[1] == SUSTAIN_PEDAL:
                    await synth.sustain(msg[2])
                elif msg[1] == ALL_NOTES_OFF:
                    await synth.all_notes_off(msg[2])
                else:
                    click.secho(f"warning: unhandled CC {msg}", err=True)
            elif t == PITCH_BEND:
                await synth.pitch_bend(128 * msg[1] + msg[2])
            else:
                if st not in handled_types:
                    click.secho(f"warning: unhandled event {msg}", err=True)


@click.command()
@click.option(
    "--config",
    help="Read configuration from this file",
    default=str(CURRENT_DIR / "aiotone-fmsynth.ini"),
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    show_default=True,
)
@click.option(
    "--make-config",
    help="Write a new configuration file to standard output",
    is_flag=True,
)
def main(config: str, make_config: bool) -> None:
    """
    This is a module which implements realtime polyphonic FM synthesis in Python
    controllable by MIDI.  Note that this is very CPU-intensive.

    It's got the following features:

    - configurable polyphony;

    - efficient sample-precise ADSR envelope;

    - efficient Operator implementation with sample-precise pitch, velocity, volume,
      and modulation control (feedback not implemented yet);

    - a 4-operator modulator with 12 algorithms inspired by Reface DX;

    - dispatches MIDI IN events like NOTE_ON and NOTE_OFF events to the synthesizer.

    To use this yourself, you will need:

    - a MIDI IN port, can be virtual (I'm using IAC in Audio MIDI Setup on macOS);

    - an AUDIO OUT, can be virtual (I'm using BlackHole on macOS);

    - a DAW project which will be configured as follows (Renoise as an example):

        - a MIDI Instrument in the DAW configured to output notes to the MIDI port that
          fmsynth listens on (I call my virtual MIDI port "IAC fmsynth");

        - a #Line Input routing in the DAW configured to catch audio from the out that
          fmsynth (like "BlackHole 16ch 1+2");

        - turn on "MIDI Return Mode" to compensate latency;

        - in Ableton Live use "External Instrument" to do this in one place and
          automatically compensate latency.

    You can customize the ports by creating a config file.  Use `--make-config` to
    output a new config to stdout.

    Then run `python -m aiotone.fmsynth --config=PATH_TO_YOUR_CONFIG_FILE`.
    """
    if make_config:
        with open(CURRENT_DIR / "aiotone-fmsynth.ini") as f:
            print(f.read())
        return

    cfg = configparser.ConfigParser()
    cfg.read(config)

    # Apparently, miniaudio (at least on Linux) doesn't enumerate devices across all backends.
    # So if we want to use a device on a non-default backend, we need to specify the backend.
    backend_name = cfg["audio-out"].get("backend")
    if backend_name:
        backend = getattr(miniaudio.Backend, backend_name)
        devices = miniaudio.Devices([backend])
    else:
        devices = miniaudio.Devices()
    playbacks = devices.get_playbacks()
    audio_out = cfg["audio-out"]["out-name"]
    sample_rate = cfg["audio-out"].getint("sample-rate")
    buffer_msec = cfg["audio-out"].getint("buffer-msec")
    polyphony = cfg["audio-out"].getint("polyphony")
    for playback in playbacks:
        if playback["name"] == audio_out:
            play_id = playback["id"]
            break
    else:
        raise click.UsageError(f"No audio out available called {audio_out}")

    with miniaudio.PlaybackDevice(
        device_id=play_id,
        nchannels=2,
        sample_rate=sample_rate,
        output_format=miniaudio.SampleFormat.SIGNED16,
        buffersize_msec=buffer_msec,
    ) as dev:
        synth = Synthesizer(sample_rate=sample_rate, polyphony=polyphony)
        stream = synth.stereo_out()
        init(stream)
        dev.start(stream)
        try:
            asyncio.run(async_main(synth, cfg["midi-in"]))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    uvloop.install()
    main()
