#!/usr/bin/env python3
"""See the docstring to main()."""

from __future__ import annotations
from typing import *

from array import array
import asyncio
import configparser
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

import click
import miniaudio
import uvloop

from . import profiling


MAX_BUFFER = 2400  # 5 ms at 48000 Hz
CURRENT_DIR = Path(__file__).parent
DEBUG = False


if TYPE_CHECKING:
    Audio = Generator[array[float], array[float], None]
    TimeStamp = float  # time.time()


# For clarity we're aliasing `next` because we are using it as an initializer of
# stateful generators to execute until (and including) its first `yield` expression
# to stop right before assigning a value sent to the generator.  Now the generator
# is ready to accept `.send(value)`.
# Note: due to this initialization, the first yield in Audio generators returns an
# empty array.
init = next


@dataclass
class Glitch:
    num_channels: int
    out_channels: tuple[int, int]
    in_channels: tuple[int, int]
    max_latency: float
    _want_frames: int = field(init=False)
    _data: list[float] = field(init=False)
    _underruns: int = field(init=False)
    _latency_ringbuf: array[float] = field(init=False)
    _latency_ts: float = field(init=False)
    _latency_index: int = field(init=False)

    def __post_init__(self) -> None:
        self._want_frames = 0
        self._data = [0] * self.num_channels
        self._underruns = 0
        self._latency_index = -1
        self._latency_ringbuf = array("f", [0.0] * 20)
        self._latency_ts = monotonic()

    def audio_stream(self) -> Audio:
        buffer_format = "f"
        buffer_size = self.num_channels * MAX_BUFFER
        out_buffer = array(buffer_format, [0.0] * buffer_size)
        in_buffer = out_buffer
        channel_pairs = list(zip(self.out_channels, self.in_channels))
        channels = list(range(self.num_channels))
        max_latency = 2 * self.max_latency
        lat_ringbuf_len = len(self._latency_ringbuf)

        def copy_buffer():
            for offset in range(0, self._want_frames, self.num_channels):
                for ch in channels:
                    out_buffer[offset + ch] = 0
                    self._data[ch] += in_buffer[offset + ch]
                for out_ch, in_ch in channel_pairs:
                    out_buffer[offset + out_ch] = in_buffer[offset + in_ch]

        with profiling.maybe(DEBUG):
            while True:
                now = monotonic()
                lat = now - self._latency_ts
                self._latency_ts = now
                self._latency_index = (self._latency_index + 1) % lat_ringbuf_len
                self._latency_ringbuf[self._latency_index] = lat
                if lat > max_latency:
                    self._underruns += 1
                self._data = [0] * self.num_channels
                copy_buffer()
                input_bytes = yield out_buffer[: self._want_frames]
                in_buffer = array(buffer_format, input_bytes)
                self._want_frames = len(in_buffer)

    def latency_avg(self) -> float:
        return sum(self._latency_ringbuf) / len(self._latency_ringbuf)


async def async_main(glitch: Glitch):
    pad = " " * 10
    while True:
        await asyncio.sleep(0.1)
        channels = ""
        for elem in glitch._data:
            channels += f"{elem:+06.2f} "[-6:]
        print(
            f"{channels} {glitch._underruns} {glitch.latency_avg():.6f} {pad}",
            end="\r",
            flush=True,
        )


def channel_tuple_from_string(channels: str) -> tuple[int, int]:
    first, second = channels.split("+", 1)
    return int(first) - 1, int(second) - 1


@click.command()
@click.option(
    "--config",
    help="Read configuration from this file",
    default=str(CURRENT_DIR / "aiotone-glitch.ini"),
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
        with open(CURRENT_DIR / "aiotone-glitch.ini") as f:
            print(f.read())
        return

    cfg = configparser.ConfigParser(
        converters={"channels": channel_tuple_from_string},
    )
    cfg.read(config)

    # Apparently, miniaudio (at least on Linux) doesn't enumerate devices across all backends.
    # So if we want to use a device on a non-default backend, we need to specify the backend.
    backend_name = cfg["audio"].get("backend")
    if backend_name:
        backend = getattr(miniaudio.Backend, backend_name)
        devices = miniaudio.Devices([backend])
    else:
        devices = miniaudio.Devices()
    playbacks = devices.get_playbacks()
    captures = devices.get_captures()
    audio_name = cfg["audio"]["io-name"]
    sample_rate = cfg["audio"].getint("sample-rate")
    buffer_msec = cfg["audio"].getint("buffer-msec")
    num_channels = cfg["audio"].getint("num-channels")

    for playback in playbacks:
        if playback["name"] == audio_name:
            play_id = playback["id"]
            break
    else:
        playback_names = ", ".join(sorted([p["name"] for p in playbacks]))
        raise click.UsageError(
            f"No audio out available called {audio_name} among {playback_names}"
        )

    for capture in captures:
        if capture["name"] == audio_name:
            capture_id = capture["id"]
            break
    else:
        capture_names = ", ".join(sorted([c["name"] for c in captures]))
        raise click.UsageError(
            f"No audio in available called {audio_name} among {capture_names}"
        )

    in_channels = cfg["audio"].getchannels("in-channels")
    out_channels = cfg["audio"].getchannels("out-channels")

    with miniaudio.DuplexStream(
        sample_rate=sample_rate,
        buffersize_msec=buffer_msec,
        playback_device_id=play_id,
        playback_format=miniaudio.SampleFormat.FLOAT32,
        playback_channels=num_channels,
        capture_device_id=capture_id,
        capture_format=miniaudio.SampleFormat.FLOAT32,
        capture_channels=num_channels,
    ) as dev:
        glitch = Glitch(
            num_channels=num_channels,
            in_channels=in_channels,
            out_channels=out_channels,
            max_latency=buffer_msec / 1000,
        )
        stream = glitch.audio_stream()
        init(stream)
        dev.start(stream)
        try:
            asyncio.run(async_main(glitch))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    uvloop.install()
    main()
