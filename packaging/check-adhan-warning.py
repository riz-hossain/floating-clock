"""Windows only: the real warning toast and Stop, and the tray menu's "Stop <prayer>'s adhan".

check-routines.py already drives Runner.on_warn/stoppable/stop_now with fakes standing in for the
host; this drives the real Tk pieces those fakes stood in for -- toast.py's "adhan" kind (real
button hit-testing, the same code path a click uses), and app.py's menu/toast wiring on top of a
real FloatingClock. Never mapped, so nothing appears on screen.

    set PYTHONPATH=<the folder holding floating_clock>
    python packaging/check-adhan-warning.py
"""
from __future__ import annotations

import sys
import tkinter as tk
from datetime import datetime, timedelta

from floating_clock import alerts
from floating_clock import prayer as prayer_mod
from floating_clock import routines
from floating_clock import toast as toast_mod
from floating_clock.settings_ui import SettingsUI

SettingsUI.settings_visible = False

from floating_clock.app import FloatingClock  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


def click(popup, key: str) -> None:
    """Press the button with this key, the way a real click does: find its rect, hit-test it."""
    button = next(b for b in popup.buttons if b.key == key)
    bx0, by0, bx1, by1 = button.rect
    cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2

    import floating_clock.win32util as w32
    real_rect, real_pos = w32.window_rect, w32.cursor_pos
    w32.window_rect = lambda hwnd: (0, 0, 0, 0)
    w32.cursor_pos = lambda: (cx, cy)
    try:
        popup._on_click(type("E", (), {"x": cx, "y": cy})())
    finally:
        w32.window_rect, w32.cursor_pos = real_rect, real_pos


root = tk.Tk()
root.withdraw()

try:
    print("toast.py: the adhan kind on its own")
    stopped: list = []
    popup = toast_mod.Toast(
        root, alerts.Fired(kind="adhan", title="Fajr adhan", detail="Playing here, in about 10s.",
                           key="adhan:x"),
        "dark", 1.0, on_close=lambda p: None, on_snooze=lambda f, m: None,
        on_stop=lambda f: stopped.append(f.title),
    )
    try:
        check("it has exactly one button, Stop, and it is the primary one",
              [b.key for b in popup.buttons] == ["stop"] and popup.buttons[0].primary)
        click(popup, "stop")
        check("clicking it calls on_stop with the toast's own Fired", stopped == ["Fajr adhan"], str(stopped))
        check("and closes the toast", popup._closed)
    finally:
        popup.close()

    print("app.py: the real warning, end to end")
    app = FloatingClock()
    try:
        app.s["prayer_cast_device"] = "Kitchen speaker"
        check("with nothing due, the tray menu offers no Stop", not any(
            "adhan" in (getattr(i, "label", "") or "") for i in app._menu_items()))

        fajr = prayer_mod.Prayer("Fajr", datetime.now() + timedelta(minutes=10))
        app.routines._remember(fajr, datetime.now())   # what the real _warn_due does before calling on_warn
        app._adhan_warning(fajr, [routines.CAST, routines.LOCAL], 8)
        app.root.update()          # runs the root.after(0, ...) _adhan_warning schedules
        check("a real toast appeared", len(app._toasts) == 1, str(app._toasts))
        popup = app._toasts[-1]
        check("headed by the prayer's own name", popup.fired.title == "Fajr adhan", popup.fired.title)
        check("saying where, and roughly when",
              popup.fired.detail == "Playing here and on Kitchen speaker, in about 8s.", popup.fired.detail)

        items = app._menu_items()
        check("and now the tray menu offers to stop it, first in the list",
              getattr(items[0], "label", "") == "Stop Fajr's adhan", getattr(items[0], "label", ""))

        click(popup, "stop")
        check("clicking the toast's Stop reaches stop_now()", app.routines.stoppable() is None)
        check("and the tray menu no longer offers it", not any(
            "adhan" in (getattr(i, "label", "") or "") for i in app._menu_items()))

        # The menu item alone, reached without a toast at all -- warnings can be switched off
        # and it is still there for whatever is actually playing.
        app.routines._remember(fajr, datetime.now())
        items = app._menu_items()
        check("the menu offers Stop for a prayer that was never warned about, just fired",
              getattr(items[0], "label", "") == "Stop Fajr's adhan")
        app._stop_adhan()
        check("and the menu's own Stop reaches it too", app.routines.stoppable() is None)

        print("what it says, kind by kind")
        check("a routine alone", app._adhan_detail([routines.HOOK]) == "Calling a routine")
        check("local alone", app._adhan_detail([routines.LOCAL]) == "Playing here")
        del app.s["prayer_cast_device"]
        check("a speaker with no name saved", app._adhan_detail([routines.CAST]) == "Playing on the speaker")
        app.s["prayer_cast_device"] = "Kitchen speaker"
        check("local and a routine together", app._adhan_detail([routines.LOCAL, routines.HOOK])
              == "Playing here, and calling a routine")
    finally:
        app.root.destroy()
except Exception:
    import traceback
    traceback.print_exc()
    failures.append("an exception")
finally:
    try:
        root.destroy()
    except Exception:
        pass

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("a warning ahead of the adhan, and Stop, reach the real toast and the real tray menu")
