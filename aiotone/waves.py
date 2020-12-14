#!/usr/bin/env python3
"""A few kinds of waveforms as arrays."""

from __future__ import annotations
from typing import *

from array import array
import math


# We want this to be symmetrical on the + and the - side.
INT16_MAXVALUE = 32767


def sine_array(sample_count: int) -> array[int]:
    """Return a monophonic signed 16-bit wavetable with a single sine cycle."""
    numbers = []
    for i in range(sample_count):
        current = round(INT16_MAXVALUE * math.sin(i / sample_count * math.tau))
        numbers.append(current)
    return array("h", numbers)


def sine12_array(sample_count: int) -> array[int]:
    """Return a monophonic signed 16-bit wavetable with a single cycle of a 1+2 sine.

    A 1+2 sine is a sine wave modulated by its first harmonic.
    """
    numbers = []
    for i in range(sample_count):
        current = round(
            INT16_MAXVALUE
            * (
                0.5 * math.sin(i / sample_count * math.tau)
                + 0.5 * math.sin(2 * i / sample_count * math.tau)
            )
        )
        numbers.append(current)
    return array("h", numbers)


def saw_array(sample_count: int) -> array[int]:
    """Return a monophonic signed 16-bit wavetable with a single sawtooth wave cycle.

    The wave cycle is in phase with the sines produced by `sine_array` et al.
    """
    numbers = []
    for i in range(sample_count // 2):
        current = round((i / sample_count) * (2 * INT16_MAXVALUE))
        numbers.append(current)
    for i in range(sample_count // 2):
        current = round(-INT16_MAXVALUE + (i / sample_count) * (2 * INT16_MAXVALUE))
        numbers.append(current)
    assert len(numbers) == sample_count
    return array("h", numbers)


def pulse_array(sample_count: int) -> array[int]:
    """Return a monophonic signed 16-bit wavetable with a single pulse wave cycle.

    The wave cycle is in phase with the sines produced by `sine_array` et al.
    """
    half = sample_count // 2
    return array("h", [INT16_MAXVALUE] * half + [-INT16_MAXVALUE] * half)


def _plot_arrays(*arrays: Tuple[array[int], str]) -> None:
    from scipy import signal
    import matplotlib.pyplot as plt

    for arr, name in arrays:
        plt.plot(arr, label=name)
    plt.legend()
    plt.show()
