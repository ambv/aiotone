"""Made for Disquiet Junto Project #685. See the docstring to main()."""

from __future__ import annotations
from typing import Awaitable, Protocol

import asyncio
import configparser
import functools
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
    POLY_AFTERTOUCH,
    MOD_WHEEL,
    FOOT_PEDAL,
    EXPRESSION_PEDAL,
    SUSTAIN_PEDAL,
    PORTAMENTO,
    PORTAMENTO_TIME,
    PITCH_BEND,
    ALL_NOTES_OFF,
    STRIP_CHANNEL,
    GET_CHANNEL,
    resolve_ports,
    silence,
    float_to_msb_lsb,
)
from .notes import C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B, Db, Eb, Gb, Ab, Bb  # NoQA
from .notes import all_notes, Notes


# types
EventDelta = float  # in seconds
TimeStamp = float  # time.time()
MidiPacket = list[int]
MidiMessage = tuple[MidiPacket, EventDelta, TimeStamp]

CURRENT_DIR = Path(__file__).parent
CONFIGPARSER_FALSE = {
    k
    for k, v in configparser.ConfigParser.BOOLEAN_STATES.items()  # type: ignore
    if v is False
}
REZ = {"rez", "resonance", "modwheel"}
PORTAMENTO_MODES = REZ | CONFIGPARSER_FALSE
CC = CONTROL_CHANGE


class PlayAsyncFunction(Protocol):
    def __call__(
        self,
        note: int,
        pulses: int,
        volume: int,
        decay: float = 0.5,
    ) -> Awaitable[None]: ...


class RawAsyncFunction(Protocol):
    def __call__(self, event: int, note: int, volume: int) -> Awaitable[None]: ...


class TrigAsyncFunction(Protocol):
    def __call__(
        self,
        pulses: int,
    ) -> Awaitable[None]: ...


def h(amount=16):
    return random.randint(0, amount) - int(amount // 2)


@dataclass
class Performance:
    in_channel: int
    iridium_port: MidiOut
    iridium_channel: int
    subh_port: MidiOut
    subh_channel: int
    to_ableton_port: MidiOut
    to_ableton_channel_1: int
    to_ableton_channel_2: int
    start_stop: bool

    # Current state of the performance
    metronome: Metronome = Factory(Metronome)
    last_expression_value: int = 64
    iridium_sequencer: asyncio.Task | None = None
    subh_sequencer: asyncio.Task | None = None
    key: Notes = C

    def __attrs_post_init__(self) -> None:
        self.play_iridium = functools.partial(
            self.play,
            out=self.iridium_port,
            channel=self.iridium_channel,
            color="magenta",
        )
        self.play_subh = functools.partial(
            self.play, out=self.subh_port, channel=self.subh_channel, color="yellow"
        )
        self.trig_subh_1 = functools.partial(
            self.trig,
            out=self.to_ableton_port,
            channel=self.to_ableton_channel_1,
            color="red",
        )
        self.trig_subh_2 = functools.partial(
            self.trig,
            out=self.to_ableton_port,
            channel=self.to_ableton_channel_2,
            color="blue",
        )

    async def setup(self) -> None:
        # Like `__attrs_post_init__` but requires awaiting so a separate step.
        silence(
            self.iridium_port, stop=self.start_stop, channels=[self.iridium_channel]
        )
        silence(self.subh_port, stop=self.start_stop, channels=[self.subh_channel])
        silence(
            self.to_ableton_port,
            stop=self.start_stop,
            channels=[self.to_ableton_channel_1, self.to_ableton_channel_2],
        )

        await self.all(CC, MOD_WHEEL, 0)
        await self.all(CC, PORTAMENTO, 0)
        await self.all(CC, PORTAMENTO_TIME, 0)

    async def play(
        self,
        note: int,
        pulses: int,
        volume: int,
        decay: float = 0.5,
        *,
        out: MidiOut,
        channel: int,
        color: str = "white",
    ) -> None:
        click.secho(f"-> {[NOTE_ON | channel, note, volume]}", fg=color, bold=True)
        note_on_length = int(round(pulses * decay, 0))
        rest_length = pulses - note_on_length
        out.send_message([NOTE_ON | channel, note, volume])
        await self.wait(note_on_length)
        click.secho(f"-> {[NOTE_OFF | channel, note, volume]}", fg=color, bold=True)
        out.send_message([NOTE_OFF | channel, note, volume])
        await self.wait(rest_length)

    async def wait(self, pulses: int) -> None:
        await self.metronome.wait(pulses)

    async def trig(
        self,
        pulses: int,
        *,
        out: MidiOut,
        channel: int,
        color: str = "white",
    ) -> None:
        # click.secho(f"-> {[NOTE_ON | channel, self.key[4], 64]}", fg=color, bold=True)
        out.send_message([NOTE_ON | channel, self.key[4], 64])
        await self.wait(1)
        # click.secho(f"-> {[NOTE_OFF | channel, self.key[4], 0]}", fg=color, bold=True)
        out.send_message([NOTE_OFF | channel, self.key[4], 0])
        await self.wait(pulses - 1)

    def send_once(self, message: list[int]) -> None:
        """Ensure that each device receives this message only once.

        When all devices are on the same MIDI OUT port (just different channels),
        certain MIDI messages which are channel agnostic, would be effectively sent
        three times.  While that doesn't matter most of the time, when it does, use this
        method to ensure a message is only received once on each device.
        """
        rp = self.iridium_port
        gp = self.subh_port
        rp.send_message(message)
        if gp is not rp:
            gp.send_message(message)

    # Messages received from `midi_consumer`
    def clock_eager(self) -> None:
        self.send_once([CLOCK])

    async def clock(self) -> None:
        await self.metronome.tick()
        self.send_once([CLOCK])

    async def start(self) -> None:
        if self.start_stop:
            self.send_once([START])
        await self.metronome.reset()
        if not self.iridium_sequencer:
            self.iridium_sequencer = asyncio.create_task(
                self.pick_me_up_iridium(self.play_iridium, self.iridium)
            )
        if not self.subh_sequencer:
            self.subh_sequencer = asyncio.create_task(
                self.pick_me_up_subharmonicon(
                    self.play_subh, self.subh, self.trig_subh_1, self.trig_subh_2
                )
            )

    async def stop(self) -> None:
        if self.iridium_sequencer:
            self.iridium_sequencer.cancel()
            self.iridium_sequencer = None
        if self.subh_sequencer:
            self.subh_sequencer.cancel()
            self.subh_sequencer = None
        if self.start_stop:
            self.send_once([STOP])
        await self.iridium(CC, ALL_NOTES_OFF, 0)
        await self.subh(CC, ALL_NOTES_OFF, 0)

    async def note_on(self, note: int, volume: int) -> None:
        for note_octaves in all_notes:
            if note in note_octaves:
                self.key = note_octaves
                break

    async def note_off(self, note: int) -> None: ...

    async def mod_wheel(self, value: int) -> None:
        await self.iridium(CC, MOD_WHEEL, value)

    async def expression(self, value: int) -> None:
        self.last_expression_value = value
        await self.iridium(CC, EXPRESSION_PEDAL, value)

    async def at(self, note: int, value: int) -> None:
        pass
        # self.note_output.send_message([POLY_AFTERTOUCH | self.out_channel, note, value])

    # Raw commands

    async def iridium(self, event: int, note: int, volume: int) -> None:
        volume = max(0, min(127, volume))
        self.iridium_port.send_message([event | self.iridium_channel, note, volume])

    async def subh(self, event: int, note: int, volume: int) -> None:
        # click.secho(f"-> {[event, note, volume]}", fg="yellow")
        volume = max(0, min(127, volume))
        self.subh_port.send_message([event | self.subh_channel, note, volume])

    async def to_ableton(self, event: int, note: int, volume: int) -> None:
        # NOTE: this needs `event | ch` on the caller end!
        click.secho(f"-> {[event, note, volume]}", fg="yellow")
        volume = max(0, min(127, volume))
        self.to_ableton_port.send_message([event, note, volume])

    async def all(self, event: int, note: int, volume: int) -> None:
        self.iridium_port.send_message([event | self.iridium_channel, note, volume])
        self.subh_port.send_message([event | self.subh_channel, note, volume])
        self.to_ableton_port.send_message(
            [event | self.to_ableton_channel_1, note, volume]
        )
        self.to_ableton_port.send_message(
            [event | self.to_ableton_channel_2, note, volume]
        )

    # Sequencers

    async def mother_simple(
        self, play: PlayAsyncFunction, raw: RawAsyncFunction
    ) -> None:
        octaves = range(1, 7)
        speeds = (24, 24, 24, 24, 12, 12)
        decays = [num / 100 for num in range(20, 50, 2)]
        decays.extend(reversed(decays))
        intervals = 10 * [0] + 4 * [7] + 2 * [5] + [10]

        for decay in itertools.cycle(decays):
            oct = random.choice(octaves)
            speed = random.choice(speeds)
            interval = random.choice(intervals)
            await play(self.key[oct] + interval, speed, 64, decay)

    async def pick_me_up_iridium(
        self, play: PlayAsyncFunction, raw: RawAsyncFunction
    ) -> None:
        seq = [
            # bar 1
            (C[3], 64 + h()),
            (E[3], 64 + h()),
            (B[3], 64 + h()),
            (A[3], 64 + h()),
            (G[3], 64 + h()),
            (D[3], 64 + h()),
            (E[3], 64 + h()),
            (C[3], 64 + h()),
            # bar 2
            (D[3], 64 + h()),
            (G[3], 64 + h()),
            (D[3], 64 + h()),
            (E[3], 64 + h()),
            (E[3], 64 + h()),
            (D[3], 64 + h()),
            (E[3], 64 + h()),
            (G[2], 64 + h()),
            # bar 3
            (C[3], 64 + h()),
            (E[3], 64 + h()),
            (B[3], 64 + h()),
            (G[3], 64 + h()),
            (A[3], 64 + h()),
            (G[3], 64 + h()),
            (E[3], 64 + h()),
            (C[3], 64 + h()),
            # bar 4
            (D[3], 64 + h()),
            (G[3], 64 + h()),
            (A[3], 64 + h()),
            (G[3], 64 + h()),
            (G[3], 64 + h()),
            (C[3], 64 + h()),
            (D[3], 64 + h()),
            (E[3], 64 + h()),
            # bar 5
            (C[3], 64 + h()),
            (G[4], 64 + h()),
            (B[4], 64 + h()),
            (A[4], 64 + h()),
            (G[4], 64 + h()),
            (C[4], 64 + h()),
            (E[4], 64 + h()),
            (C[4], 64 + h()),
            # bar 6
            (D[4], 64 + h()),
            (G[4], 64 + h()),
            (D[4], 64 + h()),
            (D[4], 64 + h()),
            (D[4], 64 + h()),
            (E[4], 64 + h()),
            (D[4], 64 + h()),
            (G[3], 64 + h()),
            # bar 7
            (C[3], 64 + h()),
            (E[4], 64 + h()),
            (B[4], 64 + h()),
            (A[4], 64 + h()),
            (G[4], 64 + h()),
            (E[4], 64 + h()),
            (C[4], 64 + h()),
            (C[4], 64 + h()),
            # bar 8
            (D[4], 64 + h()),
            (G[4], 64 + h()),
            (A[4], 64 + h()),
            (G[4], 64 + h()),
            (G[4], 64 + h()),
            (G[4], 64 + h()),
            (G[4], 64 + h()),
            (G[4], 64 + h()),
        ]

        await raw(CC, PORTAMENTO, 127)
        await raw(CC, PORTAMENTO_TIME, 1)

        base_notes: list[Notes] = [C, A, F, G, A, F, D, G, C]
        last_change = 0

        while True:
            step_num = 0
            picked_up: set[int] = set()
            last_note = None
            poly_at = 0

            while len(picked_up) < len(seq) * 8 / 10:
                step = seq[step_num]

                try:
                    if step_num in picked_up:
                        if last_note:
                            poly_at += 32
                            await raw(POLY_AFTERTOUCH, last_note, poly_at)
                            await raw(CC, EXPRESSION_PEDAL, poly_at)
                        await self.wait(12)
                        if poly_at < 192:
                            continue
                        else:
                            break

                    if step[0] != last_note:
                        if last_note:
                            await raw(NOTE_OFF, last_note, 0)
                            await raw(CC, EXPRESSION_PEDAL, 0)
                        await raw(
                            CC, PORTAMENTO_TIME, 1 if random.random() > 0.1 else 32
                        )
                        await raw(NOTE_ON, step[0], step[1])
                        last_note = step[0]
                        poly_at = 0
                    elif last_note:
                        poly_at += 32
                        await raw(POLY_AFTERTOUCH, last_note, poly_at)

                    await self.wait(12)

                    pick = random.randint(-len(seq), step_num)
                    if pick >= 0:
                        if pick in picked_up:
                            step_num = -1
                            if self.metronome.position - last_change > 240:
                                last_change = self.metronome.position
                                self.key = base_notes.pop(0)
                                base_notes.append(self.key)
                                print(f"new key: {self.key}")
                            continue

                        picked_up.add(pick)
                finally:
                    step_num += 1
                    step_num = step_num % len(seq)

            if last_note:
                await raw(NOTE_OFF, last_note, 0)
                await raw(CC, EXPRESSION_PEDAL, 0)

    async def subharmonicon_example(
        self, play: PlayAsyncFunction, raw: RawAsyncFunction
    ) -> None:
        """Subharmonicon-specific sequencing.

        Start with the following Subharmonicon settings:
        - Ableton Live's direct MIDI output to Subharmonicon *without* transport
          control;
        - Quantize disabled;
        - Polyrhythm section disabled (all four rhythms not having either SEQ1 nor
          SEQ2 enabled);
        - VCO1 and VCO2 tuned to the center position.

        You'll notice that the SUB 1 FREQ and SUB 2 FREQ settings are being set
        by CC. It doesn't matter what they're set to on the panel.

        When the internal sequencer on the Subharmonicon is playing, the internal
        envelope somehow doesn't restart, leading to quiet output. You can still
        use the VCO and SUB outputs then for external filtering and mixing.

        In this case you can still trigger SubH from the polyrhythm section, just
        make sure SEQ 1 ASSIGN and SEQ 2 ASSIGN buttons under the oscillators are
        all off, otherwise the internal sequencer knobs will influence the sequence.

        From the manual:
        - VCO1 = CC4 0-127 + CC36 0-127
        - VCO1 SUB1 = CC103 0-127 representing 16 divider values
        - VCO1 SUB2 = CC104 0-127 representing 16 divider values
        - VCO2 = CC12 0-127 + CC44 0-127
        - VCO2 SUB1 = CC105 0-127 representing 16 divider values
        - VCO2 SUB2 = CC106 0-127 representing 16 divider values
        - VCF EG ATTACK = CC23 0-127 + CC55 0-127
        - VCF EG DECAY = CC24 0-127 + CC56 0-127
        - VCA EG ATTACK = CC28 0-127 + CC60 0-127
        - VCA EG DECAY = CC29 0-127 + CC61 0-127
        - Rhythm Generator Retrigger Logic = CC113 0-63 OR (default) / 64-127 XOR

        Contrary to the manual, the 16 divider values in subs must be encoded like this
        to have effect on the instrument:
        - 4 -> div 16
        - 12 -> div 15
        - 20 -> div 14
        - ...
        - 124 -> div 1

        CC values non-quantized like presented above are ignored by the Subharmonicon.

        The rhythm generator retrigger logic only affects the internal envelopes,
        not the advancing of the sequencers.

        The SEQ OCT setting doesn't matter for MIDI notes and CC.
        The QUANTIZE setting matters, partially. You have influence over whether
        it's off entirely, on in equal temperament, or on in just temperament.
        Neither equal or just temperament quantize to actual notes in subs.
        The subdivisions are absolute.
        """

        def shift(li: list[int]) -> int:
            result = li.pop(0)
            li.append(result)
            return result

        # VCO 1
        await raw(CC, 4, 64)
        await raw(CC, 36, 0)
        # VCO 2
        await raw(CC, 12, 64)
        await raw(CC, 44, 0)
        # VCF attack
        await raw(CC, 23, 0)
        await raw(CC, 55, 0)
        # VCF decay
        await raw(CC, 24, 8)
        await raw(CC, 56, 0)
        # VCA attack
        await raw(CC, 28, 8)
        await raw(CC, 60, 0)
        # VCA decay
        await raw(CC, 29, 8)
        await raw(CC, 61, 0)

        # XOR does nothing in this use case
        await raw(CC, 113, 127)

        # Thanks to Matt Orenstein for pointing out the required offset to make
        # Subharmonicon accept the value.
        div = [8 * i + 5 for i in range(16)]

        async def subharmonics() -> None:
            val = shift(div)
            await self.wait(12)
            await raw(CC, 103, val)
            await raw(CC, 104, val)
            await self.wait(12)
            await raw(CC, 105, val)
            await raw(CC, 106, val)

        while True:
            await asyncio.gather(
                play(self.key[4], 6, 64, 1.0),
                subharmonics(),
            )

    async def subharmonicon_vco_sweep(
        self,
        play: PlayAsyncFunction,
        raw: RawAsyncFunction,
        trig1: TrigAsyncFunction,
        trig2: TrigAsyncFunction,
    ) -> None:
        """This example sweeps VCO1 and VCO2 main frequencies up and down.

        There's separate triggers through the `to_ableton` MIDI port, so that each
        Subharmonicon voice can be triggered separately.  In my case this is routed
        through ADAT control voltage, which introduces
        """

        def vco(msb_num: int, val: float) -> tuple[Awaitable, ...]:
            msb, lsb = float_to_msb_lsb(val)
            return raw(CC, msb_num, msb), raw(CC, msb_num + 32, lsb)

        div = [8 * i + 5 for i in range(16)]
        vco1 = 0.5
        vco2 = 0.25
        await asyncio.gather(*vco(4, vco1), *vco(12, vco2))
        await raw(CC, 103, div[-1])
        await raw(CC, 104, div[-2])
        await raw(CC, 105, div[-3])
        await raw(CC, 106, div[-4])

        await play(self.key[4], 1, 64, 1.0)

        async def sweep(vco1, vco2):
            i1 = 0.002
            i2 = 0.002
            while True:
                vco1 += i1
                vco2 += i2
                if vco1 >= 1.0 or vco1 <= 0.0:
                    vco1 = min(1.0, max(0.0, vco1))
                    i1 *= -1
                if vco2 >= 1.0 or vco2 <= 0.0:
                    vco2 = min(1.0, max(0.0, vco2))
                    i2 *= -1
                await asyncio.gather(*vco(4, vco1), *vco(12, vco2), self.wait(2))

        async def trigger_once(
            pulses: int, force_trig1: bool = False, force_trig2: bool = False
        ):
            trigs = []
            if force_trig1 or random.random() < 0.33:
                trigs.append(trig1(1))
            if force_trig2 or random.random() < 0.33:
                trigs.append(trig2(1))
            await asyncio.gather(*trigs, self.wait(pulses))

        async def triggers():
            speeds = (24, 24, 24, 24, 12, 12, 6, 3)
            while True:
                s = random.choice(speeds)
                if s < 12:
                    for i in range(int(12 // s)):
                        await trigger_once(
                            s, force_trig1=i % 2 == 0, force_trig2=i % 2 == 1
                        )
                else:
                    await trigger_once(s, force_trig1=True)

        await asyncio.gather(sweep(vco1, vco2), triggers())

    async def pick_me_up_subharmonicon(
        self,
        play: PlayAsyncFunction,
        raw: RawAsyncFunction,
        trig1: TrigAsyncFunction,
        trig2: TrigAsyncFunction,
    ) -> None:
        # VCO 1
        await raw(CC, 4, 64)
        await raw(CC, 36, 0)
        # VCO 2
        await raw(CC, 12, 64)
        await raw(CC, 44, 0)

        div = [8 * i + 5 for i in range(16)]
        div[0]  # C0
        div[1]  # C#0
        div[2]  # D0 20ct
        div[3]  # E 50ct
        div[4]  # F
        div[5]  # F# 35ct
        div[6]  # G#
        div[7]  # A# -15ct
        div[8]  # C1
        div[9]  # D1 20ct
        div[10]  # F1
        div[11]  # G#
        div[12]  # C2
        div[13]  # F2
        div[14]  # C3
        div[15]  # C4
        c = random.choice
        d = [div[4], div[8], div[10], div[12], div[13], div[14], div[15]]

        async def subharmonics() -> None:
            if random.random() > 0.5:
                await self.wait(12)
            else:
                await asyncio.gather(trig1(12), raw(CC, 103, c(d)), raw(CC, 104, c(d)))
            if random.random() > 0.5:
                await self.wait(12)
            else:
                await asyncio.gather(trig2(12), raw(CC, 105, c(d)), raw(CC, 106, c(d)))

        while True:
            await asyncio.gather(
                play(self.key[3] + 7, 6, 64, 1.0),
                subharmonics(),
            )


@click.command()
@click.option(
    "--config",
    help="Read configuration from this file",
    default=str(CURRENT_DIR / "aiotone-sticks.ini"),
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
    Skeleton based on aiotone.mothergen.

    You can customize the ports by creating a config file.  Use `--make-config` to
    output a new config to stdout.

    Then run `python -m aiotone.sticks --config=PATH_TO_YOUR_CONFIG_FILE`.
    """
    if make_config:
        with open(CURRENT_DIR / "aiotone-sticks.ini") as f:
            print(f.read())
        return

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(async_main(config))


async def async_main(config: str) -> None:
    queue: asyncio.Queue[MidiMessage] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    cfg = configparser.ConfigParser()
    cfg.read(config)

    inputs, outputs = resolve_ports(cfg)
    from_ableton = inputs[cfg["from-ableton"]["port-name"]]
    to_ableton = outputs[cfg["to-ableton"]["port-name"]]
    to_iridium = outputs[cfg["to-iridium"]["port-name"]]
    to_subharmonicon = outputs[cfg["to-subharmonicon"]["port-name"]]

    def midi_callback(msg, data=None):
        sent_time = time.time()
        midi_message, event_delta = msg
        try:
            loop.call_soon_threadsafe(
                queue.put_nowait, (midi_message, event_delta, sent_time)
            )
        except BaseException as be:
            click.secho(f"callback exc: {type(be)} {be}", fg="magenta", err=True)

    from_ableton.set_callback(midi_callback)

    performance = Performance(
        in_channel=cfg["from-ableton"].getint("channel", 1) - 1,
        iridium_port=to_iridium,
        iridium_channel=cfg["to-iridium"].getint("channel", 1) - 1,
        subh_port=to_subharmonicon,
        subh_channel=cfg["to-subharmonicon"].getint("channel", 1) - 1,
        to_ableton_port=to_ableton,
        to_ableton_channel_1=cfg["to-ableton"].getint("channel-1", 1) - 1,
        to_ableton_channel_2=cfg["to-ableton"].getint("channel-2", 1) - 1,
        start_stop=cfg["from-ableton"].getboolean("start-stop", False),
    )

    await performance.setup()

    try:
        await midi_consumer(queue, performance)
    except asyncio.CancelledError:
        from_ableton.cancel_callback()
        silence(to_ableton, stop=performance.start_stop)
        silence(to_iridium, stop=performance.start_stop)
        silence(to_subharmonicon, stop=performance.start_stop)


async def midi_consumer(
    queue: asyncio.Queue[MidiMessage], performance: Performance
) -> None:
    print("Waiting for MIDI messages...")
    system_realtime = {START, STOP, SONG_POSITION}
    notes = {NOTE_ON, NOTE_OFF}
    handled_types = system_realtime | notes | {CONTROL_CHANGE}
    in_ch = performance.in_channel
    while True:
        msg, delta, sent_time = await queue.get()
        latency = time.time() - sent_time
        t = msg[0]
        if t == CLOCK:
            await performance.clock()
            continue

        st = t & STRIP_CHANNEL
        if st == STRIP_CHANNEL:  # system realtime message didn't have a channel
            st = t
        elif t & GET_CHANNEL != in_ch:
            # click.secho(f"skipping {msg} not on channel {in_ch}: {t & GET_CHANNEL}")
            continue

        if __debug__:
            fg = "white"
            if t in system_realtime:
                fg = "red"
            elif t == CONTROL_CHANGE:
                fg = "green"
            elif st == POLY_AFTERTOUCH:
                fg = "cyan"
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
            await performance.note_off(msg[1])
        elif st == POLY_AFTERTOUCH:
            await performance.at(msg[1], msg[2])
        elif st == CONTROL_CHANGE:
            if msg[1] == MOD_WHEEL:
                await performance.mod_wheel(msg[2])
            elif msg[1] == FOOT_PEDAL or msg[1] == EXPRESSION_PEDAL:
                await performance.expression(msg[2])
            elif msg[1] == SUSTAIN_PEDAL:
                await performance.all(CC, SUSTAIN_PEDAL, msg[2])
            elif msg[1] == ALL_NOTES_OFF:
                await performance.all(CC, ALL_NOTES_OFF, msg[2])
            else:
                print(f"warning: unhandled CC {msg}", file=sys.stderr)
        elif st == PITCH_BEND:
            await performance.all(PITCH_BEND, msg[1], msg[2])
        else:
            if st not in handled_types:
                print(f"warning: unhandled event {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
