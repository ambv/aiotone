"""This test program uses miniaudio to capture audio from Blackhole channels 1 and 2,
and passes it through to Blackhole channels 3 and 4."""

from __future__ import annotations

from array import array
import gc
import miniaudio
import time
from typing import Generator, Literal

from aiotone.array_perf import update_buffer


playback_name = "BlackHole 16ch"
capture_name = "BlackHole 16ch"
playback_channels = 16
capture_channels = 16
devices = miniaudio.Devices()


def get_device(devices: list[dict[str, object]], name: str) -> str:
    for dev in devices:
        if dev["name"] == name:
            print(dev)
            return dev["id"]  # type: ignore
    raise LookupError(name)


playback_id = get_device(devices.get_playbacks(), playback_name)
capture_id = get_device(devices.get_captures(), capture_name)


def get_buffer_format() -> Literal["i", "l", "f"]:
    for letter in "fil":
        empty = array(letter)
        if empty.itemsize == 4:
            return letter  # type: ignore

    raise LookupError("This is an unsupported machine.")


def move_audio(in_buffer: array[float], out_buffer: array[float]) -> None:
    for offset in range(0, len(out_buffer), capture_channels):
        for ch in range(capture_channels):
            if ch == 2:
                out_buffer[offset + ch] = in_buffer[offset + 0]
            elif ch == 3:
                out_buffer[offset + ch] = in_buffer[offset + 1]
            else:
                out_buffer[offset + ch] = 0.0


def print_buffer() -> Generator[memoryview | array[float] | bytes, bytes, None]:
    nan = float("nan")
    buffer_format = get_buffer_format()
    input_bytes = yield b""
    out_buffer: array[float] = array(buffer_format, input_bytes)
    out_mem = memoryview(out_buffer)
    # NOTE: `input_bytes` is always a bytearray;
    # we need to convert to array.array manually.
    in_buffer: array[float] = array(buffer_format, input_bytes)
    while True:
        in_buffer_state = ""
        saw_nan = False
        chan_sum = [0.0] * capture_channels
        for offset in range(0, len(in_buffer), capture_channels):
            for ch in range(capture_channels):
                chan_sum[ch] += in_buffer[offset + ch]
                if in_buffer[offset + ch] is nan:
                    saw_nan = True
        if buffer_format == "f":
            for ch in range(capture_channels):
                in_buffer_state += f"{chan_sum[ch]:+010.6f} "
        else:
            for ch in range(capture_channels):
                in_buffer_state += f"{chan_sum[ch]:+010.2f} "
        print(
            f"{in_buffer.typecode}{out_buffer.typecode}"
            f" {in_buffer.buffer_info()} {out_buffer.buffer_info()}"
            f" {in_buffer.itemsize} {out_buffer.itemsize}"
            f" {in_buffer_state}" + " " * 3,
            end="\r",
            flush=True,
        )
        if saw_nan:
            print("!")
        move_audio(in_buffer, out_buffer)
        input_bytes = yield out_mem
        update_buffer(in_buffer, input_bytes)


if __name__ == "__main__":
    device = miniaudio.DuplexStream(
        sample_rate=48000,
        buffersize_msec=2,
        playback_device_id=playback_id,
        playback_format=miniaudio.SampleFormat.FLOAT32,
        playback_channels=playback_channels,
        capture_device_id=capture_id,
        capture_format=miniaudio.SampleFormat.FLOAT32,
        capture_channels=capture_channels,
    )

    with device as dev:
        stream = print_buffer()
        next(stream)

        gc.freeze()  # decrease the pool of garbage-collected memory

        dev.start(stream)  # type: ignore[arg-type]

        while True:
            time.sleep(0.1)
