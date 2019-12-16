"""See the docstring of main()."""
from __future__ import annotations

import asyncio
import itertools
import random
import time
from typing import List, Optional, Tuple

from attr import dataclass, Factory
import click
import uvloop

from .metronome import Metronome
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
    metronome: Metronome = Factory(Metronome)
    last_note: int = 48

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
        await self.metronome.wait(pulses)


@click.command()
def main() -> None:
    """
    Plays a tune on Circuit and Circuit Mono Station:

    - uses the Circuit as the clock master;
    
    - uses the Circuit as a drum machine;

    - uses the Mono Station as a paraphonic bass synthesizer.

    To use this:
    
    - have the Circuit and the Circuit Mono Station connected to your computer;

    - turn the Circuit and the Circuit Mono Station on and set them both to use empty
      sessions;

    - have the outs of the Circuit and the Circuit Mono Station connected to a mixer
      or an audio interface;

    - start the program.  Press Play on the Circuit.
    """
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
    bassline: Optional[asyncio.Task] = None
    while True:
        msg, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        if __debug__:
            print(f"{msg}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}")
        if msg[0] == CLOCK:
            performance.bass.send_message(msg)
            await performance.metronome.tick()
        elif msg[0] == START:
            performance.bass.send_message(msg)
            await performance.metronome.reset()
            if drums is None:
                drums = asyncio.create_task(drum_machine(performance))
            if bassline is None:
                bassline = asyncio.create_task(analog_synth(performance))
        elif msg[0] == STOP:
            performance.bass.send_message(msg)
            if drums is not None:
                drums.cancel()
                drums = None
                performance.drums.send_message([CONTROL_CHANGE | 9, ALL_NOTES_OFF, 0])
            if bassline is not None:
                bassline.cancel()
                bassline = None
                performance.bass.send_message([CONTROL_CHANGE | 0, ALL_NOTES_OFF, 0])
                performance.bass.send_message([CONTROL_CHANGE | 1, ALL_NOTES_OFF, 0])
        elif msg[0] == NOTE_ON:
            performance.last_note = msg[1]


async def drum_machine(performance: Performance) -> None:
    b_drum = 60
    s_drum = 62
    cl_hat = 64
    op_hat = 65

    async def bass_drum() -> None:
        while True:
            await performance.play_drum(b_drum, 24)

    async def snare_drum() -> None:
        while True:
            await performance.wait(24)
            await performance.play_drum(s_drum, 24)

    async def hihats() -> None:
        while True:
            await performance.play_drum(cl_hat, 6)
            await performance.play_drum(cl_hat, 6)
            await performance.play_drum(op_hat, 12)

    await asyncio.gather(bass_drum(), snare_drum(), hihats())


async def analog_synth(performance: Performance) -> None:
    c2 = 48
    bb1 = 46
    g1 = 43
    f1 = 41

    async def key_note() -> None:
        while True:
            await performance.play_bass(performance.last_note, 96, decay=1.0)

    async def arpeggiator() -> None:
        notes = [c2 + 24, f1 + 24, g1 + 24]
        length = 0
        for note in itertools.cycle(notes):
            current = random.choice((6, 6, 6, 12))
            if length % 96 == 0:
                await performance.wait(current)
            else:
                await performance.play_bass(note, current, volume=32, decay=0.5)
            length += current

    await asyncio.gather(key_note(), arpeggiator())


if __name__ == "__main__":
    main()
