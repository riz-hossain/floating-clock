"""The clock on Qt: the card, its menu, the meetings panel, the peek, the tray.

Everything visible is drawn by the shared Pillow code; this file is the host
glue -- timers, mouse handling, window placement -- in the same shape as the
Windows app so the two behave alike.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import webbrowser
from datetime import datetime, timedelta

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets

from .. import (
    alerts, daybar, hovercard, icon as icon_mod, meetings as meetings_mod,
    orgs as orgs_mod, outlook, render, settings as cfg, sounds, themes,
)
from ..app import _span
from ..settings_ui import clock_text
from . import bitmapwindow as bw
from .bitmapwindow import BitmapWindow

log = logging.getLogger(__name__)

CARD_MEETINGS = 3
BAR_LEAD_MINUTES = 60
CLICK_SLOP = 4
FRAME_MS = 16
DEFAULT_TRAVEL_MS = 620


class CardWindow(BitmapWindow):
    """The clock card: drag to move, click the rail, right-click for the menu."""

    def __init__(self, owner: "QtClock") -> None:
        super().__init__(tool=True)
        self.owner = owner
        self._press_origin: QtCore.QPoint | None = None
        self._press_window: QtCore.QPoint | None = None
        self._press_target: str | None = None
        self.setWindowTitle("Floating Clock")

    def enterEvent(self, _event) -> None:   # noqa: N802
        self.owner.hover = True
        self.owner.hover_icon = self.owner.icon_at(self._local_cursor())
        self.owner.paint(force=True)

    def leaveEvent(self, _event) -> None:   # noqa: N802
        self.owner.hover = False
        self.owner.hover_icon = None
        self.owner.hide_hover_card()
        self.owner.paint(force=True)

    def _local_cursor(self) -> tuple[int, int]:
        point = self.mapFromGlobal(QtGui.QCursor.pos())
        return point.x(), point.y()

    def mouseMoveEvent(self, event) -> None:   # noqa: N802
        if self._press_origin is not None and not self.owner.s["lock_position"]:
            delta = event.globalPosition().toPoint() - self._press_origin
            if self._press_target is None or delta.manhattanLength() > CLICK_SLOP * 2:
                self.move(self._press_window + delta)
                self.owner.hide_hover_card()
                return
        kind = self.owner.icon_at(self._local_cursor())
        if kind != self.owner.hover_icon:
            self.owner.hover_icon = kind
            self.owner.paint(force=True)
        self.owner.track_marker(self._local_cursor())

    def mousePressEvent(self, event) -> None:   # noqa: N802
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.owner.open_menu(event.globalPosition().toPoint())
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        self.owner.hide_hover_card()
        self.owner.peek.abort()
        self._press_origin = event.globalPosition().toPoint()
        self._press_window = self.frameGeometry().topLeft()
        icon = self.owner.icon_at(self._local_cursor())
        self._press_target = icon or (
            "meeting" if self.owner.in_calendar_region(self._local_cursor()) else None
        )
        if self._press_target is None:
            self.owner.close_panel()

    def mouseReleaseEvent(self, event) -> None:   # noqa: N802
        if event.button() != QtCore.Qt.MouseButton.LeftButton or self._press_origin is None:
            return
        moved = (event.globalPosition().toPoint() - self._press_origin).manhattanLength()
        target, self._press_target = self._press_target, None
        self._press_origin = None
        if target is not None and moved <= CLICK_SLOP:
            self.owner.activate(target)
            return
        self.owner.snap_and_remember()

    def mouseDoubleClickEvent(self, event) -> None:   # noqa: N802
        if self.owner.icon_at(self._local_cursor()) is None and not self.owner.in_calendar_region(
                self._local_cursor()):
            self.owner.open_settings()

    def wheelEvent(self, event) -> None:   # noqa: N802
        step = 1 if event.angleDelta().y() > 0 else -1
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            self.owner.set_font_size(self.owner.s["font_size"] + step * 3)
        else:
            self.owner.nudge_opacity(step * cfg.OPACITY_STEP)


class Peek:
    """The periodic glide to the centre and back, on Qt timers."""

    def __init__(self, owner: "QtClock") -> None:
        self.owner = owner
        self.active = False
        self._phase = "out"
        self._started = 0.0
        self._home = (0, 0)
        self._timer = QtCore.QTimer()
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._frame)
        self._schedule = QtCore.QTimer()
        self._schedule.setSingleShot(True)
        self._schedule.timeout.connect(self._fire)

    def schedule(self) -> None:
        self._schedule.stop()
        if not self.owner.s["peek_enabled"]:
            return
        from ..peek import Peek as _TkPeek

        delay = _TkPeek.next_slot_delay(self, datetime.now())   # same wall-clock slots
        self._schedule.start(max(1000, int(delay)))

    def _fire(self) -> None:
        self.start()
        self.schedule()

    def start(self) -> None:
        owner = self.owner
        if self.active or owner.s["click_through"] or owner.image is None:
            return
        self._home = tuple(owner.card.frameGeometry().topLeft().toTuple())
        self.active = True
        self._phase = "out"
        self._started = time.perf_counter()
        if owner.s.get("peek_sound", True):
            sounds.play("peek_in", cfg.config_dir())
        self._timer.start()

    def _frame(self) -> None:
        owner = self.owner
        image = owner.image
        if not self.active or image is None:
            self.finish()
            return
        elapsed = (time.perf_counter() - self._started) * 1000
        zoom = float(owner.s["peek_zoom"])
        hold_ms = float(owner.s["peek_hold_seconds"]) * 1000
        travel_ms = max(60.0, float(owner.s.get("peek_travel_ms", DEFAULT_TRAVEL_MS)))
        if self._phase == "out":
            progress = min(1.0, elapsed / travel_ms)
            if progress >= 1.0:
                self._phase, self._started = "hold", time.perf_counter()
        elif self._phase == "hold":
            progress = 1.0
            if elapsed >= hold_ms:
                self._phase, self._started = "back", time.perf_counter()
                if owner.s.get("peek_sound", True):
                    sounds.play("peek_out", cfg.config_dir())
        else:
            progress = 1.0 - min(1.0, elapsed / travel_ms)
            if elapsed >= travel_ms:
                self.finish()
                return
        from ..peek import ease_in_out_cubic

        eased = ease_in_out_cubic(progress)
        scale = 1.0 + (zoom - 1.0) * eased
        width, height = image.size
        frame = image if scale <= 1.001 else image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))), Image.BILINEAR)
        left, top, right, bottom = bw.work_area(owner.card)
        target = ((left + right) / 2 - frame.width / 2, (top + bottom) / 2 - frame.height / 2)
        x = self._home[0] + (target[0] - self._home[0]) * eased
        y = self._home[1] + (target[1] - self._home[1]) * eased
        owner.card.set_image(frame)
        owner.card.move_to(int(x), int(y))
        opacity = owner.target_opacity()
        owner.card.set_opacity(opacity + (1.0 - opacity) * eased)

    def finish(self) -> None:
        if not self.active:
            return
        self.active = False
        self._timer.stop()
        self.owner.card.move_to(*self._home)
        self.owner.paint(force=True)

    def abort(self) -> None:
        self.finish()


class QtClock:
    """The application: state, timers, windows. One per process."""

    def __init__(self, app: QtWidgets.QApplication) -> None:
        self.app = app
        self.s = cfg.load()
        self.renderer = render.Renderer()
        self.scheduler = alerts.Scheduler()
        self.events: list = []
        self.orgs: list = []
        self.org_marks: dict = {}
        self.calendar_error = ""
        self.poller: outlook.CalendarPoller | None = None
        self.image: Image.Image | None = None
        self.geom: dict | None = None
        self.footer_icons: tuple = ()
        self.bar_marks: tuple = ()
        self.hover = False
        self.hover_icon: str | None = None
        self.hover_mark = None
        self.hover_window: BitmapWindow | None = None
        self.panel_window: BitmapWindow | None = None
        self.panel_state = None
        self.settings_dialog = None
        self._paint_key = None
        self._snoozed: list = []
        self._marks_tried: set = set()
        self._work_end = 1.0

        self.card = CardWindow(self)
        self.scale = self.card.devicePixelRatio() if self.card.devicePixelRatio() else 1.0
        self.peek = Peek(self)
        self.tray = self._make_tray()

        self.tick_timer = QtCore.QTimer()
        self.tick_timer.timeout.connect(self.paint)
        self.alert_timer = QtCore.QTimer()
        self.alert_timer.timeout.connect(self._poll_alerts)
        self.calendar_timer = QtCore.QTimer()
        self.calendar_timer.timeout.connect(self._poll_calendar)
        self.panel_timer = QtCore.QTimer()
        self.panel_timer.timeout.connect(self._refresh_panel)

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.paint(force=True)
        self._restore_position()
        self.card.show()
        self.tick_timer.start(200)
        self.alert_timer.start(1000)
        self._start_calendar()
        self.calendar_timer.start(1000)
        self.peek.schedule()

    def quit(self) -> None:
        cfg.save(self.s)
        if self.poller is not None:
            self.poller.stop()
        self.app.quit()

    # --- rendering ------------------------------------------------------------
    def target_opacity(self) -> float:
        if self.hover and self.s["hover_boost"] and self.s["opacity"] < 0.96:
            return 0.99
        return float(self.s["opacity"])

    def _texts(self) -> tuple[str, str, str]:
        now = datetime.now()
        if self.s["use_24h"]:
            clock = now.strftime("%H:%M:%S" if self.s["show_seconds"] else "%H:%M")
            suffix = ""
        else:
            clock = now.strftime("%I:%M:%S" if self.s["show_seconds"] else "%I:%M").lstrip("0")
            suffix = now.strftime("%p").lower()
        show_date = self.s["show_date"] and not self.s["compact"]
        date = now.strftime("%a, %d %b %Y").upper() if show_date else ""
        return clock, suffix, date

    def _footer(self, now: datetime) -> tuple[tuple, tuple, tuple]:
        icons: list = []
        active: list = []
        lines: list = []
        timer = self.scheduler.active_timer(now) if self.s["show_timer"] else None
        armed = any(alarm.enabled for alarm in self.scheduler.alarms)
        watch = self.scheduler.stopwatch
        show_meetings = self.s["show_next_meeting"] and self.s["calendar_enabled"]
        if show_meetings:
            icons.append("meeting")
        icons.extend(("alarm", "timer", "stopwatch"))
        if timer is not None:
            active.append("timer")
            if not self._timer_as_badge():
                lines.append("%s  %s" % (alerts.format_duration(timer.seconds_left(now)),
                                         (timer.label or "TIMER").upper()[:16]))
        if watch.running:
            active.append("stopwatch")
            lines.append("%s  STOPWATCH" % alerts.format_duration(watch.elapsed(now)))
        if armed:
            active.append("alarm")
            if not lines:
                upcoming = self.scheduler.next_alarm(now)
                if upcoming is not None:
                    alarm, when = upcoming
                    lines.append("%s  %s" % (
                        when.strftime("%H:%M" if self.s["use_24h"] else "%I:%M").lstrip("0"),
                        (alarm.label or "ALARM").upper()[:16]))
        if show_meetings:
            if self.calendar_error and not self.events:
                lines.append("CALENDAR UNAVAILABLE")
            else:
                ahead = [e for e in self.events if e.end > now][:CARD_MEETINGS]
                if not ahead:
                    if not self.s["compact"]:
                        lines.append("NO MEETINGS AHEAD")
                else:
                    active.append("meeting")
                    lines.extend(self._meeting_line(e, now) for e in ahead)
        if self.s["compact"]:
            lines = lines[:1]
        return tuple(icons), tuple(active), tuple(lines)

    def _meeting_line(self, event, now: datetime) -> str:
        stamp = event.start.strftime("%H:%M" if self.s["use_24h"] else "%I:%M").lstrip("0")
        day = "" if event.start.date() == now.date() else event.start.strftime("%a ").upper()
        minutes = event.minutes_until(now)
        if event.is_live(now):
            gap = "NOW"
        elif minutes < 60:
            gap = "IN %dM" % max(1, int(round(minutes)))
        elif minutes < 24 * 60:
            gap = "IN %dH %dM" % divmod(int(round(minutes)), 60)
        else:
            gap = "IN %dD" % max(1, int(minutes // (24 * 60)))
        return "%s%s  %s  %s" % (day, stamp, gap, (event.subject or "(no subject)").upper()[:28])

    def _timer_as_badge(self) -> bool:
        return bool(self.s["show_date"]) and not self.s["compact"]

    def _timer_badge(self, now: datetime) -> str:
        if not self._timer_as_badge() or not self.s["show_timer"]:
            return ""
        timer = self.scheduler.active_timer(now)
        if timer is None:
            return ""
        text = alerts.format_duration(timer.seconds_left(now))
        label = (timer.label or "").strip().upper()[:12]
        return "%s  %s" % (text, label) if label else text

    def _bar_state(self, now: datetime) -> tuple[float, tuple]:
        mode = self.s.get("bar_mode", "day")
        if mode == "seconds":
            return (now.second + now.microsecond / 1_000_000) / 60.0, ()
        if mode == "meeting" or not self.s["calendar_enabled"]:
            ahead = [e for e in self.events if e.start > now]
            if not ahead:
                return 0.0, ()
            span = BAR_LEAD_MINUTES * 60
            until = (ahead[0].start - now).total_seconds()
            return max(0.0, min(1.0, 1 - until / span)), ()
        window = daybar.day_window(now, float(self.s["day_start_hour"]))
        self._work_end = daybar.work_end_fraction(
            float(self.s["day_start_hour"]), float(self.s["day_end_hour"]))
        return daybar.progress(now, window), daybar.marks(
            self.events, now, window, colour_for=self.org_colour, label_for=self.org_label)

    def paint(self, force: bool = False) -> None:
        if self.peek.active and not force:
            return
        clock, suffix, date = self._texts()
        now = datetime.now()
        fraction, marks = self._bar_state(now)
        self.bar_marks = marks
        icons, active, lines = self._footer(now)
        badge = self._timer_badge(now)
        geom = self.renderer.measure(self.s, self.scale, clock, suffix, date, lines, icons, badge)
        self.geom, self.footer_icons = geom, icons
        inner = geom["card_w"] - geom["pad_x"] * 2
        bar_px = int(fraction * inner) if geom["has_bar"] else 0
        mark_key = tuple((round(m.position, 4), m.state, m.colour) for m in marks)
        key = (clock, suffix, date, lines, icons, active, bar_px, mark_key, badge,
               round(self._work_end, 4), self.s["theme"], geom["card_w"], geom["card_h"],
               self.hover_icon, self.hover, round(self.scale, 2))
        if not force and key == self._paint_key:
            return
        self._paint_key = key
        image = self.renderer.card(
            self.s, self.scale, clock, suffix, date, fraction,
            footer_lines=lines, footer_icons=icons, active_icons=active,
            hover_icon=self.hover_icon, card_hover=self.hover, bar_marks=marks,
            work_end=self._work_end, badge=badge, premultiply=False,
        )
        self.image = image
        if self.peek.active:
            return
        self.card.set_image(image)
        self.card.set_opacity(self.target_opacity())

    # --- placement ------------------------------------------------------------
    def _restore_position(self) -> None:
        x, y = self.s["x"], self.s["y"]
        left, top, right, bottom = bw.work_area(self.card)
        margin = int(render.SHADOW_MARGIN * self.scale)
        width, height = self.image.size if self.image else (300, 150)
        if x is None or y is None:
            x, y = right - width + margin - 24, top - margin + 24
        x = max(left - margin, min(int(x), right - width + margin))
        y = max(top - margin, min(int(y), bottom - height + margin))
        self.card.move_to(x, y)

    def snap_and_remember(self) -> None:
        rect = self.card.frameGeometry()
        left, top, right, bottom = bw.work_area(self.card)
        margin = int(render.SHADOW_MARGIN * self.scale)
        gap = int(24 * self.scale)
        x, y = rect.left(), rect.top()
        if abs((x + margin) - left) < gap:
            x = left - margin
        elif abs((x + rect.width() - margin) - right) < gap:
            x = right - rect.width() + margin
        if abs((y + margin) - top) < gap:
            y = top - margin
        elif abs((y + rect.height() - margin) - bottom) < gap:
            y = bottom - rect.height() + margin
        self.card.move_to(x, y)
        self.s["x"], self.s["y"] = x, y

    # --- hit testing ----------------------------------------------------------
    def icon_at(self, point: tuple[int, int]) -> str | None:
        if self.geom is None or not self.footer_icons:
            return None
        return render.footer_icon_at(self.geom, self.footer_icons, *point)

    def in_calendar_region(self, point: tuple[int, int]) -> bool:
        if self.geom is None:
            return False
        box = render.calendar_hit_box(self.geom)
        return bool(box) and box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]

    def activate(self, target: str) -> None:
        if target == "alarm":
            self.open_settings("alarms")
        elif target == "timer":
            self.open_settings("timers")
        elif target == "stopwatch":
            self.scheduler.stopwatch.toggle()
            self.scheduler.save()
            self.paint(force=True)
        else:
            self.toggle_meetings()

    # --- hover card -----------------------------------------------------------
    def track_marker(self, point: tuple[int, int]) -> None:
        hit = None
        if self.geom is not None and self.bar_marks and not self.peek.active:
            hit = render.bar_mark_at(self.geom, self.bar_marks, self.scale, *point)
        if hit is None:
            if self.hover_mark is not None:
                self.hide_hover_card()
            return
        mark, (x0, y0, x1, _y1) = hit
        key = (mark.subject, mark.start, mark.end, mark.state, mark.count)
        if key == self.hover_mark:
            return
        self.hover_mark = key
        title, lines = self._mark_detail(mark)
        image = hovercard.render_card(
            themes.get(self.s["theme"]), self.scale, title, lines, render._hex_rgb(mark.colour))
        if self.hover_window is None:
            self.hover_window = BitmapWindow(tool=True, click_through=True)
        self.hover_window.set_image(image)
        origin = self.card.frameGeometry().topLeft()
        margin = int(hovercard.MARGIN * self.scale)
        x = origin.x() + (x0 + x1) / 2 - image.width / 2
        y = origin.y() + y0 - image.height + margin - int(hovercard.GAP * self.scale)
        self.hover_window.move_to(int(x), int(y))
        self.hover_window.show()

    def hide_hover_card(self) -> None:
        self.hover_mark = None
        if self.hover_window is not None:
            self.hover_window.hide()

    def _mark_detail(self, mark) -> tuple[str, list[str]]:
        now = datetime.now()
        title = mark.subject or "Meeting"
        if mark.count > 1:
            title += "  +%d more" % (mark.count - 1)
        when = ""
        if mark.start is not None:
            when = clock_text(mark.start, self.s["use_24h"])
            if mark.end is not None:
                when += " – " + clock_text(mark.end, self.s["use_24h"])
        if mark.state == "live" and mark.end is not None:
            timing = "in progress, %s left" % _span((mark.end - now).total_seconds())
        elif mark.state == "done":
            finished = mark.end or mark.start
            timing = "ended %s" % outlook.describe_ago(
                (now - finished).total_seconds() / 60) if finished else "over"
        elif mark.start is not None:
            timing = "in %s" % _span((mark.start - now).total_seconds())
        else:
            timing = ""
        return title, ["  ·  ".join(p for p in (when, timing) if p), mark.org]

    # --- the meetings panel ----------------------------------------------------
    def toggle_meetings(self) -> None:
        if self.panel_window is not None:
            self.close_panel()
            return
        state = meetings_mod.MeetingsPanel.__new__(meetings_mod.MeetingsPanel)
        state.provider = lambda: (self.events, self.calendar_error) if self.s["calendar_enabled"] \
            else ([], "Calendars are switched off")
        state.org_lookup = self.org_badge
        state.label_lookup = self.org_label
        state.calendar_lookup = self.calendar_label
        state.s = self.s
        state.theme = themes.get(self.s["theme"])
        state.scale = self.scale
        state.rows = []
        state.close_rect = None
        self.panel_state = state
        window = BitmapWindow(tool=True)
        window.mousePressEvent = self._panel_click   # type: ignore[assignment]
        self.panel_window = window
        self._refresh_panel()
        window.show()
        self.panel_timer.start(30_000)

    def _refresh_panel(self) -> None:
        if self.panel_window is None or self.panel_state is None:
            return
        image = self.panel_state._render()
        self.panel_window.set_image(image, premultiplied=True)
        card = self.card.frameGeometry()
        left, top, right, bottom = bw.work_area(self.card)
        card_margin = int(render.SHADOW_MARGIN * self.scale)
        margin = int(meetings_mod.MARGIN * self.scale)
        gap = int(meetings_mod.GAP * self.scale)
        x = card.left() + card_margin - margin
        y = card.bottom() + 1 - card_margin + gap - margin
        if y + image.height - margin > bottom:
            y = card.top() + card_margin - gap - image.height + margin
        x = max(left - margin, min(x, right - image.width + margin))
        self.panel_window.move_to(int(x), int(y))

    def _panel_click(self, event) -> None:
        if self.panel_window is None or self.panel_state is None:
            return
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.close_panel()
            return
        point = (event.position().x(), event.position().y())
        state = self.panel_state
        if state.close_rect and meetings_mod._inside(state.close_rect, *point):
            self.close_panel()
            return
        for rect, url in state.rows:
            if meetings_mod._inside(rect, *point):
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
                self.close_panel()
                return

    def close_panel(self) -> None:
        self.panel_timer.stop()
        if self.panel_window is not None:
            self.panel_window.close()
            self.panel_window = None
        self.panel_state = None

    # --- calendars -------------------------------------------------------------
    def _start_calendar(self) -> None:
        self.poller = outlook.CalendarPoller(
            interval=float(self.s["calendar_poll_seconds"]), use_outlook=False)
        self.poller.enabled = bool(self.s["calendar_enabled"])
        self.reload_orgs()
        self.poller.start()

    def refresh_calendar(self) -> None:
        if self.poller is not None:
            self.poller.enabled = bool(self.s["calendar_enabled"])
            self.poller.feeds = self.orgs
            self.poller.refresh_now()
        self.paint(force=True)

    def reload_orgs(self) -> None:
        self.orgs = orgs_mod.from_config(self.s["calendars"])
        if self.poller is not None:
            self.poller.feeds = self.orgs
        base = cfg.config_dir()
        pending = [o.domain for o in self.orgs if o.domain and o.domain not in self._marks_tried
                   and (not o.icon or o.icon != orgs_mod.icon_path(base, o.domain))]
        if pending:
            self._marks_tried.update(pending)
            threading.Thread(target=self._fetch_marks, name="floating-clock-icons",
                             daemon=True).start()
        else:
            self._load_marks()

    def _fetch_marks(self) -> None:
        try:
            filled = orgs_mod.ensure_icons(self.orgs, cfg.config_dir())
        except Exception:
            log.warning("Could not fetch organisation marks", exc_info=True)
            return
        QtCore.QTimer.singleShot(0, lambda: self._marks_ready(filled))

    def _marks_ready(self, filled: list) -> None:
        self.orgs = filled
        self.s["calendars"] = orgs_mod.to_config(filled)
        if self.poller is not None:
            self.poller.feeds = filled
        self._load_marks()
        self.paint(force=True)
        self._refresh_panel()

    def _load_marks(self) -> None:
        self.org_marks = {}
        for org in self.orgs:
            if not org.icon:
                continue
            try:
                with Image.open(org.icon) as image:
                    self.org_marks[org.id] = image.convert("RGBA").copy()
            except Exception:
                pass

    def _poll_calendar(self) -> None:
        if self.poller is None:
            return
        try:
            while True:
                events, error = self.poller.results.get_nowait()
                self.events, self.calendar_error = events, error
                self._discover_domains([e.domains[0] for e in events
                                        if not e.source and e.domains])
                self.paint(force=True)
                self._refresh_panel()
        except queue.Empty:
            pass

    def _discover_domains(self, domains) -> None:
        if not self.s.get("auto_org_marks", True) or not domains:
            return
        found = orgs_mod.discover(self.orgs, domains)
        if found:
            self.s["calendars"] = orgs_mod.to_config(list(self.orgs) + found)
            self.reload_orgs()

    def org_for(self, source: str):
        for org in self.orgs:
            if org.id == source:
                return org
        return None

    def org_for_event(self, event):
        source = getattr(event, "source", "")
        if source:
            return self.org_for(source)
        for domain in getattr(event, "domains", ()):
            org = orgs_mod.org_for_domain(self.orgs, domain)
            if org is not None:
                return org
        return None

    def org_colour(self, event) -> str:
        org = self.org_for_event(event)
        return org.resolved_colour() if org is not None else ""

    def org_label(self, event) -> str:
        org = self.org_for_event(event)
        return org.name if org is not None else ""

    def org_badge(self, event):
        if not self.orgs:
            return None
        org = self.org_for_event(event)
        if org is None:
            return None
        return (self.org_marks.get(org.id), org.resolved_colour(), org.initials)

    def calendar_label(self, event) -> str:
        feeds = [o for o in self.orgs if o.reads_a_calendar and o.enabled]
        if len(feeds) < 2:
            return ""
        org = self.org_for(getattr(event, "source", ""))
        return org.name if org is not None else "Calendar"

    # --- alerts --------------------------------------------------------------------
    def _poll_alerts(self) -> None:
        now = datetime.now()
        events = self.events if self.s["calendar_enabled"] else ()
        for fired in self.scheduler.due(now, events, lead_minutes=float(self.s["meeting_lead_minutes"])):
            self._notify(fired)
        waiting = []
        for due_at, fired in self._snoozed:
            if now >= due_at:
                self._notify(fired)
            else:
                waiting.append((due_at, fired))
        self._snoozed = waiting
        self.scheduler.forget_old_notifications()

    def _notify(self, fired) -> None:
        kind = {"meeting": "Meeting", "alarm": "Alarm", "timer": "Timer"}.get(fired.kind, "Floating Clock")
        title = "%s: %s" % (kind, fired.title) if fired.title else kind
        body = fired.detail or ("Click to join" if fired.join_url else "")
        if self.tray is not None:
            self.tray.showMessage(title, body, QtWidgets.QSystemTrayIcon.MessageIcon.Information, 15000)
        if self.s["alerts_sound"]:
            QtWidgets.QApplication.beep()
        self.paint(force=True)

    # --- menu, tray, settings ------------------------------------------------------
    def open_menu(self, at: QtCore.QPoint) -> None:
        from .menu import build_menu

        menu = build_menu(self)
        menu.exec(at)

    def _make_tray(self):
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return None
        pil_icon = icon_mod.render_icon(64)
        tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(bw.to_qimage(pil_icon))))
        tray.setToolTip("Floating Clock")
        tray.activated.connect(self._tray_activated)
        from .menu import build_menu

        tray.setContextMenu(build_menu(self, persistent=True))
        tray.show()
        return tray

    def _tray_activated(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_settings()
        elif reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self.peek.start()

    def open_settings(self, page: str = "clock") -> None:
        from .settings import SettingsDialog

        if self.settings_dialog is None or not self.settings_dialog.isVisible():
            self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.show_page(page)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    # --- settings changes ---------------------------------------------------------
    def set_theme(self, name: str) -> None:
        self.s["theme"] = name
        self.renderer.invalidate()
        self.paint(force=True)
        self._refresh_panel()

    def set_font_size(self, size: int) -> None:
        self.s["font_size"] = min(cfg.MAX_FONT, max(cfg.MIN_FONT, int(size)))
        self.paint(force=True)

    def set_font_family(self, family: str) -> None:
        self.s["font_family"] = family
        self.paint(force=True)

    def set_opacity(self, value: float) -> None:
        self.s["opacity"] = min(cfg.MAX_OPACITY, max(cfg.MIN_OPACITY, float(value)))
        self.card.set_opacity(self.target_opacity())

    def nudge_opacity(self, delta: float) -> None:
        self.set_opacity(self.s["opacity"] + delta)

    def toggle_setting(self, key: str, repaint: bool = True) -> None:
        self.s[key] = not bool(self.s[key])
        if key == "topmost":
            self.card.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, bool(self.s[key]))
            self.card.show()
        if key == "peek_enabled":
            self.peek.schedule()
        if key == "calendar_enabled":
            self.refresh_calendar()
        if repaint:
            self.renderer.invalidate()
            self.paint(force=True)
