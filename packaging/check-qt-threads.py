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

The calendar dialogs hand back failures the same way, and a lambda that names
the exception it caught raises NameError when it finally runs -- Python deletes
`exc` when its except block ends -- which is only logged. The last checks make
each of those workers fail and read what the dialog says.

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

from PySide6 import QtGui, QtWidgets  # noqa: E402

from floating_clock import caldav, google_oauth, ics, masjids, prayer, providers  # noqa: E402
from floating_clock.qt.clock import QtClock  # noqa: E402
from floating_clock.qt.settings import AddCalendarDialog, MasjidPicker, SettingsDialog  # noqa: E402

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


real_propose = masjids.propose
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

print("the timeline of a check")
import threading  # noqa: E402

from floating_clock import timeline  # noqa: E402

masjids.propose = real_propose                      # the real one, which reports each step


def five():
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return [prayer.Prayer(n, day + timedelta(hours=h, minutes=m))
            for n, (h, m) in zip(prayer.DAILY, ((6, 15), (13, 45), (17, 45), (19, 28), (21, 0)))]


gate = threading.Event()
entered = threading.Event()
finished = threading.Event()


def slow_inspect(address, where=None):
    """A read that takes as long as it is let to, and hears Stop."""
    line = timeline.current()
    try:
        with line.step("ask") as step:
            entered.set()
            while not gate.is_set():
                time.sleep(0.02)
                line.check()
            step.ok("received its timetable")
        prayers = five()
        return {"prayers": prayers, "status": "ok", "source": "mawaqit", "how": "", "exact": True,
                "times": masjids._day_times(prayers)}
    finally:
        finished.set()


def begin():
    gate.clear()
    entered.clear()
    finished.clear()
    picker = MasjidPicker(dialog)
    picker.box.setText("waterloo")
    picker.look()
    spin(10, lambda: picker.listing.count() > 0)
    picker.listing.setCurrentRow(0)
    picker.use()
    return picker


real_inspect = masjids.inspect
masjids.inspect = slow_inspect
kept = clock.s["prayer_ics_url"]
picker = begin()
check("choosing a masjid swaps the list for a timeline of what is being done",
      picker.pages.currentWidget() is picker.checking)
check("with Stop to give up, and the button that would start another check out of the way",
      not picker.stop_button.isHidden() and picker.back_button.isHidden() and not picker.use_button.isEnabled())
check("the worker gets as far as its first step", spin(5, entered.is_set))
spin(0.4, lambda: False)
snap = picker.trail.snapshot()
check("the timeline is running and says what", snap["busy"] and any(r["state"] == "running" for r in snap["rows"]), str(snap["rows"]))
check("and the window is drawing it, with a spinner on what is running",
      any(i["op"] == "icon" and i["state"] == "running" for i in picker.checking.canvas.drawn["items"]))
check("and a bar that has begun to move", any(i["op"] == "bar" and i["fraction"] > 0 for i in picker.checking.canvas.drawn["items"]))
check("the status line says that it takes a while and that Stop gives up", "Stop" in picker.status.text(), picker.status.text())
gate.set()
check("when it is done, what it found is shown as before",
      spin(10, lambda: "Check they match" in picker.status.text()), picker.status.text())
check("Stop goes, and Back to results comes, and the button works again",
      picker.stop_button.isHidden() and not picker.back_button.isHidden() and picker.use_button.isEnabled())
check("the timeline says it found something", picker.trail.snapshot()["outcome"] == "found")
check("and the window stops turning its spinner", not picker.checking.timer.isActive())
check("and nothing has been saved yet", clock.s["prayer_ics_url"] == kept)
picker.back_to_list()
check("Back to results shows the list again", picker.pages.currentWidget() is picker.listing and picker.back_button.isHidden())
picker.look()                    # a fresh search clears the pending proposal, so that Use starts a check again
spin(10, lambda: not picker.busy and picker.listing.count() > 0)
picker.listing.setCurrentRow(0)
picker.use()
spin(10, lambda: "Check they match" in picker.status.text())
check("with a finished check on show", picker.pages.currentWidget() is picker.checking)
picker.look()
check("a new search brings the list back, and takes Back to results away",
      picker.pages.currentWidget() is picker.listing and picker.back_button.isHidden(), picker.status.text())
spin(10, lambda: not picker.busy)

print("Stop")
picker = begin()
check("a check is under way", spin(5, entered.is_set))
picker.stop()
check("Stop is heard by the worker in the middle of a step", spin(10, finished.is_set))
check("and reported: stopped, and nothing changed",
      spin(10, lambda: "Stopped" in picker.status.text() and "Nothing was changed" in picker.status.text()), picker.status.text())
check("the timeline says it was stopped", picker.trail.snapshot()["outcome"] == "stopped", str(picker.trail.snapshot()["outcome"]))
check("what was running is not left spinning", all(r["state"] != "running" for r in picker.trail.snapshot()["rows"]))
check("nothing was saved", clock.s["prayer_ics_url"] == kept and picker.result() != picker.DialogCode.Accepted)
check("Back to results is offered, and the dialog stays open", not picker.back_button.isHidden() and picker.use_button.isEnabled())

print("closing the window in the middle of a check")
picker = begin()
check("a check is under way", spin(5, entered.is_set))
picker.reject()
check("closing the window stops it, so it does not go on opening pages for nobody", spin(10, finished.is_set))
check("and the timeline says so", spin(5, lambda: picker.trail.snapshot()["outcome"] == "stopped"), str(picker.trail.snapshot()["outcome"]))
masjids.inspect = real_inspect

print("a check that is not the real one")
masjids.propose = lambda entry, rows, progress=None, **k: {"kind": "none", "status": "Erin Centre: nothing readable."}
picker = MasjidPicker(dialog)
picker.box.setText("waterloo")
picker.look()
spin(10, lambda: picker.listing.count() > 0)
picker.listing.setCurrentRow(0)
picker.use()
check("a worker that never reports still leaves a timeline that ends", spin(10, lambda: not picker.busy)
      and picker.trail.snapshot()["outcome"] == "none" and not picker.trail.snapshot()["busy"])
masjids.propose = real_propose

print("the picture, at other sizes")
from floating_clock import palette as pal  # noqa: E402
from floating_clock.qt.timelinewidget import TimelineWidget  # noqa: E402

wide = timeline.Timeline()
wide.plan("Test Masjid", [timeline.Section("site", "The masjid's own website", "example.org", timeline.WEBSITE)])
wide.section("site")
wide.start("pages", "reading /prayer-times, /salah-timings and 2 more")
p = pal.resolve("dark", (127, 40, 255))
heights = {}
for width in (260, 420, 700):
    view = TimelineWidget(p)
    view.resize(width, 300)
    view.watch(wide)
    app.processEvents()
    view.tick()
    heights[width] = view.canvas.drawn["height"]
    view.timer.stop()
    # an independent ruler: the layout's own measure is what is being checked, so it cannot judge itself
    over = [i for i in view.canvas.drawn["items"] if i["op"] == "text" and i["anchor"] == "w"
            and i["x"] + QtGui.QFontMetrics(view.font_for(i["bold"], i["small"])).horizontalAdvance(i["text"]) > width]
    check("at %d pixels nothing runs off the edge, measured with the real fonts" % width, not over, str(over[:1]))
check("a narrower window is taller, not cut off", heights[260] > heights[700], str(heights))
for mode in ("dark", "light"):
    view = TimelineWidget(pal.resolve(mode, (127, 40, 255)))
    view.resize(500, 300)
    view.watch(wide)
    app.processEvents()
    view.timer.stop()
    image = view.grab().toImage()
    check("it paints in %s mode, and the picture is not blank" % mode,
          image.width() > 0 and len({image.pixel(x, y) for x in range(0, image.width(), 9) for y in range(0, image.height(), 9)}) > 6)

print("a calendar that cannot be added says why")


def failing(error, message):
    """A stand-in for a call to a server that fails the way the real one does."""
    def fake(*args, **kwargs):
        raise error(message)
    return fake


def adding(kind: str, email: str) -> AddCalendarDialog:
    """The add-a-calendar dialog, on the step that a provider of this kind leads to."""
    page = AddCalendarDialog(dialog)
    page.detected(providers.Detection(email, providers.domain_of(email), providers.PROVIDERS[kind]))
    return page


def press(page, caption: str) -> None:
    buttons = [b for b in page.findChildren(QtWidgets.QPushButton) if b.text() == caption]
    assert len(buttons) == 1, "%d buttons say %r" % (len(buttons), caption)
    buttons[0].click()


# Each reason is its own, so that seeing it proves this failure got through and
# not some other message the dialog might have said.
clock.s["google_client_id"] = "check-client"
page = adding("google", "someone@gmail.com")
google_oauth.sign_in = failing(google_oauth.GoogleError, "the client id was refused")
press(page, "Sign in with Google")
check("a Google sign-in that fails says what went wrong",
      spin(5, lambda: "the client id was refused" in page.status.text()), "status %r" % page.status.text())

page = adding("icloud", "someone@icloud.com")
page.secret.setText("not-the-password")
caldav.discover = failing(caldav.CalDavError, "the app password was refused")
press(page, "Find calendars")
check("so does a calendar server that will not let it in",
      spin(5, lambda: "the app password was refused" in page.status.text()), "status %r" % page.status.text())

page = adding("microsoft", "someone@outlook.com")
page.link.setText("https://example.invalid/calendar.ics")
ics.fetch = failing(ics.IcsError, "that page is not a calendar")
press(page, "Add")
check("so does an iCal address that does not work",
      spin(5, lambda: "that page is not a calendar" in page.status.text()), "status %r" % page.status.text())

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("every worker thread reaches the UI")
