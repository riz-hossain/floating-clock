"""Offscreen: the Qt tray menu's "Stop <prayer>'s adhan", and what the warning notification says.

check-routines.py already drives Runner.on_warn/stoppable/stop_now with fakes standing in for the
host; this drives the real Qt pieces those fakes stood in for -- qt/menu.py's stoppable()-gated
item, and qt/clock.py's _adhan_warning/_adhan_detail/stop_adhan. A tray balloon has no button of
its own, so there is no click to drive here, only that the menu reflects what is pending and that
Stop reaches stop_now() -- the same shape as the Tk side's check-adhan-warning.py, minus the toast.

    PYTHONPATH=<folder holding floating_clock> QT_QPA_PLATFORM=offscreen \\
        python packaging/check-qt-adhan-warning.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from PySide6 import QtWidgets  # noqa: E402

from floating_clock import prayer as prayer_mod  # noqa: E402
from floating_clock import routines  # noqa: E402
from floating_clock.qt import menu as menu_mod  # noqa: E402
from floating_clock.qt.clock import QtClock  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


def labels(qmenu) -> list:
    if isinstance(qmenu, str):    # safe() caught an exception building it; treat as "no labels"
        return [qmenu]
    return [a.text() for a in qmenu.actions() if not a.isSeparator()]


def safe(fn, *a, **kw):
    """Call fn, or say what it raised -- so a regression that removes what this is checking
    fails a check here instead of crashing the whole run before it can be reported."""
    try:
        return fn(*a, **kw)
    except Exception as exc:                        # noqa: BLE001
        return "!! raised %s: %s" % (exc.__class__.__name__, exc)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
clock = QtClock(app)

try:
    print("the tray menu: Stop only when something is pending")
    menu = safe(menu_mod.build_menu, clock)
    check("with nothing due, no Stop item", not any("adhan" in t for t in labels(menu)), str(labels(menu)))

    fajr = prayer_mod.Prayer("Fajr", datetime.now() + timedelta(minutes=10))
    clock.routines._remember(fajr, datetime.now())
    menu = safe(menu_mod.build_menu, clock)
    check("once something is pending, it is offered, first, by the prayer's own name",
          labels(menu)[:1] == ["Stop Fajr's adhan"], str(labels(menu)[:3]))

    notified: list = []
    clock._notify = lambda fired: notified.append(fired)
    safe(clock.stop_adhan)
    check("stop_adhan() reaches stop_now()", clock.routines.stoppable() is None)
    menu = safe(menu_mod.build_menu, clock)
    check("and the menu no longer offers it", not any("adhan" in t for t in labels(menu)), str(labels(menu)))

    print("_adhan_warning(): a native notification, informational (no button on a tray balloon)")
    clock.routines._remember(fajr, datetime.now())
    clock.s["prayer_cast_device"] = "Kitchen speaker"
    clock._adhan_warning(fajr, [routines.CAST, routines.LOCAL], 8)
    app.processEvents()        # _adhan_warning posts to the UI thread via ui.post; run that turn
    check("posts one notification", len(notified) == 1, str(notified))
    check("headed by the prayer's own name", notified[0].title == "Fajr adhan", notified[0].title)
    check("saying where, roughly when, and how to stop it since there is no button here",
          notified[0].detail == "Playing here and on Kitchen speaker, in about 8s. "
          "Right-click the tray icon to stop it.", notified[0].detail)

    print("what it says, kind by kind")
    check("a routine alone", clock._adhan_detail([routines.HOOK]) == "Calling a routine")
    check("local alone", clock._adhan_detail([routines.LOCAL]) == "Playing here")
    del clock.s["prayer_cast_device"]
    check("a speaker with no name saved", clock._adhan_detail([routines.CAST]) == "Playing on the speaker")
    clock.s["prayer_cast_device"] = "Kitchen speaker"
    check("local and a routine together", clock._adhan_detail([routines.LOCAL, routines.HOOK])
          == "Playing here, and calling a routine")
except Exception:
    import traceback
    traceback.print_exc()
    failures.append("an exception")

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("the Qt tray menu offers to stop a pending adhan, and the warning notification says why")
