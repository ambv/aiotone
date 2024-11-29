#!/usr/bin/env python3

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

import click
import numpy
from PIL import Image

from aiotone import audiofile
from aiotone.colors import get_color, colors_to_buckets, convert_html_to_hsv


BETA = 3.14159265359 * 2.55


def freq_from_pcm(pcm, window, step, channels):
    """Yields real FFTs from data chunks of `window` size in `pcm` double array."""

    # XXX doesn't pad data with zeroes at the start
    offset = 0
    while offset < pcm.shape[0]:
        data = numpy.zeros(window, numpy.float64)
        for ch in channels:
            chunk = pcm[offset : offset + window, ch]
            if len(chunk) < window:
                chunk = numpy.pad(chunk, [(0, window - len(chunk))])
            data += chunk
        result = numpy.fft.rfft(data * kaiser(len(data)))

        # some transformations suggested by
        # https://lo.calho.st/posts/numpy-spectrogram/
        result = result[: window // 2]
        result = numpy.absolute(result) * 2.0 / window
        result = result.clip(-120)
        yield result
        offset += step


@lru_cache()
def kaiser(length):
    """Memoized Kaiser window, saves a lot of time recomputing the same shape.

    See more at:
    https://en.wikipedia.org/wiki/Kaiser_window
    """
    return numpy.kaiser(length, BETA)


def convert_channels_to_list(channels):
    if channels is None or channels == "ALL":
        return []

    return [int(elem.strip()) for elem in channels.split(",")]


@click.command()
@click.argument("file")
@click.option(
    "-b",
    "--brightness",
    type=click.IntRange(min=1),
    default=8,
    help="Brightness multiplier",
)
@click.option(
    "-c",
    "--channels",
    type=convert_channels_to_list,
    default=None,
    help="A comma-separated list of channels to use",
)
def main(
    file,
    window=4800,
    step=600,
    brightness=8,
    prepend=0,
    fps=10,
    crop_height=2160,
    colors=None,
    channels=None,
):
    width = 0
    height = 0

    if not colors:
        colors = "#000000,#0000ff,#008080,#00ff00,#ffffff"
    if not channels:
        channels = "ALL"

    colors = convert_html_to_hsv(colors)
    color_buckets = colors_to_buckets(colors, min=0, max=1)

    audio_data, audio_rate = audiofile.read(file)
    audio_channels = audio_data.shape[1]
    audio_duration = audio_data.shape[0] / audio_rate
    freq_samples = []
    window = window or audio_rate
    step = step or int(round(audio_rate / fps))
    channels = channels or list(range(audio_channels))
    channels_str = ",".join(str(c) for c in channels)

    print(file)
    print("     duration:", audiofile.duration_str(audio_duration))
    print("  sample rate:", audio_rate)
    print("     channels:", audio_channels)
    print("       window:", window)
    print("         step:", step, "(fps: {})".format(fps))
    print("Calculating FFT...", end="\r")

    for freq in freq_from_pcm(audio_data, window, step, channels):
        width += 1
        height = len(freq)
        freq_samples.append(freq)

    rgb = numpy.zeros((height, width + prepend, 3), "uint8")

    print("  image width:", width)
    print(" image height:", height)
    print("Preparing image...", end="\r")

    custom_black = get_color(0, brightness, color_buckets)
    for x in range(prepend):
        for y in range(height):
            rgb[y, x] = custom_black

    print("Applying colors...", end="\r")
    for x, freq in enumerate(freq_samples, prepend):
        freq = freq[:height]
        for y, f in enumerate(reversed(freq)):
            rgb[y, x] = get_color(f, brightness, color_buckets)

    print("Creating in-memory PNG image", end="\r")
    i = Image.fromarray(rgb, mode="RGB")
    img_path = Path(file).stem + f".b{brightness}.c{channels_str}.png"
    print("Saving image to", img_path, " " * 10, end="\r")
    i.save(img_path)
    print("Saved image to", img_path, " " * 10)


if __name__ == "__main__":
    main()
