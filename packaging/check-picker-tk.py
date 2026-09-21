"""Drive the Windows masjid picker: search, pick, look near this computer, and the check before saving.

Windows only: it opens the Tk dialog. Nearly all of it is never mapped, so
nothing appears on screen; where the layout itself is measured the window is
mapped fully transparent, for as long as that takes. What the workers fetch is
replaced with canned answers, so there is no network, and the settings go to a
scratch folder -- never the real one.

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

from floating_clock import masjids, prayer, whereami  # noqa: E402
from floating_clock import widgets as w  # noqa: E402
from floating_clock.settings_ui import SettingsUI  # noqa: E402

SettingsUI.settings_visible = False                 # built in full, never shown

from floating_clock.app import FloatingClock  # noqa: E402

failures: list[str] = []
PATIENCE = float(os.environ.get("CHECK_PATIENCE", "1"))       # a fraction of every wait, for a run that is expected to fail


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
    deadline = time.time() + seconds * PATIENCE

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


class Inside:
    """Where a click lands: on the button."""
    x = y = 1


def press(button) -> bool:
    """Press a button the way a click does: nothing happens if it is not enabled."""
    if button is None or not button.enabled:
        return False
    button._release(Inside)
    return True


def main_of(win):
    """The main button, whatever it says at the moment."""
    for text in ("Use this masjid", "Done", "Try again"):
        hit = find(win, w.Button, text)
        if hit is not None:
            return hit
    return None


def showing(win) -> str:
    """What fills the middle: the list, or the timeline."""
    listed = find(win, w.ScrollFrame).winfo_manager() == "pack"
    timed = find(win, w.TimelineView).winfo_manager() == "pack"
    return "list" if listed and not timed else "timeline" if timed and not listed else "both or neither"


def search_and_pick(win):
    find(win, w.Field).set("waterloo")
    listing = find(win, w.RowList)
    ok = act(lambda: press(find(win, w.Button, "Search")), 10,
             lambda: len(listing._rows) > 0)
    listing.select("0")                            # as a click on the first row does
    return ok


try:
    app.open_settings()
    app._show_page("prayer")
    app.root.update_idletasks()

    print("the picker")
    win = open_picker()
    check("with nothing found yet, there is nothing to use, and the main button says so by being off",
          not main_of(win).enabled and main_of(win).text == "Use this masjid")
    check("Search and Near me are both there", find(win, w.Button, "Search").enabled and find(win, w.Button, "Near me").enabled)
    check("a search fills the list", search_and_pick(win))
    before = app.s.get("prayer_ics_url", "")
    shown = lambda: any("Check they match" in t for t in labels(win))   # noqa: E731
    check("with a masjid picked, the main button says what it does and can be pressed",
          main_of(win).text == "Use this masjid" and main_of(win).enabled)
    check("a masjid that can be read is shown before it is saved",
          act(lambda: press(main_of(win)), 10, shown),
          "; ".join(labels(win))[-200:])
    check("and nothing is saved yet", app.s.get("prayer_ics_url", "") == before)
    check("the main button now says Done", main_of(win).text == "Done" and main_of(win).enabled, main_of(win).text)
    check("and the line above says to press it", any("Press Done to use them" in t for t in labels(win)), "; ".join(labels(win))[-200:])
    check("Done keeps it and the dialog closes",
          act(lambda: press(main_of(win)), 10,
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
          act(lambda: press(main_of(win)), 10, said),
          "; ".join(labels(win))[-160:])
    check("and the dialog stays open", win.winfo_exists())
    check("and the main button offers Try again, not Done", main_of(win).text == "Try again" and main_of(win).enabled, main_of(win).text)
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
          act(lambda: press(main_of(win)), 10, shown),
          "; ".join(labels(win))[-200:])
    check("and nothing is saved yet", app.s.get("prayer_ics_url") == kept, repr(app.s.get("prayer_ics_url")))
    check("and the dialog stays open", win.winfo_exists())
    check("Done keeps them",
          main_of(win).text == "Done" and act(lambda: press(main_of(win)), 10, lambda: not win.winfo_exists()))
    check("the address is saved", app.s.get("prayer_ics_url") == "http://erin.example", repr(app.s.get("prayer_ics_url")))
    check("with where the masjid is, and its name",
          app.s.get("prayer_lat") == 43.77 and app.s.get("prayer_lon") == -80.06
          and app.s.get("prayer_masjid_name") == "Erin Centre")
    check("and these are its own times", app.s.get("prayer_proxy_for") == "")

    print("a neighbour's times are labelled as one's and kept only when Done is pressed")
    PROXY = dict(PROPOSAL, kind="proxy", name="Nearer Masjid", asked="Erin Centre", km=1.1,
                 address="http://nearer.example", latitude=43.78)
    masjids.propose = lambda entry, rows, progress=None, **k: dict(PROXY)
    win = open_picker()
    search_and_pick(win)
    said = lambda: any("Nearer Masjid's times, not Erin Centre's" in t for t in labels(win))   # noqa: E731
    check("the neighbour is named, and the masjid it stands in for",
          act(lambda: press(main_of(win)), 10, said),
          "; ".join(labels(win))[-200:])
    check("and nothing is saved yet", app.s.get("prayer_ics_url") == "http://erin.example")
    check("Done keeps them",
          main_of(win).text == "Done" and act(lambda: press(main_of(win)), 10, lambda: not win.winfo_exists()))
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
        act(lambda: press(main_of(win)), 10, entered.is_set)
        return win

    def drawn_text(view):
        canvas = view.canvas
        return [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]

    callback_errors: list = []
    app.root.report_callback_exception = lambda kind, value, tb: callback_errors.append("%s: %s" % (kind.__name__, value))
    real_inspect = masjids.inspect
    masjids.inspect = slow_inspect
    kept = app.s.get("prayer_ics_url", "")
    win = begin()
    view = find(win, w.TimelineView)
    check("choosing a masjid swaps the list for a timeline of what is being done", showing(win) == "timeline", showing(win))
    check("with Stop to give up, and the buttons that would start another check out of the way",
          find(win, w.Button, "Stop").winfo_manager() == "pack" and find(win, w.Button, "Back to results").winfo_manager() == ""
          and not main_of(win).enabled and not find(win, w.Button, "Search").enabled and not find(win, w.Button, "Near me").enabled)
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
    check("Stop goes, Back to results comes, and the main button says Done and works",
          find(win, w.Button, "Stop").winfo_manager() == "" and find(win, w.Button, "Back to results").winfo_manager() == "pack"
          and main_of(win).text == "Done" and main_of(win).enabled)
    check("the timeline says it found something", view.line.snapshot()["outcome"] == "found")
    check("and nothing has been saved yet", app.s.get("prayer_ics_url", "") == kept)
    act(lambda: press(find(win, w.Button, "Back to results")), 2, lambda: showing(win) == "list")
    check("Back to results shows the list again", showing(win) == "list" and view.winfo_manager() == "", showing(win))
    check("with the main button back to what it was, and the masjid still picked", main_of(win).text == "Use this masjid" and main_of(win).enabled)
    check("and the line beneath says again what was found, not what was found out about the last check",
          any(t.startswith("Found 1. Pick one") for t in labels(win)), "; ".join(labels(win))[-200:])
    win.destroy()

    print("Stop")
    win = begin()
    view = find(win, w.TimelineView)
    check("a check is under way", entered.is_set())
    act(lambda: press(find(win, w.Button, "Stop")), 10, finished.is_set)
    check("Stop is heard by the worker in the middle of a step", finished.is_set())
    said = lambda: any("Stopped" in t and "Nothing was changed" in t for t in labels(win))   # noqa: E731
    check("and reported: stopped, and nothing changed", pump(10, said), "; ".join(labels(win))[-200:])
    check("the timeline says it was stopped", view.line.snapshot()["outcome"] == "stopped", str(view.line.snapshot()["outcome"]))
    check("what was running is not left spinning", all(r["state"] != "running" for r in view.line.snapshot()["rows"]))
    check("nothing was saved, and the dialog stays open", app.s.get("prayer_ics_url", "") == kept and win.winfo_exists())
    check("Back to results is offered, and so is Try again", find(win, w.Button, "Back to results").winfo_manager() == "pack"
          and main_of(win).text == "Try again" and main_of(win).enabled, main_of(win).text)
    stopped_line = view.line
    gate.clear()
    entered.clear()
    finished.clear()
    act(lambda: press(main_of(win)), 10, entered.is_set)
    check("Try again checks the same masjid again, with a new timeline running",
          entered.is_set() and showing(win) == "timeline" and view.line is not stopped_line
          and view.line.snapshot()["busy"] and stopped_line.snapshot()["outcome"] == "stopped",
          "%s entered=%s new=%s" % (showing(win), entered.is_set(), view.line is not stopped_line))
    gate.set()
    check("and this time it finds the times, and offers Done", pump(10, lambda: main_of(win).text == "Done"), "; ".join(labels(win))[-200:])
    win.destroy()

    print("closing the window in the middle of a check")
    win = begin()
    view = find(win, w.TimelineView)
    line = view.line
    check("a check is under way", entered.is_set())
    win.destroy()
    check("closing the window stops it, so it does not go on opening pages for nobody", pump(10, finished.is_set))
    check("and the timeline says so", pump(5, lambda: line.snapshot()["outcome"] == "stopped"), str(line.snapshot()["outcome"]))
    pump(0.5, lambda: False)
    check("and what it finds after the window has gone is dropped quietly, not thrown at Tk", not callback_errors, str(callback_errors[:1]))
    masjids.inspect = real_inspect

    print("a check that is not the real one")
    masjids.propose = lambda entry, rows, progress=None, **k: {"kind": "none", "status": "Erin Centre: nothing readable."}
    win = open_picker()
    search_and_pick(win)
    act(lambda: press(main_of(win)), 10, lambda: any("Nothing was changed" in t for t in labels(win)))
    view = find(win, w.TimelineView)
    check("a worker that never reports still leaves a timeline that ends",
          view.line.snapshot()["outcome"] == "none" and not view.line.snapshot()["busy"])
    win.destroy()
    masjids.propose = real_propose

    print("a search, or a look near this computer, that comes back after the window is closed")
    late = threading.Event()
    real_search, real_locate = masjids.search, whereami.locate

    def late_search(text="", *a, **k):
        late.wait(5)
        return ([{"name": "Late Masjid", "city": "Waterloo, Ontario", "slug": "late", "source": "mawaqit", "iqama": True, "km": 1.0}], "")

    def late_locate(*a, **k):
        late.wait(5)
        return whereami.Where(43.4643, -80.5204, "windows", "", 0.035)

    masjids.search, whereami.locate = late_search, late_locate
    for what, button in (("search", "Search"), ("look near this computer", "Near me")):
        late.clear()
        callback_errors.clear()
        win = open_picker()
        find(win, w.Field).set("waterloo")
        act(lambda button=button: press(find(win, w.Button, button)), 0.4, lambda: False)
        check("a %s is under way, with the window still open" % what, win.winfo_exists() and find(win, w.Button, "Near me").enabled is False)
        win.destroy()
        late.set()
        pump(1.0, lambda: False)
        check("the answer of a %s that arrives after the window is closed is dropped quietly" % what, not callback_errors, str(callback_errors[:1]))
    masjids.search, whereami.locate = real_search, real_locate

    print("the window fits the screen, whatever it is showing")
    from floating_clock.settings_ui import SettingsUI as UI

    def shown(win_):
        """Map the picker, fully transparent, so that it has a real layout to measure."""
        parent = app._settings_win
        parent.attributes("-alpha", 0.0)
        parent.deiconify()
        deadline = time.time() + 5
        while time.time() < deadline and not parent.winfo_viewable():
            parent.update()
            time.sleep(0.02)
        win_.attributes("-alpha", 0.0)
        win_.deiconify()
        deadline = time.time() + 5
        while time.time() < deadline and not (win_.winfo_viewable() and win_.winfo_height() > 50):
            win_.update()
            time.sleep(0.02)
        win_.update_idletasks()
        win_.update()
        return win_

    def outer(win_):
        """(left, top, right, bottom) of the whole window, title bar and borders included."""
        side = win_.winfo_rootx() - win_.winfo_x()               # a border, which is the same on both sides and underneath
        return (win_.winfo_x(), win_.winfo_y(), win_.winfo_rootx() + win_.winfo_width() + side,
                win_.winfo_rooty() + win_.winfo_height() + side)

    def controls_in(win_):
        """(what is wrong with where the buttons are, the height the list or timeline has)."""
        win_.update_idletasks()
        win_.update()
        wrong = []
        for text in ("Search", "Near me", "Cancel"):
            b = find(win_, w.Button, text)
            if b is None or not b.winfo_ismapped():
                wrong.append("%s is not on show" % text)
        b = main_of(win_)
        if b is None or not b.winfo_ismapped():
            wrong.append("the main button is not on show")
        for b in (main_of(win_), find(win_, w.Button, "Cancel"), find(win_, w.Button, "Search")):
            if b is not None and b.winfo_ismapped():
                left, top = b.winfo_rootx() - win_.winfo_rootx(), b.winfo_rooty() - win_.winfo_rooty()
                if left < 0 or top < 0 or left + b.winfo_width() > win_.winfo_width() or top + b.winfo_height() > win_.winfo_height():
                    wrong.append("%s lies outside the window: %d,%d %dx%d in %dx%d" % (
                        b.text, left, top, b.winfo_width(), b.winfo_height(), win_.winfo_width(), win_.winfo_height()))
        middle = find(win_, w.ScrollFrame) if showing(win_) == "list" else find(win_, w.TimelineView)
        return wrong, middle.winfo_height()

    real_area = UI._picker_area
    minimum = app._ui.px(60)                        # two rows of the list, or a few lines of the timeline
    for name, area in (("the real monitor", None), ("a 1366x768 laptop", (0, 0, 1366, 728)), ("a small 1024x600 screen", (0, 0, 1024, 560)),
                       ("a monitor to the left of the main one", (-1920, 0, 0, 1040))):
        UI._picker_area = real_area if area is None else (lambda self, parent, area=area: area)
        big = [{"name": "Test Masjid %d" % i, "city": "Waterloo, Ontario", "slug": "test-masjid-%d" % i, "source": "mawaqit",
                "iqama": True, "km": i / 2} for i in range(60)]
        masjids.search = lambda text="", *a, **k: (big, "")
        win = shown(open_picker())
        area_now = UI._picker_area(app, app._settings_win)
        wrong, room = controls_in(win)
        check("%s: at the start, every button is on show inside the window, with %d px for the list" % (name, room),
              not wrong and room >= minimum, "; ".join(wrong) + " room=%d" % room)
        check("%s: the window is no bigger than the room there is (%dx%d in %dx%d)" % (
            name, win.winfo_width(), win.winfo_height(), area_now[2] - area_now[0], area_now[3] - area_now[1]),
            win.winfo_width() <= area_now[2] - area_now[0] and win.winfo_height() <= area_now[3] - area_now[1],
            "%dx%d" % (win.winfo_width(), win.winfo_height()))
        box = outer(win)
        check("%s: and the whole of it, title bar and borders too, lies inside that monitor" % name,
              box[0] >= area_now[0] and box[1] >= area_now[1] and box[2] <= area_now[2] and box[3] <= area_now[3],
              "%s in %s" % (box, area_now))
        roomy = win.winfo_height() >= app._ui.px(520)
        check("%s: the explanation above the search box is %s" % (name, "kept" if roomy else "dropped, to leave the list room"),
              any("come with their congregation times" in t for t in labels(win)) == roomy, "%d tall" % win.winfo_height())
        start = (win.winfo_width(), win.winfo_height())
        search_and_pick(win)
        wrong, room = controls_in(win)
        check("%s: with sixty masjids found, the buttons are still there and the window has not grown" % name,
              not wrong and (win.winfo_width(), win.winfo_height()) == start, "; ".join(wrong) + " %s" % (win.winfo_width(), win.winfo_height()).__str__())
        prayer.load = lambda base, url="", now=None, force=False, fetcher=None, **kw: (
            [prayer.Prayer("Fajr", soon)], "Test Masjid . updated just now")
        masjids.propose = lambda entry, rows, progress=None, **k: dict(PROPOSAL, name="A Masjid With A Rather Long Name Indeed Centre",
                                                                     times=PROPOSAL["times"])
        act(lambda: press(main_of(win)), 10, lambda: main_of(win).text == "Done")
        wrong, room = controls_in(win)
        check("%s: with the times found and a long paragraph beneath, Done is there, inside, and the timeline still has %d px" % (name, room),
              not wrong and main_of(win).text == "Done" and room >= minimum and (win.winfo_width(), win.winfo_height()) == start,
              "; ".join(wrong) + " room=%d" % room)
        masjids.propose = lambda entry, rows, progress=None, **k: {"kind": "none", "status": "Test Masjid: " + "nothing readable at all. " * 6}
        act(lambda: press(find(win, w.Button, "Back to results")), 2, lambda: showing(win) == "list")
        act(lambda: press(main_of(win)), 10, lambda: main_of(win).text == "Try again")
        wrong, room = controls_in(win)
        check("%s: after a failure with a very long reason, the buttons are still there" % name,
              not wrong and main_of(win).text == "Try again" and (win.winfo_width(), win.winfo_height()) == start, "; ".join(wrong))
        win.destroy()
    UI._picker_area = real_area
    masjids.search = lambda text="", *a, **k: ([{
        "name": "Test Masjid", "city": "Waterloo, Ontario", "slug": "test-masjid",
        "source": "mawaqit", "iqama": True}], "")
    masjids.propose = real_propose

    print("the wheel scrolls what is showing")
    many = [{"name": "Test Masjid %d" % i, "city": "Waterloo, Ontario", "slug": "test-masjid-%d" % i, "source": "mawaqit", "iqama": True}
            for i in range(60)]
    masjids.search = lambda text="", *a, **k: (many, "")
    win = shown(open_picker())
    search_and_pick(win)
    scroller = find(win, w.ScrollFrame)
    win.update()
    check("with sixty in the list, there is more than fits", scroller.canvas.yview()[1] < 0.5, str(scroller.canvas.yview()))
    win.event_generate("<MouseWheel>", delta=-120)
    win.update()
    moved = scroller.canvas.yview()[0]
    check("the wheel scrolls it, wherever the pointer is in the window", moved > 0, str(scroller.canvas.yview()))
    win.event_generate("<MouseWheel>", delta=120)
    win.update()
    check("and back up again", scroller.canvas.yview()[0] < moved, str(scroller.canvas.yview()))
    win.event_generate("<MouseWheel>", delta=-120)
    win.event_generate("<MouseWheel>", delta=-120)
    win.update()
    check("scrolled down again, before a new search", scroller.canvas.yview()[0] > 0, str(scroller.canvas.yview()))
    find(win, w.Field).set("waterloo")
    act(lambda: press(find(win, w.Button, "Search")), 10, lambda: len(find(win, w.RowList)._rows) > 0)
    check("a new set of results starts at the top, with none of them picked",
          scroller.canvas.yview()[0] == 0 and find(win, w.RowList).selected_key is None and not main_of(win).enabled,
          str(scroller.canvas.yview()))
    win.destroy()
    masjids.search = lambda text="", *a, **k: ([{
        "name": "Test Masjid", "city": "Waterloo, Ontario", "slug": "test-masjid",
        "source": "mawaqit", "iqama": True}], "")

    print("masjids near me")
    EXACT = whereami.Where(43.4643, -80.5204, "windows", "", 0.035)
    APPROX = whereami.Where(43.45, -80.49, "internet", "Kitchener, Ontario", 25.0,
                            why_not="location is switched off in Windows; turn it on in Settings > Privacy & security > Location")
    NEAR = [{"name": "Nearest Masjid", "city": "Waterloo, Ontario", "slug": "nearest", "source": "mawaqit", "iqama": True, "km": 0.6},
            {"name": "Further Masjid", "city": "Kitchener, Ontario", "slug": "further", "source": "mawaqit", "iqama": True, "km": 4.2}]
    asked_for: list = []

    def around_search(text="", *a, **k):
        asked_for.append(dict(k, text=text))
        return list(NEAR), ""

    real_search, real_locate = masjids.search, whereami.locate
    masjids.search = around_search
    whereami.locate = lambda *a, **k: EXACT
    prayer.load = lambda base, url="", now=None, force=False, fetcher=None, **kw: ([prayer.Prayer("Fajr", soon)], "Test Masjid . updated just now")
    kept = app.s.get("prayer_ics_url", "")
    win = open_picker()
    check("the picker says how to find masjids, and mentions Near me", any("press Near me" in t for t in labels(win)), "; ".join(labels(win))[-200:])
    act(lambda: press(find(win, w.Button, "Near me")), 10, lambda: len(find(win, w.RowList)._rows) == 2)
    listing = find(win, w.RowList)
    check("Near me lists the masjids around this computer", len(listing._rows) == 2, "; ".join(labels(win))[-200:])
    check("nearest first, with how far", listing._rows[0].primary.cget("text") == "Nearest Masjid"
          and "0.6 km" in listing._rows[0].secondary.cget("text"), listing._rows[0].secondary.cget("text"))
    check("it looked within 20 km of the point Windows gave, for no name",
          asked_for[-1].get("lat") == 43.4643 and asked_for[-1].get("radius_km") == 20.0 and asked_for[-1].get("text") == "", str(asked_for[-1]))
    check("and says how many, and how it knows where you are",
          any(t.startswith("Found 2 masjids within 20 km of this computer's location, from Windows.") for t in labels(win)), "; ".join(labels(win))[-260:])
    check("the list is on show, nothing is picked, and Search and Near me are usable",
          showing(win) == "list" and listing.selected_key is None and not main_of(win).enabled
          and find(win, w.Button, "Search").enabled and find(win, w.Button, "Near me").enabled)
    listing.select("1")
    check("picking one lets it be used", main_of(win).enabled and main_of(win).text == "Use this masjid")
    check("and checking it, then Done, keeps it",
          act(lambda: press(main_of(win)), 10, lambda: main_of(win).text == "Done")
          and app.s.get("prayer_ics_url", "") == kept and act(lambda: press(main_of(win)), 10, lambda: not win.winfo_exists())
          and str(app.s.get("prayer_ics_url", "")).endswith("/en/further"), repr(app.s.get("prayer_ics_url")))

    whereami.locate = lambda *a, **k: APPROX
    win = open_picker()
    act(lambda: press(find(win, w.Button, "Near me")), 10, lambda: len(find(win, w.RowList)._rows) == 2)
    check("a position worked out from the internet address says so, and offers a way to correct it",
          any("worked out from your internet address" in t and "Not the right place? Type a town or a postal code" in t for t in labels(win)),
          "; ".join(labels(win))[-260:])
    check("and why Windows would not give a better one", any("location is switched off in Windows" in t for t in labels(win)))
    win.destroy()

    def cannot(*a, **k):
        raise whereami.Unavailable("Windows is not letting desktop apps see where this computer is. Nor could it be worked out from your internet address: no location service answered")

    whereami.locate = cannot
    asked_for.clear()
    win = open_picker()
    act(lambda: press(find(win, w.Button, "Near me")), 10, lambda: main_of(win).text == "Try again")
    check("when it cannot find where you are, it says why, and offers Try again",
          any(t.startswith("Could not find where you are: Windows is not letting desktop apps") for t in labels(win))
          and main_of(win).text == "Try again", "; ".join(labels(win))[-260:])
    check("the timeline stays up to show what was tried, with Back to results", showing(win) == "timeline"
          and find(win, w.Button, "Back to results").winfo_manager() == "pack")
    view = find(win, w.TimelineView)
    check("headed by what went wrong", view.line.snapshot()["words"]["none"] == "Could not find where you are"
          and any("Could not find where you are" in i for i in drawn_text(view)), str(drawn_text(view)[:4]))
    check("and no search was made for masjids near nowhere", asked_for == [], str(asked_for))
    whereami.locate = lambda *a, **k: EXACT
    act(lambda: press(main_of(win)), 10, lambda: len(find(win, w.RowList)._rows) == 2)
    check("once that is put right, Try again gets the list", showing(win) == "list" and len(find(win, w.RowList)._rows) == 2)
    win.destroy()

    held, inside = threading.Event(), threading.Event()

    started: list = []

    def waits(*a, **k):
        started.append(1)
        with timeline.current().step("locate"):
            inside.set()
            while not held.is_set():
                time.sleep(0.02)
                timeline.current().check()
        return EXACT

    whereami.locate = waits
    asked_for.clear()
    win = open_picker()
    act(lambda: press(find(win, w.Button, "Near me")), 10, inside.is_set)
    check("Near me is under way, with the timeline and Stop", showing(win) == "timeline" and find(win, w.Button, "Stop").winfo_manager() == "pack")
    check("pressing it again does nothing, since it is not on offer", not find(win, w.Button, "Near me").enabled and not press(find(win, w.Button, "Near me")))
    act(lambda: find(win, w.Button, "Near me").command(), 0.5, lambda: False)         # even if something calls it directly
    check("and no second look is started", len(started) == 1, str(len(started)))
    act(lambda: press(find(win, w.Button, "Stop")), 10, lambda: main_of(win).text == "Try again")
    check("Stop is heard while finding where you are, and reported as stopped", any(t == "Stopped." for t in labels(win)), "; ".join(labels(win))[-200:])
    check("and no search was made", asked_for == [], str(asked_for))
    held.set()
    win.destroy()
    masjids.search, whereami.locate = real_search, real_locate

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
