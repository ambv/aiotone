from __future__ import annotations

import asyncio
import time
from typing import List, Optional, Tuple

from attr import dataclass
import click
import uvloop

from .midi import (
    MidiOut,
    NOTE_OFF,
    NOTE_ON,
    CLOCK,
    START,
    STOP,
    CONTROL_CHANGE,
    ALL_NOTES_OFF,
    get_ports,
)


# types
EventDelta = float  # in seconds
TimeStamp = float  # time.time()
MidiPacket = List[int]
MidiMessage = Tuple[MidiPacket, EventDelta, TimeStamp]


@dataclass
class Performance:
    drums: MidiOut
    bass: MidiOut
    pulse_delta: float = 0.02  # 125 BPM (0.02 / 60 / 24 pulses per quarter note)

    async def play_drum(
        self, note: int, pulses: int, volume: int = 127, decay: float = 0.5
    ) -> None:
        await self.play(self.drums, 9, note, pulses, volume, decay)

    async def play_bass(
        self, note: int, pulses: int, volume: int = 127, decay: float = 0.5
    ) -> None:
        await self.play(self.bass, 0, note, pulses, volume, decay)

    async def play(
        self,
        out: MidiOut,
        channel: int,
        note: int,
        pulses: int,
        volume: int,
        decay: float = 0.5,
    ) -> None:
        note_on_length = int(round(pulses * decay, 0))
        rest_length = pulses - note_on_length
        out.send_message([NOTE_ON | channel, note, volume])
        await self.wait(note_on_length)
        out.send_message([NOTE_OFF | channel, note, volume])
        await self.wait(rest_length)

    async def wait(self, pulses: int) -> None:
        await asyncio.sleep(pulses * self.pulse_delta)


@click.command()
def main() -> None:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(async_main())


async def async_main() -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    try:
        from_circuit, to_circuit = get_ports("Circuit", clock_source=True)
        from_mono_station, to_mono_station = get_ports("Circuit Mono Station")
    except ValueError as port:
        click.secho(f"{port} not connected", fg="red", err=True)
        raise click.Abort

    def midi_callback(msg, data=None):
        sent_time = time.time()
        midi_message, event_delta = msg
        try:
            loop.call_soon_threadsafe(
                queue.put_nowait, (midi_message, event_delta, sent_time)
            )
        except BaseException as be:
            click.secho(f"callback exc: {type(be)} {be}", fg="red", err=True)

    from_circuit.set_callback(midi_callback)
    from_mono_station.close_port()  # we won't be using that one now
    performance = Performance(drums=to_circuit, bass=to_mono_station)
    try:
        await midi_consumer(queue, performance)
    except asyncio.CancelledError:
        from_circuit.cancel_callback()
        to_circuit.send_message([STOP])
        to_mono_station.send_message([STOP])
        to_mono_station.send_message([CONTROL_CHANGE | 0, ALL_NOTES_OFF, 0])
        to_mono_station.send_message([CONTROL_CHANGE | 1, ALL_NOTES_OFF, 0])


async def midi_consumer(
    queue: asyncio.Queue[MidiMessage], performance: Performance
) -> None:
    drums: Optional[asyncio.Task] = None
    last_msg: MidiPacket = [0]
    while True:
        msg, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        if __debug__:
            print(f"{msg}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}")
        if msg[0] == CLOCK:
            performance.bass.send_message(msg)
            if last_msg[0] == CLOCK:
                performance.pulse_delta = delta
        elif msg[0] == START:
            performance.bass.send_message(msg)
            if drums is None:
                drums = asyncio.create_task(drum_machine(performance))
        elif msg[0] == STOP:
            performance.bass.send_message(msg)
            if drums is not None:
                drums.cancel()
                drums = None
                performance.drums.send_message([CONTROL_CHANGE | 9, ALL_NOTES_OFF, 0])
        last_msg = msg


async def drum_machine(performance: Performance) -> None:
    b_drum = 60
    s_drum = 62
    cl_hat = 64
    op_hat = 65

    while True:
        await performance.play_drum(b_drum, 24)


if __name__ == "__main__":
    main()
