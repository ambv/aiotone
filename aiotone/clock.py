from __future__ import annotations

import click


def ms_from_bpm(bpm: float) -> float:
    """Return length of a whole note in ms given `bpm`."""
    return 240000 / bpm


@click.command()
@click.argument("BPM", type=float)
def main(bpm) -> None:
    print(f"BPM: {bpm}")
    note = ms_from_bpm(bpm)
    for i in range(7):
        divisor = 2 ** i
        pad = " " if divisor < 10 else ""
        print(f"{pad}1/{divisor} = {note / divisor:.4f} ms")
    print()
    print("Triplets:")
    for i in (3, 6, 12):
        pad = " " if i < 10 else ""
        print(f"{pad}1/{i} = {note / i:.4f} ms")


if __name__ == "__main__":
    main()
