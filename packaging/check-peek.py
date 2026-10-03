"""Offline checks that a peek keeps to the same minutes of every hour.

A peek comes at the wall clock's multiples of its interval, counted from
midnight. Every 30 minutes that is :00 and :30, all day; every 28 it is :56,
:24, :52 and on round the hour, which reads as random. One notch of a slider
once moved 30 to 28 and it stayed that way for weeks, so the settings now only
hold intervals that fit the hour, and a saved one that does not is put right.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-peek.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import peek, settings as cfg  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


def peeks(interval: int) -> list[datetime]:
    """Every peek in one day, by the clock's own scheduling."""
    now, end = datetime(2026, 10, 1, 23, 59), datetime(2026, 10, 3)
    found = []
    while True:
        now += timedelta(seconds=peek.seconds_until_boundary(now, interval))
        if now >= end:
            return found
        found.append(now)
        now += timedelta(seconds=1)


def minutes_by_hour(times) -> set:
    """The minutes past the hour a peek came at, one set for each hour that had any."""
    hours: dict = {}
    for when in times:
        hours.setdefault(when.hour, set()).add(when.minute)
    return {tuple(sorted(found)) for found in hours.values()}


print("the intervals the settings hold")
for interval in cfg.PEEK_INTERVALS:
    seen = minutes_by_hour(peeks(interval))
    check("every %d min comes at the same minutes of every hour it comes in" % interval,
          len(seen) == 1, str(sorted(seen)[:3]))
check("every 30 min is :00 and :30", minutes_by_hour(peeks(30)) == {(0, 30)})
check("but every 28 min wanders round the hour, which is why it is not one of them",
      len(minutes_by_hour(peeks(28))) > 1 and 28 not in cfg.PEEK_INTERVALS)

print("an interval that does not fit")
for given, wanted in ((28, 30), (29, 30), (31, 30), (25, 30), (30, 30), (45, 60), (8, 10),
                      (1, 1), (0, 1), (90, 120), (500, 240), ("28", 30), (27.6, 30)):
    got = cfg.peek_interval(given)
    check("%r becomes %d" % (given, wanted), got == wanted, str(got))

data = cfg.sanitise(dict(cfg.DEFAULTS, peek_interval_minutes=28))
check("a saved 28 is put back to 30 when the settings are read", data["peek_interval_minutes"] == 30,
      str(data["peek_interval_minutes"]))
check("as a whole number, the way the rest of the clock uses it",
      isinstance(data["peek_interval_minutes"], int))
data = cfg.sanitise(dict(cfg.DEFAULTS, peek_interval_minutes=15))
check("and one that fits is left as it is", data["peek_interval_minutes"] == 15)
data = cfg.sanitise(dict(cfg.DEFAULTS, peek_interval_minutes="often"))
check("nonsense falls back to the default", data["peek_interval_minutes"] == cfg.DEFAULTS["peek_interval_minutes"])
check("and the default is one of them", cfg.DEFAULTS["peek_interval_minutes"] in cfg.PEEK_INTERVALS)

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all peek checks passed")
