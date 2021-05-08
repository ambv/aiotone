#!/usr/bin/env python3.9
from __future__ import annotations

from pathlib import Path
import sys

import click
import soundfile as sf
import pyloudnorm as pyln


def loudness(path: Path) -> float:
    data, rate = sf.read(path)
    meter = pyln.Meter(rate)
    return meter.integrated_loudness(data)


@click.command()
@click.argument("file", nargs=-1)
def main(file: list[str]) -> None:
    for f in file:
        p = Path(f)
        if p.is_file():
            print(f"{p} - {loudness(p)}")
        else:
            print(f"{p} does not exist", file=sys.stderr)


if __name__ == "__main__":
    main()
