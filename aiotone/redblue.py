"""See the docstring to main()."""

from __future__ import annotations

import asyncio
import configparser
from enum import Enum
from pathlib import Path
import sys
import time
from typing import Callable, Dict, List, Sequence, Tuple

from attrs import define, field, Factory
import click
import numpy as np
from scipy.interpolate import interp1d
import uvloop

from . import monome
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
    EXPRESSION_PEDAL,
    SUSTAIN_PEDAL,
    FOOT_PEDAL,
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
from .slew import SlewGenerator


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
SUSTAIN_PEDAL_PORTAMENTO = {"sustain", "damper"}
PORTAMENTO_MODES = {"legato"} | SUSTAIN_PEDAL_PORTAMENTO | CONFIGPARSER_FALSE
WHITE_KEYS = {0, 2, 4, 5, 7, 9, 11}


class NoteMode(Enum):
    REGULAR = 0
    POWER = 1
    RED = 2
    BLUE = 3


@define
class MIDIMonitorGridApp(monome.GridApp):
    performance: Performance
    width: int = 0
    height: int = 0
    connected: bool = False
    _counter: int = 0
    _buffer: np.ndarray = field(init=False)
    _leds: np.ndarray = field(init=False)
    grid: monome.Grid = field(init=False)  # inherited from monome.GridApp

    def __post_init__(self) -> None:
        super().__init__()
        self._buffer = np.zeros(8, dtype=(">i", 8))
        self._leds = np.zeros(16, dtype=(">i", 8))

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

    def on_grid_disconnect(self) -> None:
        print("Grid disconnected")
        self.connected = False

    def on_grid_key(self, x: int, y: int, s: int) -> None:
        pass

    async def handle_leds(self) -> None:
        fps = 0
        last_sec = time.monotonic()
        while True:
            fps += 1
            self.draw()
            now = time.monotonic()
            if now - last_sec >= 1:
                self.performance.render_fps = fps / (now - last_sec)
                fps = 0
                last_sec = now
            await asyncio.sleep(0.03)

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.handle_leds())
        except asyncio.CancelledError:
            self.disconnect()
            raise

    def draw(self) -> None:
        if not self.connected:
            return

        leds = self._leds
        leds[:] = 0
        notes = self.performance.notes
        for y in range(0, 8):
            for x in range(0, 12):
                match notes.get(12 * (9 - y) + x):
                    case NoteMode.REGULAR | NoteMode.POWER:
                        leds[x][y] = 15
                    case NoteMode.RED:
                        leds[x][y] = 15
                    case NoteMode.BLUE:
                        leds[x][y] = 11
                    case None:
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
    red_port: MidiOut
    red_channel: int
    red_mod_wheel: bool
    red_expression_pedal: bool
    blue_port: MidiOut
    blue_channel: int
    blue_mod_wheel: bool
    blue_expression_pedal: bool
    pass_clock: bool
    start_stop: bool
    portamento: str
    damper_portamento_max: int
    accent_volume: int
    expression_min: int
    expression_max: int

    # Current state of the performance
    metronome: Metronome = Factory(Metronome)
    notes: Dict[int, NoteMode] = Factory(dict)
    last_expression_value: int = 64
    last_color: NoteMode = NoteMode.BLUE
    is_accent: bool = False
    is_portamento: bool = False
    render_fps: float = 0.0

    # Modes
    power_chord: bool = False
    duophon: bool = False

    # Internal state
    mod_wheel_target: Callable = field(init=False)
    expression_pedal_target: Callable = field(init=False)
    expression_scale: interp1d = field(init=False)
    expression_slew: SlewGenerator = field(init=False)
    expression_last_sent: int = field(init=False, default=-1)

    def __post_init__(self) -> None:
        if self.red_mod_wheel and self.blue_mod_wheel:
            self.mod_wheel_target = self.cc_both
        elif self.red_mod_wheel:
            self.mod_wheel_target = self.cc_red
        elif self.blue_mod_wheel:
            self.mod_wheel_target = self.cc_blue
        else:
            self.mod_wheel_target = self.cc_none

        if self.red_expression_pedal and self.blue_expression_pedal:
            self.expression_pedal_target = self.cc_both
        elif self.red_expression_pedal:
            self.expression_pedal_target = self.cc_red
        elif self.blue_expression_pedal:
            self.expression_pedal_target = self.cc_blue
        else:
            self.expression_pedal_target = self.cc_none

        self.expression_scale = interp1d(
            [0, 127], [self.expression_min, self.expression_max]
        )

        self.expression_slew = SlewGenerator(
            "expression slew", callback=self._expression_cb
        )

    def __attrs_post_init__(self) -> None:
        self.__post_init__()

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
        if self.pass_clock:
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

    async def legato_portamento(self, note: int) -> None:
        if not self.portamento == "legato":
            return

        if len(self.notes):
            last_note = list(self.notes.keys())[-1]
            if note > last_note:  # glide up
                p_time = 64
            elif note < last_note:  # glide down
                p_time = 80
            await self.cc_both(PORTAMENTO, 127)
            await self.cc_both(PORTAMENTO_TIME, p_time)
            self.is_portamento = True
        else:
            await self.cc_both(PORTAMENTO, 0)
            await self.cc_both(PORTAMENTO_TIME, 0)
            self.is_portamento = False

    async def damper_portamento(self, value: int) -> bool:
        """Handle sustain pedal-driven portamento.

        If returns True, the dispatcher should send regular sustain messages, too."""
        if self.portamento not in SUSTAIN_PEDAL_PORTAMENTO:
            return True

        if value == 0:
            await self.cc_both(PORTAMENTO, 0)
            await self.cc_both(PORTAMENTO_TIME, 0)
            self.is_portamento = False
        else:
            await self.cc_both(PORTAMENTO, 127)
            converted_value = int(self.damper_portamento_max * value / 127)
            await self.cc_both(PORTAMENTO_TIME, converted_value)
            self.is_portamento = True

        return self.portamento == "sustain"

    async def note_on(self, note: int, volume: int) -> None:
        if note == A[0]:
            self.power_chord = False
            self.duophon = False
            return

        if note == Bb[0]:
            self.power_chord = False
            self.duophon = True
            return

        if note == B[0]:
            self.power_chord = True
            self.duophon = False
            return

        was_accent = self.is_accent
        self.is_accent = (
            was_accent and len(self.notes) > 0
        ) or volume >= self.accent_volume
        if self.is_accent != was_accent:
            await self.expression(self.last_expression_value, self.is_accent)

        if self.power_chord:
            self.notes[note] = NoteMode.POWER
            await self.red(NOTE_ON, note, volume)
            await self.blue(NOTE_ON, note + 7, volume)
        elif self.duophon:
            red_notes = 0
            blue_notes = 0
            closest_note = 128
            closest_mode = NoteMode.REGULAR
            for old_note, old_mode in self.notes.items():
                if old_mode == NoteMode.RED:
                    red_notes += 1
                elif old_mode == NoteMode.BLUE:
                    blue_notes += 1
                note_distance = abs(note - old_note)
                if note_distance <= closest_note:
                    # <= because we want the last played note with the smallest distance
                    closest_mode = old_mode
                    closest_note = note_distance

            if self.is_portamento:
                use_channel = closest_mode
            elif self.last_color == NoteMode.RED:
                if not blue_notes:
                    use_channel = NoteMode.BLUE
                elif not red_notes:
                    use_channel = NoteMode.RED
                else:
                    use_channel = NoteMode.BLUE
            else:
                if not red_notes:
                    use_channel = NoteMode.RED
                elif not blue_notes:
                    use_channel = NoteMode.BLUE
                else:
                    use_channel = NoteMode.RED

            self.notes[note] = use_channel
            self.last_color = use_channel
            if use_channel == NoteMode.RED:
                await self.red(NOTE_ON, note, volume)
            else:
                await self.blue(NOTE_ON, note, volume)
        else:
            self.notes[note] = NoteMode.REGULAR
            await self.both(NOTE_ON, note, volume)

    async def note_off(self, note: int) -> None:
        if note in (A[0], Bb[0], B[0]):
            return

        mode = self.notes.pop(note, NoteMode.REGULAR)

        if mode == NoteMode.POWER:
            await self.red(NOTE_OFF, note, 0)
            await self.blue(NOTE_OFF, note + 7, 0)
        elif mode == NoteMode.RED:
            await self.red(NOTE_OFF, note, 0)
        elif mode == NoteMode.BLUE:
            await self.blue(NOTE_OFF, note, 0)
        else:
            await self.both(NOTE_OFF, note, 0)

    async def mod_wheel(self, value: int) -> None:
        await self.mod_wheel_target(MOD_WHEEL, value)

    async def expression(self, value: int, accent: bool | None = None) -> None:
        scaled_value = int(self.expression_scale(value))
        slew_value: float | tuple[float, float] = scaled_value

        self.last_expression_value = value  # sic, raw value

        if accent is not None:
            if accent:
                slew_value = (min(scaled_value + 32, 127), scaled_value)
                print("[accent slew]", slew_value)
                await self._expression_cb(slew_value[0])

        await self.expression_slew.update(slew_value)

    async def _expression_cb(self, cc_slew: float) -> None:
        cc = int(round(cc_slew))
        if cc == self.expression_last_sent:
            return
        self.expression_last_sent = cc
        await self.expression_pedal_target(MOD_WHEEL, cc)

    async def at(self, note: int, value: int) -> None:
        pass

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

    async def cc_none(self, type: int, value: int) -> None:
        pass


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

    - supports ACCENT notes which add +24 to expression when a key is hit strongly and
      the EXPRESSION_PEDAL is set to 87.5% or less (the trigger volume is configured
      by the "accent-volume" setting; accents saturate at 87.5% expression, for more use
      the pedal);

    - supports play modes: hit A-0 (lowest key on the 88-key keyboard) to engage regular
      mode, hit B-0 to engage power chord mode (the second Mother plays the dominant to
      the first Mother's tonic), hit Bb-0 to engage duophonic mode (the first and the
      second Mother play notes interchangeably, giving you real 2-voice polyphony);

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

        - an empty MIDI track to actually play the instrument

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

    uvloop.install()
    asyncio.run(async_main(config))


async def async_main(config: str) -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    cfg = configparser.ConfigParser()
    cfg.read(config)
    if cfg["from-ableton"].getint("channel") != 1:
        click.secho("from-ableton channel must be 1, sorry")
        raise click.Abort from None

    # Configure the `from_ableton` port
    try:
        from_ableton, to_ableton = get_ports(
            cfg["from-ableton"]["port-name"], clock_source=True
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

    from_ableton.set_callback(midi_callback)

    # Configure the `to_mother_red` port
    if cfg["from-ableton"]["port-name"] == cfg["to-mother-red"]["port-name"]:
        to_mother_red = to_ableton
    else:
        try:
            to_mother_red = get_out_port(cfg["to-mother-red"]["port-name"])
        except ValueError as port:
            click.secho(f"{port} not connected", fg="red", err=True)
            raise click.Abort from None

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
            raise click.Abort from None

    portamento_mode = cfg["from-ableton"]["portamento"]
    if portamento_mode not in PORTAMENTO_MODES:
        click.secho(
            f"from-ableton/portamento mode not recognized. Got {portamento_mode!r}, "
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
        pass_clock=cfg["from-ableton"].getboolean("pass-clock"),
        start_stop=cfg["from-ableton"].getboolean("start-stop"),
        portamento=cfg["from-ableton"]["portamento"],
        red_mod_wheel=cfg["to-mother-red"].getboolean("mod-wheel"),
        blue_mod_wheel=cfg["to-mother-blue"].getboolean("mod-wheel"),
        red_expression_pedal=cfg["to-mother-red"].getboolean("expression-pedal"),
        blue_expression_pedal=cfg["to-mother-blue"].getboolean("expression-pedal"),
        damper_portamento_max=cfg["from-ableton"].getint("damper-portamento-max"),
        accent_volume=cfg["from-ableton"].getint("accent-volume"),
        expression_min=cfg["from-ableton"].getint("expression-min"),
        expression_max=cfg["from-ableton"].getint("expression-max"),
    )
    grid_app = MIDIMonitorGridApp(performance)
    try:
        async with asyncio.TaskGroup() as tg:

            def serialosc_device_added(id, type, port):
                if type == "monome 128":
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
                await performance.legato_portamento(msg[1])
                await performance.note_on(msg[1], msg[2])
            elif t == NOTE_OFF:
                await performance.note_off(msg[1])
                await performance.legato_portamento(msg[1])
            elif t == POLY_AFTERTOUCH:
                await performance.at(msg[1], msg[2])
            elif t == CONTROL_CHANGE:
                if msg[1] == MOD_WHEEL:
                    await performance.mod_wheel(msg[2])
                elif msg[1] == EXPRESSION_PEDAL or msg[1] == FOOT_PEDAL:
                    await performance.expression(msg[2])
                elif msg[1] == SUSTAIN_PEDAL:
                    if await performance.damper_portamento(msg[2]):
                        await performance.cc_both(SUSTAIN_PEDAL, msg[2])
                elif msg[1] == ALL_NOTES_OFF:
                    await performance.cc_both(ALL_NOTES_OFF, msg[2])
                else:
                    print(f"warning: unhandled CC {msg}", file=sys.stderr)
            elif t == PITCH_BEND:
                # Note: this does nothing on the Mother 32 firmware 1.0.  We need to
                # simulate this with virtual note_on events with portamento.
                await performance.both(PITCH_BEND, msg[1], msg[2])
            else:
                if st not in handled_types:
                    print(f"warning: unhandled event {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
