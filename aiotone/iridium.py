"""See the docstring to main()."""

from __future__ import annotations

import asyncio
import configparser
from enum import Enum
from pathlib import Path
import sys
import time
from typing import Dict, List, Tuple

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
    SONG_POSITION,
    CONTROL_CHANGE,
    POLY_AFTERTOUCH,
    MOD_WHEEL,
    FOOT_PEDAL,
    SUSTAIN_PEDAL,
    PITCH_BEND,
    ALL_NOTES_OFF,
    STRIP_CHANNEL,
    get_ports,
    get_out_port,
    silence,
)
from .notes import C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B, Db, Eb, Gb, Ab, Bb  # NoQA


# types
EventDelta = float  # in seconds
TimeStamp = float  # time.time()
MidiPacket = List[int]
MidiMessage = Tuple[MidiPacket, EventDelta, TimeStamp]


CURRENT_DIR = Path(__file__).parent
CONFIGPARSER_FALSE = {
    k
    for k, v in configparser.ConfigParser.BOOLEAN_STATES.items()  # type: ignore
    if v is False
}


class NoteMode(Enum):
    REGULAR = 0


@dataclass
class Performance:
    note_output: MidiOut
    in_channel: int
    out_channel: int
    start_stop: bool
    catch_damper: bool
    polyphony: int

    # Current state of the performance
    is_sustain: bool = False
    notes_down: list[int] = Factory(list)
    notes_sustained: list[int] = Factory(list)
    last_expr: int = -1
    last_mod: int = -1

    def __post_init__(self) -> None:
        if self.catch_damper:
            self.sustain = self.own_sustain
            self.note_on = self.own_note_on
            self.note_off = self.own_note_off
        else:
            self.sustain = self.sustain_passthrough
            self.note_on = self.note_on_passthrough
            self.note_off = self.note_off_passthrough

    def __attrs_post_init__(self) -> None:
        self.__post_init__()

    async def clock(self) -> None:
        self.note_output.send_message([CLOCK])

    async def start(self) -> None:
        if self.start_stop:
            self.note_output.send_message([START])

    async def stop(self) -> None:
        silence(port=self.note_output, stop=self.start_stop)
        self.notes_down.clear()
        self.notes_sustained.clear()

    async def mod_wheel(self, value: int) -> None:
        if self.last_mod == value:
            return
        self.last_mod = value
        await self.cc(MOD_WHEEL, value)

    async def expression(self, value: int) -> None:
        if self.last_expr == value:
            return
        self.last_expr = value
        await self.cc(FOOT_PEDAL, value)

    async def note_on_passthrough(self, note: int, velocity: int) -> None:
        await self.out(NOTE_ON, note, velocity)

    async def note_off_passthrough(self, note: int, velocity: int) -> None:
        await self.out(NOTE_OFF, note, velocity)
    
    async def sustain_passthrough(self, value: int) -> None:
        await self.cc(SUSTAIN_PEDAL, value)

    async def own_note_on(self, note: int, velocity: int) -> None:
        off = False 
        for i, note_sustained in enumerate(self.notes_sustained):
            if note_sustained == note:
                self.notes_sustained.pop(i)
                off = True
                break
        for i, note_down in enumerate(self.notes_down):
            if note_down == note:
                self.notes_down.pop(i)
                off = True
                break
        if off:
            await self.out(NOTE_OFF, note, 0)
        while (len(self.notes_sustained) + len(self.notes_down)) >= self.polyphony:
            if self.notes_sustained:
                await self.out(NOTE_OFF, self.notes_sustained.pop(0), 0)
            else:
                await self.out(NOTE_OFF, self.notes_down.pop(0), 0)
        self.notes_down.append(note)
        await self.out(NOTE_ON, note, velocity)

    async def own_note_off(self, note: int, velocity: int) -> None:
        off = False
        for i, note_down in enumerate(self.notes_down):
            if note_down == note:
                self.notes_down.pop(i)
                off = True
                break

        if self.is_sustain:
            off = False
            for i, note_sustained in enumerate(self.notes_sustained):
                if note_sustained == note:
                    self.notes_sustained.pop(i)
                    break
            self.notes_sustained.append(note)

        if off:
            await self.out(NOTE_OFF, note, velocity)

    async def own_sustain(self, value: int) -> None:
        if value == 0:
            self.is_sustain = False
            while self.notes_sustained:
                await self.out(NOTE_OFF, self.notes_sustained.pop(0), 0)
        else:
            self.is_sustain = True

    # Raw commands

    async def out(self, event: int, note: int, volume: int) -> None:
        self.note_output.send_message([event | self.out_channel, note, volume])

    async def at(self, note: int, value: int) -> None:
        self.note_output.send_message([POLY_AFTERTOUCH | self.out_channel, note, value])

    async def cc(self, type: int, value: int) -> None:
        self.note_output.send_message([CONTROL_CHANGE | self.out_channel, type, value])


@click.command()
@click.option(
    "--config",
    help="Read configuration from this file",
    default=str(CURRENT_DIR / "aiotone-iridium.ini"),
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    show_default=True,
)
@click.option(
    "--make-config",
    help="Write a new configuration file to standard output",
    is_flag=True,
)
def main(config: str, make_config: bool) -> None:
    """
    This module works around some bugs in Waldorf Iridium Keyboard. It works around the
    following bugs:

    BUG: Iridium Keyboard sends back CC4 MIDI messages it receives on USB MIDI;
    BUG: when it receives CC64 0, Iridium Keyboard sends back NOTE OFF messages over USB MIDI
         for notes that were held by the sustain pedal;
    BUG: Iridium Keyboard blindly assigns new voices to the same note being played when
         the sustain pedal is held down (CC64 >= 64), this makes the sound muddy but more importantly
         wastes polyphonic voices which leads to the bugs below being a common occurrence;
    BUG: when all voices of polyphony are used and the player plays two notes, Iridium Keyboard
         assigns both notes to the same least-recently-used voice. In effect only one new note plays.
         This is especially annoying and noticeable when playing with two hands: one hand plays
         a bass line and the other hand plays a melody in higher register;
    BUG: when the player holds the sustain pedal down, and holds a bass note with the left hand, and
         plays, say, triplets with the other hand, soon enough the voices of polyphony will be
         exhausted and the bass note will be cut because it's the "least-recently-used" voice, even
         if the triplets only ever used three keys.

    This module works around those problems by implementing "catch-damper", essentially its own
    implementation of the sustain pedal that never passes CC 64 to Iridium Keyboard at all. Instead
    it implements behavior of holding notes, and sustaining notes by the pedal, and playing over the
    same sustained note (which first sends a NOTE OFF to the previous voice that played the same
    note). Additionally, this module deduplicates MIDI CC4 and CC1 messages, working around the
    USB MIDI loop that Iridium Keyboard introduces.

    You can use this module either as a MIDI filter for either "MIDI From" fields on Ableton MIDI
    tracks or "MIDI To" fields on External Instruments. In either case, you still want to disable
    record arming of the Iridium track in Ableton Live when playing back MIDI content you recorded
    because the MIDI loop *will* play back CC4 messages.
    """
    if make_config:
        with open(CURRENT_DIR / "aiotone-iridium.ini") as f:
            print(f.read())
        return

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(async_main(config))


async def async_main(config: str) -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    cfg = configparser.ConfigParser()
    cfg.read(config)
    if cfg["note-input"].getint("channel") != 1:
        click.secho("from-ableton channel must be 1, sorry")
        raise click.Abort

    # Configure the `from_ableton` port
    try:
        note_input, note_output= get_ports(
            cfg["note-input"]["port-name"], clock_source=True
        )
    except ValueError as port:
        click.secho(f"from-ableton port {port} not connected", fg="red", err=True)
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

    note_input.set_callback(midi_callback)

    if cfg["note-input"]["port-name"] != cfg["note-output"]["port-name"]:
        try:
            note_output = get_out_port(cfg["note-output"]["port-name"])
        except ValueError as port:
            click.secho(f"{port} not connected", fg="red", err=True)
            raise click.Abort

    performance = Performance(
        note_output=note_output,
        in_channel=cfg["note-input"].getint("channel"),
        out_channel=cfg["note-output"].getint("channel"),
        start_stop=cfg["note-input"].getboolean("start-stop"),
        catch_damper=cfg["note-input"].getboolean("catch-damper"),
        polyphony=cfg["note-output"].getint("polyphony"),
    )
    try:
        await midi_consumer(queue, performance)
    except asyncio.CancelledError:
        note_input.cancel_callback()
        silence(note_output)


async def midi_consumer(
    queue: asyncio.Queue[MidiMessage], performance: Performance
) -> None:
    print("Waiting for MIDI messages...")
    silence(performance.note_output, channels=[performance.out_channel])
    system_realtime = {START, STOP, SONG_POSITION}
    notes = {NOTE_ON, NOTE_OFF}
    handled_types = system_realtime | notes | {CONTROL_CHANGE}
    while True:
        msg, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        # Note hack below. We are matching the default which is channel 1 only.
        # This is what we want.
        t = msg[0]
        if t == CLOCK:
            await performance.clock()
        else:
            st = t & STRIP_CHANNEL
            if st == STRIP_CHANNEL:  # system realtime message didn't have a channel
                st = t
            if __debug__ and st == t:
                fg = "white"
                if t in system_realtime:
                    fg = "blue"
                elif t == CONTROL_CHANGE:
                    fg = "green"
                elif t == POLY_AFTERTOUCH:
                    fg = "magenta"
                click.secho(
                    f"{msg}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}", fg=fg
                )
            if t == START:
                await performance.start()
            elif t == STOP:
                await performance.stop()
            elif t == NOTE_ON:
                await performance.note_on(msg[1], msg[2])
            elif t == NOTE_OFF:
                await performance.note_off(msg[1], msg[2])
            elif t == POLY_AFTERTOUCH:
                await performance.at(msg[1], msg[2])
            elif t == CONTROL_CHANGE:
                if msg[1] == MOD_WHEEL:
                    await performance.mod_wheel(msg[2])
                elif msg[1] == FOOT_PEDAL:
                    await performance.expression(msg[2])
                elif msg[1] == SUSTAIN_PEDAL:
                    await performance.sustain(msg[2])
                elif msg[1] == ALL_NOTES_OFF:
                    await performance.cc(ALL_NOTES_OFF, msg[2])
                else:
                    print(f"warning: unhandled CC {msg}", file=sys.stderr)
            elif t == PITCH_BEND:
                await performance.out(PITCH_BEND, msg[1], msg[2])
            else:
                if st not in handled_types:
                    print(f"warning: unhandled event {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()