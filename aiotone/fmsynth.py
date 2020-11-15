#!/usr/bin/env python3
from __future__ import annotations
from typing import *

from array import array
import asyncio
import configparser
from dataclasses import dataclass
import math
from pathlib import Path

import click
import miniaudio
import uvloop

from . import profiling


# We want this to be symmetrical on the + and the - side.
INT16_MAXVALUE = 32767
CURRENT_DIR = Path(__file__).parent
DEBUG = False


if TYPE_CHECKING:
    Audio = Generator[array[int], int, None]


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


async def async_main(synth: Synthesizer) -> None:
    # TODO: respond to MIDI like in `redblue` and `mothergen`.
    i = 0
    while True:
        if i % (synth.polyphony / 2) == 0:
            await asyncio.sleep(2.5)
        else:
            await asyncio.sleep(0.1)
        synth.voices[i % synth.polyphony].reset = True
        i += 1


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
            asyncio.run(async_main(synth))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    uvloop.install()
    main()