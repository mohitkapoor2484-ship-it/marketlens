from datetime import timedelta
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

from .domain import now_utc


@lru_cache(maxsize=2)
def calendar(market):
    return xcals.get_calendar("XASX" if market == "ASX" else "XNYS", start="2000-01-01", end="2035-12-31")


def sessions(market, start, end):
    return [x.date().isoformat() for x in calendar(market).sessions_in_range(start, end)]


def session_times(market, day):
    cal = calendar(market)
    if not cal.is_session(day):
        raise ValueError(f"{day} is not a {market} session")
    return cal.session_open(day).to_pydatetime(), cal.session_close(day).to_pydatetime()


def latest_closed(market, now=None, lag_minutes=0):
    now = now or now_utc()
    end = now.date()
    candidates = sessions(market, (end - timedelta(days=15)).isoformat(), end.isoformat())
    for day in reversed(candidates):
        if session_times(market, day)[1] + timedelta(minutes=lag_minutes) <= now:
            return day
    raise ValueError("No eligible recently closed session")


def elapsed_sessions(market, after, through):
    return sum(day > after for day in sessions(market, after, through))
