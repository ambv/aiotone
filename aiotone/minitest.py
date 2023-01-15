from __future__ import annotations

from array import array
import miniaudio
import time
from typing import Generator, Literal


playback_name = "BlackHole 16ch"
capture_name = "BlackHole 16ch"
playback_channels = 16
capture_channels = 16
buffer_samples = 1024
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


def print_buffer() -> Generator[array[int], array[int], None]:
    buffer_size = buffer_samples * capture_channels
    buffer_format = get_buffer_format()
    buffer = array(buffer_format, [0] * buffer_size)
    nan = float("nan")
    while True:
        # NOTE: `input_bytes` is always a bytearray;
        # we need to convert to array.array manually.
        input_bytes = yield buffer
        buffer = array(buffer_format, input_bytes)
        buffer_state = ""
        saw_nan = False
        chan_sum = [0] * capture_channels
        for offset in range(0, len(buffer), capture_channels):
            for ch in range(capture_channels):
                chan_sum[ch] += buffer[offset + ch]
                if buffer[offset + ch] is nan:
                    saw_nan = True
        for ch in range(capture_channels):
            buffer_state += f"{chan_sum[ch]:+010.2f} "
        print(
            f"{buffer.typecode} {buffer.buffer_info()} {buffer_state}" + " " * 3,
            end="\r",
            flush=True,
        )
        if saw_nan:
            print("!")


if __name__ == "__main__":
    if False:
        device = miniaudio.CaptureDevice(
            sample_rate=48000,
            buffersize_msec=1,
            device_id=capture_id,
            input_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=capture_channels,
        )
    else:
        device = miniaudio.DuplexStream(
            sample_rate=48000,
            buffersize_msec=1,
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
        dev.start(stream)
        while True:
            time.sleep(0.1)
