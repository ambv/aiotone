#!/usr/bin/env python3

"""
Makes a beatfile from a spectrogram.
"""

from __future__ import annotations

from pathlib import Path
import sys

import click
from PIL import Image


Image.MAX_IMAGE_PIXELS = sys.maxsize


def is_bar(img, x) -> bool:
    val = 0
    for i in range(-1, 3):
        for j in range(16):
            val += sum(img.getpixel((x + i, 1746 + j)))
    return val > 4000


def is_beat(img, x) -> bool:
    val = 0
    for i in range(-1, 3):
        for j in range(16):
            val += sum(img.getpixel((x + i, 1746 + j)))
    return val > 2400


def is_kick(img, x) -> bool:
    val = 0
    for i in range(-1, 5):
        for j in range(32):
            val += sum(img.getpixel((x + i, 2359 + j)))
    return val > 60000


def gen_line(lines: list[str], prev_out: str) -> str:
    if "Bbk" in lines:
        return "Bbk"

    if "Bb" in lines:
        return "Bb"

    if "b" in lines and "b" not in prev_out:
        return "b"

    return ""


"""
Notes:
- 80 pixels per second
- 8 pixels per 10th of a second
- first 4 pixels are "missing"
"""


@click.command()
@click.argument("input")
@click.argument("output")
def main(input, output) -> None:
    in_path = Path(input)
    out_path = Path(output)
    img = Image.open(in_path)
    lines_buf = []
    out = ""
    with out_path.open("w") as f:
        for x in range(5, img.width - 8, 2):
            line = ""
            if is_bar(img, x):
                line += "B"
            if is_beat(img, x):
                line += "b"
            if is_kick(img, x):
                line += "k"
            lines_buf.append(line)
            if len(lines_buf) == 4:
                out = gen_line(lines_buf, out)
                f.write(out + "\n")
                lines_buf.clear()


if __name__ == "__main__":
    main()
