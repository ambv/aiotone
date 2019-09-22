from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional, Tuple

from attr import dataclass
import click
from rtmidi import MidiIn, MidiOut
import uvloop


__version__ = "19.9.0"

# types
EventDelta = float  # in seconds
TimeStamp = float  # time.time()
MidiPacket = List[int]
MidiMessage = Tuple[MidiPacket, EventDelta, TimeStamp]


NOTE_OFF = 0b10000000
NOTE_ON = 0b10010000
POLY_AFTERTOUCH = 0b10100000
CONTROL_CHANGE = 0b10110000
PROGRAM_CHANGE = 0b11000000
CHAN_AFTERTOUCH = 0b11010000
PITCH_BEND = 0b11100000
SYSEX = 0b11110000
SYSEX_RT = 0b11111000
PANIC = 0b11111111
CLOCK = 0b11111000
START = 0b11111010
STOP = 0b11111100

STRIP_CHANNEL = 0b11110000


@dataclass
class Context:
    drums: MidiOut
    bass: MidiOut
    tick: float = 0.02  # 125 BPM (0.02 / 60 / 24 pulses per quarter note)

    async def play_drum(
        self, note: int, length: int, volume: int = 127, decay: float = 0.5
    ) -> None:
        await self.play(self.drums, 9, note, length, volume, decay)

    async def play(
        self,
        out: MidiOut,
        channel: int,
        note: int,
        length: int,
        volume: int,
        decay: float = 0.5,
    ) -> None:
        note_on_length = int(round(length * decay, 0))
        rest_length = length - note_on_length
        out.send_message([NOTE_ON | channel, note, volume])
        await self.wait(note_on_length)
        out.send_message([NOTE_OFF | channel, note, volume])
        await self.wait(rest_length)

    async def wait(self, pulses: int) -> None:
        for _ in range(pulses):
            await asyncio.sleep(self.tick)


@click.command()
def main() -> None:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(async_main())


async def async_main() -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    from_circuit, to_circuit = get_ports("Circuit", clock_source=True)
    from_mono_station, to_mono_station = get_ports("Circuit Mono Station")

    def midi_callback(msg, data=None):
        sent_time = time.time()
        midi_message, event_delta = msg
        try:
            loop.call_soon_threadsafe(
                queue.put_nowait, (midi_message, event_delta, sent_time)
            )
        except BaseException as be:
            print(f"callback exc: {type(be)} {be}")

    from_circuit.set_callback(midi_callback)
    context = Context(drums=to_circuit, bass=to_mono_station)
    try:
        await midi_printer(queue, context)
    except asyncio.CancelledError:
        from_circuit.cancel_callback()


async def midi_printer(queue: asyncio.Queue[MidiMessage], context: Context) -> None:
    drums: Optional[asyncio.Task] = None
    last_msg: MidiPacket = [0]
    while True:
        msg, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        print(f"{msg}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}")
        if msg[0] == CLOCK and last_msg[0] == CLOCK:
            context.tick = delta
        if msg[0] == START and drums is None:
            drums = asyncio.create_task(drum_machine(context))
        if msg[0] == STOP and drums is not None:
            drums.cancel()
            drums = None
        last_msg = msg


async def drum_machine(context: Context) -> None:
    on = NOTE_ON | 9  # note on on Channel 10 (0-indexed)
    off = NOTE_OFF | 9  # note off on Channel 10 (0-indexed)
    b_drum = 60
    s_drum = 62
    cl_hat = 64
    op_hat = 65

    async def bass_drum() -> None:
        while True:
            await context.play_drum(b_drum, 24)

    async def snare_drum() -> None:
        while True:
            await context.wait(24)
            await context.play_drum(s_drum, 24)

    async def hihats() -> None:
        while True:
            await context.play_drum(cl_hat, 6)
            await context.play_drum(cl_hat, 6)
            await context.play_drum(op_hat, 12)

    await asyncio.gather(bass_drum(), snare_drum(), hihats())


def get_ports(port_name: str, *, clock_source: bool = False) -> Tuple[MidiIn, MidiOut]:
    midi_in = MidiIn()
    midi_out = MidiOut()

    midi_in_ports = midi_in.get_ports()
    midi_out_ports = midi_out.get_ports()
    try:
        midi_in.open_port(midi_in_ports.index(port_name))
    except ValueError:
        click.secho(f"{port_name} (in) not connected", fg="red", err=True)
        raise click.Abort

    if clock_source:
        midi_in.ignore_types(timing=False)
    try:
        midi_out.open_port(midi_out_ports.index(port_name))
    except ValueError:
        click.secho(f"{port_name} (out) not connected", fg="red", err=True)
        raise click.Abort

    return midi_in, midi_out


if __name__ == "__main__":
    main()
