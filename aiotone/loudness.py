#!/usr/bin/env python3.9
from __future__ import annotations

from pathlib import Path
import sys

import click
import pyloudnorm as pyln

from aiotone import audiofile


@click.command()
@click.option("--quiet", is_flag=True, default=False)
@click.argument("file", nargs=-1)
def main(quiet: bool, file: list[str]) -> None:
    for f in file:
        p = Path(f)
        if p.is_file():
            data, rate = audiofile.read(p, quiet)
            meter = pyln.Meter(rate)
            loudness = meter.integrated_loudness(data)
            print(f"{p} ({rate / 1000:.1f} kHz)  =  {loudness:.3f}")
        else:
            print(f"{p} does not exist", file=sys.stderr)


if __name__ == "__main__":
    main()
