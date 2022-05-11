from __future__ import annotations
from typing import *

import asyncio
from dataclasses import dataclass, field
import inspect


@dataclass
class SlewGenerator:
    """A lag generator, interpolating values.

    Creating it automatically instantiates a task on the current running event loop
    which calls `callback` many times (`steps`) interpolating each value change.
    You can request a value change by calling `update()`.

    By design, if multiple calls to `update()` were made while the slew was busy
    interpolating some previous value, those calls are ignored save for the last one.
    """

    name: str
    callback: Callable[[float], None] | Callable[[float], Coroutine[None, None, None]]
    value: float = 0.0
    steps: int = 128  # number of steps between each value received
    rate: float = 2  # steps/ms
    _new_value: Optional[float] = None
    _task: asyncio.Task = field(init=False)
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self) -> None:
        self._task = asyncio.create_task(
            self.task(), name=f"slew generator for {self.name}"
        )
        self._lock = asyncio.Lock()

    def __del__(self) -> None:
        try:
            self._task.cancel()
        except BaseException:
            pass

    async def update(self, value: float) -> None:
        async with self._lock:
            self._new_value = value

    async def task(self) -> None:
        steps = self.steps
        step_sleep = 1 / (1000 * self.rate)
        current_value = self.value
        while True:
            while self._new_value is None:
                await asyncio.sleep(step_sleep)
            async with self._lock:
                new_value = self._new_value
                self._new_value = None
            step = (new_value - current_value) / steps
            for _i in range(steps):
                current_value += step
                coro = self.callback(current_value)
                if inspect.iscoroutine(coro):
                    await coro
                await asyncio.sleep(step_sleep)
                if self._new_value is not None:
                    break
            self.value = current_value
