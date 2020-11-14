#!/usr/bin/env python3
from __future__ import annotations
from typing import *

from array import array
import math
import miniaudio
import time


def sine_array(sample_count: int) -> array[int]:
    int16_maxvalue = 32767
    numbers = []
    for i in range(sample_count):
        current = round(int16_maxvalue * math.sin(i / sample_count * math.tau))
        numbers.append(current)
    return array("h", numbers)


def endless_sine(sample_count: int) -> Generator[array[int], int, None]:
    sine = sine_array(sample_count)
    result = array("h")
    want_frames = yield result
    i = 0
    while True:
        result = array("h")
        left = want_frames
        while left:
            left = want_frames - len(result)
            result.extend(sine[i : i + left])
            i += left
            if i > sample_count - 1:
                i = 0
        # print(want_frames, i, len(result))
        want_frames = yield result


def stereo_mixer() -> Generator[array[int], int, None]:
    result = array("h")
    sin = endless_sine(88 * 3)
    next(sin)
    sin2 = endless_sine(66 * 3)
    next(sin2)
    sin3 = endless_sine(99)
    next(sin3)

    want_frames = yield result
    out_buffer = array("h", [0] * (2 * want_frames))

    while True:
        mono = sin.send(want_frames)
        mono2 = sin2.send(want_frames)
        mono3 = sin3.send(want_frames)
        for i in range(want_frames):
            out_buffer[2 * i] = int(
                0.33 * 0.9 * mono[i] + 0.33 * 0.1 * mono2[i] + 0.33 * 0.5 * mono3[i]
            )
            out_buffer[2 * i + 1] = int(
                0.33 * 0.1 * mono[i] + 0.33 * 0.9 * mono2[i] + 0.33 * 0.5 * mono3[i]
            )
        want_frames = yield out_buffer[: 2 * want_frames]


def trace(gen):
    r = []
    s = yield None
    while True:
        print(s, len(r))
        try:
            r = gen.send(s)
        except StopIteration:
            return
        s = yield r


def main() -> None:
    devices = miniaudio.Devices()
    playbacks = devices.get_playbacks()
    play_id = playbacks[1]["id"]
    if True:
        stream = stereo_mixer()
        next(stream)
    if False:
        wav = ".local/wt1.wav"
        stream = trace(miniaudio.stream_file(wav))
        next(stream)
    with miniaudio.PlaybackDevice(
        device_id=play_id,
        nchannels=2,
        sample_rate=44100,
        output_format=miniaudio.SampleFormat.SIGNED16,
        buffersize_msec=10,
    ) as dev:
        dev.start(stream)
        while True:
            time.sleep(0.1)


if __name__ == "__main__":
    main()