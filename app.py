"""The floating clock window: input, menu, settings dialog, lifecycle.

Tk owns the window handle, the message loop and every mouse/keyboard binding,
but nothing Tk draws is ever shown -- the window is layered, so its pixels come
from the Pillow-rendered card pushed through UpdateLayeredWindow.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

from . import (
    hovercard, popupmenu,
    alerts, daybar, meetings as meetings_mod, orgs as orgs_mod, outlook,
    peek as peek_mod, prayer as prayer_mod, render, routines as routines_mod,
    settings as cfg, themes, toast as toast_mod, tray as tray_mod,
    win32util as w32,
)
from . import __version__, logsetup
from .settings_ui import SettingsUI
from .timetext import clock_text, span as _span

log = logging.getLogger(__name__)

# The tray icon's hover text: the name, and which build is running.
TRAY_TOOLTIP = "Floating Clock %s" % __version__

# A clock that could not read its settings at startup restarts itself rather
# than running on the defaults -- Alexa triggers off -- until someone notices.
# A fresh start is what fixed it by hand after the update of 9 September.
# Capped, so a folder that never opens cannot become a restart loop.
SETTINGS_RETRY_S = 60
SETTINGS_MAX_RESTARTS = 5
RESTART_ARG = "--restart-attempt"
WAIT_ARG = "--wait-for-pid"          # read by __main__
RELAUNCH_FLAGS = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                  | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))


def restart_attempt(argv) -> int:
    """How many times this clock has already restarted itself: 0 normally."""
    for arg in argv:
        if str(arg).startswith(RESTART_ARG + "="):
            try:
                return max(0, int(str(arg).split("=", 1)[1]))
            except ValueError:
                return 0
    return 0


def should_restart_for_settings(load_failed: bool, attempt: int,
                                limit: int = SETTINGS_MAX_RESTARTS) -> bool:
    return bool(load_failed) and attempt < limit


def relaunch_command(argv, attempt: int, pid: int, executable: str | None = None,
                     frozen: bool | None = None) -> list[str]:
    """The command line for a fresh copy of this clock.

    Keeps whatever else the clock was started with, replaces the restart
    count, and tells the new copy which process to wait out first.
    """
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    keep = [str(arg) for arg in list(argv)[1:]
            if not str(arg).startswith((RESTART_ARG + "=", WAIT_ARG + "="))]
    base = [executable] if frozen else [executable, "-m", "floating_clock"]
    return base + keep + ["%s=%d" % (RESTART_ARG, attempt), "%s=%d" % (WAIT_ARG, pid)]

HOTKEY_CLICK_THROUGH = 1
HOTKEY_QUIT = 2
HOTKEY_SHOW = 3

SNAP_DISTANCE = 24
FADE_STEPS = 10
FADE_INTERVAL = 18

CARD_MEETINGS = 3        # meeting lines under the clock
# A loop that hits an error logs it at most this often, and carries on.
LOOP_ERROR_EVERY_S = 600.0
# The clock writes one line saying it is alive, and what it will do next,
# this often -- so a stopped loop shows as a gap rather than as nothing.
HEARTBEAT_EVERY = timedelta(hours=1)
# How often the alert loop asks whether the masjid's calendar is due a look.
BAR_LEAD_MINUTES = 60    # run-up over which the progress bar fills
CLICK_SLOP = 4          # px of travel still counted as a click

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command() -> str:
    """Command line that relaunches this app, frozen or from source."""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    launcher = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(launcher):
        launcher = sys.executable
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return '"%s" -m floating_clock' % launcher if package_root else '"%s"' % launcher


def startup_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, cfg.APP_NAME)
        return True
    except (ImportError, OSError):
        return False


def set_startup(enabled: bool, command: str) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, cfg.APP_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, cfg.APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except (ImportError, OSError):
        return False


class FloatingClock(SettingsUI):
    def __init__(self) -> None:
        self.s = cfg.load()
        self.renderer = render.Renderer()
        self._drag = None
        self._paint_key = None
        self._scale = 1.0
        self._hover = False
        self._fade = 0.0
        self._settings_win = None
        self._closing = False
        self.scheduler = alerts.Scheduler()
        self.events: list[outlook.Event] = []
        self.orgs: list = []
        self.org_marks: dict = {}
        self.calendar_error = ""
        self.poller: outlook.CalendarPoller | None = None
        self._toasts: list[toast_mod.Toast] = []
        self._panel: meetings_mod.MeetingsPanel | None = None
        self._press_target = None
        self._press_origin = None
        self._hover_icon: str | None = None   # rail icon under the pointer
        self._hover_mark = None               # bar marker under the pointer
        self._hovercard: hovercard.HoverCard | None = None
        self._bar_marks: tuple = ()
        self._geom: dict | None = None
        self._footer_icons: tuple[str, ...] = ()
        self._snoozed: list[tuple[datetime, alerts.Fired]] = []
        # Iqama times from the masjid's calendar, and the sentence the
        # settings page shows about where they came from.
        self.prayer_loader = prayer_mod.Loader(
            self.s, cfg.config_dir,
            to_ui=lambda fn: self.root.after(0, fn),
            on_ready=self._prayers_ready,
        )
        # What has already been shaken for, and already reminded about, so
        # neither fires twice for the same meeting or prayer.
        self._nudged: set[str] = set()
        self._prayer_notified: set[str] = set()
        # What each prayer sets off -- the trigger URLs, the speaker and this
        # computer's own playback -- and the last word on how that went,
        # which the settings page shows. Shared with the Qt host, which owns
        # one of these too.
        self.routines = routines_mod.Runner(
            self.s, on_status=self._routine_status, on_warn=self._adhan_warning)
        self._last_heartbeat: datetime | None = None
        self._loop_errors: dict[str, float] = {}
        self.peek = None
        self._positioned = False
        self.tray = None

        w32.enable_dpi_awareness()

        self.root = tk.Tk()
        self.root.title("Floating Clock")
        self.root.report_callback_exception = self._report_callback_exception
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.s["topmost"]))
        self.root.withdraw()
        self.root.update_idletasks()

        self.hwnd = self._resolve_hwnd()
        w32.update_ex_style(self.hwnd, add=w32.WS_EX_LAYERED | w32.WS_EX_TOOLWINDOW)
        self._scale = w32.dpi_for_window(self.hwnd) / 96.0

        self._build_menu()
        self._bind_events()
        self._start_hotkeys()

        self._paint(force=True)
        self._restore_position()
        self.root.deiconify()
        self._apply_click_through()
        self._fade_in()
        self._tick()
        self._poll_hotkeys()
        self._poll_rescue()
        self._start_calendar()
        self.prayer_loader.start()
        self._poll_alerts()
        self._autosave()
        if cfg.LOAD_FAILED:
            self.root.after(3000, self._settings_unreadable)
        self.peek = peek_mod.Peek(self)
        self.peek.schedule()
        self._start_tray()
        if self.s["minimized"]:
            # Last after the tray: without a tray icon there would be nothing
            # to click to bring the clock back, and set_minimized knows that.
            self.set_minimized(True)

    # --- window ------------------------------------------------------------
    def _report_callback_exception(self, exc_type, exc, tb) -> None:
        """Tk prints handler errors to stderr, which a windowed app has not
        got: log them, or a broken menu item just silently does nothing."""
        log.error("Error in a UI handler", exc_info=(exc_type, exc, tb))

    def _resolve_hwnd(self) -> int:
        window_id = self.root.winfo_id()
        try:
            parent = w32.user32.GetParent(window_id)
        except OSError:
            parent = 0
        return parent or window_id

    @property
    def _in_use(self) -> bool:
        """Whether someone is working with the clock right now: the pointer
        over it, the meetings panel or menu open, or a drag in progress. A
        translucent clock is fine to glance at, but while it is being used
        whatever sits behind it must not show through the text."""
        if not self.s["hover_boost"]:
            return False
        return bool(
            self._hover
            or self._drag
            or self._panel is not None
            or getattr(self, "_menu_popup", None) is not None
        )

    @property
    def _target_opacity(self) -> float:
        if self._in_use:
            return 1.0
        return float(self.s["opacity"])

    def _paint(self, force: bool = False) -> None:
        clock, suffix, date = self._texts()
        now = datetime.now()
        fraction, marks = self._bar_state(now)
        self._bar_marks = marks   # kept for the hover detail's hit-test

        dpi_scale = w32.dpi_for_window(self.hwnd) / 96.0
        if abs(dpi_scale - self._scale) > 0.01:
            self._scale = dpi_scale
            self.renderer.invalidate()
            force = True

        icons, active, lines = self._footer(now)
        badge = self._timer_badge(now)
        geom = self.renderer.measure(
            self.s, self._scale, clock, suffix, date, lines, icons, badge
        )
        # Kept for hit-testing: the card is a bitmap, so the only way to
        # know what a click landed on is the geometry it was drawn from.
        self._geom, self._footer_icons = geom, icons
        inner = geom["card_w"] - geom["pad_x"] * 2
        # Repaint only when a pixel would actually change: the bar advances one
        # pixel every couple of hundred milliseconds, so most ticks are no-ops.
        bar_px = int(fraction * inner) if geom["has_bar"] else 0
        # Marks move only when a meeting starts, ends or becomes the next one,
        # so folding them into the key costs nothing and catches those flips.
        mark_key = tuple((round(m.position, 4), m.state) for m in marks)
        solid = self._in_use
        key = (clock, suffix, date, lines, icons, active, bar_px, mark_key, badge,
               round(getattr(self, "_work_end", 1.0), 4), self.s["theme"],
               geom["card_w"], geom["card_h"], self._hover_icon, self._hover, solid)
        if not force and key == self._paint_key:
            return
        self._paint_key = key

        image = self.renderer.card(
            self.s, self._scale, clock, suffix, date, fraction,
            footer_lines=lines, footer_icons=icons,
            hover_icon=self._hover_icon, card_hover=self._hover,
            active_icons=active, bar_marks=marks,
            work_end=getattr(self, "_work_end", 1.0), badge=badge, solid=solid,
            now_needle=self.s.get("bar_mode", "day") == "day",
        )
        width, height = image.size
        if (width, height) != (self.root.winfo_width(), self.root.winfo_height()):
            self.root.geometry("%dx%d" % (width, height))
            self.root.update_idletasks()
            # The card is anchored top-left, so it grows to the right and down.
            # Parked against an edge -- which is where a clock usually lives --
            # a new footer line or a bigger font would push it off screen.
            # Not before the window is placed and shown: a withdrawn window
            # reports a zero rect, and clamping that would park it at 0,0
            # and overwrite the saved position.
            if (
                self._positioned
                and not self._drag
                and not (self.peek is not None and self.peek.active)
            ):
                self.s["x"], self.s["y"] = w32.clamp_to_screen(
                    self.hwnd, inset=int(render.SHADOW_MARGIN * self._scale)
                )
        self._image = image
        if self.peek is not None and self.peek.active:
            # The animation pushes its own scaled frame each tick; pushing the
            # unscaled one here would make the card flicker mid-flight.
            return
        w32.push_layered_bitmap(self.hwnd, image, self._fade * self._target_opacity)

    def _push(self) -> None:
        """Re-push the cached bitmap; used for opacity-only changes."""
        if self.peek is not None and self.peek.active:
            return
        image = getattr(self, "_image", None)
        if image is not None:
            w32.push_layered_bitmap(self.hwnd, image, self._fade * self._target_opacity)

    def _fade_in(self, step: int = 1) -> None:
        self._fade = min(1.0, step / FADE_STEPS)
        self._push()
        if step < FADE_STEPS:
            self.root.after(FADE_INTERVAL, self._fade_in, step + 1)

    def _restore_position(self) -> None:
        self.root.update_idletasks()
        left, top, right, bottom = w32.window_rect(self.hwnd)
        width, height = right - left, bottom - top
        x, y = self.s["x"], self.s["y"]
        if x is None or y is None:
            wl, wt, wr, wb = w32.work_area(self.hwnd)
            margin = int(render.SHADOW_MARGIN * self._scale)
            # Inset the visible card, not the window: the shadow margin around
            # it is transparent padding.
            x, y = wr - width + margin - 24, wt - margin + 24
        w32.move_window(self.hwnd, int(x), int(y))
        self.s["x"], self.s["y"] = w32.clamp_to_screen(
            self.hwnd, inset=int(render.SHADOW_MARGIN * self._scale)
        )
        self._positioned = True

    def _position(self) -> tuple[int, int]:
        left, top, _r, _b = w32.window_rect(self.hwnd)
        return (left, top)

    def _apply_click_through(self) -> None:
        if self.s["click_through"]:
            w32.update_ex_style(self.hwnd, add=w32.WS_EX_TRANSPARENT)
        else:
            w32.update_ex_style(self.hwnd, remove=w32.WS_EX_TRANSPARENT)

    # --- content -----------------------------------------------------------
    def _texts(self) -> tuple[str, str, str]:
        """(time, am/pm suffix, date) -- the suffix is set separately so it can
        be drawn smaller and baseline-aligned against the big digits."""
        now = datetime.now()
        if self.s["use_24h"]:
            clock = now.strftime("%H:%M:%S" if self.s["show_seconds"] else "%H:%M")
            suffix = ""
        else:
            clock = now.strftime("%I:%M:%S" if self.s["show_seconds"] else "%I:%M")
            clock = clock.lstrip("0")
            suffix = now.strftime("%p").lower()
        show_date = self.s["show_date"] and not self.s["compact"]
        date = now.strftime("%a, %d %b %Y").upper() if show_date else ""
        return clock, suffix, date

    def _footer(
        self, now: datetime | None = None
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """(icons, active, lines) for the accent block under the date.

        The glyph row is permanent -- it is the card's button bar, and a
        shortcut you can only see once you are already using the feature is no
        shortcut. Ones with something running are drawn in the accent colour,
        the rest sit muted, so the row doubles as the status line it used to be.

        `now` is passed in by _paint so the whole card is drawn for one
        instant -- the gaps, the bar and the digits cannot disagree.
        """
        now = now or datetime.now()
        icons: list[str] = []
        active: list[str] = []
        lines: list[str] = []

        timer = self.scheduler.active_timer(now) if self.s["show_timer"] else None
        armed_alarm = any(alarm.enabled for alarm in self.scheduler.alarms)
        watch = self.scheduler.stopwatch

        show_meetings = self.s["show_next_meeting"] and self.s["calendar_enabled"]
        if show_meetings:
            icons.append("meeting")
        icons.extend(("alarm", "timer", "stopwatch"))

        if timer is not None:
            active.append("timer")
            if not self._timer_as_badge():
                lines.append("%s  %s" % (
                    alerts.format_duration(timer.seconds_left(now)),
                    (timer.label or "TIMER").upper()[:16],
                ))
        if watch.running:
            active.append("stopwatch")
            lines.append("%s  STOPWATCH" % alerts.format_duration(watch.elapsed(now)))
        if armed_alarm:
            active.append("alarm")
            if not lines:
                upcoming = self.scheduler.next_alarm(now)
                if upcoming is not None:
                    alarm, when = upcoming
                    lines.append("%s  %s" % (
                        when.strftime("%H:%M" if self.s["use_24h"] else "%I:%M").lstrip("0"),
                        (alarm.label or "ALARM").upper()[:16],
                    ))

        if show_meetings:
            if self.calendar_error and not self.events:
                # Only when nothing at all could be read. One broken feed
                # beside a working Outlook is a note in the panel, not a
                # blank card.
                lines.append("CALENDAR UNAVAILABLE")
            else:
                # All-day entries are not something to count down to; the
                # panel lists them under their own heading.
                ahead = [
                    e for e in self.events
                    if e.end > now and not getattr(e, "all_day", False)
                ][:CARD_MEETINGS]
                whole_day = [
                    e for e in self.events
                    if getattr(e, "all_day", False) and e.start <= now < e.end
                ]
                if ahead:
                    active.append("meeting")
                    lines.extend(self._meeting_line(e, now) for e in ahead)
                elif not whole_day and not self.s["compact"]:
                    # Compact shows only what there is; the glyph alone says
                    # the calendar is on.
                    lines.append("NO MEETINGS AHEAD")
                # An all-day entry takes a spare line after the timed ones,
                # with ALL DAY where the countdown would be: visible, but
                # never mistaken for the meeting you are in.
                for e in whole_day[:max(0, CARD_MEETINGS - len(ahead))]:
                    lines.append("ALL DAY  %s" % (e.subject or "").upper()[:24])

        # The next prayer takes a line of its own under the meetings, never
        # in place of one: what is on the card is what is coming, in order.
        prayer_line = self._prayer_line(now)
        if prayer_line and len(lines) <= CARD_MEETINGS:
            lines.append(prayer_line)

        if self.s["compact"]:
            # One line: the timer, stopwatch or alarm you are waiting on, else
            # the next meeting. The glyph row stays whole -- it is the button
            # bar, and the accent still says what is running.
            lines = lines[:1]
        return tuple(icons), tuple(active), tuple(lines)

    def _timer_as_badge(self) -> bool:
        """With a date row to sit on, a running timer becomes the badge in the
        top-right corner rather than a footer line; the compact card, which
        has no date row, keeps it as its one line."""
        return bool(self.s["show_date"]) and not self.s["compact"]

    def _timer_badge(self, now: datetime | None = None) -> str:
        """'4:10  TEA' for the badge, or '' when no timer is running."""
        if not self._timer_as_badge() or not self.s["show_timer"]:
            return ""
        now = now or datetime.now()
        timer = self.scheduler.active_timer(now)
        if timer is None:
            return ""
        text = alerts.format_duration(timer.seconds_left(now))
        label = (timer.label or "").strip().upper()[:12]
        return "%s  %s" % (text, label) if label else text

    def _meeting_line(self, event, now: datetime) -> str:
        """One meeting row for the card: when, how far off, and what."""
        stamp = event.start.strftime(
            "%H:%M" if self.s["use_24h"] else "%I:%M"
        ).lstrip("0")
        if event.start.date() != now.date():
            # Three rows reach into tomorrow easily, and a bare "9:00"
            # would read as this morning.
            stamp = "%s %s" % (event.start.strftime("%a").upper(), stamp)
        return "%s  %s  %s" % (
            stamp,
            "NOW" if event.is_live(now) else outlook.describe_gap(
                event.minutes_until(now)
            ).upper(),
            event.subject.upper()[:24],
        )

    def _prayer_line(self, now: datetime) -> str:
        """The next iqama, once it is close enough to be worth a line.

        Far off it would only take space from the meetings; inside the hour
        it is the next thing that will actually move you.
        """
        # getattr: the card is drawn from the first tick, which can land
        # before the times have been read.
        prayers = getattr(self, "prayers", ())
        if not self.s.get("prayer_enabled", True) or not prayers:
            return ""
        item = prayer_mod.next_prayer(prayers, now)
        if item is None:
            return ""
        minutes = item.minutes_until(now)
        if minutes > float(self.s.get("prayer_show_minutes", 60)):
            return ""
        stamp = item.iqama.strftime(
            "%H:%M" if self.s["use_24h"] else "%I:%M"
        ).lstrip("0")
        return "%s  %s  %s IQAMA" % (
            stamp, outlook.describe_gap(minutes).upper(), item.name.upper(),
        )

    def _bar_fraction(self, now: datetime) -> float:
        """How full the progress bar sits.

        Through the meeting you are in, or across the run-up to the next one.
        An empty bar means nothing is close, which is itself worth knowing --
        and is why it no longer tracks the passing minute, which only told
        you what the seconds were already saying.
        """
        events = [e for e in self.events if not getattr(e, "all_day", False)]
        if not (self.s["calendar_enabled"] and events):
            return 0.0
        live = next((e for e in events if e.is_live(now)), None)
        if live is not None:
            span = (live.end - live.start).total_seconds()
            if span <= 0:
                return 1.0
            return max(0.0, min(1.0, (now - live.start).total_seconds() / span))
        ahead = [e for e in events if e.start > now]
        if not ahead:
            return 0.0
        following = min(ahead, key=lambda e: e.start)
        # Fill across a fixed run-up, or from the end of the previous meeting
        # when that is later: a bar creeping over a three-day gap would never
        # look like it was moving at all.
        opened = following.start - timedelta(minutes=BAR_LEAD_MINUTES)
        finished = [e.end for e in events if e.end <= now]
        if finished:
            opened = max(opened, max(finished))
        span = (following.start - opened).total_seconds()
        if span <= 0:
            return 1.0
        return max(0.0, min(1.0, (now - opened).total_seconds() / span))

    def _bar_state(self, now: datetime) -> tuple[float, tuple]:
        """(fill, marks) for the progress bar, per the chosen mode.

        In day mode the bar stops being about the next meeting and becomes the
        day itself: the fill is how far through it you are, and each dot is a
        meeting -- so "how much of today is left, and how much of it is booked"
        is one glance rather than arithmetic.
        """
        mode = self.s.get("bar_mode", "day")
        if mode == "seconds":
            return (now.second + now.microsecond / 1_000_000) / 60.0, ()
        if mode == "meeting" or not self.s["calendar_enabled"]:
            return self._bar_fraction(now), ()

        window = daybar.day_window(now, float(self.s["day_start_hour"]))
        self._day_window = window
        self._work_end = daybar.work_end_fraction(
            float(self.s["day_start_hour"]), float(self.s["day_end_hour"])
        )
        return (
            daybar.progress(now, window),
            daybar.marks(
                self.events, now, window,
                colour_for=self.org_colour, label_for=self.org_label,
            ),
        )

    def _tick(self) -> None:
        try:
            self._paint()
            peek = getattr(self, "peek", None)
            if peek is not None:
                # The peek's slot is checked against the wall clock here, so a
                # Tk timer that runs late cannot push "every 30 min" off :00/:30.
                peek.poll()
        except Exception:
            self._loop_error("clock face")
        finally:
            # The bar now moves at meeting pace, not once a second, so the tick
            # only has to keep up with the digits. Re-armed whatever happened:
            # one bad frame must not stop the clock.
            self._rearm(250, self._tick)

    # --- keeping the loops alive -------------------------------------------
    def _rearm(self, ms: int, callback) -> None:
        """Schedule a loop's next turn, unless the clock is shutting down."""
        if getattr(self, "_closing", False):
            return
        try:
            self.root.after(ms, callback)
        except Exception:
            pass    # Tk is gone: there is nothing left to schedule on

    def _guarded(self, name: str, step) -> None:
        """Run one step of a loop so that its failure is only its own."""
        try:
            step()
        except Exception:
            self._loop_error(name)

    def _loop_error(self, name: str) -> None:
        """Log a loop's error, at most once every few minutes per loop.

        Called from inside an except block. The loop carries on either way;
        this makes the failure visible without flooding the log from
        something that runs four times a second.
        """
        errors = getattr(self, "_loop_errors", None)
        if errors is None:
            errors = self._loop_errors = {}
        stamp = time.monotonic()
        last = errors.get(name)
        if last is None or stamp - last >= LOOP_ERROR_EVERY_S:
            errors[name] = stamp
            log.exception("The %s loop hit an error and carried on", name)

    def _heartbeat(self, now: datetime) -> None:
        """One line an hour: alive, how many prayer times, what fires next.

        Two days of silence once went unnoticed because nothing was written at
        all. With this, a stopped loop shows up as a gap in the log.
        """
        last = getattr(self, "_last_heartbeat", None)
        if last is not None and now - last < HEARTBEAT_EVERY:
            return
        if self.prayer_loader.loading:
            # The times are still arriving. A line now would say nothing is
            # armed a moment before it is -- and read like the very fault
            # the heartbeat is there to rule out.
            return
        self._last_heartbeat = now
        logsetup.ensure()
        prayers = list(getattr(self, "prayers", ()) or ())
        through = max(p.iqama for p in prayers).strftime("%a %d %b") if prayers else "none"
        if self.s.get("prayer_enabled", True):
            upcoming = self.routines.next_due(prayers, now)
            armed = ("next %s %s at %s" % (upcoming[2], upcoming[0].name,
                                           upcoming[1].strftime("%a %H:%M"))
                     if upcoming else "nothing armed")
        else:
            armed = "off"
        log.info("Alive %s: %d prayer times through %s; routines %s; settings %s",
                 __version__, len(prayers), through, armed,
                 "unreadable" if cfg.LOAD_FAILED else "ok")

    # --- mutators ----------------------------------------------------------
    def set_opacity(self, value: float) -> None:
        value = round(min(cfg.MAX_OPACITY, max(cfg.MIN_OPACITY, float(value))), 2)
        self.s["opacity"] = value
        self._push()
        if self._settings_open():
            self.opacity_var.set(int(round(value * 100)))

    def nudge_opacity(self, delta: float) -> None:
        self.set_opacity(self.s["opacity"] + delta)

    def set_font_size(self, size: int) -> None:
        self.s["font_size"] = min(cfg.MAX_FONT, max(cfg.MIN_FONT, int(size)))
        self._paint(force=True)
        if self._settings_open():
            self.font_var.set(self.s["font_size"])

    def set_theme(self, name: str) -> None:
        self.s["theme"] = name
        self.theme_var.set(name)
        self.renderer.invalidate()
        self._paint(force=True)
        # The dialog and menus borrow the theme's accent.
        self._restyle_menus()
        self._restyle_settings()

    def set_font_family(self, family: str) -> None:
        self.s["font_family"] = family
        self.family_var.set(family)
        self.renderer.invalidate()
        self._paint(force=True)

    def toggle_click_through(self) -> None:
        self.s["click_through"] = not self.s["click_through"]
        self.var_click_through.set(self.s["click_through"])
        self._apply_click_through()

    def _toggle(self, key: str, var: tk.BooleanVar, repaint: bool = False) -> None:
        self.s[key] = bool(var.get())
        if repaint:
            self.renderer.invalidate()
            self._paint(force=True)

    def _set_click_through(self) -> None:
        """Shared by the menu entry and the settings checkbox, so the two can
        never disagree about the window style."""
        self.s["click_through"] = bool(self.var_click_through.get())
        self._apply_click_through()

    def _toggle_topmost(self) -> None:
        self.s["topmost"] = bool(self.var_topmost.get())
        self.root.attributes("-topmost", self.s["topmost"])

    def _toggle_startup(self) -> None:
        wanted = bool(self.var_startup.get())
        if not set_startup(wanted, startup_command()):
            self.var_startup.set(startup_enabled())

    def peek_now(self) -> None:
        if self.peek is not None:
            self.peek.start()

    def reschedule_peek(self) -> None:
        if self.peek is not None:
            self.peek.schedule()

    def set_minimized(self, minimized: bool) -> None:
        """Hide the clock, leaving only the tray icon -- or bring it back.

        The peek is untouched: its timer keeps running, and a peek that fires
        while minimized shows the card at the centre and hides it again after.
        That is deliberate -- a clock you have tucked away still needs to
        nudge you -- and only unticking the peek itself switches it off.
        """
        minimized = bool(minimized)
        if minimized and self.tray is None:
            # No tray icon means nothing to click to get the clock back.
            minimized = False
        self.s["minimized"] = minimized
        self.var_minimized.set(minimized)
        mid_peek = self.peek is not None and self.peek.active
        if minimized:
            self._close_panel()
            # Mid-peek the window stays up: finish() hides it on landing.
            if not mid_peek:
                w32.show_window(self.hwnd, False)
        else:
            w32.show_window(self.hwnd, True)
            if not mid_peek:
                self._paint(force=True)

    def _reset_position(self) -> None:
        # "Show me the clock" -- so it must not stay tucked in the tray.
        self.set_minimized(False)
        self.s["x"] = self.s["y"] = None
        self._restore_position()

    # --- events ------------------------------------------------------------
    def _bind_events(self) -> None:
        self.root.bind("<Button-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", self._drag_end)
        self.root.bind("<Double-Button-1>", self._on_double_click)
        self.root.bind("<Button-3>", self._popup)
        self.root.bind("<MouseWheel>", self._wheel)
        self.root.bind("<Enter>", self._enter)
        self.root.bind("<Leave>", self._leave)
        self.root.bind("<Motion>", self._motion)

    def _card_point(self) -> tuple[int, int]:
        """Cursor position in the card's own pixels."""
        left, top, _r, _b = w32.window_rect(self.hwnd)
        cx, cy = w32.cursor_pos()
        return cx - left, cy - top

    def _icon_at_cursor(self) -> str | None:
        """Which rail icon the cursor is over, if any."""
        if self._geom is None or not self._footer_icons:
            return None
        x, y = self._card_point()
        return render.footer_icon_at(self._geom, self._footer_icons, x, y)

    def _in_calendar_region(self) -> bool:
        """Is the cursor anywhere in the lower block -- bar or meeting lines?"""
        if self._geom is None:
            return False
        box = render.calendar_hit_box(self._geom)
        if box is None:
            return False
        x, y = self._card_point()
        x0, y0, x1, y1 = box
        return x0 <= x <= x1 and y0 <= y <= y1

    def _motion(self, _event) -> None:
        # Only the icon under the pointer changes the picture, so most motion
        # events cost one hit-test and no repaint.
        kind = self._icon_at_cursor()
        if kind != self._hover_icon:
            self._hover_icon = kind
            self._paint(force=True)
        self._track_marker()

    # --- the hover detail over a bar marker ---------------------------------
    def _hover_card(self) -> hovercard.HoverCard:
        card = self._hovercard
        if (card is None or card.theme.name != self.s["theme"]
                or abs(card.scale - self._scale) > 0.01):
            if card is not None:
                card.destroy()
            card = self._hovercard = hovercard.HoverCard(
                self.root, self.s["theme"], self._scale
            )
        return card

    def _hide_hover_card(self) -> None:
        self._hover_mark = None
        if self._hovercard is not None:
            self._hovercard.hide()

    def _track_marker(self) -> None:
        """Float the meeting's detail while the pointer is over its block."""
        hit = None
        if (self._geom is not None and self._bar_marks and not self._drag
                and not (self.peek is not None and self.peek.active)):
            x, y = self._card_point()
            hit = render.bar_mark_at(self._geom, self._bar_marks, self._scale, x, y)
        if hit is None:
            if self._hover_mark is not None:
                self._hide_hover_card()
            return
        mark, (x0, y0, x1, _y1) = hit
        key = (mark.subject, mark.start, mark.end, mark.state, mark.count)
        if key == self._hover_mark:
            return
        self._hover_mark = key
        left, top, _r, _b = w32.window_rect(self.hwnd)
        title, lines = self._mark_detail(mark)
        self._hover_card().show(
            key, title, lines,
            (int(left + (x0 + x1) / 2), int(top + y0)),
            render._hex_rgb(mark.colour),
        )

    def _mark_detail(self, mark) -> tuple[str, list[str]]:
        """Title and lines for a marker: what, when, how far off, whose."""
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
            timing = ("ended %s" % outlook.describe_ago(
                (now - finished).total_seconds() / 60)) if finished else "over"
        elif mark.start is not None:
            timing = "in %s" % _span((mark.start - now).total_seconds())
        else:
            timing = ""
        first = "  ·  ".join(part for part in (when, timing) if part)
        return title, [first, mark.org]

    def _on_calendar_icon(self) -> bool:
        """Kept for the hover affordance: is the pointer on something clickable?"""
        return self._icon_at_cursor() is not None or self._in_calendar_region()

    def _activate(self, target: str) -> None:
        """Route a click on the card's lower half."""
        if target == "alarm":
            self.open_settings_tab("alarms")
        elif target == "timer":
            self.open_settings_tab("timers")
        elif target == "stopwatch":
            self.toggle_stopwatch()
        else:
            self.toggle_meetings()

    def toggle_stopwatch(self) -> None:
        self.scheduler.stopwatch.toggle()
        self.scheduler.save()
        self._paint(force=True)

    def reset_stopwatch(self) -> None:
        self.scheduler.stopwatch.reset()
        self.scheduler.save()
        self._paint(force=True)

    def _on_double_click(self, _event) -> None:
        # A second click in the lower block is still the lower block, not the
        # card -- opening settings from under the meeting list would be a
        # surprise.
        if self._press_target is not None or self._on_calendar_icon():
            return
        self.open_settings()

    def _drag_start(self, _event) -> None:
        self._hide_hover_card()
        if self.peek is not None:
            if self.peek.active and self.s["minimized"]:
                # Grabbing a peek that came from the tray is the natural way
                # to say "I want the clock back". Restored first, so the
                # abort below leaves the card up rather than hiding it.
                self.set_minimized(False)
            self.peek.abort()
        self._press_origin = w32.cursor_pos()
        icon = self._icon_at_cursor()
        self._press_target = icon or ("meeting" if self._in_calendar_region() else None)
        if self._press_target is None:
            # Anywhere else on the card dismisses the panel, which is the
            # closest thing a focusless layered window has to click-away.
            self._close_panel()
        if self.s["lock_position"]:
            return
        # The lower block is still draggable: a press there only counts as a
        # click if the pointer did not travel, so the card never becomes an
        # area you cannot pick up.
        self._drag = (self._press_origin, self._position())

    def _drag_move(self, _event) -> None:
        if not self._drag:
            return
        (start_cx, start_cy), (start_x, start_y) = self._drag
        cx, cy = w32.cursor_pos()
        w32.move_window(self.hwnd, start_x + (cx - start_cx), start_y + (cy - start_cy))

    def _drag_end(self, _event) -> None:
        target, self._press_target = self._press_target, None
        moved = 0
        if self._press_origin is not None:
            cx, cy = w32.cursor_pos()
            moved = max(abs(cx - self._press_origin[0]), abs(cy - self._press_origin[1]))
            self._press_origin = None
        if target is not None and moved <= CLICK_SLOP * self._scale:
            self._drag = None
            self._activate(target)
            return
        if not self._drag:
            return
        self._drag = None
        self._snap_to_edges()
        self.s["x"], self.s["y"] = self._position()

    def _snap_to_edges(self) -> None:
        """Nudge the card flush to a screen edge when dropped close to one.

        The shadow margin is transparent padding, so the visible card sits
        inset from the window bounds -- snapping accounts for that.
        """
        margin = int(render.SHADOW_MARGIN * self._scale)
        x, y, right_edge, bottom_edge = w32.window_rect(self.hwnd)
        width, height = right_edge - x, bottom_edge - y
        left, top, right, bottom = w32.work_area(self.hwnd)
        gap = int(SNAP_DISTANCE * self._scale)

        if abs((x + margin) - left) < gap:
            x = left - margin
        elif abs((x + width - margin) - right) < gap:
            x = right - width + margin
        if abs((y + margin) - top) < gap:
            y = top - margin
        elif abs((y + height - margin) - bottom) < gap:
            y = bottom - height + margin
        w32.move_window(self.hwnd, x, y)
        w32.clamp_to_screen(self.hwnd, inset=margin)

    def _wheel(self, event) -> None:
        step = 1 if event.delta > 0 else -1
        if event.state & 0x0004:  # Ctrl held
            self.set_font_size(self.s["font_size"] + step * 3)
        else:
            self.nudge_opacity(step * cfg.OPACITY_STEP)

    def _enter(self, _event) -> None:
        self._hover = True
        self._hover_icon = self._icon_at_cursor()
        # The card is a bitmap with no widget hover state: the rail lifting
        # to full strength is how it says its icons are buttons.
        self._paint(force=True)
        self._push()

    def _leave(self, _event) -> None:
        self._hover = False
        self._hover_icon = None
        self._hide_hover_card()
        self._paint(force=True)
        self._push()

    def _popup(self, event) -> None:
        self._open_menu(event.x_root, event.y_root)

    # --- hotkeys -----------------------------------------------------------
    def _start_hotkeys(self) -> None:
        ctrl_alt = w32.MOD_CONTROL | w32.MOD_ALT | w32.MOD_NOREPEAT
        ctrl_shift_alt = ctrl_alt | w32.MOD_SHIFT
        ctrl_shift = w32.MOD_CONTROL | w32.MOD_SHIFT | w32.MOD_NOREPEAT

        def candidates(letter: str, *function_keys: int) -> list[tuple[int, int]]:
            combos = [(ctrl_alt, ord(letter)), (ctrl_shift_alt, ord(letter))]
            combos += [(ctrl_alt, vk) for vk in function_keys]
            combos += [(ctrl_shift, vk) for vk in function_keys]
            return combos

        self.hotkeys = w32.HotkeyListener(
            {
                HOTKEY_CLICK_THROUGH: candidates("C", 0x78, 0x7B),  # F9, F12
                HOTKEY_QUIT: candidates("Q", 0x79, 0x7A),           # F10, F11
                HOTKEY_SHOW: candidates("K", 0x77, 0x76),           # F8, F7
            }
        )
        self.hotkeys.start()
        self.hotkeys.ready.wait(timeout=2.0)
        self.escape_hotkey = self.hotkeys.label(HOTKEY_CLICK_THROUGH)
        if not self.escape_hotkey:
            # Click-through swallows every mouse event, so its only way back is
            # a global hotkey. With none registered it must stay unavailable.
            self.s["click_through"] = False
            self.var_click_through.set(False)

    def _click_through_label(self) -> str:
        if not getattr(self, "escape_hotkey", ""):
            return "Click-through   (no shortcut free)"
        return "Click-through   %s" % self.escape_hotkey

    def _check_rescue(self) -> None:
        """A second launch drops a flag file asking us to undo click-through.

        Once click-through is on, the window ignores the mouse entirely, so
        relaunching from the Start Menu is the recovery anyone reaches for
        first -- it should work rather than doing nothing.
        """
        flag = cfg.rescue_path()
        if not os.path.exists(flag):
            return
        try:
            os.remove(flag)
        except OSError:
            pass
        if self.s["click_through"]:
            self.s["click_through"] = False
            self.var_click_through.set(False)
            self._apply_click_through()
        self.s["lock_position"] = False
        self.var_lock.set(False)
        self.root.attributes("-topmost", True)
        self._reset_position()
        if self.s["opacity"] < 0.5:
            self.set_opacity(0.9)

    def _poll_hotkeys(self) -> None:
        try:
            while True:
                pressed = self.hotkeys.events.get_nowait()
                if pressed == HOTKEY_CLICK_THROUGH:
                    self.toggle_click_through()
                elif pressed == HOTKEY_SHOW:
                    self._reset_position()
                elif pressed == HOTKEY_QUIT:
                    self.quit()
                    return
        except queue.Empty:
            pass
        except Exception:
            self._loop_error("hotkeys")
        self._rearm(120, self._poll_hotkeys)

    # --- tray icon ---------------------------------------------------------
    def _start_tray(self) -> None:
        try:
            self.tray = tray_mod.Tray(TRAY_TOOLTIP)
        except Exception:
            self.tray = None   # never let the tray stop the clock from running
            return
        self._poll_tray()

    def _poll_tray(self) -> None:
        if self.tray is None:
            return
        try:
            self._drain_tray()
        except Exception:
            self._loop_error("tray")
        self._rearm(200, self._poll_tray)

    def _drain_tray(self) -> None:
        try:
            while True:
                event = self.tray.events.get_nowait()
                if event == "left" and self.s["minimized"]:
                    # Tucked away: a click on the tray brings it back.
                    self.set_minimized(False)
                elif event == "left":
                    # Show me where it is, and make sure it is reachable.
                    if self.s["click_through"]:
                        self.s["click_through"] = False
                        self.var_click_through.set(False)
                        self._apply_click_through()
                    self.peek_now()
                elif event == "double":
                    self.open_settings()
                elif event == "right":
                    x, y = w32.cursor_pos()
                    self._open_menu(x, y)
        except queue.Empty:
            pass
        self._update_tray_tooltip()

    def _update_tray_tooltip(self) -> None:
        if self.tray is None:
            return
        clock, suffix, _date = self._texts()
        _icons, _active, lines = self._footer()
        tip = "Floating Clock  %s%s" % (clock, (" " + suffix) if suffix else "")
        # Two lines at most: the tooltip is capped at 128 characters.
        for line in lines[:2]:
            tip += "\n" + line.title()
        self.tray.set_tooltip(tip)

    def _poll_rescue(self) -> None:
        self._guarded("rescue", self._check_rescue)
        self._rearm(1000, self._poll_rescue)

    # --- calendar ----------------------------------------------------------
    def _start_calendar(self) -> None:
        if self.poller is None:
            # The poller reads a wide window for the meetings panel; the card
            # keeps its own, narrower horizon in _footer().
            self.poller = outlook.CalendarPoller(
                interval=float(self.s["calendar_poll_seconds"]),
            )
            self.poller.enabled = bool(self.s["calendar_enabled"])
            self.reload_orgs()
            self.poller.start()
        else:
            self.poller.enabled = bool(self.s["calendar_enabled"])
            self.reload_orgs()
            self.poller.refresh_now()
        self._poll_calendar()

    def reload_orgs(self) -> None:
        """Re-read the configured calendars and hand them to the poller.

        Marks are fetched on a worker: a company whose website is slow must not
        hold up the clock's own event loop.
        """
        self.orgs = orgs_mod.from_config(self.s["calendars"])
        if self.poller is not None:
            self.poller.feeds = self.orgs
        # A domain whose site gave no icon this session is not asked again on
        # every poll; the monogram stands in until the next launch.
        tried = getattr(self, "_marks_tried", None)
        if tried is None:
            tried = self._marks_tried = set()
        base = cfg.config_dir()
        pending = [
            org.domain for org in self.orgs
            if org.domain and org.domain not in tried
            and (not org.icon or org.icon != orgs_mod.icon_path(base, org.domain))
        ]
        if pending:
            tried.update(pending)
            threading.Thread(
                target=self._fetch_marks, args=(list(self.orgs),),
                name="floating-clock-icons", daemon=True,
            ).start()
        else:
            self._load_marks()

    def _discover_orgs(self, events) -> None:
        """Give every company seen in Outlook an organisation of its own.

        A feed is configured by hand, but an Outlook meeting arrives with the
        organiser's and guests' addresses, and the domain in those is all it
        takes to look a company up. Only the leading domain of a meeting
        creates an entry -- badging every guest's employer would bury the
        list -- and the entries are marked automatic so they can be renamed
        or given a logo on the Calendars page.
        """
        self._discover_domains([
            event.domains[0] for event in events
            if not getattr(event, "source", "") and getattr(event, "domains", ())
        ])

    def _discover_domains(self, domains) -> None:
        """Add an organisation, and go fetch its logo, for each new domain."""
        if not self.s.get("auto_org_marks", True) or not domains:
            return
        found = orgs_mod.discover(self.orgs, domains)
        if not found:
            return
        log.info("New organisations from the calendar: %s",
                 ", ".join(org.domain for org in found))
        self.s["calendars"] = orgs_mod.to_config(list(self.orgs) + found)
        self.reload_orgs()

    def _fetch_marks(self, snapshot: list) -> None:
        try:
            filled = orgs_mod.ensure_icons(snapshot, cfg.config_dir())
        except Exception:
            log.warning("Could not fetch organisation marks", exc_info=True)
            return
        # Back to the Tk thread before touching settings or repainting.
        self.root.after(0, self._marks_ready, snapshot, filled)

    def _marks_ready(self, snapshot: list, filled: list) -> None:
        # Merged into the list as it stands, never swapped for the worker's
        # copy: a calendar added while logos were being fetched must stay.
        merged = orgs_mod.merge_marks(self.orgs, snapshot, filled)
        self.orgs = merged
        self.s["calendars"] = orgs_mod.to_config(merged)
        if self.poller is not None:
            self.poller.feeds = merged
        self._load_marks()
        self._paint(force=True)
        if self._panel is not None:
            self._panel.refresh()

    def _load_marks(self) -> None:
        """Decode each cached PNG once, keyed by org id, for the renderer."""
        self.org_marks = {}
        for org in self.orgs:
            if not org.icon:
                continue
            try:
                from PIL import Image

                with Image.open(org.icon) as image:
                    self.org_marks[org.id] = image.convert("RGBA").copy()
            except Exception:
                log.debug("unreadable mark for %s", org.id)

    def org_for(self, source: str):
        for org in self.orgs:
            if org.id == source:
                return org
        return None

    def org_for_event(self, event):
        """The organisation a meeting belongs to: its feed, else its domains."""
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

    def calendar_label(self, event) -> str:
        """Which calendar a meeting came from -- only once there is more than
        one, since 'Outlook' on every row of an Outlook-only panel is noise."""
        feeds = [org for org in self.orgs if org.ics_url and org.enabled]
        if not feeds:
            return ""
        source = getattr(event, "source", "")
        if not source:
            return "Outlook"
        org = self.org_for(source)
        return org.name if org is not None else "Calendar"

    def org_badge(self, event):
        """(mark, colour, initials) for a row, or None to leave it unbadged.

        An Outlook meeting is badged with the company its addresses point at;
        one with nobody external in it stays plain -- unless feeds are
        configured too, when an unmarked Outlook row would be the one
        ambiguous line on the panel, so it gets a neutral "OL".
        """
        if not self.orgs:
            return None
        if isinstance(event, str):   # a bare feed id, from older callers
            event = outlook.Event("", datetime.now(), datetime.now(), source=event)
        org = self.org_for_event(event)
        if org is None:
            if (not getattr(event, "source", "") and self.s["calendar_enabled"]
                    and any(o.ics_url for o in self.orgs)):
                return (None, "#5c6270", "OL")
            return None
        return (self.org_marks.get(org.id), org.resolved_colour(), org.initials)

    def _poll_calendar(self) -> None:
        try:
            self._drain_calendar()
        except Exception:
            self._loop_error("calendar")
        self._rearm(1000, self._poll_calendar)

    def _drain_calendar(self) -> None:
        if self.poller is not None:
            try:
                while True:
                    events, error = self.poller.results.get_nowait()
                    self.events, self.calendar_error = events, error
                    self._discover_orgs(events)
                    self._paint(force=True)
                    if self._panel is not None:
                        self._panel.refresh()
            except queue.Empty:
                pass
            try:
                while True:
                    self._discover_domains(self.poller.domains.get_nowait())
            except queue.Empty:
                pass

    def _meetings_snapshot(self) -> tuple[list, str]:
        """(events, error) for the meetings panel."""
        if not self.s["calendar_enabled"]:
            return [], "Outlook calendar is switched off"
        return self.events, self.calendar_error

    def toggle_meetings(self) -> None:
        """Open the meetings panel, or close the one already showing."""
        if self._panel is not None:
            self._close_panel()
            return
        try:
            self._panel = meetings_mod.MeetingsPanel(
                self.root, self._meetings_snapshot, self.s, self._scale,
                on_close=self._panel_closed, anchor=w32.window_rect(self.hwnd),
                org_lookup=self.org_badge, label_lookup=self.org_label,
                calendar_lookup=self.calendar_label,
                prayers_provider=lambda: self.prayers,
            )
        except Exception:
            # A panel failing must never take the clock down with it.
            self._panel = None
        self._solidify()

    def _close_panel(self) -> None:
        if self._panel is not None:
            self._panel.close()

    def _panel_closed(self, _panel) -> None:
        self._panel = None
        self._solidify()

    def _solidify(self) -> None:
        """Repaint after something that changes whether the clock is in use,
        so it turns solid the moment a panel opens and eases back after."""
        if self._closing:
            return
        try:
            self._paint(force=True)
            self._push()
        except tk.TclError:
            pass

    def refresh_calendar(self) -> None:
        if self.poller is not None:
            self.poller.enabled = bool(self.s["calendar_enabled"])
            self.poller.refresh_now()

    # --- prayer times ------------------------------------------------------
    # The reading and the refreshing live in prayer.Loader, which the Qt host
    # owns one of too. What is left here is the two places this host differs:
    # what the rest of it reads, and what happens when times land.
    @property
    def prayers(self) -> list:
        return self.prayer_loader.prayers

    @property
    def prayer_status(self) -> str:
        return self.prayer_loader.status

    def _prayers_ready(self, found: list, status: str) -> None:
        self._paint(force=True)
        self._refresh_prayer_page()

    def refresh_prayers(self, force: bool = True) -> None:
        """Settings page hook: fetch the calendar again now."""
        self.prayer_loader.refresh(force=force)

    # --- alerts ------------------------------------------------------------
    def _poll_alerts(self) -> None:
        """Everything that fires: meeting toasts, prayer reminders, Alexa
        triggers, the nudge, snoozes, the prayer-time check and the heartbeat.

        Each step runs on its own and the loop is re-armed whatever happens.
        It used to re-arm at the very end, so any one step raising stopped
        every alert for good -- silently, until the clock was restarted.
        """
        try:
            now = datetime.now()
            events = self.events if self.s["calendar_enabled"] else ()
            lead = float(self.s["meeting_lead_minutes"])
            self._guarded("prayer routines",
                          lambda: self.routines.poll(getattr(self, "prayers", ()), now))
            self._guarded("meeting reminders", lambda: [
                self._show_toast(fired)
                for fired in self.scheduler.due(now, events, lead_minutes=lead)
            ])
            self._guarded("prayer reminders", lambda: self._poll_prayer_alerts(now))
            self._guarded("nudge", lambda: self._poll_nudge(now, events))
            self._guarded("snoozed alerts", lambda: self._poll_snoozed(now))
            self._guarded("prayer-time check", lambda: self.prayer_loader.poll(now))
            self._guarded("heartbeat", lambda: self._heartbeat(now))
            self._guarded("notification pruning", self.scheduler.forget_old_notifications)
        except Exception:
            self._loop_error("alerts")
        finally:
            self._rearm(1000, self._poll_alerts)

    def _poll_snoozed(self, now: datetime) -> None:
        """Re-fire anything the user snoozed once its time comes back around."""
        still_waiting = []
        for due_at, fired in self._snoozed:
            if now >= due_at:
                self._show_toast(fired)
            else:
                still_waiting.append((due_at, fired))
        self._snoozed = still_waiting

    def _warn_settings_unreadable(self) -> None:
        """Said once, and left up until dismissed: running on defaults is
        not something to find out about by a prayer going unannounced."""
        self._show_toast(alerts.Fired(
            kind="alarm", title="Settings could not be read",
            detail="Running on defaults, and not saving over your real settings. "
                   "Restart the clock to try again.",
            key="settings-unreadable",
        ))

    def _settings_unreadable(self) -> None:
        """Try a fresh start in a minute; only ask for help once that has
        been tried enough times to say it is not going to work on its own."""
        attempt = restart_attempt(sys.argv)
        if should_restart_for_settings(cfg.LOAD_FAILED, attempt):
            log.warning("Settings could not be read at startup; starting afresh in %d s "
                        "(restart %d of %d)", SETTINGS_RETRY_S, attempt + 1,
                        SETTINGS_MAX_RESTARTS)
            self.root.after(SETTINGS_RETRY_S * 1000, self._restart_for_settings)
        else:
            log.error("Settings still unreadable after %d restarts; asking for help", attempt)
            self._warn_settings_unreadable()

    def _restart_for_settings(self) -> None:
        """Start a fresh copy of the clock, then step aside for it."""
        if not cfg.LOAD_FAILED or getattr(self, "_closing", False):
            return
        attempt = restart_attempt(sys.argv) + 1
        command = relaunch_command(sys.argv, attempt, os.getpid())
        try:
            subprocess.Popen(command, close_fds=True, creationflags=RELAUNCH_FLAGS)
        except OSError:
            log.exception("Could not start a fresh copy of the clock")
            self._warn_settings_unreadable()
            return
        log.warning("Handing over to a fresh copy of the clock (restart %d)", attempt)
        self.quit()

    def _poll_prayer_alerts(self, now: datetime) -> None:
        """A toast the set number of minutes before each iqama."""
        if not self.s.get("prayer_enabled", True) or not self.prayers:
            return
        lead = float(self.s.get("prayer_lead_minutes", 5))
        for item in prayer_mod.due(self.prayers, now, lead, self._prayer_notified):
            self._prayer_notified.add(item.key)
            minutes = item.minutes_until(now)
            self._show_toast(alerts.Fired(
                kind="prayer",
                title="%s iqama" % item.name,
                detail="%s · %s · %s" % (
                    item.time_text(bool(self.s["use_24h"])),
                    "now" if minutes < 1 else "in %d min" % round(minutes),
                    prayer_mod.SOURCE_NAME,
                ),
                key=item.key,
            ))
        if len(self._prayer_notified) > 60:
            self._prayer_notified = prayer_mod.prune_keys(self._prayer_notified, now)

    def _routine_status(self, kind: str, text: str) -> None:
        """A worker has something to report. Hop to the UI thread and show it.

        The Runner is host-neutral and knows nothing about Tk; getting back
        to the main thread is this host's side of the bargain.
        """
        try:
            self.root.after(0, self._refresh_prayer_page)
        except Exception:
            pass                       # the window has gone; the log still has it

    def _adhan_warning(self, item, kinds: list, seconds_left: int) -> None:
        """A prayer's routine, speaker or local playback is about to fire. Hop to the UI thread."""
        try:
            self.root.after(0, self._show_adhan_toast, item, kinds, seconds_left)
        except Exception:
            pass                       # the window has gone

    def _show_adhan_toast(self, item, kinds: list, seconds_left: int) -> None:
        self._show_toast(alerts.Fired(
            kind="adhan", title="%s adhan" % item.name,
            detail="%s, in about %ds." % (self._adhan_detail(kinds), max(0, round(seconds_left))),
            key="adhan:%s" % item.key,
        ))

    def _adhan_detail(self, kinds: list) -> str:
        where = []
        if routines_mod.LOCAL in kinds:
            where.append("here")
        if routines_mod.CAST in kinds:
            device = str(self.s.get("prayer_cast_device") or "").strip()
            where.append("on %s" % device if device else "on the speaker")
        text = "Playing %s" % " and ".join(where) if where else ""
        if routines_mod.HOOK in kinds:
            text = "%s, and calling a routine" % text if text else "Calling a routine"
        return text or "Playing"

    def _stop_adhan(self, _fired=None) -> None:
        """The toast's Stop button, and the tray menu's -- the same action either way."""
        self.routines.stop_now()

    # The settings page's "Test" buttons, which prove the wiring without
    # waiting for a prayer.
    def fire_routine(self, name: str, url: str) -> None:
        self.routines.fire_hook(name, url)

    def fire_cast(self, name: str, media: str) -> None:
        self.routines.fire_cast(name, media)

    def fire_local(self, name: str, media: str) -> None:
        self.routines.fire_local(name, media)

    def _poll_nudge(self, now: datetime, events) -> None:
        """A meeting a minute out: fly the clock in and shake it.

        Clicking the clock stops it -- the press handler aborts any trip in
        flight, which is the same gesture that stops a periodic peek.
        """
        if not self.s.get("nudge_enabled", True) or self.peek is None:
            return
        soon = alerts.imminent(
            events, now, float(self.s.get("nudge_lead_minutes", 1)), self._nudged
        )
        if not soon:
            return
        for key, _event in soon:
            self._nudged.add(key)
        if len(self._nudged) > 200:
            self._nudged = prayer_mod.prune_keys(self._nudged, now)
        log.info("Nudging for %r", soon[0][1].subject)
        self.peek.nudge()

    def _show_toast(self, fired) -> None:
        try:
            popup = toast_mod.Toast(
                self.root, fired, self.s["theme"], self._scale,
                on_close=self._close_toast, on_snooze=self._snooze, on_stop=self._stop_adhan,
            )
        except Exception:
            # A popup failing must never take the clock down with it.
            return
        self._toasts.append(popup)
        popup.show(len(self._toasts) - 1, sound=bool(self.s["alerts_sound"]))
        # Meeting reminders and finished timers step aside on their own; an alarm waits to be
        # acknowledged; an adhan warning is given longer, since Stop stays useful well past the
        # 30 seconds anything else gets -- the tray menu offers the same Stop for as long as that.
        if fired.kind == "adhan":
            self.root.after(60000, popup.close)
        elif fired.kind != "alarm":
            self.root.after(30000, popup.close)

    def _close_toast(self, popup) -> None:
        if popup in self._toasts:
            self._toasts.remove(popup)
        for index, other in enumerate(self._toasts):
            other.place(index)

    def _snooze(self, fired, minutes: float) -> None:
        self._snoozed.append((datetime.now() + timedelta(minutes=minutes), fired))

    # --- menu --------------------------------------------------------------
    def _menu_style(self) -> dict:
        """The right-click menu follows the same light/dark palette as the dialog."""
        p = self._palette()
        return dict(
            tearoff=0, bg=p.card, fg=p.fg, activebackground=p.accent,
            activeforeground=p.on_accent, activeborderwidth=0, borderwidth=0,
            relief="flat", selectcolor=p.accent_text, disabledforeground=p.disabled,
        )

    def _restyle_menus(self) -> None:
        # The popup is drawn from the theme each time it opens, so there is
        # nothing to restyle; one that is open is simply closed.
        if getattr(self, "_menu_popup", None) is not None:
            self._menu_popup.close()

    def _build_menu(self) -> None:
        self.var_24h = tk.BooleanVar(value=self.s["use_24h"])
        self.var_seconds = tk.BooleanVar(value=self.s["show_seconds"])
        self.var_date = tk.BooleanVar(value=self.s["show_date"])
        self.var_bar = tk.BooleanVar(value=self.s["seconds_bar"])
        self.var_tabular = tk.BooleanVar(value=self.s["tabular_digits"])
        self.var_click_through = tk.BooleanVar(value=self.s["click_through"])
        self.var_topmost = tk.BooleanVar(value=self.s["topmost"])
        self.var_hover = tk.BooleanVar(value=self.s["hover_boost"])
        self.var_lock = tk.BooleanVar(value=self.s["lock_position"])
        self.var_startup = tk.BooleanVar(value=startup_enabled())
        self.var_calendar = tk.BooleanVar(value=self.s["calendar_enabled"])
        self.var_next_meeting = tk.BooleanVar(value=self.s["show_next_meeting"])
        self.var_sound = tk.BooleanVar(value=self.s["alerts_sound"])
        self.var_show_timer = tk.BooleanVar(value=self.s["show_timer"])
        self.var_peek = tk.BooleanVar(value=self.s["peek_enabled"])
        self.var_minimized = tk.BooleanVar(value=self.s["minimized"])
        self.var_compact = tk.BooleanVar(value=self.s["compact"])
        self.theme_var = tk.StringVar(value=self.s["theme"])
        self.family_var = tk.StringVar(value=self.s["font_family"])

        self._menu_popup = None

    def _hotkey_text(self, hotkey_id: int, fallback: str) -> str:
        hotkeys = getattr(self, "hotkeys", None)
        return (hotkeys.label(hotkey_id) if hotkeys is not None else "") or fallback

    def _menu_items(self) -> list:
        """The right-click menu, built fresh each time it opens so every tick
        and label reflects the settings as they stand."""
        pm, s = popupmenu, self.s

        def flip(var, then):
            # The toggle handlers read their BooleanVar; a drawn menu has no
            # widget to flip it for them.
            def run():
                var.set(not var.get())
                then()
            return run

        theme_items = [
            pm.radio(name, (lambda n=name: s["theme"] == n),
                     (lambda n=name: self.set_theme(n)))
            for name in themes.THEMES
        ]
        opacity_items = [
            pm.radio("%d%%" % pct, (lambda p=pct: int(round(s["opacity"] * 100)) == p),
                     (lambda p=pct: self.set_opacity(p / 100)))
            for pct in (100, 90, 80, 70, 60, 50, 40, 30, 20, 10)
        ]
        size_items = [
            pm.radio("%d px" % size, (lambda z=size: int(s["font_size"]) == z),
                     (lambda z=size: self.set_font_size(z)))
            for size in (24, 32, 44, 52, 68, 88, 120)
        ]
        font_items = [
            pm.radio(family, (lambda f=family: s["font_family"] == f),
                     (lambda f=family: self.set_font_family(f)))
            for family in render.FONT_FILES
        ]
        checks = [
            pm.check(label, (lambda k=key: bool(s[k])),
                     flip(var, lambda k=key, v=var, r=repaint: self._toggle(k, v, r)))
            for label, var, key, repaint in (
                ("24-hour clock", self.var_24h, "use_24h", True),
                ("Show seconds", self.var_seconds, "show_seconds", True),
                ("Show date", self.var_date, "show_date", True),
                ("Meeting progress bar", self.var_bar, "seconds_bar", True),
                ("Monospaced digits", self.var_tabular, "tabular_digits", True),
                ("Compact view", self.var_compact, "compact", True),
                ("Solid while in use", self.var_hover, "hover_boost", False),
                ("Lock position", self.var_lock, "lock_position", False),
            )
        ]
        clock_checks, view_checks = checks[:6], checks[6:]   # compact view is a look
        appearance = [
            pm.submenu("Theme", theme_items),
            pm.submenu("Opacity", opacity_items),
            pm.submenu("Size", size_items),
            pm.submenu("Font", font_items),
            pm.separator(),
            *clock_checks,
        ]
        behaviour = [
            *view_checks,
            pm.check("Always on top", lambda: bool(s["topmost"]),
                     flip(self.var_topmost, self._toggle_topmost)),
            pm.check(self._click_through_label(), lambda: bool(s["click_through"]),
                     flip(self.var_click_through, self._set_click_through),
                     enabled=bool(getattr(self, "escape_hotkey", ""))),
            pm.separator(),
            pm.check("Start with Windows", lambda: bool(self.var_startup.get()),
                     flip(self.var_startup, self._toggle_startup)),
            pm.check("Minimize to tray", lambda: bool(s["minimized"]),
                     lambda: self.set_minimized(not s["minimized"])),
            pm.command("Peek now", self.peek_now),
        ]
        calendar = [
            pm.check("Outlook calendar", lambda: bool(s["calendar_enabled"]),
                     flip(self.var_calendar, self._toggle_calendar)),
            pm.separator(),
            pm.command("Meeting reminders…", lambda: self.open_settings_tab("meetings")),
            pm.command("Calendars…", lambda: self.open_settings_tab("calendars")),
            pm.command("Prayer times…", lambda: self.open_settings_tab("prayer")),
        ]
        timers = [
            pm.command("Alarms…", lambda: self.open_settings_tab("alarms")),
            pm.command("Timers…", lambda: self.open_settings_tab("timers")),
            pm.separator(),
            pm.command("Start / stop stopwatch", self.toggle_stopwatch),
            pm.command("Reset stopwatch", self.reset_stopwatch),
        ]
        # Stopping an adhan, when one is due or playing, comes before everything else: it is
        # urgent in a way nothing else on this menu is, and it is not always there.
        stopping = self.routines.stoppable()
        # A short top level: the two things you reach for, then one submenu
        # per topic, then the two ways out.
        return [
            *([pm.command("Stop %s's adhan" % stopping.name, self._stop_adhan)] if stopping else []),
            pm.command("Today's meetings", self.toggle_meetings),
            pm.command("Settings…", self.open_settings),
            pm.separator(),
            pm.submenu("Appearance", appearance),
            pm.submenu("Behaviour", behaviour),
            pm.submenu("Calendar", calendar),
            pm.submenu("Alarms & timers", timers),
            pm.separator(),
            pm.command("Reset position", self._reset_position,
                       self._hotkey_text(HOTKEY_SHOW, "Ctrl+Alt+K")),
            pm.command("Exit", self.quit, self._hotkey_text(HOTKEY_QUIT, "Ctrl+Alt+Q")),
            pm.separator(),
            pm.command("Floating Clock %s" % __version__, lambda: None, enabled=False),
        ]

    def _open_menu(self, x: int, y: int) -> None:
        if self._menu_popup is not None:
            self._menu_popup.close()
        self._hide_hover_card()
        self._menu_popup = popupmenu.PopupMenu(
            self.root, self.s["theme"], self._scale, self._menu_items(),
            on_close=self._menu_closed,
        )
        self._menu_popup.show(x, y)
        self._solidify()

    def _menu_closed(self) -> None:
        self._menu_popup = None
        self._solidify()

    # --- lifecycle ---------------------------------------------------------
    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.s["x"], self.s["y"] = self._position()
        cfg.save(self.s)
        self.scheduler.save()
        self._close_panel()
        if self.peek is not None:
            self.peek.cancel_timer()
        if self.tray is not None:
            self.tray.destroy()
        if self.poller is not None:
            self.poller.stop()
        self._fade_out()

    def _fade_out(self, step: int = FADE_STEPS) -> None:
        self._fade = max(0.0, step / FADE_STEPS)
        self._push()
        if step > 0:
            self.root.after(FADE_INTERVAL, self._fade_out, step - 1)
        else:
            self.root.destroy()

    def _autosave(self) -> None:
        """Write the settings whenever they have changed.

        They used to be saved only on a clean exit, and an upgrade stops the
        clock with taskkill -- so every toggle, and every company discovered
        since launch, was lost each time a new build went on. Checked every
        half minute; a save is cheap and the file is small.
        """
        try:
            snapshot = json.dumps(self.s, sort_keys=True, default=str)
            if snapshot != getattr(self, "_saved_snapshot", None):
                cfg.save(self.s)
                self._saved_snapshot = snapshot
        except Exception:
            log.debug("autosave failed", exc_info=True)
        self._rearm(30_000, self._autosave)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            if not self._closing:
                cfg.save(self.s)
