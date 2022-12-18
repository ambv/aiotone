#!/usr/bin/env python3.9
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

import click
import soundfile as sf
import pyloudnorm as pyln


stderr = sys.stderr


def loudness(path: Path) -> float:
    data, rate = read(path)
    meter = pyln.Meter(rate)
    return meter.integrated_loudness(data)


def read(path: Path) -> tuple[np.array, int]:
    try:
        data, rate = sf.read(path)
    except RuntimeError as re:
        exc = re
    else:
        return data, rate
    
    ntf = tempfile.NamedTemporaryFile(suffix='.aiff')
    out_path = Path(ntf.name)
    ntf.close()
    print(f"Converting {path} to {out_path.name}... ", end="", flush=True, file=stderr)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(path),
                "-vn",
                "-c:v",
                "copy",
                "-c:a",
                "pcm_s16be",
                "-y",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as cpe:
        print("failed.", file=stderr)
        for word in cpe.cmd:
            if " " in word:
                word = f'"{word}"'
            print(word, end=" ", file=stderr)
        print()
        if cpe.stdout.strip():
            print(cpe.stdout.decode(), file=stderr)
        if cpe.stderr.strip():
            print(cpe.stderr.decode(), file=stderr)
        raise exc from None
    except FileNotFoundError:
        print("failed; ffmpeg not installed.", file=stderr)
        raise exc from None
    try:
        data, rate = sf.read(out_path)
    except RuntimeError as re:
        print("failed.", file=stderr)
        raise exc from None
    else:
        print("success.", file=stderr)
        os.unlink(out_path)
        return data, rate
    


@click.command()
@click.argument("file", nargs=-1)
def main(file: list[str]) -> None:
    for f in file:
        p = Path(f)
        if p.is_file():
            print(f"{p}  =  {loudness(p):.3f}")
        else:
            print(f"{p} does not exist", file=sys.stderr)


if __name__ == "__main__":
    main()
