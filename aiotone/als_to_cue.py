"""
Convert locators in an Ableton Live project to a .cue file.

Creates an intermediate .csv file for debugging.

NOTE: this will report wrong times if the project's tempo is variable.
"""

import csv
import gzip
from pathlib import Path
import re
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

    tempo: float = 120.0
    tempo_el = root.find("*/MasterTrack/DeviceChain/Mixer/Tempo/Manual")
    if tempo_el is not None:
        tempo = float(tempo_el.get("Value") or 120.0)

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
            writer.writerow(["", name, time])

    return output


@click.command()
# @click.option("--quiet", is_flag=True, default=False)
@click.argument("input")
def main(input: str) -> None:
    input_path = Path(input)
    if input_path.suffix == ".als":
        input_path = convert_als_to_csv(input_path)
    output_path = input_path.with_suffix(".cue")
    track = 0
    with open(input_path) as f_in, open(output_path, "w") as f_out:
        c = csv.reader(f_in)
        f_out.write('TITLE ""\n')
        f_out.write('FILE "" WAVE\n')
        for row in c:
            track += 1
            timestamp = convert_timestamp(row[2])
            f_out.write(f"  TRACK {track:0>2} AUDIO\n")
            f_out.write(f'    TITLE "{row[1]}"\n')
            f_out.write(f"    INDEX 01 {timestamp}\n")
    with open(output_path) as f:
        for i, line in enumerate(f.readlines()):
            print(i + 1, line.rstrip())


if __name__ == "__main__":
    main()
