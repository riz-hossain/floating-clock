"""A Qt slider must be clicked before the wheel touches it.

QSlider's own wheelEvent responds from the moment the pointer is over it, focus or no
focus -- so scrolling down a settings page full of sliders (opacity, font size...)
silently changes whichever one the pointer happens to be crossing, instead of
scrolling past it. qt.settings._Slider fixes that: unarmed, it ignores the wheel
(which Qt then offers to the parent QScrollArea instead); a click arms it until the
pointer leaves again.

Offscreen, so it runs anywhere, including CI.

    PYTHONPATH=<folder holding floating_clock> QT_QPA_PLATFORM=offscreen \\
        python packaging/check-qt-slider.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from floating_clock.qt.settings import _Slider  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


def wheel(delta: int = 120) -> QtGui.QWheelEvent:
    """A fresh <MouseWheel>-equivalent: acceptedness is per-instance, so each check gets its own."""
    point = QtCore.QPointF(5, 5)
    return QtGui.QWheelEvent(point, point, QtCore.QPoint(0, 0), QtCore.QPoint(0, delta),
                             QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                             QtCore.Qt.ScrollPhase.NoScrollPhase, False)


def press() -> QtGui.QMouseEvent:
    point = QtCore.QPointF(5, 5)
    return QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonPress, point, QtCore.Qt.MouseButton.LeftButton,
                             QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    print("the widget on its own")
    slider = _Slider(QtCore.Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(50)

    check("starts unarmed", not slider._engaged)
    ev = wheel(120)
    slider.wheelEvent(ev)
    check("hovering and wheeling, with no click, ignores the event",
          not ev.isAccepted(), "accepted=%s" % ev.isAccepted())
    check("and does not change the value", slider.value() == 50, str(slider.value()))

    slider.mousePressEvent(press())
    check("a click arms it", slider._engaged)
    before = slider.value()
    ev = wheel(-120)
    slider.wheelEvent(ev)
    check("wheeling it now is accepted", ev.isAccepted(), "accepted=%s" % ev.isAccepted())
    check("and changes the value", slider.value() != before, "%s -> %s" % (before, slider.value()))

    slider.leaveEvent(QtCore.QEvent(QtCore.QEvent.Type.Leave))
    check("moving off it disarms it", not slider._engaged)
    before = slider.value()
    ev = wheel(-120)
    slider.wheelEvent(ev)
    check("wheeling it again, without a fresh click, does nothing",
          not ev.isAccepted() and slider.value() == before,
          "accepted=%s %s -> %s" % (ev.isAccepted(), before, slider.value()))

    print("inside a settings page")
    from floating_clock.qt.clock import QtClock
    from floating_clock.qt.settings import SettingsDialog

    clock = QtClock(app)
    dialog = SettingsDialog(clock)
    dialog.show()
    app.processEvents()

    scroll = dialog.stack.widget(0)          # PAGES[0] is "clock", and nav starts there
    # The dialog itself has a generous minimum size the clock page fits inside without
    # scrolling; pin the scroll area on its own well below that, so there is somewhere to
    # scroll to, the way a smaller monitor or a taller page would give it for real.
    scroll.setFixedHeight(120)
    app.processEvents()
    page_slider = dialog.findChild(_Slider)
    check("the Size slider on the clock page is built as the click-armed kind",
          page_slider is not None, "found a plain QSlider instead" if dialog.findChild(QtWidgets.QSlider)
          else "found no slider at all")

    if page_slider is not None:
        bar = scroll.verticalScrollBar()
        check("this page is tall enough here to actually scroll",
              bar.maximum() > bar.minimum(), "range 0..%d" % bar.maximum())
        check("nothing has been clicked on this dialog's slider yet", not page_slider._engaged)

        before_value = page_slider.value()
        ev = wheel(-120)
        page_slider.wheelEvent(ev)
        check("a slider that was never clicked ignores the wheel here too",
              not ev.isAccepted() and page_slider.value() == before_value,
              "accepted=%s %s -> %s" % (ev.isAccepted(), before_value, page_slider.value()))

        page_slider.mousePressEvent(press())
        clicked_value = page_slider.value()
        ev = wheel(-120)
        page_slider.wheelEvent(ev)
        check("once clicked, the same wheel changes it",
              ev.isAccepted() and page_slider.value() != clicked_value,
              "accepted=%s %s -> %s" % (ev.isAccepted(), clicked_value, page_slider.value()))

        page_slider.leaveEvent(QtCore.QEvent(QtCore.QEvent.Type.Leave))
        left_value = page_slider.value()
        ev = wheel(-120)
        page_slider.wheelEvent(ev)
        check("moving off it and wheeling again, without a fresh click, ignores it once more",
              not ev.isAccepted() and page_slider.value() == left_value,
              "accepted=%s %s -> %s" % (ev.isAccepted(), left_value, page_slider.value()))
    dialog.close()
except Exception:
    import traceback
    traceback.print_exc()
    failures.append("an exception")

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("a Qt slider needs a click before the wheel can touch it")
