"""See the docstring to main()."""

from __future__ import annotations

import asyncio
import configparser
from pathlib import Path
import sys
import time
from typing import List, Sequence, Set, Tuple

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
    ALL_NOTES_OFF,
    STRIP_CHANNEL,
    get_ports,
    get_out_port,
    silence,
)


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
PORTAMENTO_MODES = {"damper", "sustain", "legato"} | CONFIGPARSER_FALSE


@dataclass
class Performance:
    red_port: MidiOut
    red_channel: int
    blue_port: MidiOut
    blue_channel: int
    start_stop: bool
    portamento: str
    damper_portamento_max: int
    metronome: Metronome = Factory(Metronome)
    notes: Set[int] = Factory(set)

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

    def send_once(self, message: Sequence[int]) -> None:
        rp = self.red_port
        bp = self.blue_port
        rp.send_message(message)
        if bp is not rp:
            bp.send_message(message)

    async def clock(self) -> None:
        self.send_once([CLOCK])

    async def start(self) -> None:
        if self.start_stop:
            self.send_once([START])
        await self.metronome.reset()

    async def stop(self) -> None:
        if self.start_stop:
            self.send_once([STOP])
        await self.cc_red(ALL_NOTES_OFF, 0)
        await self.cc_blue(ALL_NOTES_OFF, 0)
        self.notes.clear()

    async def legato_portamento(self) -> None:
        if len(self.notes) > 1:
            await self.cc_both(PORTAMENTO, 127)
            await self.cc_both(PORTAMENTO_TIME, 64)
        elif len(self.notes) == 0:
            await self.cc_both(PORTAMENTO, 0)
            await self.cc_both(PORTAMENTO_TIME, 0)

    async def damper_portamento(self, value: int) -> None:
        if value == 0:
            await self.cc_both(PORTAMENTO, 0)
            await self.cc_both(PORTAMENTO_TIME, 0)
        else:
            await self.cc_both(PORTAMENTO, 127)
            converted_value = int(self.damper_portamento_max * value / 127)
            await self.cc_both(PORTAMENTO_TIME, converted_value)

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
    default=str(CURRENT_DIR / "aiotone-redblue.ini"),
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
    This is a module which allows controlling two Moog Mother 32 synthesizers as
    a single rich instrument.

    It's got the following features:

    - dispatches NOTE_ON and NOTE_OFF events to both Mothers at the same time, allowing
      for real rich chorus;

    - dispatches MOD_WHEEL to the first Mother's MOD_WHEEL (which you can use via the
      ASSIGN CV output);

    - dispatches EXPRESSION_PEDAL to the second Mother's MOD_WHEEL (which you can use
      via the ASSIGN CV output);

    - allows for controlling portamento during MIDI performance either using legato
      notes or the damper pedal (if the mode is "sustain", the pedal will still sustain
      the notes played, if the mode is "damper" then it will no longer sustain notes,
      only control portamento).

    To use this yourself, you will need:

    - two Mother 32 synthesizers, let's call them Red and Blue

    - MIDI connections to both Mothers, let's say Red on Channel 2, Blue on Channel 3

    - an IAC port called "IAC aiotone" which you can configure in Audio MIDI Setup on
      macOS

    - an Ableton project which will be configured as follows:

        - an External Instrument MIDI track for Red:

            - MIDI FROM: IAC (IAC aiotone) Ch. 11

            - MIDI TO: [your audio interface] Ch. 2

            - Audio From: [your audio interface] [input with a connection from Red's
              Line Out]

            - Turn MONITOR to IN

            - Panning: ALL LEFT

        - an External Instrument MIDI track for Blue:

            - MIDI FROM: IAC (IAC aiotone) Ch. 12

            - MIDI TO: [your audio interface] Ch. 3

            - Audio From: [your audio interface] [input with a connection from Blue's
              Line Out]

            - Turn MONITOR to IN

            - Panning: ALL RIGHT

        - an empty MIDI track to actually play the intrument

            - MIDI FROM: [your audio interface] Ch. 1

            - MIDI TO: IAC (IAC aiotone) Ch. 1

    You can customize the ports by creating a config file.  Use `--make-config` to
    output a new config to stdout.

    Then run `python -m aiotone.redblue --config=PATH_TO_YOUR_CONFIG_FILE`.

    With this setup, you record notes on the empty MIDI track and `aiotone` dispatches
    them.  If you'd like to "freeze" the MIDI so you don't need aiotone enabled anymore,
    just record the MIDI on the MIDI tracks for Red and Blue.
    """
    if make_config:
        with open(CURRENT_DIR / "aiotone-redblue.ini") as f:
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

    porta_mode = cfg["from-ableton"]["portamento"]
    if porta_mode not in PORTAMENTO_MODES:
        click.secho(
            f"from-ableton/portamento mode not recognized. Got {porta_mode!r}, "
            f"expected one of {', '.join(PORTAMENTO_MODES)}",
            fg="red",
            err=True,
        )
        raise click.Abort

    performance = Performance(
        red_port=to_mother_red,
        blue_port=to_mother_blue,
        red_channel=cfg["to-mother-red"].getint("channel") - 1,
        blue_channel=cfg["to-mother-blue"].getint("channel") - 1,
        start_stop=cfg["from-ableton"].getboolean("start-stop"),
        portamento=cfg["from-ableton"]["portamento"],
        damper_portamento_max=cfg["from-ableton"].getint("damper-portamento-max"),
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
    sustain_pedal_portamento = {"sustain", "damper"}
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
                click.secho(
                    f"{msg}\tevent delta: {delta:.4f}\tlatency: {latency:.4f}", fg=fg
                )
            if t == START:
                await performance.start()
            elif t == STOP:
                await performance.stop()
            elif t == NOTE_ON:
                performance.notes.add(msg[1])
                if performance.portamento == "legato":
                    await performance.legato_portamento()
                await performance.both(NOTE_ON, msg[1], msg[2])
            elif t == NOTE_OFF:
                performance.notes.remove(msg[1])
                if performance.portamento == "legato":
                    await performance.legato_portamento()
                await performance.both(NOTE_OFF, msg[1], msg[2])
            elif t == CONTROL_CHANGE:
                if msg[1] == MOD_WHEEL:
                    await performance.cc_red(MOD_WHEEL, msg[2])
                elif msg[1] == EXPRESSION_PEDAL:
                    await performance.cc_blue(MOD_WHEEL, msg[2])
                elif msg[1] == SUSTAIN_PEDAL:
                    if performance.portamento in sustain_pedal_portamento:
                        await performance.damper_portamento(msg[2])
                        if performance.portamento == "sustain":
                            await performance.cc_both(SUSTAIN_PEDAL, msg[2])
                elif msg[1] == ALL_NOTES_OFF:
                    await performance.cc_both(ALL_NOTES_OFF, msg[2])
                else:
                    print(f"warning: unhandled CC {msg}", file=sys.stderr)
            else:
                if st not in handled_types:
                    print(f"warning: unhandled event {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
