"""Multisampler.

NEXT STEPS:
- [x] play a note via MIDI with some set velocity
- [ ] record the sample until silence
- [ ] left and right silence trim
- [ ] save a WAV file
"""

from __future__ import annotations

from array import array
import configparser
import gc
from pathlib import Path
import re
from threading import Event
import time
from typing import Generator, Literal

import click
import miniaudio

from .array_perf import update_buffer, move_audio
from . import midi, notes


# types
EventDelta = float  # in seconds
TimeStamp = float  # time.time()
MidiPacket = list[int]
MidiMessage = tuple[MidiPacket, EventDelta, TimeStamp]


CURRENT_DIR = Path(__file__).parent
CONFIG = CURRENT_DIR / "aiotone-samplesnake.ini"
CONFIGPARSER_FALSE = {
    k
    for k, v in configparser.ConfigParser.BOOLEAN_STATES.items()  # type: ignore
    if v is False
}
SILENCE = Event()


def get_device(devices: list[dict[str, object]], name: str) -> str:
    for dev in devices:
        if dev["name"] == name:
            print(dev)
            return dev["id"]  # type: ignore
    raise LookupError(name)


def get_buffer_format() -> Literal["i", "l", "f"]:
    for letter in "fil":
        empty = array(letter)
        if empty.itemsize == 4:
            return letter  # type: ignore

    raise LookupError("This is an unsupported machine.")


def move_audio_py(
    in_buffer: array[float],
    in_l: int,
    in_r: int,
    out_buffer: array[float],
    out_l: int,
    out_r: int,
    channel_sum: array[float],
) -> None:
    chlen = len(channel_sum)
    for offset in range(0, len(out_buffer), chlen):
        for ch in range(chlen):
            channel_sum[ch] += abs(in_buffer[offset + ch])

            if ch == out_l:
                out_buffer[offset + ch] = in_buffer[offset + in_l]
            elif ch == out_r:
                out_buffer[offset + ch] = in_buffer[offset + in_r]
            else:
                out_buffer[offset + ch] = 0.0


def process_audio(
    channel_count: int,
    record_channels: list[int],
    play_channels: list[int],
    silence_threshold: float,
) -> Generator[memoryview | array[float] | bytes, bytes, None]:
    nan = float("nan")
    buffer_format = get_buffer_format()
    input_bytes = yield b""
    out_buffer: array[float] = array(buffer_format, input_bytes)
    out_mem = memoryview(out_buffer)
    # NOTE: `input_bytes` is always a bytearray;
    # we need to convert to array.array manually.
    in_buffer: array[float] = array(buffer_format, input_bytes)
    chan_sum = array(buffer_format, [0.0] * channel_count)
    in_l, in_r = record_channels
    out_l, out_r = play_channels
    silent_iterations = 0
    while True:
        in_buffer_state = ""
        saw_nan = False
        move_audio(in_buffer, in_l, in_r, out_buffer, out_l, out_r, chan_sum)
        for ch in range(channel_count):
            if chan_sum[ch] is nan:
                saw_nan = True
            in_buffer_state += f"{chan_sum[ch]:+09.5f} "
        if chan_sum[in_l] < silence_threshold and chan_sum[in_r] < silence_threshold:
            if not SILENCE.is_set():
                silent_iterations += 1
                if silent_iterations >= 50:
                    SILENCE.set()
                    silent_iterations = 0
        else:
            SILENCE.clear()

        print(
            f"{in_buffer.typecode}{out_buffer.typecode}"
            # f" {in_buffer.buffer_info()}"
            # f" {out_buffer.buffer_info()}"
            # f" {in_buffer.itemsize} {out_buffer.itemsize}"
            f" {silent_iterations:02d} {SILENCE.is_set()}"
            f" {in_buffer_state}" + " " * 3,
            end="\r",
            flush=True,
        )
        if saw_nan:
            print("!")
        input_bytes = yield out_mem
        update_buffer(in_buffer, input_bytes)
        for ch in range(channel_count):
            chan_sum[ch] = 0.0


def play_notes(
    midi_out: midi.MidiOut, channel: int, sampling: configparser.SectionProxy
) -> None:
    hold = sampling.getseconds("hold")
    cooldown = sampling.getseconds("cooldown")
    midi.silence(midi_out, channels=[channel])
    for note in sampling.getnotes("notes"):
        for octave in sorted(sampling.getintlist("octaves")):
            for velocity in sorted(sampling.getintlist("velocities")):
                click.secho("\nWaiting for silence...", fg="blue")
                SILENCE.wait()
                time.sleep(cooldown)
                n = note[octave]
                n_str = notes.note_to_name[n]
                click.secho(f"\n{n_str} NOTE ON at {velocity}...", fg="green")
                midi_out.send_message([midi.NOTE_ON | channel, n, velocity])
                time.sleep(hold)
                midi_out.send_message([midi.NOTE_OFF | channel, n, velocity])
                click.secho(f"\n{n_str} NOTE OFF...", fg="yellow")
    click.secho("\nWaiting for silence...", fg="blue")
    SILENCE.wait()
    click.secho("\nAll notes done.", fg="magenta")


def convert_intlist(s: str) -> list[int]:
    return [int(n.strip()) for n in s.split(",")]


def convert_channels(s: str) -> list[int]:
    return [int(ch.strip()) - 1 for ch in s.split(",")]


def convert_notes(s: str) -> list[notes.Notes]:
    names = [n.strip() for n in s.split(",")]
    result = []
    for name in names:
        name = name.replace("#", "s")
        note = getattr(notes, name)
        if note not in notes.all_notes:
            raise ValueError(f"not a note: {name}")
        result.append(note)
    return result


SECOND_RE = re.compile(r"^\s*(?P<num>\d+(\.\d+)?)(?P<unit>[mu]?s)\s*$")


def convert_seconds(s: str) -> float:
    if match := SECOND_RE.match(s):
        num = float(match.group("num"))
        unit = match.group("unit")
        if unit == "us":
            return num / 1_000_000
        if unit == "ms":
            return num / 1_000
        return num
    return float(s.strip())


@click.command()
@click.option(
    "--config",
    help="Read configuration from this file",
    default=str(CONFIG),
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
    You can customize the ports by creating a config file.  Use `--make-config` to
    output a new config to stdout.

    Then run `python -m aiotone.samplesnake --config=PATH_TO_YOUR_CONFIG_FILE`.
    """
    if make_config:
        print(CONFIG.read_text())
        return

    cfg = configparser.ConfigParser(
        converters={
            "channels": convert_channels,
            "notes": convert_notes,
            "intlist": convert_intlist,
            "seconds": convert_seconds,
        },
    )
    cfg.read(config)
    audio_in = cfg["audio-in"]
    audio_out = cfg["audio-out"]
    if audio_in.getint("sample-rate") != audio_out.getint("sample-rate"):
        click.secho(
            "resampling not supported;"
            " use the same sample rate in audio-in and audio-out",
            fg="red",
        )
        raise click.Abort

    if audio_in.getint("channels") != audio_out.getint("channels"):
        click.secho(
            "number of channels must be the same in audio-in and audio-out",
            fg="red",
        )
        raise click.Abort

    playback_name = audio_out["name"]
    playback_channel_count = audio_out.getint("channel-count")
    playback_channels = audio_out.getchannels("play")
    capture_name = audio_in["name"]
    capture_channel_count = audio_in.getint("channel-count")
    capture_channels = audio_in.getchannels("record")
    sampling = cfg["sampling"]
    silence_threshold = sampling.getfloat("silence-threshold")

    audio_devices = miniaudio.Devices()
    playback_id = get_device(audio_devices.get_playbacks(), playback_name)
    capture_id = get_device(audio_devices.get_captures(), capture_name)

    audio_device = miniaudio.DuplexStream(
        sample_rate=audio_in.getint("sample-rate"),
        buffersize_msec=2,
        playback_device_id=playback_id,
        playback_format=miniaudio.SampleFormat.FLOAT32,
        playback_channels=playback_channel_count,
        capture_device_id=capture_id,
        capture_format=miniaudio.SampleFormat.FLOAT32,
        capture_channels=capture_channel_count,
    )

    try:
        midi_out = midi.get_output(cfg["midi-out"]["name"])
    except ValueError as port:
        click.secho(f"midi-out port {port} not connected", fg="red", err=True)
        raise click.Abort

    try:
        midi_channel = cfg["midi-out"].getint("channel") - 1
    except (ValueError, TypeError):
        click.secho("midi-out channel must be an integer", fg="red", err=True)
        raise click.Abort

    with audio_device as audio:
        stream = process_audio(
            capture_channel_count,
            capture_channels,
            playback_channels,
            silence_threshold,
        )
        next(stream)
        gc.freeze()  # decrease the pool of garbage-collected memory
        audio.start(stream)  # type: ignore[arg-type]

        play_notes(midi_out, midi_channel, sampling)


if __name__ == "__main__":
    main()
