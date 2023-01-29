from __future__ import annotations
from typing import *

from contextlib import contextmanager, nullcontext
import cProfile
from dataclasses import dataclass
import io
import pstats


Profile = cProfile.Profile
SortKey = pstats.SortKey  # type: ignore


def f8(x):
    return "%10.6f" % x


pstats.f8 = f8


@contextmanager
def profile() -> Iterator:
    try:
        with Profile() as pr:
            yield pr
    finally:
        st = pstats.Stats(pr).sort_stats(SortKey.CALLS)
        st.print_stats()
        st.sort_stats(SortKey.CUMULATIVE)
        st.print_stats()


def maybe(toggle: bool) -> ContextManager:
    if toggle:
        return profile()

    return nullcontext()


def stats_from_profile(pr: Profile, sort_by: SortKey = SortKey.TIME) -> pstats.Stats:
    return pstats.Stats(pr).sort_stats(sort_by)


def stats_as_str(ps: pstats.Stats) -> str:
    strio = io.StringIO()
    old_stream = ps.stream
    try:
        ps.stream = strio
        ps.print_stats()
        return strio.getvalue()
    finally:
        ps.stream = old_stream


@dataclass
class Result:
    pstats: pstats.Stats | None = None


@contextmanager
def get_stats() -> Iterator:
    result = Result()
    try:
        with cProfile.Profile() as pr:
            yield result
    finally:
        result.pstats = stats_from_profile(pr)
