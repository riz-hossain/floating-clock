"""A slider must be clicked before the wheel touches it.

Windows only: it drives the real Tk widget (never mapped for the unit checks; fully
transparent but mapped, so Windows will actually deliver input, for the one that
scrolls a real settings page). Run it after changing widgets.Slider or how the
settings window routes the wheel.

Every settings page is a column of sliders (opacity, font size, snooze lengths...)
inside one scrolling frame. Before this, a Slider took the wheel the moment the
pointer crossed it -- so scrolling down the page with the wheel silently changed
whichever slider happened to be under the pointer at the time, instead of scrolling
past it. It now has to be clicked first, and moving off it again asks for a fresh
click before the wheel touches it once more.

    set PYTHONPATH=<the folder holding floating_clock>
    python packaging/check-slider.py
"""
import sys
import tkinter as tk

from floating_clock import palette as pal_mod
from floating_clock import widgets as w
from floating_clock.settings_ui import SettingsUI

SettingsUI.settings_visible = False

from floating_clock.app import FloatingClock

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


class Wheel:
    """A stand-in <MouseWheel> event: all Slider._wheel reads off it is .delta."""

    def __init__(self, delta: int) -> None:
        self.delta = delta


class At:
    """A stand-in <Button-1> event: all Slider._press reads off it is .x."""

    def __init__(self, x: int) -> None:
        self.x = x


app = FloatingClock()

try:
    print("the widget on its own")
    ui = w.Ui(app.root, pal_mod.resolve("dark", (127, 40, 255)), 1.0)
    # app.root's own background is a raw Tk system-colour name ("SystemButtonFace"), which
    # Pillow cannot render -- give the slider a real hex colour of its own instead.
    var = tk.IntVar(master=app.root, value=5)
    slider = w.Slider(app.root, ui, 0, 10, var, integer=True, bg=ui.p.window)
    slider.pack()
    app.root.update_idletasks()

    check("starts unarmed", not slider._armed)
    result = slider._wheel(Wheel(120))
    check("hovering and wheeling, with no click, does not consume the event",
          result is None, repr(result))
    check("and does not change the value", var.get() == 5, str(var.get()))

    slider._press(At(10))
    check("a click arms it", slider._armed)
    before = var.get()
    result = slider._wheel(Wheel(120))
    check("wheeling it now is consumed", result == "break", repr(result))
    check("and changes the value", var.get() == before + slider.step,
          "%s -> %s" % (before, var.get()))
    slider._release(None)
    check("releasing the button leaves it armed -- no need to hold it down to keep nudging",
          slider._armed)
    before = var.get()
    slider._wheel(Wheel(120))
    check("so wheeling again, still hovering, still works",
          var.get() == before + slider.step, "%s -> %s" % (before, var.get()))

    slider._leave(None)
    check("moving off it disarms it", not slider._armed)
    before = var.get()
    result = slider._wheel(Wheel(120))
    check("wheeling it again, without a fresh click, does nothing",
          result is None and var.get() == before, "%s -> %s" % (before, var.get()))

    at_top = tk.IntVar(master=app.root, value=10)
    top_slider = w.Slider(app.root, ui, 0, 10, at_top, integer=True, bg=ui.p.window)
    top_slider._press(At(999))          # far past the right edge: pins it at its top end
    result = top_slider._wheel(Wheel(120))
    check("armed at its top end, a further wheel-up is still consumed even though nothing moves",
          result == "break" and at_top.get() == 10, repr((result, at_top.get())))

    print("a real settings page")
    app.open_settings()
    app._show_page("clock")
    app.root.update_idletasks()
    win = app._settings_win
    win.attributes("-alpha", 0.0)
    win.wm_geometry("420x220")     # smaller than the page, so there is somewhere to scroll to
    win.deiconify()
    win.update()

    def find(node, kind):
        for child in node.winfo_children():
            if isinstance(child, kind):
                return child
            hit = find(child, kind)
            if hit is not None:
                return hit
        return None

    page_slider = find(win, w.Slider)
    scroll = app._scroll
    check("the page is short enough here that it can actually be scrolled",
          scroll.canvas.yview() != (0.0, 1.0), str(scroll.canvas.yview()))

    before_value = page_slider.variable.get()
    before_scroll = scroll.canvas.yview()
    page_slider.event_generate("<MouseWheel>", delta=-120)
    win.update()
    check("scrolling the wheel over a slider that was never clicked moves the page",
          scroll.canvas.yview() != before_scroll, str(scroll.canvas.yview()))
    check("and leaves that slider's own value alone",
          page_slider.variable.get() == before_value,
          "%s -> %s" % (before_value, page_slider.variable.get()))

    # Mid-track, not an edge: a click right at one end would pin the value there, and the
    # wheel could then only push it the one way that is already blocked, with nothing to see.
    mid_x, mid_y = page_slider.winfo_width() // 2, page_slider.winfo_height() // 2
    page_slider.event_generate("<Button-1>", x=mid_x, y=mid_y)
    page_slider.event_generate("<ButtonRelease-1>", x=mid_x, y=mid_y)
    win.update()
    clicked_value = page_slider.variable.get()
    clicked_scroll = scroll.canvas.yview()
    page_slider.event_generate("<MouseWheel>", delta=-120)
    win.update()
    check("once it has been clicked, the same wheel changes it instead",
          page_slider.variable.get() != clicked_value,
          "%s -> %s" % (clicked_value, page_slider.variable.get()))
    check("and the page does not also scroll on that turn of the wheel",
          scroll.canvas.yview() == clicked_scroll,
          "%s -> %s" % (clicked_scroll, scroll.canvas.yview()))

    page_slider.event_generate("<Leave>")
    win.update()
    left_value = page_slider.variable.get()
    left_scroll = scroll.canvas.yview()
    page_slider.event_generate("<MouseWheel>", delta=-120)
    win.update()
    check("moving off it and wheeling again, without a fresh click, scrolls the page once more",
          scroll.canvas.yview() != left_scroll, str(scroll.canvas.yview()))
    check("not the slider that was clicked earlier in the session",
          page_slider.variable.get() == left_value,
          "%s -> %s" % (left_value, page_slider.variable.get()))
    win.destroy()
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
print("a slider needs a click before the wheel can touch it")
