"""See the docstring to main()."""

from __future__ import annotations
from typing import *

import asyncio
import configparser
import itertools
from pathlib import Path
import random
import sys
import time

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
    MOD_WHEEL,
    EXPRESSION_PEDAL,
    SUSTAIN_PEDAL,
    PORTAMENTO,
    PORTAMENTO_TIME,
    PITCH_BEND,
    ALL_NOTES_OFF,
    STRIP_CHANNEL,
    get_ports,
    get_out_port,
    silence,
)
from .notes import C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B, Db, Eb, Gb, Ab, Bb  # NoQA
from .notes import all_notes


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


class PlayAsyncFunction(Protocol):
    def __call__(
        self, note: int, pulses: int, volume: int, decay: float = 0.5,
    ) -> Awaitable[None]:
        ...


class RawAsyncFunction(Protocol):
    def __call__(self, event: int, note: int, volume: int) -> Awaitable[None]:
        ...


class CcAsyncFunction(Protocol):
    def __call__(self, type: int, value: int) -> Awaitable[None]:
        ...


@dataclass
class Performance:
    red_port: MidiOut
    red_channel: int
    blue_port: MidiOut
    blue_channel: int
    start_stop: bool

    # Current state of the performance
    metronome: Metronome = Factory(Metronome)
    last_expression_value: int = 64
    blue_sequencer: Optional[asyncio.Task] = None
    red_sequencer: Optional[asyncio.Task] = None
    key: List[int] = C

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

    async def play_red(
        self, note: int, pulses: int, volume: int, decay: float = 0.5,
    ) -> None:
        await self.play(self.red_port, self.red_channel, note, pulses, volume, decay)

    async def play_blue(
        self, note: int, pulses: int, volume: int, decay: float = 0.5,
    ) -> None:
        await self.play(self.blue_port, self.blue_channel, note, pulses, volume, decay)

    async def wait(self, pulses: int) -> None:
        await self.metronome.wait(pulses)

    def send_once(self, message: Sequence[int]) -> None:
        """Ensure that each Mother receives this message only once.
        
        When both Mothers are on the same MIDI OUT port (just different channels),
        certain MIDI messages which are channel agnostic, would be effectively sent
        twice.  While that doesn't matter most of the time, when it does, use this
        method to ensure a message is only received once on each device.
        """
        rp = self.red_port
        bp = self.blue_port
        rp.send_message(message)
        if bp is not rp:
            bp.send_message(message)

    async def clock(self) -> None:
        await self.metronome.tick()
        self.send_once([CLOCK])

    async def start(self) -> None:
        if self.start_stop:
            self.send_once([START])
        await self.metronome.reset()
        if not self.red_sequencer:
            self.red_sequencer = asyncio.create_task(
                self.sequencer(self.play_red, self.cc_red, self.red)
            )
        if not self.blue_sequencer:
            self.blue_sequencer = asyncio.create_task(
                self.sequencer(self.play_blue, self.cc_blue, self.blue)
            )

    async def stop(self) -> None:
        if self.red_sequencer:
            self.red_sequencer.cancel()
            self.red_sequencer = None
        if self.blue_sequencer:
            self.blue_sequencer.cancel()
            self.blue_sequencer = None
        if self.start_stop:
            self.send_once([STOP])
        await self.cc_red(ALL_NOTES_OFF, 0)
        await self.cc_blue(ALL_NOTES_OFF, 0)

    async def sequencer(
        self, play: PlayAsyncFunction, cc: CcAsyncFunction, raw: RawAsyncFunction
    ) -> None:
        octaves = range(1, 7)
        speeds = (24, 24, 24, 24, 12, 12)
        decays = [num / 100 for num in range(20, 90, 2)]
        decays.extend(reversed(decays))
        intervals = (0, 0, 0, 0, 0, 0, 0, 0, 7, 7, 7, 7, 5, 5, 10)

        for decay in itertools.cycle(decays):
            oct = random.choice(octaves)
            speed = random.choice(speeds)
            interval = random.choice(intervals)
            await play(self.key[oct] + interval, speed, 64, decay)

    async def note_on(self, note: int, volume: int) -> None:
        for note_octaves in all_notes:
            if note in note_octaves:
                self.key = note_octaves
                break

    async def note_off(self, note: int) -> None:
        ...

    async def mod_wheel(self, value: int) -> None:
        await self.cc_red(MOD_WHEEL, value)

    async def expression(self, value: int) -> None:
        self.last_expression_value = value
        await self.cc_blue(MOD_WHEEL, value)

    # Raw commands

    async def red(self, event: int, note: int, volume: int) -> None:
        self.red_port.send_message([event | self.red_channel, note, volume])

    async def blue(self, event: int, note: int, volume: int) -> None:
        self.blue_port.send_message([event | self.blue_channel, note, volume])

    async def both(self, event: int, note: int, volume: int) -> None:
        self.red_port.send_message([event | self.red_channel, note, volume])
        self.blue_port.send_message([event | self.blue_channel, note, volume])

    async def cc_red(self, type: int, value: int) -> None:
        self.red_port.send_message([CONTROL_CHANGE | self.red_channel, type, value])

    async def cc_blue(self, type: int, value: int) -> None:
        self.blue_port.send_message([CONTROL_CHANGE | self.blue_channel, type, value])

    async def cc_both(self, type: int, value: int) -> None:
        self.red_port.send_message([CONTROL_CHANGE | self.red_channel, type, value])
        self.blue_port.send_message([CONTROL_CHANGE | self.blue_channel, type, value])


@click.command()
@click.option(
    "--config",
    help="Read configuration from this file",
    default=str(CURRENT_DIR / "aiotone-mothergen.ini"),
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
    This is a module which generates music on two Moog Mother 32 synthesizers.

    To use this yourself, you will need:

    - two Mother 32 synthesizers, let's call them Red and Blue

    - MIDI connections to both Mothers, let's say Red on Channel 2, Blue on Channel 3

    - an IAC port called "IAC aiotone" which you can configure in Audio MIDI Setup on
      macOS

    You can customize the ports by creating a config file.  Use `--make-config` to
    output a new config to stdout.

    Then run `python -m aiotone.mothergen --config=PATH_TO_YOUR_CONFIG_FILE`.
    """
    if make_config:
        with open(CURRENT_DIR / "aiotone-mothergen.ini") as f:
            print(f.read())
        return

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(async_main(config))


async def async_main(config: str) -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    cfg = configparser.ConfigParser()
    cfg.read(config)
    if cfg["from-ableton"].getint("channel") != 1:
        click.secho("from-ableton channel must be 1, sorry")
        raise click.Abort

    # Configure the `from_ableton` port
    try:
        from_ableton, to_ableton = get_ports(
            cfg["from-ableton"]["port-name"], clock_source=True
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

    from_ableton.set_callback(midi_callback)

    # Configure the `to_mother_red` port
    if cfg["from-ableton"]["port-name"] == cfg["to-mother-red"]["port-name"]:
        to_mother_red = to_ableton
    else:
        try:
            to_mother_red = get_out_port(cfg["to-mother-red"]["port-name"])
        except ValueError as port:
            click.secho(f"{port} not connected", fg="red", err=True)
            raise click.Abort

    # Configure the `to_mother_blue` port
    if cfg["from-ableton"]["port-name"] == cfg["to-mother-blue"]["port-name"]:
        to_mother_blue = to_ableton
    elif cfg["to-mother-red"]["port-name"] == cfg["to-mother-blue"]["port-name"]:
        to_mother_blue = to_mother_red
    else:
        try:
            to_mother_blue = get_out_port(cfg["to-mother-blue"]["port-name"])
        except ValueError as port:
            click.secho(f"{port} not connected", fg="red", err=True)
            raise click.Abort

    performance = Performance(
        red_port=to_mother_red,
        blue_port=to_mother_blue,
        red_channel=cfg["to-mother-red"].getint("channel") - 1,
        blue_channel=cfg["to-mother-blue"].getint("channel") - 1,
        start_stop=cfg["from-ableton"].getboolean("start-stop"),
    )
    try:
        await midi_consumer(queue, performance)
    except asyncio.CancelledError:
        from_ableton.cancel_callback()
        silence(to_ableton)


async def midi_consumer(
    queue: asyncio.Queue[MidiMessage], performance: Performance
) -> None:
    print("Waiting for MIDI messages...")
    silence(performance.red_port, channels=[performance.red_channel])
    silence(performance.blue_port, channels=[performance.blue_channel])
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
            continue

        st = t & STRIP_CHANNEL
        if st == STRIP_CHANNEL:  # system realtime message didn't have a channel
            st = t
        if __debug__ and st == t:
            fg = "white"
            if t in system_realtime:
                fg = "blue"
            elif t == CONTROL_CHANGE:
                fg = "green"
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
            await performance.note_off(msg[1])
        elif t == CONTROL_CHANGE:
            if msg[1] == MOD_WHEEL:
                await performance.mod_wheel(msg[2])
            elif msg[1] == EXPRESSION_PEDAL:
                await performance.expression(msg[2])
            elif msg[1] == SUSTAIN_PEDAL:
                await performance.cc_both(SUSTAIN_PEDAL, msg[2])
            elif msg[1] == ALL_NOTES_OFF:
                await performance.cc_both(ALL_NOTES_OFF, msg[2])
            else:
                print(f"warning: unhandled CC {msg}", file=sys.stderr)
        elif t == PITCH_BEND:
            # Note: this requires Mother 32 firmware 2.0.
            await performance.both(PITCH_BEND, msg[1], msg[2])
        else:
            if st not in handled_types:
                print(f"warning: unhandled event {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
