#!/usr/bin/env python3
from __future__ import annotations
from typing import *

from array import array
import math
import miniaudio
import time
import cProfile
import pstats


# We want this to be symmetrical on the + and the - side.
INT16_MAXVALUE = 32767


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


def stereo_mixer() -> Audio:
    voices = [
        panning(endless_sine(88 * 3), -0.9),
        panning(endless_sine(66 * 3), 0.9),
        auto_pan(endless_sine(99), endless_sine(32768)),
        panning(endless_sine(44), 0.5),
        panning(endless_sine(88 * 4), -0.5),
    ]
    num_voices = len(voices)
    mix_down = 1 / num_voices
    stereo = [init(v) for v in voices]
    want_frames = yield stereo[0]

    out_buffer = array("h", [0] * (2 * want_frames))
    try:
        with cProfile.Profile() as pr:
            while True:
                stereo = [v.send(want_frames) for v in voices]
                for i in range(0, 2 * want_frames):
                    out_buffer[i] = int(sum([mix_down * s[i] for s in stereo]))
                want_frames = yield out_buffer[: 2 * want_frames]
    finally:
        st = pstats.Stats(pr).sort_stats(pstats.SortKey.CALLS)
        st.print_stats()
        st.sort_stats(pstats.SortKey.CUMULATIVE)
        st.print_stats()


def main() -> None:
    devices = miniaudio.Devices()
    playbacks = devices.get_playbacks()
    play_id = playbacks[1]["id"]
    stream = stereo_mixer()
    init(stream)
    with miniaudio.PlaybackDevice(
        device_id=play_id,
        nchannels=2,
        sample_rate=44100,
        output_format=miniaudio.SampleFormat.SIGNED16,
        buffersize_msec=10,
    ) as dev:
        dev.start(stream)
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()