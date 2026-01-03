"""
Convert locators in an Ableton Live project to a .cue file.

Creates an intermediate .csv file for debugging.

NOTE: this will report wrong times if the project's tempo is variable.
"""

import csv
from dataclasses import dataclass
import gzip
from pathlib import Path
import re
import struct
import sys
import xml.etree.ElementTree as ET

import click
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3._specs import CTOCFlags, Encoding
from mutagen.id3._frames import Frame, CHAP, CTOC, TIT2
from mutagen.id3._tags import ID3Tags, save_frame
from mutagen.id3._util import ID3SaveConfig


TS_RE = re.compile(r"^(?P<h>\d+):(?P<m>\d+):(?P<s>\d+)(\.(?P<mil>\d+))?$")


def convert_timestamp(input: str) -> str:
    ts = TS_RE.match(input)
    if ts is None:
        raise ValueError("invalid input")
    h = int(ts.group("h"))
    m = int(ts.group("m"))
    s = int(ts.group("s"))
    mil = int(ts.group("mil") or 0) // 10
    return f"{60 * h + m:0>2}:{s:0>2}:{mil:0>2}"


def convert_als_to_csv(input: Path) -> Path:
    output = input.with_suffix(".csv")

    f = gzip.open(input)

    tree = ET.parse(f)
    root = tree.getroot()

    path = "DeviceChain/Mixer/Tempo/Manual"
    tempo: float = 120.0
    if (tempo_el := root.find(f"LiveSet/MasterTrack/{path}")) is not None:
        tempo = float(tempo_el.get("Value") or 120.0)
    elif (tempo_el := root.find(f"LiveSet/MainTrack/{path}")) is not None:
        tempo = float(tempo_el.get("Value") or 120.0)
    else:
        print("warning: tempo assumed as 120 BPM", file=sys.stderr)

    divider = tempo / 60

    locators = root.find("*/Locators")
    if not locators:
        raise LookupError(f"Couldn't find locators in document: {input}")

    with output.open("w", newline="") as csvfile:
        writer = csv.writer(
            csvfile, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL
        )
        for locator in locators.iterfind("Locators/Locator"):
            time_el = locator.find("Time")
            name_el = locator.find("Name")
            if time_el is None or name_el is None:
                continue
            t = float(time_el.get("Value") or 0) / divider
            time = f"{t // 3600:01.0f}:{(t % 3600) // 60:02.0f}:{int(t % 60):02d}.{1000 * (t - int(t)):03.0f}"
            name = name_el.get("Value")
            writer.writerow(["", name, time, t])

    return output


@dataclass
class Chapter:
    title: str
    timestamp: float
    ts_text_minutes: str
    ts_text_hours: str


def embed_chapters_flac(chapters: list[Chapter], file_path: Path) -> None:
    """Embed chapters as Vorbis Comment Chapter Extension tags in a FLAC file.

    Uses the format from https://wiki.xiph.org/Chapter_Extension:
    CHAPTERxxx=HH:MM:SS.mmm
    CHAPTERxxxNAME=Chapter Title
    """
    audio = FLAC(file_path)

    keys_to_remove = [key for key in audio.keys() if key.upper().startswith("CHAPTER")]
    for key in keys_to_remove:
        del audio[key]

    for i, chapter in enumerate(chapters, 1):
        # Format: HH:MM:SS.mmm
        h = int(chapter.timestamp // 3600)
        m = int((chapter.timestamp % 3600) // 60)
        s = int(chapter.timestamp % 60)
        ms = int((chapter.timestamp - int(chapter.timestamp)) * 1000)
        timestamp = f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

        audio[f"CHAPTER{i:03d}"] = timestamp
        audio[f"CHAPTER{i:03d}NAME"] = chapter.title

    audio.save()
    print(f"Embedded {len(chapters)} chapters in {file_path}")


def embed_chapters_wav(chapters: list[Chapter], file_path: Path) -> None:
    """Embed chapters as cue points with labels in a WAV file.

    Uses the standard RIFF WAV format:
    - 'cue ' chunk: contains cue points with sample positions
    - 'LIST' chunk (type 'adtl'): contains 'labl' sub-chunks with labels
    """
    with open(file_path, "rb") as f:
        riff = f.read(4)
        if riff != b"RIFF":
            raise ValueError(f"Not a RIFF file: {file_path}")
        riff_size = struct.unpack("<I", f.read(4))[0]
        wave = f.read(4)
        if wave != b"WAVE":
            raise ValueError(f"Not a WAVE file: {file_path}")

        chunks: list[tuple[bytes, bytes]] = []
        sample_rate = 48000  # default, will be read from fmt chunk
        while f.tell() < riff_size + 8:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size = struct.unpack("<I", f.read(4))[0]
            chunk_data = f.read(chunk_size)
            if chunk_size % 2:
                f.read(1)  # padding byte

            if chunk_id == b"fmt ":
                sample_rate = struct.unpack("<I", chunk_data[4:8])[0]
                chunks.append((chunk_id, chunk_data))
            elif chunk_id in (b"cue ", b"LIST"):
                pass  # skip existing cue and LIST chunks
            else:
                chunks.append((chunk_id, chunk_data))

    # Build cue chunk
    num_cues = len(chapters)
    cue_data = struct.pack("<I", num_cues)
    for i, chapter in enumerate(chapters, 1):
        sample_pos = int(chapter.timestamp * sample_rate)
        # dwName, dwPosition, fccChunk, dwChunkStart, dwBlockStart, dwSampleOffset
        cue_data += struct.pack("<II4sIII", i, sample_pos, b"data", 0, 0, sample_pos)

    adtl_data = b"adtl"
    for i, chapter in enumerate(chapters, 1):
        label = chapter.title.encode("utf-8") + b"\x00"
        if len(label) % 2:
            label += b"\x00"  # pad to even length
        labl_data = struct.pack("<I", i) + label
        adtl_data += b"labl" + struct.pack("<I", len(labl_data)) + labl_data

    with open(file_path, "wb") as f:
        total_size = 4  # 'WAVE'
        for chunk_id, chunk_data in chunks:
            total_size += 8 + len(chunk_data)
            if len(chunk_data) % 2:
                total_size += 1
        total_size += 8 + len(cue_data)  # cue chunk
        if len(cue_data) % 2:
            total_size += 1
        total_size += 8 + len(adtl_data)  # LIST chunk
        if len(adtl_data) % 2:
            total_size += 1

        f.write(b"RIFF")
        f.write(struct.pack("<I", total_size))
        f.write(b"WAVE")

        for chunk_id, chunk_data in chunks:
            f.write(chunk_id)
            f.write(struct.pack("<I", len(chunk_data)))
            f.write(chunk_data)
            if len(chunk_data) % 2:
                f.write(b"\x00")

        f.write(b"cue ")
        f.write(struct.pack("<I", len(cue_data)))
        f.write(cue_data)
        if len(cue_data) % 2:
            f.write(b"\x00")

        f.write(b"LIST")
        f.write(struct.pack("<I", len(adtl_data)))
        f.write(adtl_data)
        if len(adtl_data) % 2:
            f.write(b"\x00")

    print(f"Embedded {len(chapters)} chapters in {file_path}")


def _id3_write_ordered(self: ID3Tags, config: ID3SaveConfig) -> bytearray:
    """Modified ID3Tags._write that preserves CHAP frame order.

    Mutagen's default _write sorts frames by size, scrambling chapter order.
    This version sorts CHAP frames by their element_id number instead.
    """
    order = ["TIT2", "TPE1", "TRCK", "TALB", "TPOS", "TDRC", "TCON"]
    frame_data = [(f, save_frame(f, config=config)) for f in self.values()]

    def _get_prio(frame: Frame) -> int:
        try:
            return order.index(frame.FrameID)
        except ValueError:
            return len(order)

    def _sort_key(items: tuple) -> tuple[int, int, int | str]:
        frame, data = items
        prio = _get_prio(frame)
        if frame.FrameID == "CHAP":
            try:
                chap_num = int(frame.element_id[3:])
            except (ValueError, AttributeError):
                chap_num = 0
            return (prio, 0, chap_num)
        if frame.FrameID == "CTOC":
            return (prio, 0, -1)
        return (prio, len(data), frame.HashKey)

    frame_bytes: list[bytes] = [d for (_, d) in sorted(frame_data, key=_sort_key)]
    if self._unknown_v2_version == config.v2_version:
        frame_bytes.extend(data for data in self.unknown_frames if len(data) > 10)
    return bytearray().join(frame_bytes)


def embed_chapters_mp3(chapters: list[Chapter], file_path: Path) -> None:
    """Embed chapters as ID3 CHAP frames with a CTOC table of contents.

    Uses ID3v2.3 CHAP and CTOC frames as described in:
    https://id3.org/id3v2-chapters-1.0
    """
    audio = MP3(file_path)
    if audio.tags is None:
        audio.add_tags()
        assert audio.tags is not None

    keys_to_remove = [
        key
        for key in audio.tags.keys()
        if key.startswith("CHAP:") or key.startswith("CTOC:")
    ]
    for key in keys_to_remove:
        del audio.tags[key]

    duration_ms = int(audio.info.length * 1000)

    child_ids = []
    for i, chapter in enumerate(chapters):
        element_id = f"chp{i}"
        child_ids.append(element_id)

        start_ms = int(chapter.timestamp * 1000)
        if i + 1 < len(chapters):
            end_ms = int(chapters[i + 1].timestamp * 1000)
        else:
            end_ms = duration_ms

        chap = CHAP(
            element_id=element_id,
            start_time=start_ms,
            end_time=end_ms,
            start_offset=0xFFFFFFFF,
            end_offset=0xFFFFFFFF,
            sub_frames=[TIT2(encoding=Encoding.UTF16, text=[chapter.title])],
        )
        audio.tags.add(chap)

    ctoc = CTOC(
        element_id="toc",
        flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
        child_element_ids=child_ids,
        sub_frames=[],
    )
    audio.tags.add(ctoc)

    # Temporarily monkey-patch ID3Tags._write to preserve CHAP order
    original_write = ID3Tags._write
    ID3Tags._write = _id3_write_ordered
    try:
        audio.save()
    finally:
        ID3Tags._write = original_write

    print(f"Embedded {len(chapters)} chapters in {file_path}")


@click.command()
@click.option(
    "-c/-C",
    "--show-cue/--hide-cue",
    is_flag=True,
    show_default=True,
    default=False,
    help="Display the .cue sheet file",
)
@click.option(
    "-t/-T",
    "--show-ts/--hide-ts",
    is_flag=True,
    show_default=True,
    default=True,
    help="Display concise timestamps",
)
@click.option(
    "--embed",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Embed chapter markers in the specified FLAC, WAV, or MP3 file",
)
@click.argument("input")
def main(input: str, show_cue: bool, show_ts: bool, embed: Path | None) -> None:
    input_path = Path(input)
    if input_path.suffix == ".als":
        input_path = convert_als_to_csv(input_path)
    output_path = input_path.with_suffix(".cue")
    chapters = []
    with open(input_path) as f_in:
        csv_file = csv.reader(f_in)
        for row in csv_file:
            try:
                ts_raw = float(row[3])
                ts_hours = row[2]
                ts_mins = convert_timestamp(ts_hours)
            except (IndexError, ValueError):
                print("invalid row:", row, file=sys.stderr)
                raise
            c = Chapter(
                title=row[1],
                timestamp=ts_raw,
                ts_text_minutes=ts_mins,
                ts_text_hours=ts_hours.split(".")[0],
            )
            chapters.append(c)

    chapters.sort(key=lambda c: c.timestamp)

    if embed is not None:
        suffix = embed.suffix.lower()
        if suffix == ".flac":
            embed_chapters_flac(chapters, embed)
        elif suffix == ".wav":
            embed_chapters_wav(chapters, embed)
        elif suffix == ".mp3":
            embed_chapters_mp3(chapters, embed)
        else:
            raise click.ClickException(f"Unsupported file format: {suffix}")

    with open(output_path, "w") as f_out:
        f_out.write('TITLE ""\n')
        f_out.write('FILE "" WAVE\n')
        for i, c in enumerate(chapters, 1):
            f_out.write(f"  TRACK {i:0>2} AUDIO\n")
            f_out.write(f'    TITLE "{c.title}"\n')
            f_out.write(f"    INDEX 01 {c.ts_text_minutes}\n")

    if show_cue:
        with open(output_path) as f:
            for i, line in enumerate(f.readlines()):
                print(i + 1, line.rstrip())

    if show_ts:
        for c in chapters:
            print(f"[{c.ts_text_hours:>08}] {c.title}")


if __name__ == "__main__":
    main()
