from __future__ import annotations
from typing import *

import asyncio
from dataclasses import dataclass, field
import inspect
import time


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
    rate: float = 1  # steps/ms
    _new_value: float | tuple[float, float] | None = None
    _task: asyncio.Task = field(init=False)
    _lock: asyncio.Lock = field(init=False)
    _requests: int = field(init=False)
    _last_rps_check: float = field(init=False)

    def __post_init__(self) -> None:
        self._task = asyncio.create_task(
            self.task(), name=f"slew generator for {self.name}"
        )
        self._lock = asyncio.Lock()
        self._requests = 0
        self._last_rps_check = time.monotonic()

    def __del__(self) -> None:
        try:
            self._task.cancel()
        except BaseException:
            pass

    async def update(self, value: float | tuple[float, float]) -> None:
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
                if isinstance(self._new_value, tuple):
                    current_value, new_value = self._new_value
                else:
                    new_value = self._new_value
                self._new_value = None
            step = (new_value - current_value) / steps
            for _i in range(steps):
                current_value += step
                self._requests += 1
                if self._requests % 100 == 0:
                    now = time.monotonic()
                    diff = now - self._last_rps_check
                    if diff > 1:
                        print(f"[{self.name}] RPS =", round(self._requests / diff))
                        self._last_rps_check = now
                        self._requests = 0
                coro = self.callback(current_value)
                if inspect.iscoroutine(coro):
                    await coro
                if self._new_value is not None:
                    break
                await asyncio.sleep(step_sleep)
            self.value = current_value
