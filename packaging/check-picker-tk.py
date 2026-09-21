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


real_propose = masjids.propose
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

    print("the timeline of a check")
    import threading

    from floating_clock import timeline

    masjids.propose = real_propose               # the real one, which reports each step

    def five():
        day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return [prayer.Prayer(n, day + timedelta(hours=h, minutes=m))
                for n, (h, m) in zip(prayer.DAILY, ((6, 15), (13, 45), (17, 45), (19, 28), (21, 0)))]

    gate, entered, finished = threading.Event(), threading.Event(), threading.Event()

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
        win = open_picker()
        search_and_pick(win)
        act(lambda: find(win, w.Button, "Use this masjid").command(), 10, entered.is_set)
        return win

    def drawn_text(view):
        canvas = view.canvas
        return [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]

    real_inspect = masjids.inspect
    masjids.inspect = slow_inspect
    kept = app.s.get("prayer_ics_url", "")
    win = begin()
    view = find(win, w.TimelineView)
    check("choosing a masjid swaps the list for a timeline of what is being done",
          view.winfo_manager() == "pack" and find(win, w.RowList).winfo_manager() == "")
    check("with Stop to give up, and the button that would start another check out of the way",
          find(win, w.Button, "Stop").winfo_manager() == "pack" and find(win, w.Button, "Back to results").winfo_manager() == ""
          and not find(win, w.Button, "Use this masjid").enabled)
    check("the worker gets as far as its first step", entered.is_set())
    act(lambda: None, 0.5, lambda: False)
    check("the window is drawing it, with the step that is running and how long it has taken",
          any("Asking mawaqit.net" in t for t in drawn_text(view)) and any("Checking" in t for t in drawn_text(view)),
          str(drawn_text(view)[:6]))
    check("and the check is under way, with a bar that has begun to move",
          view.line.snapshot()["busy"] and view.line.snapshot()["fraction"] > 0)
    check("the status line says that it takes a while and that Stop gives up",
          any("Stop gives up" in t for t in labels(win)), "; ".join(labels(win))[-200:])
    gate.set()
    shown = lambda: any("Check they match" in t for t in labels(win))   # noqa: E731
    check("when it is done, what it found is shown as before", pump(10, shown), "; ".join(labels(win))[-200:])
    check("Stop goes, Back to results comes, and the button works again",
          find(win, w.Button, "Stop").winfo_manager() == "" and find(win, w.Button, "Back to results").winfo_manager() == "pack"
          and find(win, w.Button, "Use this masjid").enabled)
    check("the timeline says it found something", view.line.snapshot()["outcome"] == "found")
    check("and nothing has been saved yet", app.s.get("prayer_ics_url", "") == kept)
    act(lambda: find(win, w.Button, "Back to results").command(), 2, lambda: find(win, w.RowList).winfo_manager() == "pack")
    check("Back to results shows the list again", find(win, w.RowList).winfo_manager() == "pack" and view.winfo_manager() == "")
    win.destroy()

    print("Stop")
    win = begin()
    view = find(win, w.TimelineView)
    check("a check is under way", entered.is_set())
    act(lambda: find(win, w.Button, "Stop").command(), 10, finished.is_set)
    check("Stop is heard by the worker in the middle of a step", finished.is_set())
    said = lambda: any("Stopped" in t and "Nothing was changed" in t for t in labels(win))   # noqa: E731
    check("and reported: stopped, and nothing changed", pump(10, said), "; ".join(labels(win))[-200:])
    check("the timeline says it was stopped", view.line.snapshot()["outcome"] == "stopped", str(view.line.snapshot()["outcome"]))
    check("what was running is not left spinning", all(r["state"] != "running" for r in view.line.snapshot()["rows"]))
    check("nothing was saved, and the dialog stays open", app.s.get("prayer_ics_url", "") == kept and win.winfo_exists())
    check("Back to results is offered", find(win, w.Button, "Back to results").winfo_manager() == "pack")
    win.destroy()

    print("closing the window in the middle of a check")
    win = begin()
    view = find(win, w.TimelineView)
    line = view.line
    check("a check is under way", entered.is_set())
    win.destroy()
    check("closing the window stops it, so it does not go on opening pages for nobody", pump(10, finished.is_set))
    check("and the timeline says so", pump(5, lambda: line.snapshot()["outcome"] == "stopped"), str(line.snapshot()["outcome"]))
    masjids.inspect = real_inspect

    print("a check that is not the real one")
    masjids.propose = lambda entry, rows, progress=None, **k: {"kind": "none", "status": "Erin Centre: nothing readable."}
    win = open_picker()
    search_and_pick(win)
    act(lambda: find(win, w.Button, "Use this masjid").command(), 10, lambda: any("Nothing was changed" in t for t in labels(win)))
    view = find(win, w.TimelineView)
    check("a worker that never reports still leaves a timeline that ends",
          view.line.snapshot()["outcome"] == "none" and not view.line.snapshot()["busy"])
    win.destroy()
    masjids.propose = real_propose

    print("the picture")
    top = tk.Toplevel(app.root)
    top.withdraw()
    from floating_clock import palette as pal_mod
    for mode in ("dark", "light"):
        ui = w.Ui(top, pal_mod.resolve(mode, (127, 40, 255)), 1.0)
        sample = timeline.Timeline()
        sample.plan("Test Masjid", [timeline.Section("site", "The masjid's own website", "example.org", timeline.WEBSITE)])
        sample.section("site")
        sample.start("pages", "reading /prayer-times, /salah-timings and 2 more")
        for width in (260, 420, 700):
            holder = tk.Frame(top, width=width, height=300)
            holder.pack_propagate(False)
            picture = w.TimelineView(holder, ui, height=300)
            picture.canvas.configure(width=width)
            picture.pack(fill="both", expand=True)
            picture.watch(sample)
            picture._draw()
            over = []
            for item in picture.canvas.find_all():
                if picture.canvas.type(item) != "text":
                    continue
                left, _top, right, _bottom = picture.canvas.bbox(item)
                if right > width:
                    over.append((picture.canvas.itemcget(item, "text"), right))
            check("in %s mode at %d pixels nothing runs off the edge, measured with the real font" % (mode, width),
                  not over, str(over[:1]))
            picture.stop()
            holder.destroy()
    top.destroy()
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
