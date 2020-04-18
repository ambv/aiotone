from __future__ import annotations
from typing import *

import asyncio

import click
import uvloop

from .midi import get_ports


async def async_main() -> None:
    try:
        from_circuit, to_circuit = get_ports("Circuit", clock_source=True)
        from_mono_station, to_mono_station = get_ports("Circuit Mono Station")
    except ValueError as port:
        click.secho(f"{port} is not available", fg="red", err=True)
        raise click.Abort


@click.command()
def main() -> None:
    uvloop.install()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
