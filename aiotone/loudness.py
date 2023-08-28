#!/usr/bin/env python3.9
from __future__ import annotations

from pathlib import Path
import sys

import click
import pyloudnorm as pyln

from aiotone import audiofile


@click.command()
@click.argument("file", nargs=-1)
def main(file: list[str]) -> None:
    for f in file:
        p = Path(f)
        if p.is_file():
            data, rate = audiofile.read(p)
            meter = pyln.Meter(rate)
            loudness = meter.integrated_loudness(data)
            print(f"{p}  =  {loudness:.3f}")
        else:
            print(f"{p} does not exist", file=sys.stderr)


if __name__ == "__main__":
    main()
