"""See the docstring to main()."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import configparser
from enum import Enum
import os
from pathlib import Path
import sys
import time
from typing import List, Tuple

from attrs import define, field, Factory
import click
import numpy as np
import uvloop

from . import monome
from . import profiling
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
    EXPRESSION_PEDAL,
    PITCH_BEND,
    ALL_NOTES_OFF,
    ALL_SOUND_OFF,
    STRIP_CHANNEL,
    GET_CHANNEL,
    BANK_SELECT,
    BANK_SELECT_LSB,
    PROGRAM_CHANGE,
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
MidiNote = int
Velocity = int
Coro = Coroutine[None, None, None]


DEBUG = False
CURRENT_DIR = Path(__file__).parent
CONFIGPARSER_FALSE = {
    k
    for k, v in configparser.ConfigParser.BOOLEAN_STATES.items()  # type: ignore
    if v is False
}
# CCs used by Iridium for sending and receiving modulation
MODULATION_CC = set(range(16, 32)) | {BANK_SELECT, BANK_SELECT_LSB}
WHITE_KEYS = {0, 2, 4, 5, 7, 9, 11}


class NoteMode(Enum):
    REGULAR = 0


@define
class MIDIMonitorGridApp(monome.GridApp):
    performance: Performance
    width: int = 0
    height: int = 0
    connected: bool = False
    _counter: int = 0
    _buffer: np.ndarray = field(init=False)
    _leds: np.ndarray = field(init=False)
    _input_queue: asyncio.Queue[tuple[MidiNote, Velocity]] = field(init=False)
    grid: monome.Grid = field(init=False)  # inherited from monome.GridApp

    def __post_init__(self) -> None:
        super().__init__()
        self._buffer = np.zeros(8, dtype=(">i", 8))
        self._leds = np.zeros(16, dtype=(">i", 8))
        self._input_queue = asyncio.Queue(maxsize=128)

    def __attrs_post_init__(self) -> None:
        self.__post_init__()

    async def connect(self, port: int) -> None:
        await self.grid.connect("127.0.0.1", port)

    def on_grid_ready(self) -> None:
        self.width = self.grid.width
        self.height = self.grid.height
        self.connected = True
        g = "Grid"
        if self.grid.varibright:
            g = "Varibright grid"
        print(f"{g} {self.width}x{self.height} connected")
        if self.height > 8:
            print("Constraining to first 8 rows")
            self.height = 8

    def on_grid_disconnect(self) -> None:
        print("Grid disconnected")
        self.connected = False

    def on_grid_key(self, x: int, y: int, s: int) -> None:
        note = 12 * (9 - y) + x
        velocity = 72 if s else 0
        try:
            self._input_queue.put_nowait((note, velocity))
        except asyncio.QueueFull:
            click.secho("Grid input queue full", fg="red", err=True)

    async def handle_input_queue(self) -> None:
        q = self._input_queue
        p = self.performance
        while True:
            note, velocity = await q.get()
            if velocity:
                await p.note_on(note, velocity)
            else:
                await p.note_off(note, velocity)

    async def handle_leds(self) -> None:
        fps = 0
        last_sec = time.monotonic()
        while True:
            fps += 1
            self.draw()
            now = time.monotonic()
            if now - last_sec >= 1:
                self.render_fps = fps / (now - last_sec)
                fps = 0
                last_sec = now
            await asyncio.sleep(0.03)

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.handle_input_queue())
                tg.create_task(self.handle_leds())
        except asyncio.CancelledError:
            self.disconnect()
            raise

    def draw(self) -> None:
        if not self.connected:
            return

        leds = self._leds
        leds[:] = 0
        notes = {}
        notes_down = self.performance.notes_down
        notes_sustained = self.performance.notes_sustained
        for note in notes_sustained:
            notes[note] = 8
        for note in notes_down:
            notes[note] = 15
        for y in range(0, 8):
            for x in range(0, 12):
                cur_note = 12 * (9 - y) + x
                if n := notes.get(cur_note):
                    leds[x][y] = n
                else:
                    leds[x][y] = 2 if x in WHITE_KEYS else 1

        b = self._buffer
        for x_offset in range(0, self.width, 8):
            for y_offset in range(0, self.height, 8):
                b[:] = 0
                for x in range(8):
                    for y in range(8):
                        b[y][x] = leds[x_offset + x][y_offset + y]
                self.grid.led_level_map_raw(x_offset, y_offset, b)

    def disconnect(self) -> None:
        if not self.connected:
            return

        self.grid.led_level_all(0)
        self.grid.disconnect()


@define
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
    render_fps: float = 0.0

    # Internal state
    sustain: Callable[[int], Coro] = field(init=False)
    note_on: Callable[[int, int], Coro] = field(init=False)
    note_off: Callable[[int, int], Coro] = field(init=False)

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

    def clock_eager(self) -> None:
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
        # await self.cc(FOOT_PEDAL, value)
        await self.cc(EXPRESSION_PEDAL, value)

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

    async def pc(self, program: int) -> None:
        self.note_output.send_message([PROGRAM_CHANGE | self.out_channel, program])

    async def bend(self, msb: int, lsb: int) -> None:
        self.note_output.send_message([PITCH_BEND | self.out_channel, msb, lsb])


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
    This module works around some bugs in Waldorf Iridium Keyboard. It works around
    the following bugs:

    BUG: Iridium Keyboard sends back CC4 MIDI messages it receives on USB MIDI;
    BUG: when it receives CC64 0, Iridium Keyboard sends back NOTE OFF messages
         over USB MIDI for notes that were held by the sustain pedal. It already sent
         NOTE OFF once for those notes when the player lifted their fingers while still
         holding the sustain pedal.
    BUG: Iridium Keyboard blindly assigns new voices to the same note being
         played when the sustain pedal is held down (CC64 >= 64), this makes the
         sound muddy but more importantly wastes polyphonic voices which leads to the
         bugs below being a common occurrence;
    BUG: when all voices of polyphony are used and the player plays two notes,
         Iridium Keyboard assigns both notes to the same least-recently-used voice.
         In effect only one new note plays.  This is especially annoying and
         noticeable when playing with two hands: one hand plays a bass line and the
         other hand plays a melody in higher register;
    BUG: when the player holds the sustain pedal down, and holds a bass note
         with the left hand, and plays, say, triplets with the other hand, soon
         enough the voices of polyphony will be exhausted and the bass note will be
         cut because it's the "least-recently-used" voice, even if the triplets only
         ever used three keys.

    This module works around those problems by implementing "catch-damper",
    essentially its own implementation of the sustain pedal that never passes CC
    64 to Iridium Keyboard at all. Instead it implements behavior of holding
    notes, and sustaining notes by the pedal, and playing over the same
    sustained note (which first sends a NOTE OFF to the previous voice that
    played the same note). Additionally, this module deduplicates MIDI CC4 and
    CC1 messages, as well as translates incoming CC4 into CC11, working around
    a nasty USB MIDI loop that Iridium Keyboard introduces.

    You can use this module either as a MIDI filter for either "MIDI From"
    fields on Ableton MIDI tracks or "MIDI To" fields on External Instruments.
    In either case, you still want to disable record arming of the Iridium track
    in Ableton Live when playing back MIDI content you recorded because the MIDI
    loop *will* play back CC4 messages.
    """
    if make_config:
        with open(CURRENT_DIR / "aiotone-iridium.ini") as f:
            print(f.read())
        return

    if not DEBUG:
        uvloop.install()
    print(os.getpid())
    with profiling.maybe(DEBUG):
        asyncio.run(async_main(config))


async def async_main(config: str) -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_running_loop()

    cfg = configparser.ConfigParser()
    cfg.read(config)

    # Configure the `from_ableton` port
    try:
        note_input, note_output = get_ports(
            cfg["note-input"]["port-name"], clock_source=True
        )
    except ValueError as port:
        click.secho(f"from-ableton port {port} not connected", fg="red", err=True)
        raise click.Abort from None

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
        while True:
            try:
                note_output = get_out_port(cfg["note-output"]["port-name"])
            except ValueError as port:
                click.secho(f"{port} not connected, waiting...", fg="red", err=True)
                await asyncio.sleep(1.0)
            else:
                break

    performance = Performance(
        note_output=note_output,
        in_channel=cfg["note-input"].getint("channel"),
        out_channel=cfg["note-output"].getint("channel"),
        start_stop=cfg["note-input"].getboolean("start-stop"),
        catch_damper=cfg["note-input"].getboolean("catch-damper"),
        polyphony=cfg["note-output"].getint("polyphony"),
    )
    grid_app = MIDIMonitorGridApp(performance)
    try:
        async with asyncio.TaskGroup() as tg:

            def serialosc_device_added(id, type, port):
                if type == "monome 128" or type == "monome zero":
                    tg.create_task(grid_app.connect(port))
                else:
                    print(
                        f"warning: unknown Monome device connected"
                        f" - type {type!r}, id {id}"
                    )

            serialosc = monome.SerialOsc()
            serialosc.device_added_event.add_handler(serialosc_device_added)

            await serialosc.connect()
            tg.create_task(grid_app.run())
            tg.create_task(midi_consumer(queue, performance))
            # tg.create_task(stress_test(queue))
    except asyncio.CancelledError:
        note_input.cancel_callback()
        silence(note_output)


async def stress_test(queue: asyncio.Queue[MidiMessage]) -> None:
    i = 0
    while True:
        i += 1
        if i % 64 == 0:
            msg = [176, 64, 64]
        elif i % 32 == 0:
            msg = [176, 64, 0]
        else:
            msg = [176, 4, i % 128]
        await queue.put((msg, 0, time.time()))
        if i % 10000 == 0:
            await asyncio.sleep(3)
        else:
            await asyncio.sleep(0.001)


async def midi_consumer(
    queue: asyncio.Queue[MidiMessage], performance: Performance
) -> None:
    print("Waiting for MIDI messages...")
    silence(performance.note_output, channels=[performance.out_channel])
    system_realtime = {START, STOP, SONG_POSITION}
    notes = {NOTE_ON, NOTE_OFF}
    handled_types = system_realtime | notes | {CONTROL_CHANGE}
    in_ch = performance.in_channel - 1
    while True:
        msg, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        t = msg[0]
        if t == CLOCK:
            performance.clock_eager()
            continue

        st = t & STRIP_CHANNEL
        if st == STRIP_CHANNEL:  # system realtime message didn't have a channel
            st = t
        elif t & GET_CHANNEL != in_ch:
            click.secho(f"skipping {msg} not on channel {in_ch}: {t & GET_CHANNEL}")
            continue

        if __debug__:
            fg = "white"
            if st in system_realtime:
                fg = "blue"
            elif st == CONTROL_CHANGE:
                fg = "green"
            elif st == POLY_AFTERTOUCH:
                fg = "magenta"
            click.secho(
                f"{msg}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}", fg=fg
            )
        if st == START:
            await performance.start()
        elif st == STOP:
            await performance.stop()
        elif st == NOTE_ON:
            await performance.note_on(msg[1], msg[2])
        elif st == NOTE_OFF:
            await performance.note_off(msg[1], msg[2])
        elif st == POLY_AFTERTOUCH:
            await performance.at(msg[1], msg[2])
        elif st == CONTROL_CHANGE:
            if msg[1] == MOD_WHEEL:
                await performance.mod_wheel(msg[2])
            elif msg[1] == FOOT_PEDAL or msg[1] == EXPRESSION_PEDAL:
                await performance.expression(msg[2])
            elif msg[1] == SUSTAIN_PEDAL:
                await performance.sustain(msg[2])
            elif msg[1] in MODULATION_CC:
                await performance.cc(msg[1], msg[2])
            elif msg[1] == ALL_NOTES_OFF:
                await performance.cc(ALL_NOTES_OFF, msg[2])
                await performance.cc(SUSTAIN_PEDAL, 0)
            elif msg[1] == ALL_SOUND_OFF:
                await performance.cc(ALL_SOUND_OFF, msg[2])
                await performance.cc(SUSTAIN_PEDAL, 0)
            else:
                print(f"warning: unhandled CC {msg[1]}", file=sys.stderr)
        elif st == PITCH_BEND:
            await performance.bend(msg[1], msg[2])
        elif st == PROGRAM_CHANGE:
            await performance.pc(msg[1])
        else:
            if st not in handled_types:
                print(f"warning: unhandled event {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
