from __future__ import annotations
from typing import *

import asyncio
import time

import click
import uvloop

from .midi import get_ports

# types
EventDelta = float  # in seconds
TimeStamp = float  # time.time()
MidiPacket = List[int]
MidiMessage = Tuple[MidiPacket, EventDelta, TimeStamp]


async def async_main() -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    try:
        from_circuit, to_circuit = get_ports("Circuit", clock_source=True)
        from_mono_station, to_mono_station = get_ports("Circuit Mono Station")
    except ValueError as port:
        click.secho(f"{port} is not available", fg="red", err=True)
        raise click.Abort

    def midi_callback(msg: Tuple[MidiPacket, EventDelta], data: Any = None) -> None:
        sent_time = time.time()
        midi_packet, event_delta = msg
        midi_message = (midi_packet, event_delta, sent_time)
        loop.call_soon_threadsafe(queue.put_nowait, midi_message)

    from_circuit.set_callback(midi_callback)
    from_mono_station.close_port()

    await midi_consumer(queue)


async def midi_consumer(queue: asyncio.Queue[MidiMessage]) -> None:
    while True:
        pkt, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        if __debug__:
            click.echo(f"{pkt}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}")


@click.command()
def main() -> None:
    uvloop.install()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
