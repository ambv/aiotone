from typing import *
from array import array


FMAudio = Generator[array[int], array[int], None]


def saturate(value: float) -> int:
    ...


def calculate_panning(
    pan: float, mono: array[int], stereo: array[int], want_frames: int
) -> None:
    ...


class Envelope:
    def __init__(self, a: int, d: int, s: float, r: int) -> None:
        ...

    def reset(self) -> None:
        ...

    def release(self) -> None:
        ...

    def advance(self) -> float:
        ...

    def is_silent(self) -> bool:
        ...


class Operator:
    def __init__(
        self,
        wave: array[int],
        sample_rate: int,  # Hz, like: 44100
        envelope: Envelope,
        volume: float = 1.0,  # 0.0 - 1.0; relative attenuation
        pitch: float = 440.0,  # Hz
    ) -> None:
        ...

    # Current state of the operator, modified during `mono_out()`
    current_velocity: float = 0.0
    reset: bool = False

    def is_silent(self) -> bool:
        ...

    def note_on(self, pitch: float, volume: float) -> None:
        ...

    def note_off(self, pitch: float, volume: float) -> None:
        ...

    def mono_out(self) -> FMAudio:
        ...

    def modulate(self, out_buffer: array[int], modulator: array[int], w_i: int) -> int:
        ...
