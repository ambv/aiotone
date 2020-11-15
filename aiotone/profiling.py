from __future__ import annotations
from typing import *

from contextlib import contextmanager, nullcontext
import cProfile
import pstats


SortKey = pstats.SortKey  # type: ignore


@contextmanager
def profile() -> Iterator:
    try:
        with cProfile.Profile() as pr:
            yield
    finally:
        st = pstats.Stats(pr).sort_stats(SortKey.CALLS)
        st.print_stats()
        st.sort_stats(SortKey.CUMULATIVE)
        st.print_stats()


def maybe(toggle: bool) -> ContextManager:
    if toggle:
        return profile()

    return nullcontext()