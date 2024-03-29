#!/usr/bin/env python3
"""See the docstring to main()."""

from __future__ import annotations
from typing import *

from array import array
import asyncio
import configparser
from dataclasses import dataclass, field
import gc
from pathlib import Path
from time import monotonic

import click
import miniaudio
import uvloop

from . import profiling


MAX_BUFFER = 2400  # 5 ms at 48000 Hz
CURRENT_DIR = Path(__file__).parent
DEBUG = True
PROFILE_AUDIO_THREAD = False


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
    profiler: profiling.Profile | None = None
    _want_frames: int = field(init=False)
    _data: list[float] = field(init=False)
    _underruns: int = field(init=False)
    _latency_ringbuf: array[float] = field(init=False)
    _latency_ts: float = field(init=False)
    _latency_index: int = field(init=False)
    _processing_ts: float = field(init=False)  # always up to date
    _proc_time: float = field(init=False)  # from the time of the lsat underrun
    _proc_stats: str = field(init=False)

    def __post_init__(self) -> None:
        self._want_frames = 0
        self._data = [0] * self.num_channels
        self._underruns = 0
        self._latency_index = -1
        self._latency_ringbuf = array("f", [0.0] * 20)
        self._latency_ts = monotonic()
        self._processing_ts = monotonic()
        self._processing_ts = 0.0
        self._proc_time = 0.0
        self._proc_stats = ""

    def audio_stream(self) -> Audio:
        buffer_format = "f"
        buffer_size = self.num_channels * MAX_BUFFER
        out_buffer = array(buffer_format, [0.0] * buffer_size)
        in_buffer = out_buffer
        channel_pairs = list(zip(self.out_channels, self.in_channels))
        channels = list(range(self.num_channels))
        lat_ringbuf_len = len(self._latency_ringbuf)

        # This generator will get updates every `max_latency` seconds. In the ideal
        # scenario each iteration would take 0.0 seconds to process so the minimal
        # observed latency is 1 * `max_latency`.
        #
        # As long as a single iteration of this generator + the framework overhead
        # don't take more than one additional `max_latency` period then the
        # audio buffer will get populated quickly enough.  If, however, it takes
        # more, there will an audible buffer underrun.  This is why we're using
        # 2 * max_latency here.
        max_latency = 2 * self.max_latency

        def copy_buffer():
            for offset in range(0, self._want_frames, self.num_channels):
                for ch in channels:
                    out_buffer[offset + ch] = 0
                    self._data[ch] += in_buffer[offset + ch]
                for out_ch, in_ch in channel_pairs:
                    out_buffer[offset + out_ch] = in_buffer[offset + in_ch]

        while True:
            now = monotonic()
            lat = now - self._latency_ts
            self._latency_index = (self._latency_index + 1) % lat_ringbuf_len
            self._latency_ringbuf[self._latency_index] = lat
            if lat > max_latency:
                self._underruns += 1
                self._proc_time = self._processing_ts
            self._data = [0] * self.num_channels
            copy_buffer()
            input_bytes = yield out_buffer[: self._want_frames]
            in_buffer = array(buffer_format, input_bytes)
            self._want_frames = len(in_buffer)
            self._processing_ts = monotonic() - now
            self._latency_ts = now

    def latency_avg(self) -> float:
        return sum(self._latency_ringbuf) / len(self._latency_ringbuf)

    def proc_time(self) -> float:
        return self._proc_time

    def wrap_data_callback(self, dev: miniaudio.AbstractDevice):
        wrapped = dev._data_callback

        def _data_callback(device, output, input, framecount):
            before = self._underruns
            wrapped(device, output, input, framecount)
            after = self._underruns
            if self.profiler:
                if after > before:
                    st = profiling.stats_from_profile(self.profiler)
                    self._proc_stats = profiling.stats_as_str(st)
                if PROFILE_AUDIO_THREAD:
                    self.profiler.disable()
                    self.profiler = profiling.Profile()
                    self.profiler.enable()
                else:
                    self.profiler.clear()

        dev._data_callback = _data_callback


async def async_main(glitch: Glitch):
    pad = " " * 10
    last_underruns = 0
    while True:
        await asyncio.sleep(0.1)
        channels = ""
        for elem in glitch._data:
            channels += f"{elem:+06.2f} "[-7:]

        new_underruns = glitch._underruns > last_underruns
        if new_underruns:
            last_underruns = glitch._underruns
            print(
                f"{channels} {glitch._underruns} {glitch.proc_time():.6f}"
                f" {glitch.latency_avg():.6f} {pad}",
                end="\r",
                flush=True,
            )
            if new_underruns:
                print()
                if DEBUG:
                    print(glitch._proc_stats)


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
    """
    Audio processor. Doesn't do anything yet besides passing data from input to output.

    You can customize the ports by creating a config file.  Use `--make-config` to
    output a new config to stdout.

    Then run `python -m aiotone.glitch --config=PATH_TO_YOUR_CONFIG_FILE`.
    """

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
        if DEBUG:
            glitch.wrap_data_callback(dev)
        with profiling.maybe(DEBUG) as profiler:
            if profiler is not None:
                glitch.profiler = profiler
            dev.start(stream)
            try:
                gc.collect(0)
                gc.collect(1)
                gc.collect(2)
                asyncio.run(async_main(glitch))
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    uvloop.install()
    main()
