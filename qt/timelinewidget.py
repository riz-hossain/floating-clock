"""The timeline of a masjid check, drawn for the Mac and Linux window.

Everything about where things go is decided in timelineview.py, which this and the
Windows window share. This asks the timeline for a snapshot a few times a second and
paints what the layout says: a bar, a stopwatch, and a line for each thing the clock
is doing or has done, with what came of it.

It polls on a timer that lives on the UI thread. Nothing is ever posted to it from
the worker that does the reading, which is the hop that has gone wrong here before
(see qt/ui.py).
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from .. import palette as pal
from .. import timeline, timelineview as view

TICK_MS = 120          # how often it looks: fast enough that the spinner turns and the seconds run


class _Canvas(QtWidgets.QWidget):
    """The picture itself, as tall as it needs to be."""

    def __init__(self, owner: "TimelineWidget") -> None:
        super().__init__()
        self.owner = owner
        self.drawn: dict = {"height": 0, "items": [], "running": None}
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(self.owner.p.field))
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        for item in self.drawn["items"]:
            getattr(self, "_" + item["op"])(painter, item)
        painter.end()

    # --- what the layout may ask for --------------------------------------------------------
    def _colour(self, role: str) -> QtGui.QColor:
        p = self.owner.p
        return QtGui.QColor({
            view.FG: p.fg, view.SOFT: p.fg_soft, view.MUTED: p.muted, view.DIM: p.disabled,
            view.GOOD: p.success, view.BAD: p.danger,
        }.get(role, p.fg))

    def _rail(self, painter: QtGui.QPainter, item: dict) -> None:
        pen = QtGui.QPen(QtGui.QColor(self.owner.p.control_border), 2)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(item["x"], item["y1"]), QtCore.QPointF(item["x"], item["y2"]))

    def _bar(self, painter: QtGui.QPainter, item: dict) -> None:
        p = self.owner.p
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(p.trough))
        painter.drawRoundedRect(QtCore.QRectF(x, y, w, h), h / 2, h / 2)
        fill = {view.GOOD: p.success, view.BAD: p.danger, view.MUTED: p.muted}.get(item["role"], p.accent)
        painter.setBrush(QtGui.QColor(fill))
        width = max(h, w * item["fraction"]) if item["fraction"] > 0 else 0
        if width:
            painter.drawRoundedRect(QtCore.QRectF(x, y, width, h), h / 2, h / 2)

    def _text(self, painter: QtGui.QPainter, item: dict) -> None:
        painter.setFont(self.owner.font_for(item["bold"], item["small"]))
        painter.setPen(self._colour(item["role"]))
        width = self.owner.measure(item["text"], item["bold"], item["small"]) + 6
        left = item["x"] - width if item["anchor"] == "e" else item["x"]
        flags = QtCore.Qt.AlignmentFlag.AlignVCenter | (
            QtCore.Qt.AlignmentFlag.AlignRight if item["anchor"] == "e" else QtCore.Qt.AlignmentFlag.AlignLeft)
        painter.drawText(QtCore.QRectF(left, item["y"], width, item["h"]), int(flags), item["text"])

    def _icon(self, painter: QtGui.QPainter, item: dict) -> None:
        p = self.owner.p
        d, x, y, state = item["d"], item["x"], item["y"], item["state"]
        box = QtCore.QRectF(x + 1, y + 1, d - 2, d - 2)
        stroke = max(1.6, d / 9.0)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        if state == timeline.DONE or state == timeline.FAILED:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(p.success if state == timeline.DONE else p.danger))
            painter.drawEllipse(box)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), stroke, QtCore.Qt.PenStyle.SolidLine,
                                      QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
            if state == timeline.DONE:
                path = QtGui.QPainterPath(QtCore.QPointF(x + d * 0.28, y + d * 0.53))
                path.lineTo(x + d * 0.44, y + d * 0.68)
                path.lineTo(x + d * 0.73, y + d * 0.34)
                painter.drawPath(path)
            else:
                painter.drawLine(QtCore.QPointF(x + d * 0.34, y + d * 0.34), QtCore.QPointF(x + d * 0.66, y + d * 0.66))
                painter.drawLine(QtCore.QPointF(x + d * 0.66, y + d * 0.34), QtCore.QPointF(x + d * 0.34, y + d * 0.66))
        elif state == timeline.RUNNING:
            painter.setPen(QtGui.QPen(QtGui.QColor(pal.mix(p.accent, p.field, 0.65)), stroke))
            painter.drawEllipse(box)
            painter.setPen(QtGui.QPen(QtGui.QColor(p.accent), stroke + 0.6, QtCore.Qt.PenStyle.SolidLine,
                                      QtCore.Qt.PenCapStyle.RoundCap))
            painter.drawArc(box, int(-item["phase"] * 45 * 16), 110 * 16)
        elif state == timeline.SKIPPED:
            painter.setPen(QtGui.QPen(QtGui.QColor(p.muted), stroke * 0.8))
            painter.drawEllipse(box)
            painter.drawLine(QtCore.QPointF(x + d * 0.32, y + d / 2), QtCore.QPointF(x + d * 0.68, y + d / 2))
        else:                                                         # waiting
            painter.setPen(QtGui.QPen(QtGui.QColor(p.disabled), stroke * 0.8))
            painter.drawEllipse(box)


class TimelineWidget(QtWidgets.QScrollArea):
    """A scrolling picture of a Timeline. `watch(line)` starts it, `stop()` draws it a last time."""

    def __init__(self, p: pal.Palette, parent=None) -> None:
        super().__init__(parent)
        self.p = p
        self.line: timeline.Timeline | None = None
        self.phase = 0
        self.following = True                 # keeps the step that is running in view, until the person scrolls
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QScrollArea { background: %(field)s; border: 1px solid %(border)s; }"
            "QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }"
            "QScrollBar::handle:vertical { background: %(handle)s; border-radius: 3px; min-height: 28px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
            % dict(field=p.field, border=p.field_border, handle=p.control_border))
        self.canvas = _Canvas(self)
        self.setWidget(self.canvas)
        self.setAccessibleName("What the clock is doing to find this masjid's times")
        self.verticalScrollBar().actionTriggered.connect(self._scrolled_by_person)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.tick)

    # --- measuring, for the layout ------------------------------------------------------------
    def font_for(self, bold: bool, small: bool) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        if small:
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.9) if font.pointSizeF() > 0 else 9.0)
        font.setBold(bold)
        return font

    def measure(self, text: str, bold: bool = False, small: bool = False) -> int:
        return QtGui.QFontMetrics(self.font_for(bold, small)).horizontalAdvance(text)

    def metrics(self) -> view.Metrics:
        normal = QtGui.QFontMetrics(self.font_for(False, False))
        small = QtGui.QFontMetrics(self.font_for(False, True))
        # the width is what there is with the scroll bar showing, whether it is or not: if it
        # were the width there is without, a bar appearing would narrow the view, re-wrap the
        # text, change the height, and take the bar away again
        room = self.width() - 2 - self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)
        return view.Metrics(width=max(160, room), line=normal.height() + 8,
                            small_line=small.height() + 3, icon=max(16, normal.height() + 2),
                            icon_small=max(12, normal.height() - 2))

    # --- following one timeline ---------------------------------------------------------------
    def watch(self, line: timeline.Timeline) -> None:
        self.line = line
        self.following = True
        self.phase = 0
        self.verticalScrollBar().setValue(0)
        self.timer.start()
        self.tick()

    def stop(self) -> None:
        """It is over: stop the clock, and draw it as it now is."""
        self.timer.stop()
        self.tick()

    def tick(self) -> None:
        if self.line is None:
            return
        snap = self.line.snapshot()
        self.phase = (self.phase + 1) % 8
        self.canvas.drawn = view.layout(snap, self.metrics(), self.measure, self.phase)
        self.canvas.setMinimumHeight(self.canvas.drawn["height"])
        self.canvas.update()
        self.setAccessibleDescription("%s. %s" % (view.headline(snap)[0], snap["now"]))
        if self.following and self.canvas.drawn["running"] is not None:
            self._show(self.canvas.drawn["running"])
        if not snap["busy"]:
            self.timer.stop()

    def _show(self, span: tuple) -> None:
        bar = self.verticalScrollBar()
        room = self.viewport().height()
        top, bottom = span
        if top < bar.value() or bottom > bar.value() + room:
            bar.setValue(int(max(0, top - room / 3)))

    def _scrolled_by_person(self, _action) -> None:
        self.following = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.line is not None:
            self.tick()
