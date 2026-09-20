"""Drive the Windows masjid picker: search, pick, and the check before saving.

Windows only: it opens the Tk dialog (never mapped, so nothing appears on
screen). What the workers fetch is replaced with canned answers, so there is
no network, and the settings go to a scratch folder -- never the real one.

    set PYTHONPATH=<the folder holding floating_clock>
    python packaging/check-picker-tk.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import tkinter as tk
from datetime import datetime, timedelta

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import masjids, prayer  # noqa: E402
from floating_clock import widgets as w  # noqa: E402
from floating_clock.settings_ui import SettingsUI  # noqa: E402

SettingsUI.settings_visible = False                 # built in full, never shown

from floating_clock.app import FloatingClock  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


soon = datetime.now() + timedelta(hours=1)
prayer.load = lambda base, url="", now=None, force=False, fetcher=None, **kw: (
    [prayer.Prayer("Fajr", soon)], "Test Masjid · updated just now")
masjids.search = lambda text="", *a, **k: ([{
    "name": "Test Masjid", "city": "Waterloo, Ontario", "slug": "test-masjid",
    "source": "mawaqit", "iqama": True}], "")

app = FloatingClock()


def pump(seconds: float, until) -> bool:
    """Run the Tk event loop until `until()` or the time is up.

    A real mainloop, not update() in a loop: Tk only accepts after() from
    another thread while the main thread is inside mainloop, and the workers
    hand their answers back that way. Sleeping between updates leaves gaps in
    which the hand-back is refused -- which the app itself would never see.
    """
    deadline = time.time() + seconds

    def poll() -> None:
        if until() or time.time() > deadline:
            app.root.quit()
        else:
            app.root.after(20, poll)

    app.root.after(20, poll)
    app.root.mainloop()
    return bool(until())


def act(action, seconds: float, until) -> bool:
    """Do something on the main thread from inside the loop, then wait."""
    app.root.after(0, action)
    return pump(seconds, until)


def find(node, kind, text=None):
    for child in node.winfo_children():
        if isinstance(child, kind) and (text is None or getattr(child, "text", None) == text):
            return child
        hit = find(child, kind, text)
        if hit is not None:
            return hit
    return None


def labels(node):
    out = []
    for child in node.winfo_children():
        try:
            out.append(str(child.cget("text")))
        except Exception:
            pass
        out.extend(labels(child))
    return out


def open_picker():
    app._open_masjid_picker()
    app.root.update_idletasks()
    return [t for t in app._settings_win.winfo_children() if isinstance(t, tk.Toplevel)][-1]


def search_and_pick(win):
    find(win, w.Field).set("waterloo")
    listing = find(win, w.RowList)
    ok = act(lambda: find(win, w.Button, "Search").command(), 10,
             lambda: len(listing._rows) > 0)
    listing.selected_key = "0"
    return ok


try:
    app.open_settings()
    app._show_page("prayer")
    app.root.update_idletasks()

    print("the picker")
    win = open_picker()
    check("a search fills the list", search_and_pick(win))
    before = app.s.get("prayer_ics_url", "")
    shown = lambda: any("Check they match" in t for t in labels(win))   # noqa: E731
    check("a masjid that can be read is shown before it is saved",
          act(lambda: find(win, w.Button, "Use this masjid").command(), 10, shown),
          "; ".join(labels(win))[-200:])
    check("and nothing is saved yet", app.s.get("prayer_ics_url", "") == before)
    check("a second press keeps it and the dialog closes",
          act(lambda: find(win, w.Button, "Use this masjid").command(), 10,
              lambda: not win.winfo_exists()))
    check("its address is now the one in use",
          str(app.s.get("prayer_ics_url", "")).endswith("/en/test-masjid"),
          repr(app.s.get("prayer_ics_url")))
    kept = app.s["prayer_ics_url"]

    print("a masjid that cannot be read")
    prayer.load = lambda base, url="", now=None, force=False, fetcher=None, **kw: (
        [], "Could not read the prayer times: that site does not publish a timetable.")
    win = open_picker()
    search_and_pick(win)
    said = lambda: any("Nothing was changed" in t for t in labels(win))   # noqa: E731
    check("says nothing was changed",
          act(lambda: find(win, w.Button, "Use this masjid").command(), 10, said),
          "; ".join(labels(win))[-160:])
    check("and the dialog stays open", win.winfo_exists())
    check("and the address that works is exactly as it was",
          app.s.get("prayer_ics_url") == kept, repr(app.s.get("prayer_ics_url")))
    PROPOSAL = {
        "kind": "read", "address": "http://erin.example", "name": "Erin Centre", "how": "guessed",
        "source": "scrape", "status": "read", "asked": "", "km": None, "latitude": 43.77, "longitude": -80.06,
        "times": [("Fajr", "05:30"), ("Dhuhr", "14:00"), ("Asr", "18:00"), ("Maghrib", "19:22"),
                  ("Isha", "20:00")]}

    print("a reading taken off a web page is shown before it is kept")
    masjids.propose = lambda entry, rows, progress=None, **k: dict(PROPOSAL)
    win = open_picker()
    search_and_pick(win)
    shown = lambda: any("Read from Erin Centre" in t for t in labels(win))   # noqa: E731
    check("the five times read are shown",
          act(lambda: find(win, w.Button, "Use this masjid").command(), 10, shown),
          "; ".join(labels(win))[-200:])
    check("and nothing is saved yet", app.s.get("prayer_ics_url") == kept, repr(app.s.get("prayer_ics_url")))
    check("and the dialog stays open", win.winfo_exists())
    check("a second press keeps them",
          act(lambda: find(win, w.Button, "Use this masjid").command(), 10, lambda: not win.winfo_exists()))
    check("the address is saved", app.s.get("prayer_ics_url") == "http://erin.example", repr(app.s.get("prayer_ics_url")))
    check("with where the masjid is, and its name",
          app.s.get("prayer_lat") == 43.77 and app.s.get("prayer_lon") == -80.06
          and app.s.get("prayer_masjid_name") == "Erin Centre")
    check("and these are its own times", app.s.get("prayer_proxy_for") == "")

    print("a neighbour's times are labelled as one's and kept only on a second press")
    PROXY = dict(PROPOSAL, kind="proxy", name="Nearer Masjid", asked="Erin Centre", km=1.1,
                 address="http://nearer.example", latitude=43.78)
    masjids.propose = lambda entry, rows, progress=None, **k: dict(PROXY)
    win = open_picker()
    search_and_pick(win)
    said = lambda: any("Nearer Masjid's times, not Erin Centre's" in t for t in labels(win))   # noqa: E731
    check("the neighbour is named, and the masjid it stands in for",
          act(lambda: find(win, w.Button, "Use this masjid").command(), 10, said),
          "; ".join(labels(win))[-200:])
    check("and nothing is saved yet", app.s.get("prayer_ics_url") == "http://erin.example")
    check("a second press keeps them",
          act(lambda: find(win, w.Button, "Use this masjid").command(), 10, lambda: not win.winfo_exists()))
    check("and the settings say whose they really are",
          app.s.get("prayer_ics_url") == "http://nearer.example" and app.s.get("prayer_proxy_for") == "Erin Centre"
          and app.s.get("prayer_masjid_name") == "Nearer Masjid")

    print("an address typed by hand is not a place the picker knew")
    app.prayer_url_field.set("http://elsewhere.example")
    act(lambda: app._set_prayer_url(), 5, lambda: True)
    check("forgets where the masjid was, and that it was borrowed",
          app.s.get("prayer_lat") is None and app.s.get("prayer_proxy_for") == ""
          and app.s.get("prayer_masjid_name") == "", repr(app.s.get("prayer_proxy_for")))
except Exception:
    import traceback

    traceback.print_exc()
    failures.append("an exception")
finally:
    try:
        app.root.destroy()
    except Exception:
        pass

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("the picker checks a masjid before it saves it")
