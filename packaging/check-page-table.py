"""Offline checks for reading the default masjid's year from its page's own script.

Waterloo Masjid's calendar is out around each clock change, has Jumu'ah at 13:30
all year and Fajr five minutes early in winter. Its page has none of that, so the
clock asks the page's script instead. These pin down the reading, that a saved
table outranks the calendar, and what is left out when only the calendar remains.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-page-table.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta

from floating_clock import astro, ics, pagetable, prayer, webrender

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


# CI runs on UTC; the times below are on an Eastern clock after it went back.
astro.local_offset_hours = lambda when=None: -5.0

NOW = datetime(2026, 11, 1, 10, 0)


def script_answer(page, question):
    """What the masjid's script says: every month it is asked for, in its own writing."""
    months = json.loads(re.search(r"JSON\.stringify\((\[.*?\]\])\.flatMap", question).group(1))
    rows = []
    for year, month in months:
        day = date(year, month + 1, 1)
        while day.month == month + 1:
            dhuhr = "12:40, 1:55" if day.weekday() == 4 else "12:45"
            rows.append([day.year, day.month, day.day, "6:15", dhuhr, "3:45", "5:12", "7:30"])
            day += timedelta(days=1)
    asked.append(page)
    return json.dumps(rows)


asked: list[str] = []
calendar_reads: list[str] = []
CALENDAR = "\n".join(
    ["BEGIN:VCALENDAR", "VERSION:2.0"]
    + ["BEGIN:VEVENT\nUID:%s\nDTSTART:%s\nSUMMARY:%s Iqama\nEND:VEVENT" % (name, at, name)
       for name, at in (("Fajr", "20261106T063500"), ("Jumaa", "20261106T133000"),
                        ("Isha", "20261106T193000"))]
    + ["END:VCALENDAR", ""])


def fake_calendar(url):
    calendar_reads.append(url)
    return CALENDAR


ics.fetch = fake_calendar
webrender.available = lambda: True
webrender.answer_of = script_answer


def on(prayers, day):
    return {p.name: p.iqama.strftime("%H:%M") for p in prayers if p.iqama.day == day}


print("what the page's script says")
text = pagetable.fetch("https://example.org/", "A masjid", NOW.date(), script_answer)
days = json.loads(text)["days"]
check("a year ahead, across the new year", "2026-12-31" in days and "2027-11-30" in days,
      "%s to %s" % (min(days), max(days)))
check("twelve-hour times become the clock's",
      days["2026-11-02"] == {"Fajr": "06:15", "Dhuhr": "12:45", "Asr": "15:45",
                             "Maghrib": "17:12", "Isha": "19:30"}, str(days["2026-11-02"]))
check("a one o'clock Dhuhr is in the afternoon", pagetable._clock("Dhuhr", "1:45") == "13:45")
check("an eleven o'clock Dhuhr is in the morning", pagetable._clock("Dhuhr", "11:50") == "11:50")
check("a Fajr is never moved", pagetable._clock("Fajr", "6:40") == "06:40")
window = datetime(2026, 11, 6), datetime(2026, 11, 6, 23, 59)
friday = dict((name, when.strftime("%H:%M")) for name, when in pagetable.parse(text, *window))
check("a Friday's Dhuhr is its first Jumu'ah", friday.get("Jumuah") == "12:40" and "Dhuhr" not in friday,
      str(friday))
check("another day's window holds nothing of this one",
      not pagetable.parse(text, datetime(2025, 1, 1), datetime(2025, 1, 2)))

print()
print("a script that cannot be trusted is not read")
for name, answer in (("no answer at all", None),
                     ("a row short of a prayer", json.dumps([[2026, 11, 1, "6:15", "12:45"]])),
                     ("a time that is not one", json.dumps([[2026, 11, 1, "soon", "12:45", "3:45", "5:12", "7:30"]])),
                     ("nothing for today", json.dumps([[2026, 11, 2, "6:15", "12:45", "3:45", "5:12", "7:30"]]))):
    try:
        pagetable.fetch("https://example.org/", "A masjid", NOW.date(), lambda *_: answer)
        check(name, False, "no error")
    except pagetable.TableError:
        check(name, True)

print()
print("the default masjid")
folder = tempfile.mkdtemp(prefix="check-table-")
found, status = prayer.load(folder, "", now=NOW, force=True)
check("is read from its page's table", asked[-1:] == [prayer.WATERLOO_MASJID_PAGE] and not calendar_reads)
check("this Friday's Jumu'ah is the page's, not the calendar's", on(found, 6).get("Jumuah") == "12:40",
      str(on(found, 6)))
check("the days after the clock change are all there", len(on(found, 2)) == 5 and len(on(found, 3)) == 5)
check("under its own name, with no complaint", "Waterloo Masjid" in status and "wrong" not in status, status)
check("and the reading is saved", prayer._cached_source(prayer._read(prayer.cache_path(folder))) == pagetable.SOURCE)

before = len(asked)
found, _ = prayer.load(folder, "", now=NOW)
check("a later load uses the saved reading", len(asked) == before and on(found, 6).get("Jumuah") == "12:40")

print()
print("the site is down, with a table saved")


def down(page, question):
    raise webrender.RenderError("the page could not be reached")


webrender.answer_of = down
found, status = prayer.load(folder, "", now=NOW, force=True)
check("the saved table is kept", on(found, 6).get("Jumuah") == "12:40", str(on(found, 6)))
check("the calendar is not asked", not calendar_reads, str(calendar_reads))
check("and the status says it could not reach it", prayer.UNREACHED in status, status)

class Today(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


prayer.datetime = Today
loader = prayer.Loader({}, folder, lambda fn: fn(), lambda *_: None)
loader.loading = True
loader._done(found, status)
check("which backs off, so a browser is not started every quarter hour", loader.failures == 1)
loader.loading = True
loader._done(found, "Waterloo Masjid")
check("until a read works", loader.failures == 0)
prayer.datetime = datetime

print()
print("no table to be had")
fresh = tempfile.mkdtemp(prefix="check-table-")
found, status = prayer.load(fresh, "", now=NOW, force=True)
check("a site that is down falls back to the calendar", len(calendar_reads) == 1)
check("whose Jumu'ah is left out rather than shown wrong", "Jumuah" not in on(found, 6), str(on(found, 6)))
check("and the other prayers kept", on(found, 6).get("Isha") == "19:30", str(on(found, 6)))
check("the status says so", "Jumu'ah" in status, status)
webrender.available = lambda: False
fresh = tempfile.mkdtemp(prefix="check-table-")
found, _ = prayer.load(fresh, "", now=NOW, force=True)
check("so does a machine with no browser", len(calendar_reads) == 2 and on(found, 6).get("Isha") == "19:30")
webrender.available = lambda: True
webrender.answer_of = script_answer

print()
print("what must not change")
before = len(asked)
pasted = tempfile.mkdtemp(prefix="check-table-")
found, _ = prayer.load(pasted, "https://example.org/iqamah.ics", now=NOW, force=True,
                       fetcher=lambda _: CALENDAR)
check("a pasted calendar is read as it is, Jumu'ah and all", on(found, 6).get("Jumuah") == "13:30"
      and len(asked) == before, str(on(found, 6)))

print()
print("the sun still has the last word")
astro.local_offset_hours = lambda when=None: -4.0     # an hour out from the table's clock
folder = tempfile.mkdtemp(prefix="check-table-")
found, status = prayer.load(folder, "", now=NOW, force=True)
check("a table the sun rules out is not shown", "wrong" in status and not on(found, 2),
      "%s %s" % (status, on(found, 2)))
astro.local_offset_hours = lambda when=None: -5.0

print()
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all good")
