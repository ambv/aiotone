#!/usr/bin/env python3
from __future__ import annotations

from functools import partial
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Callable

import soundfile as sf


stderr = sys.stderr


if TYPE_CHECKING:
    import numpy.typing as npt


def duration_str(duration: float) -> str:
    minutes = int(duration // 60)
    seconds = duration - 60 * minutes
    return f"{minutes}:{seconds:0<.3f}"


def empty() -> None:
    return None


def read(
    path: Path, quiet: bool = False, print: Callable[..., None] = print
) -> tuple[npt.NDArray, int]:
    """Return an tuple with a numpy array of samples and the sample rate.

    The numpy array contains all channels and the contents is normalized
    float64 (double precision).
    """
    try:
        data, rate = sf.read(path)
    except RuntimeError as re:
        exc = re
    else:
        return data, rate

    ntf = tempfile.NamedTemporaryFile(suffix=".aiff")
    out_path = Path(ntf.name)
    ntf.close()
    convert_message: Callable[[], None] = partial(
        print,
        f"Converting {path} to {out_path.name}... ",
        end="",
        flush=True,
        file=stderr,
    )
    if not quiet:
        convert_message()
        convert_message = empty
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
        convert_message()
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
        convert_message()
        print("failed; ffmpeg not installed.", file=stderr)
        raise exc from None
    try:
        data, rate = sf.read(out_path)
    except RuntimeError:
        convert_message()
        print("failed.", file=stderr)
        raise exc from None
    else:
        if not quiet:
            print("success.", file=stderr)
        os.unlink(out_path)
        return data, rate
