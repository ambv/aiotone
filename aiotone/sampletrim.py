from __future__ import annotations

from array import array
from pathlib import Path

import click
import miniaudio


def max_sample(samples: array[int]) -> int:
    sample_max = max(samples)
    sample_min = min(samples)
    return max(sample_max, abs(sample_min))


def normalize_samples(samples: array[int]) -> None:
    sample_max = max_sample(samples)
    if sample_max >= 0.99 * 32767:
        # don't normalize when it's almost there
        return
    ratio = 32767 / sample_max
    for i in range(len(samples)):
        samples[i] = max(-32767, min(32767, int(samples[i] * ratio)))


def trim_samples(samples: array[int]) -> None:
    start_index = 0
    end_index = len(samples)
    click.secho(f"Before: {start_index}:{end_index}")

    window_size = 16
    minimum_signal = int(0.005 * 32767)
    for offset in range(0, end_index, window_size):
        if max_sample(samples[offset : offset + window_size]) > minimum_signal:
            start_index = offset
            break

    minimum_signal = int(0.001 * 32767)
    for offset in range(end_index - window_size, -1, -window_size):
        if max_sample(samples[offset : offset + window_size]) > minimum_signal:
            end_index = offset + window_size
            break

    click.secho(f"After: {start_index}:{end_index}")
    samples[:] = samples[start_index:end_index]


def ease_out_cubic(x: float) -> float:
    return 1 - x * x * x


def fade_out(samples: array[int]) -> None:
    if samples[-1] == 0:
        return

    fade_out_length = 44
    for i in range(fade_out_length):
        samples[-fade_out_length + i] = int(
            ease_out_cubic(i / (fade_out_length - 1)) * samples[-fade_out_length + i]
        )


@click.command()
@click.argument("file", nargs=-1)
def main(file: list[str]) -> None:
    """Trim silence from samples given on the command line, save as 44.1k 16-bit WAV."""
    for f in file:
        p = Path(f)
        if p.is_file():
            sound = miniaudio.decode_file(
                str(p),
                sample_rate=44100,
                output_format=miniaudio.SampleFormat.SIGNED16,
                dither=miniaudio.DitherMode.TRIANGLE,
            )
            click.secho(sound)
            normalize_samples(sound.samples)
            trim_samples(sound.samples)
            # sound.samples[:] = sound.samples[: 44100 * 2]
            fade_out(sound.samples)
            sound.num_frames = len(sound.samples) // sound.nchannels
            sound.duration = sound.num_frames / sound.sample_rate
            out_dir = p.parent / "44100_16bit"
            out_dir.mkdir(exist_ok=True)

            miniaudio.wav_write_file(str(out_dir / p.name), sound)
        else:
            click.secho(f"{p} does not exist", err=True)


if __name__ == "__main__":
    main()
