from __future__ import annotations

import asyncio
from typing import List

from attr import dataclass, Factory


class Countdown(asyncio.Future):
    def __init__(self, value: int, *, loop=None) -> None:
        super().__init__(loop=loop)
        self._value = value

    def tick(self) -> None:
        self._value -= 1
        if self._value == 0 and not self.done():
            self.set_result(None)


@dataclass
class Metronome:
    pulse_delta: float = 0.02  # 125 BPM (0.02 / 60 / 24 pulses per quarter note)
    position: int = 0  # pulses since START
    countdowns: List[Countdown] = Factory(list)
    countdowns_lock: asyncio.Lock = Factory(asyncio.Lock)

    async def wait(self, pulses: int) -> None:
        if pulses == 0:
            return

        countdown = Countdown(pulses)
        async with self.countdowns_lock:
            self.countdowns.append(countdown)
        await countdown

    async def reset(self) -> None:
        self.position = 0
        async with self.countdowns_lock:
            for countdown in self.countdowns:
                if not countdown.done():  # could have been cancelled by CTRL-C
                    countdown.cancel()
            self.countdowns = []

    async def tick(self) -> None:
        self.position += 1
        done_indexes: List[int] = []
        async with self.countdowns_lock:
            for index, countdown in enumerate(self.countdowns):
                countdown.tick()
                if countdown.done():
                    done_indexes.append(index)
            for index in reversed(done_indexes):
                del self.countdowns[index]
