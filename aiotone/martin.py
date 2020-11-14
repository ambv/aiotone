#!/usr/bin/env python3
# for Disquiet 0456: https://llllllll.co/t/disquiet-junto-project-0456-line-up/36613/

"""
martin - converts an image with horizontal lines into JSON with a list of lists of numbers

Usage:
    martin <file>
    martin --help

Options:
    -h, --help      This info.
"""

from __future__ import annotations
from typing import *

import json
import sys

import docopt
from PIL import Image


if TYPE_CHECKING:
    Line = List[float]


def gen_energy_lines(image: Image, *, threshold: int = 25) -> Iterator[Line]:
    energy_line = [0.0] * image.width
    for line in range(image.height):
        current_energy = [0.0] * image.width
        for x in range(image.width):
            current_energy[x] = (image.getpixel((x, line)) + 1) / 256
        if max(current_energy) >= threshold / 256:
            for i, energy in enumerate(current_energy):
                energy_line[i] += energy
        elif max(energy_line) >= threshold / 256:
            # End of visible line in the image, emit current data and prepare a new one.
            yield energy_line
            energy_line = [0.0] * image.width
    if max(energy_line) > threshold:
        # Left-over line at the end of the image. Emit it before exiting.
        yield energy_line


def main(file: str) -> None:
    with Image.open(file) as source:
        lines = list(gen_energy_lines(source))
    json.dump(lines, sys.stdout)


if __name__ == '__main__':
    args = docopt.docopt(__doc__)
    main(file=args['<file>'])
