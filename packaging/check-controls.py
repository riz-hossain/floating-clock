"""Assert that every settings control is actually on screen.

Windows only: it drives the Tk host. Run it after changing the settings
layout.

    set PYTHONPATH=<the folder holding floating_clock>
    python packaging/check-controls.py

Tk hands out space in packing order and silently leaves a control unmapped
when a row has run out, so a button can exist, compile, build and ship while
being invisible -- which is exactly what happened to Find my masjid in
1.10.0. Checking that a widget exists does not catch it; checking that it is
mapped and wider than a couple of pixels does.
"""
import sys, traceback
from floating_clock.settings_ui import SettingsUI, PAGES
SettingsUI.settings_visible = False
from floating_clock.app import FloatingClock
import floating_clock.widgets as w

WATCHED = (w.Button, w.Field, w.Dropdown, w.Switch, w.Segmented)

app = FloatingClock()
rc = 0
try:
    app.open_settings()
    app.root.update_idletasks()
    for page_key, *_ in PAGES:
        app._show_page(page_key)
        app.root.update_idletasks()
        app._settings_win.update_idletasks()
        page = app._pages[page_key]
        bad, count = [], 0

        def walk(node):
            global count
            for child in node.winfo_children():
                if isinstance(child, WATCHED):
                    count += 1
                    label = getattr(child, "text", child.__class__.__name__)
                    if not child.winfo_ismapped() or child.winfo_width() <= 2:
                        bad.append("%s(%s) w=%d" % (child.__class__.__name__,
                                                    str(label)[:18],
                                                    child.winfo_width()))
                walk(child)
        walk(page)
        print("%s %-10s %2d controls, %d hidden %s"
              % ("OK  " if not bad else "BAD ", page_key, count, len(bad),
                 "; ".join(bad) if bad else ""))
        if bad:
            rc = 1
except Exception:
    traceback.print_exc(); rc = 1
finally:
    try: app.root.destroy()
    except Exception: pass
sys.exit(rc)
