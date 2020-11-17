#!/usr/bin/env python3
"""See the docstring to main()."""

from __future__ import annotations
from typing import *

from array import array
import asyncio
from collections import defaultdict
import configparser
from dataclasses import dataclass
import math
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


# We want this to be symmetrical on the + and the - side.
INT16_MAXVALUE = 32767
CURRENT_DIR = Path(__file__).parent
DEBUG = False


if TYPE_CHECKING:
    Audio = Generator[array[int], int, None]
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


def sine_array(sample_count: int) -> array[int]:
    """Return a monophonic signed 16-bit wavetable with a single sine cycle."""
    numbers = []
    for i in range(sample_count):
        current = round(INT16_MAXVALUE * math.sin(i / sample_count * math.tau))
        numbers.append(current)
    return array("h", numbers)


def endless_sine(sample_count: int) -> Audio:
    sine = sine_array(sample_count)
    result = array("h")
    want_frames = yield result

    result.extend([0] * want_frames)
    sine_i = 0
    while True:
        for res_i in range(want_frames):
            result[res_i] = sine[sine_i]
            sine_i += 1
            if sine_i == sample_count:
                sine_i = 0
        want_frames = yield result[:want_frames]


def panning(mono: Audio, pan: float = 0.0) -> Audio:
    result = init(mono)
    want_frames = yield result

    out_buffer = array("h", [0] * (2 * want_frames))
    while True:
        mono_buffer = mono.send(want_frames)
        for i in range(want_frames):
            out_buffer[2 * i] = int((-pan + 1) / 2 * mono_buffer[i])
            out_buffer[2 * i + 1] = int((pan + 1) / 2 * mono_buffer[i])
        want_frames = yield out_buffer[: 2 * want_frames]


def auto_pan(mono: Audio, panner: Audio) -> Audio:
    result = init(mono)
    result = init(panner)
    want_frames = yield result

    out_buffer = array("h", [0] * (2 * want_frames))
    while True:
        mono_buffer = mono.send(want_frames)
        panning = panner.send(want_frames)
        for i in range(want_frames):
            pan = panning[i] / INT16_MAXVALUE
            out_buffer[2 * i] = int((-pan + 1) / 2 * mono_buffer[i])
            out_buffer[2 * i + 1] = int((pan + 1) / 2 * mono_buffer[i])
        want_frames = yield out_buffer[: 2 * want_frames]


class Synthesizer:
    def __init__(self, *, polyphony: int) -> None:
        self.polyphony = polyphony
        self.reset_voices()

    def reset_voices(self) -> None:
        polyphony = self.polyphony
        operators = self._gen_operators()
        self.panning = [(2 * i / (polyphony - 1) - 1) for i in range(polyphony)]
        self.voices = [next(operators) for i in range(polyphony)]
        self._note_on_counter = 0

    def stereo_out(self) -> Audio:
        """A stereo mixer."""

        voices = [
            panning(self.voices[i].mono_out(), self.panning[i])
            for i in range(self.polyphony)
        ]

        mix_down = 1 / self.polyphony
        stereo = [init(v) for v in voices]
        want_frames = yield stereo[0]

        out_buffer = array("h", [0] * (2 * want_frames))
        with profiling.maybe(DEBUG):
            while True:
                stereo = [v.send(want_frames) for v in voices]
                for i in range(0, 2 * want_frames):
                    out_buffer[i] = int(sum([mix_down * s[i] for s in stereo]))
                want_frames = yield out_buffer[: 2 * want_frames]

    def _gen_operators(self) -> Iterator[Operator]:
        while True:
            yield Operator(endless_sine(88 * 3), a=48, d=48000)
            yield Operator(endless_sine(66 * 3), a=480, d=48000)
            yield Operator(endless_sine(99), a=4800, d=48000)
            yield Operator(endless_sine(44), a=96000, d=48000)
            yield Operator(endless_sine(88 * 4), a=48000, d=48000)

    # MIDI support

    async def clock(self) -> None:
        ...

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def note_on(self, note: int, volume: int) -> None:
        self.voices[self._note_on_counter % self.polyphony].reset = True
        self._note_on_counter += 1

    async def note_off(self, note: int, volume: int) -> None:
        ...

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
        ...

    async def all_notes_off(self, value: int) -> None:
        ...


@dataclass
class Operator:
    wave: Audio
    a: int  # in number of samples
    d: int  # in number of samples
    volume: float = 0.0  # 0.0 - 1.0; current volume, not used for relative attenuation
    samples_since_reset: int = -1
    reset: bool = False

    def mono_out(self) -> Audio:
        """A resettable envelope."""
        result = init(self.wave)
        want_frames = yield result

        out_buffer = array("h", [0] * want_frames)
        while True:
            wave = self.wave
            volume = self.volume
            a = self.a
            d = self.d
            samples_since_reset = self.samples_since_reset
            if samples_since_reset == -1:
                for i in range(want_frames):
                    out_buffer[i] = 0
            else:
                mono_buffer = wave.send(want_frames)
                for i in range(want_frames):
                    if samples_since_reset <= a:
                        volume = samples_since_reset / a
                    elif samples_since_reset - a <= d:
                        volume = 1.0 - (samples_since_reset - a) / d
                    else:
                        volume = 0.0
                    out_buffer[i] = int(volume * mono_buffer[i])
                    samples_since_reset += 1
            if self.reset:
                self.reset = False
                self.samples_since_reset = 0
                self.volume = 0.0
            elif volume > 0:
                self.samples_since_reset = samples_since_reset
                self.volume = volume
            else:
                self.samples_since_reset = -1
                self.volume = 0.0
            want_frames = yield out_buffer[:want_frames]


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
                await synth.note_on(msg[1], msg[2])
            elif t == NOTE_OFF:
                await synth.note_off(msg[1], msg[2])
            elif t == CONTROL_CHANGE:
                last[msg[1]] = msg[2]
                if msg[1] == MOD_WHEEL:
                    await synth.mod_wheel(128 * msg[2] + last[MOD_WHEEL_LSB])
                elif msg[1] == MOD_WHEEL_LSB:
                    await synth.mod_wheel(128 * last[MOD_WHEEL] + msg[2])
                if msg[1] == EXPRESSION_PEDAL:
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
    controllable by MIDI. Note that this is very CPU-intensive.

    It's got the following features:

    - configurable polyphony;

    - AD envelope (no sustain yet);

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

    devices = miniaudio.Devices()
    playbacks = devices.get_playbacks()
    audio_out = cfg["audio-out"]["out-name"]
    sample_rate = cfg["audio-out"].getint("sample-rate")
    buffer_msec = cfg["audio-out"].getint("buffer-msec")
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
        synth = Synthesizer(polyphony=10)
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