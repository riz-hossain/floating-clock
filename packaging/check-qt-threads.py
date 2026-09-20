"""Do the Qt host's worker threads actually reach the UI?

The prayer loader, the masjid search and the check that runs before a masjid is
saved all work on a plain Python thread and have to hand their answer back to
the UI thread. QTimer.singleShot(0, fn) looks like the way to do that and is
not: a timer needs an event loop in the thread that starts it, a plain thread
has none, and the call is silently never made. On macOS and Linux that left
every clock with no prayer times and a picker stuck on "Searching..." -- and
nothing here caught it, because every earlier check called the display methods
directly and never let the event loop run.

Here the event loop runs. What the workers fetch is replaced with canned
answers, so this needs no network.

    PYTHONPATH=<folder holding floating_clock> QT_QPA_PLATFORM=offscreen \\
        python packaging/check-qt-threads.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from PySide6 import QtWidgets  # noqa: E402

from floating_clock import masjids, prayer  # noqa: E402
from floating_clock.qt.clock import QtClock  # noqa: E402
from floating_clock.qt.settings import MasjidPicker, SettingsDialog  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


app = QtWidgets.QApplication([])


def spin(seconds: float, until) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline and not until():
        app.processEvents()
        time.sleep(0.02)
    return bool(until())


soon = datetime.now() + timedelta(hours=1)
FOUND = ([prayer.Prayer("Fajr", soon)], "Test Masjid · updated just now")
prayer.load = lambda base, url="", now=None, force=False, fetcher=None, **kw: FOUND
masjids.search = lambda text="", *a, **k: ([{
    "name": "Test Masjid", "city": "Waterloo, Ontario", "slug": "test-masjid",
    "source": "mawaqit", "iqama": True}], "")

clock = QtClock(app)
clock.s["prayer_enabled"] = True
clock.s["prayer_ics_url"] = ""

print("the prayer loader")
clock.prayer_loader.refresh(force=True)
check("its worker's result reaches the UI thread",
      spin(10, lambda: bool(clock.prayer_loader.prayers)), "nothing delivered")
check("and it is no longer marked busy", not clock.prayer_loader.loading)

dialog = SettingsDialog(clock)
clock.settings_dialog = dialog

print("the masjid picker")
picker = MasjidPicker(dialog)
picker.box.setText("waterloo")
picker.look()
check("a search fills the list", spin(10, lambda: picker.listing.count() > 0),
      "still %r" % picker.status.text())
check("and stops saying it is searching", not picker.busy)

picker.listing.setCurrentRow(0)
before = dialog.prayer_url.text()
picker.use()
check("a masjid that can be read is shown before it is saved",
      spin(10, lambda: "Check they match" in picker.status.text()), "status %r" % picker.status.text())
check("and nothing is saved yet", dialog.prayer_url.text() == before)
picker.use()
check("a second press keeps it and the dialog closes",
      spin(10, lambda: picker.result() == picker.DialogCode.Accepted),
      "status %r" % picker.status.text())
check("its address is now the one in use",
      dialog.prayer_url.text().endswith("/en/test-masjid")
      and clock.s["prayer_ics_url"].endswith("/en/test-masjid"),
      "box %r" % dialog.prayer_url.text())

print("a masjid that cannot be read")
prayer.load = lambda base, url="", now=None, force=False, fetcher=None, **kw: (
    [], "Could not read the prayer times: that site does not publish a timetable.")
kept = dialog.prayer_url.text()
again = MasjidPicker(dialog)
again.box.setText("waterloo")
again.look()
spin(10, lambda: again.listing.count() > 0)
again.listing.setCurrentRow(0)
again.use()
spin(10, lambda: not again.busy)
check("is refused, and the dialog stays open",
      again.result() != again.DialogCode.Accepted)
check("says nothing was changed", "Nothing was changed" in again.status.text(),
      again.status.text())
check("and leaves the address that works exactly as it was",
      dialog.prayer_url.text() == kept and clock.s["prayer_ics_url"] == kept,
      "box %r" % dialog.prayer_url.text())

PROPOSAL = {
    "kind": "read", "address": "http://erin.example", "name": "Erin Centre", "how": "guessed",
    "source": "scrape", "status": "read", "asked": "", "km": None, "latitude": 43.77, "longitude": -80.06,
    "times": [("Fajr", "05:30"), ("Dhuhr", "14:00"), ("Asr", "18:00"), ("Maghrib", "19:22"), ("Isha", "20:00")]}


def choose(proposal):
    masjids.propose = lambda entry, rows, progress=None, **k: dict(proposal)
    picker = MasjidPicker(dialog)
    picker.box.setText("waterloo")
    picker.look()
    spin(10, lambda: picker.listing.count() > 0)
    picker.listing.setCurrentRow(0)
    picker.use()
    return picker


print("a reading taken off a web page is shown before it is kept")
prayer.load = lambda base, url="", now=None, force=False, fetcher=None, **kw: FOUND
kept = clock.s["prayer_ics_url"]
picker = choose(PROPOSAL)
check("the five times read are shown", spin(10, lambda: "Read from Erin Centre" in picker.status.text()),
      picker.status.text())
check("and nothing is saved yet", clock.s["prayer_ics_url"] == kept and picker.result() != picker.DialogCode.Accepted)
picker.use()
check("a second press keeps them", spin(10, lambda: picker.result() == picker.DialogCode.Accepted))
check("the address, where the masjid is, and its name are saved",
      clock.s["prayer_ics_url"] == "http://erin.example" and clock.s["prayer_lat"] == 43.77
      and clock.s["prayer_masjid_name"] == "Erin Centre" and clock.s["prayer_proxy_for"] == "")

print("a neighbour's times are labelled as one's")
picker = choose(dict(PROPOSAL, kind="proxy", name="Nearer Masjid", asked="Erin Centre", km=1.1,
                     address="http://nearer.example", latitude=43.78))
check("the neighbour is named, and the masjid it stands in for",
      spin(10, lambda: "Nearer Masjid's times, not Erin Centre's" in picker.status.text()), picker.status.text())
check("and nothing is saved yet", clock.s["prayer_ics_url"] == "http://erin.example")
picker.use()
check("a second press keeps them, and the settings say whose they really are",
      spin(10, lambda: picker.result() == picker.DialogCode.Accepted)
      and clock.s["prayer_ics_url"] == "http://nearer.example" and clock.s["prayer_proxy_for"] == "Erin Centre")

print("an address typed by hand is not a place the picker knew")
dialog.prayer_url.setText("http://elsewhere.example")
dialog._set_prayer_url()
check("forgets where the masjid was, and that it was borrowed",
      clock.s["prayer_lat"] is None and clock.s["prayer_proxy_for"] == "" and clock.s["prayer_masjid_name"] == "")

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("every worker thread reaches the UI")
