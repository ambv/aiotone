from __future__ import annotations
from typing import *

import asyncio
import time

import attr
import click
import uvloop

from .metronome import Metronome
from .midi import get_ports, silence, MidiOut, CLOCK, START, STOP, NOTE_ON, NOTE_OFF

# types
EventDelta = float  # in seconds
TimeStamp = float  # time.time()
MidiPacket = List[int]
MidiMessage = Tuple[MidiPacket, EventDelta, TimeStamp]


@attr.dataclass
class Performance:
    drums: MidiOut
    bass: MidiOut
    # pulse_delta: float = 0.02  # 125 BPM (0.02 / 60 / 24 pulses per quarter note)
    metronome: Metronome = attr.Factory(Metronome)

    async def play_drum(
        self, note: int, pulses: int, volume: int = 127, decay: float = 0.5
    ) -> None:
        await self.play(
            self.drums, channel=9, note=note, pulses=pulses, volume=volume, decay=decay
        )

    async def play(
        self,
        out: MidiOut,
        channel: int,
        note: int,
        pulses: int,
        volume: int,
        decay: float,
    ) -> None:
        note_on_length = int(round(pulses * decay, 0))
        rest_length = pulses - note_on_length
        out.send_message([NOTE_ON | channel, note, volume])
        await self.wait(note_on_length)
        out.send_message([NOTE_OFF | channel, note, volume])
        await self.wait(rest_length)

    async def wait(self, pulses: int) -> None:
        await self.metronome.wait(pulses)


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
        try:
            loop.call_soon_threadsafe(queue.put_nowait, midi_message)
        except BaseException as be:
            click.secho(f"callback failed: {be}", fg="red", err=True)

    from_circuit.set_callback(midi_callback)
    from_mono_station.close_port()
    performance = Performance(drums=to_circuit, bass=to_mono_station)
    try:
        await midi_consumer(queue, performance)
    except asyncio.CancelledError:
        from_circuit.cancel_callback()
        silence(to_circuit)
        silence(to_mono_station)


async def midi_consumer(
    queue: asyncio.Queue[MidiMessage], performance: Performance
) -> None:
    drums: Optional[asyncio.Task] = None
    last_pkt: MidiPacket = [0]
    while True:
        pkt, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        if __debug__:
            if last_pkt[0] == CLOCK:
                click.echo("\r" if pkt[0] == CLOCK else "\n", nl=False)
            click.echo(
                f"{pkt}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}",
                nl=pkt[0] != CLOCK,
            )
        if pkt[0] == CLOCK:
            performance.bass.send_message(pkt)
            await performance.metronome.tick()
        elif pkt[0] == START:
            performance.bass.send_message(pkt)
            await performance.metronome.reset()
            if drums is None:
                drums = asyncio.create_task(drum_machine(performance))
        elif pkt[0] == STOP:
            performance.bass.send_message(pkt)
            if drums is not None:
                drums.cancel()
                drums = None
                silence(performance.drums)
        last_pkt = pkt


async def drum_machine(performance: Performance) -> None:
    b_drum = 60
    s_drum = 62
    cl_hat = 64
    op_hat = 65
    while True:
        await performance.play_drum(b_drum, pulses=24)


@click.command()
def main() -> None:
    uvloop.install()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
