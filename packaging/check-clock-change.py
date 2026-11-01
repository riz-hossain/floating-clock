"""Offline checks for a masjid's calendar that has the clock change on the wrong date.

Waterloo Masjid's calendar kept the old times for three days after the clocks
changed, in March and again in November, so the azan was called an hour out
while its own page was right. These checks pin down that the sun catches such
a day, that the day goes whole, and that today then comes from the page.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-clock-change.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime

from floating_clock import astro, prayer, scrape

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


# CI runs on UTC; the times below are on an Eastern clock after it went back.
astro.local_offset_hours = lambda when=None: -5.0

WHERE = prayer.WATERLOO_MASJID_POSITION
NOW = datetime(2026, 11, 1, 10, 0)
LATE = ("0645", "1345", "1700", "1818", "2000")    # summer's table: an hour out
RIGHT = ("0615", "1245", "1545", "1714", "1930")
NAMES = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


def feed(days: dict) -> str:
    events = []
    for day, times in days.items():
        for name, at in zip(NAMES, times):
            events.append("BEGIN:VEVENT\nUID:%s-%s\nDTSTART:%sT%s00\nSUMMARY:%s Iqama\nEND:VEVENT"
                          % (day, name, day, at, name))
    return "BEGIN:VCALENDAR\nVERSION:2.0\n%s\nEND:VCALENDAR\n" % "\n".join(events)


SLIPPED = feed({"20261101": LATE, "20261102": LATE, "20261103": LATE,
                "20261104": RIGHT, "20261105": RIGHT})
CLEAN = feed({"20261101": ("0615", "1245", "1545", "1718", "1930"), "20261102": RIGHT})

reads: list[str] = []
page_answer = {"sun_checked": True, "fails": False}


def fake_page(page, today=None, opener=None, deadline=0, where=None, render=None):
    reads.append(page)
    if page_answer["fails"]:
        raise scrape.ScrapeError("no times on it")
    return json.dumps({
        "source": "scrape", "name": "A masjid", "page": page, "asof": today.isoformat(),
        "how": "guessed", "quality": 1, "computed": [],
        "sun_checked": page_answer["sun_checked"], "jumuah": False,
        "iqamah": {"Fajr": "06:15", "Dhuhr": "12:45", "Asr": "15:45",
                   "Maghrib": "17:18", "Isha": "19:30"},
    })


scrape.fetch = fake_page
prayer._renderer = lambda: None


def run(text, url=prayer.WATERLOO_MASJID_ICS, where=WHERE, force=False, folder=None, now=NOW):
    folder = folder or tempfile.mkdtemp(prefix="check-clock-")
    return prayer.load(folder, url, now=now, force=force, fetcher=lambda _: text, where=where)


def on(prayers, day):
    return {p.name: p.iqama.strftime("%H:%M") for p in prayers if p.iqama.day == day}


print("a calendar that missed the clock change")
found, status = run(SLIPPED)
check("today's times are the page's", on(found, 1) == {
    "Fajr": "06:15", "Dhuhr": "12:45", "Asr": "15:45", "Maghrib": "17:18", "Isha": "19:30"},
    "got %r" % on(found, 1))
check("a slipped day goes whole, Fajr with Maghrib", not on(found, 2) and not on(found, 3),
      "got %r %r" % (on(found, 2), on(found, 3)))
check("the days after it are kept", on(found, 4).get("Maghrib") == "17:14", "got %r" % on(found, 4))
check("in order", [p.iqama for p in found] == sorted(p.iqama for p in found))
check("the default calendar's page is what is read", reads[-1] == prayer.WATERLOO_MASJID_PAGE)
check("the status says so", "3 days from Sun 01 Nov" in status and "web page" in status, status)

print()
print("the page is read once a day")
folder = tempfile.mkdtemp(prefix="check-clock-")
run(SLIPPED, folder=folder)
before = len(reads)
found, _ = run(SLIPPED, folder=folder)
check("a later load uses the saved reading", len(reads) == before and on(found, 1).get("Asr") == "15:45")
found, _ = run(SLIPPED, folder=folder, force=True)
check("the nightly check or Refresh reads it again", len(reads) == before + 1)
next_day = datetime(2026, 11, 2, 10, 0)
found, _ = run(SLIPPED, folder=folder, now=next_day)
check("and so does the next day", len(reads) == before + 2)

print()
print("refuses rather than guesses")
page_answer["fails"] = True
found, status = run(SLIPPED)
check("a page that will not read leaves today empty", not on(found, 1), "got %r" % on(found, 1))
check("and the status says there are none", "no times for today" in status, status)
check("later good days are still kept", on(found, 4).get("Fajr") == "06:15")
page_answer["fails"] = False
page_answer["sun_checked"] = False
found, _ = run(SLIPPED)
check("a page the sun has not checked is not used", not on(found, 1), "got %r" % on(found, 1))
page_answer["sun_checked"] = True

print()
print("what must not change")
before = len(reads)
found, status = run(CLEAN)
check("a good calendar is read as it is", on(found, 1).get("Maghrib") == "17:18"
      and on(found, 2).get("Maghrib") == "17:14")
check("without touching the page", len(reads) == before)
check("and without a complaint", "wrong" not in status, status)
found, _ = run(SLIPPED, url="https://example.org/iqamah.ics", where=None)
check("no position, no sun to check by", on(found, 1).get("Maghrib") == "18:18")
found, status = run(SLIPPED, where=None)
check("the default calendar knows its own position", on(found, 1).get("Maghrib") == "17:18", status)
before = len(reads)
found, status = run(SLIPPED, url="https://example.org/iqamah.ics")
check("a pasted calendar has no page to read", len(reads) == before and not on(found, 1))

print()
print("the other end of the year")
astro.local_offset_hours = lambda when=None: -4.0
march = feed({"20260308": ("0635", "1245", "1645", "1928", "2100")})
kept, refused = prayer.against_sun(
    prayer.parse_ics(march, datetime(2026, 3, 8), datetime(2026, 3, 9)), WHERE)
check("a Dhuhr that forgot the clocks went forward is refused",
      not kept and "Dhuhr" in str(refused), "got %r" % (refused,))

print()
print("the loader keeps asking while today has no times")
class Today(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 1, 10, 0)


prayer.datetime = Today
loader = prayer.Loader({}, tempfile.mkdtemp(prefix="check-clock-"), lambda fn: fn(), lambda *_: None)
loader.loading = True
tomorrow = [prayer.Prayer("Fajr", datetime(2026, 11, 2, 6, 15))]
loader._done(tomorrow, "")
check("times for other days alone count as a failure", loader.failures == 1)
loader.loading = True
loader._done([prayer.Prayer("Fajr", datetime(2026, 11, 1, 6, 15))], "")
check("times for today end it", loader.failures == 0)
prayer.datetime = datetime

print()
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all good")
