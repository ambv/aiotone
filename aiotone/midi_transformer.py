#!/usr/bin/env python3
"""See the docstring to main()."""

from __future__ import annotations
from typing import *

from pathlib import Path

from mido import MidiFile


def main(path: Path) -> None:
    mid = MidiFile(path.expanduser())
    cancel = False
    for msg in mid.tracks[0]:
        if msg.type != "note_on":
            continue

        if cancel:
            msg.velocity = 1
            cancel = False
        else:
            cancel = True

    name_stem = path.with_suffix("").name

    mid.save((path.expanduser().parent / (name_stem + "-R")).with_suffix(".mid"))


if __name__ == "__main__":
    main(Path("~/Dropbox/RPLKTR/_decay/california-e-piano.mid"))
