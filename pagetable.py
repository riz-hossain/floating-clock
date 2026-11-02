"""A year of iqamas from the script that draws a masjid's own page.

Waterloo Masjid's page works its times out in the browser, from a table and its
own rules: the clock change, the week after it, Jumu'ah, Ramadan. Its script
will give any day it is asked for, so it is asked, in a browser. Copying the
rules here would go quietly wrong the first time the masjid changed one.

The reading is saved like any other, so a laptop that is offline still has the
year, and a read that fails leaves the saved one in place.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

SOURCE = "table"
# This month through the same month next year, so there is always a year ahead.
MONTHS = 13
NAMES = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
QUESTION = ("JSON.stringify(%s.flatMap(([y, m]) => PrayerTimes.getMonthlyPrayerTimes(y, m))"
            ".map(d => [d.year, d.month + 1, d.date, d.fajr_iqamah, d.dhuhr_iqamah,"
            " d.asr_iqamah, d.magrib_iqamah, d.isha_iqamah]))")


class TableError(Exception):
    """The page's script did not give a timetable the clock can trust."""


def _months(today: date) -> list:
    """[year, zero-based month] pairs: the script does not roll a month over a year."""
    first = today.year * 12 + today.month - 1
    return [[(first + i) // 12, (first + i) % 12] for i in range(MONTHS)]


def _clock(name: str, written: str) -> str:
    """"1:45" under Dhuhr -> "13:45". The script writes twelve-hour times with no am or pm.

    A Friday's Dhuhr holds its Jumu'ahs, "12:40, 1:55"; the first is the one kept,
    as it is when a page is read.
    """
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(written).split(",")[0].strip())
    if not match:
        raise TableError("%s is written %r" % (name, written))
    hour = int(match.group(1))
    if name == "Dhuhr" and hour < 11 or name in ("Asr", "Maghrib", "Isha") and hour < 12:
        hour += 12
    return "%02d:%s" % (hour, match.group(2))


def fetch(page: str, name: str, today: date, ask) -> str:
    """The cache text for `page`. `ask(page, question)` answers in its browser."""
    answer = ask(page, QUESTION % json.dumps(_months(today)))
    if not isinstance(answer, str):
        raise TableError("the page's script did not answer")
    days = {}
    try:
        for row in json.loads(answer):
            times = zip(NAMES, row[3:], strict=True)
            days[date(row[0], row[1], row[2]).isoformat()] = {n: _clock(n, at) for n, at in times}
    except (ValueError, TypeError, IndexError) as exc:
        raise TableError("its timetable has changed shape (%s)" % exc) from None
    if today.isoformat() not in days:
        raise TableError("its timetable has no times for today")
    return json.dumps({"source": SOURCE, "name": name, "page": page,
                       "asof": today.isoformat(), "days": days})


def parse(text: str, window_start: datetime, window_end: datetime) -> list:
    """(name, iqama) inside the window. A Friday's Dhuhr is its Jumu'ah."""
    try:
        days = json.loads(text)["days"]
    except (ValueError, KeyError, TypeError):
        return []
    found = []
    day = window_start.date()
    while day <= window_end.date():
        for name, at in (days.get(day.isoformat()) or {}).items():
            when = datetime.combine(day, datetime.strptime(at, "%H:%M").time())
            if window_start <= when <= window_end:
                found.append(("Jumuah" if name == "Dhuhr" and day.weekday() == 4 else name, when))
        day += timedelta(days=1)
    found.sort(key=lambda item: (item[1], item[0]))
    return found


def source_name(text: str) -> str:
    try:
        return str(json.loads(text).get("name") or "")
    except ValueError:
        return ""
