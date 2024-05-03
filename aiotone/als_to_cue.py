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
import sys
import xml.etree.ElementTree as ET

import click


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
@click.argument("input")
def main(input: str, show_cue: bool, show_ts: bool) -> None:
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
