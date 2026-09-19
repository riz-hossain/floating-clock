"""Alarms, timers and meeting reminders: state, persistence and scheduling.

Deliberately free of UI and COM so the firing rules can be tested directly.
The app calls `Scheduler.due()` on every tick and shows whatever comes back.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import settings as cfg

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS = (0, 1, 2, 3, 4)


def alerts_path() -> str:
    return os.path.join(os.path.dirname(cfg.settings_path()), "alerts.json")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Alarm:
    hour: int = 9
    minute: int = 0
    label: str = "Alarm"
    days: list[int] = field(default_factory=list)  # empty = fire once, then off
    enabled: bool = True
    id: str = field(default_factory=_new_id)
    last_fired: str = ""  # ISO date+minute, so a restart cannot double-fire

    def time_text(self, use_24h: bool = False) -> str:
        if use_24h:
            return "%02d:%02d" % (self.hour, self.minute)
        hour = self.hour % 12 or 12
        return "%d:%02d %s" % (hour, self.minute, "am" if self.hour < 12 else "pm")

    def repeat_text(self) -> str:
        if not self.days:
            return "Once"
        if sorted(self.days) == list(WEEKDAYS):
            return "Weekdays"
        if len(self.days) == 7:
            return "Every day"
        return ", ".join(DAY_NAMES[d] for d in sorted(self.days))

    def next_occurrence(self, now: datetime) -> datetime | None:
        """When this alarm will next go off, or None if it never will."""
        if not self.enabled:
            return None
        candidate = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if not self.days:
            return candidate if candidate > now else candidate + timedelta(days=1)
        for offset in range(8):
            day = candidate + timedelta(days=offset)
            if day.weekday() in self.days and day > now:
                return day
        return None

    def should_fire(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if now.hour != self.hour or now.minute != self.minute:
            return False
        if self.days and now.weekday() not in self.days:
            return False
        return self.last_fired != now.strftime("%Y-%m-%dT%H:%M")

    def mark_fired(self, now: datetime) -> None:
        self.last_fired = now.strftime("%Y-%m-%dT%H:%M")
        if not self.days:
            self.enabled = False  # one-shot alarms disarm themselves


@dataclass
class Timer:
    total_seconds: int = 300
    label: str = "Timer"
    id: str = field(default_factory=_new_id)
    ends_at: str = ""      # ISO timestamp while running
    remaining: float = 0.0  # seconds left while paused
    fired: bool = False

    @property
    def running(self) -> bool:
        return bool(self.ends_at)

    def seconds_left(self, now: datetime | None = None) -> float:
        if not self.running:
            return max(0.0, self.remaining)
        now = now or datetime.now()
        try:
            return max(0.0, (datetime.fromisoformat(self.ends_at) - now).total_seconds())
        except ValueError:
            return 0.0

    def start(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        seconds = self.remaining if self.remaining > 0 else self.total_seconds
        self.ends_at = (now + timedelta(seconds=seconds)).isoformat()
        self.remaining = 0.0
        self.fired = False

    def pause(self, now: datetime | None = None) -> None:
        if self.running:
            self.remaining = self.seconds_left(now)
            self.ends_at = ""

    def reset(self) -> None:
        self.ends_at = ""
        self.remaining = 0.0
        self.fired = False

    def should_fire(self, now: datetime | None = None) -> bool:
        return self.running and not self.fired and self.seconds_left(now) <= 0


@dataclass
class Stopwatch:
    """Counts up. Stored as a start instant plus banked seconds rather than a
    running total, so it stays correct across a restart and needs no ticking."""

    started_at: str = ""      # ISO timestamp while running
    accumulated: float = 0.0  # seconds banked by previous runs

    @property
    def running(self) -> bool:
        return bool(self.started_at)

    def elapsed(self, now: datetime | None = None) -> float:
        if not self.running:
            return max(0.0, self.accumulated)
        now = now or datetime.now()
        try:
            since = (now - datetime.fromisoformat(self.started_at)).total_seconds()
        except ValueError:
            return max(0.0, self.accumulated)
        return max(0.0, self.accumulated + since)

    def start(self, now: datetime | None = None) -> None:
        if not self.running:
            self.started_at = (now or datetime.now()).isoformat()

    def stop(self, now: datetime | None = None) -> None:
        if self.running:
            self.accumulated = self.elapsed(now)
            self.started_at = ""

    def toggle(self, now: datetime | None = None) -> None:
        self.stop(now) if self.running else self.start(now)

    def reset(self) -> None:
        self.started_at = ""
        self.accumulated = 0.0


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def parse_duration(text: str) -> int | None:
    """Accepts '90', '5m', '1h30m', '00:90', '1:30:00'. Returns seconds."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if ":" in text:
        try:
            parts = [int(p) for p in text.split(":")]
        except ValueError:
            return None
        while len(parts) < 3:
            parts.insert(0, 0)
        hours, minutes, seconds = parts[-3:]
        return hours * 3600 + minutes * 60 + seconds
    total = 0
    number = ""
    matched = False
    for char in text:
        if char.isdigit():
            number += char
        elif char in "hms" and number:
            total += int(number) * {"h": 3600, "m": 60, "s": 1}[char]
            number = ""
            matched = True
        elif char == " ":
            continue
        else:
            return None
    if number:
        # A bare number means minutes, which is what people mean by "set a 5".
        total += int(number) * (1 if matched else 60)
    return total or None


def imminent(events, now: datetime, lead_minutes: float, seen=()) -> list:
    """(key, event) for meetings starting inside the lead, minus those `seen`.

    The rule behind the nudge, kept here beside the other firing rules and
    free of any window so it can be tested directly. All-day entries never
    count: they "start" at midnight, and shaking the clock over a holiday
    would teach the user to ignore it.
    """
    ready = []
    for event in events:
        if getattr(event, "all_day", False):
            continue
        key = "nudge:%s:%s" % (getattr(event, "entry_id", ""), event.start.isoformat())
        if key in seen:
            continue
        if 0 <= event.minutes_until(now) <= lead_minutes:
            ready.append((key, event))
    return ready


@dataclass
class Fired:
    """Something that just came due and needs to be shown."""

    kind: str          # "meeting" | "alarm" | "timer" | "prayer"
    title: str
    detail: str = ""
    join_url: str = ""
    key: str = ""      # de-duplication key
    source_id: str = ""


class Scheduler:
    """Owns alarms and timers, and decides what is due."""

    def __init__(self) -> None:
        self.alarms: list[Alarm] = []
        self.timers: list[Timer] = []
        self.stopwatch = Stopwatch()
        self._notified: set[str] = set()
        self._lock = threading.Lock()
        # Set when alerts.json is there but would not open: the empty lists
        # that leaves must never be saved over the alarms still in the file.
        self.load_failed = False
        self.load()

    # --- persistence -------------------------------------------------------
    def load(self) -> None:
        path = alerts_path()
        self.load_failed = False
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except ValueError:
            # Not valid JSON: keep a copy, then start clean and let saves
            # through -- otherwise no alarm could ever be kept again.
            aside = "%s.unreadable-%s.json" % (path[:-5], time.strftime("%Y%m%d-%H%M%S"))
            try:
                shutil.copyfile(path, aside)
            except OSError:
                pass
            logging.getLogger(__name__).error(
                "%s is not valid JSON; kept a copy at %s", path, aside)
            return
        except OSError:
            self.load_failed = True
            logging.getLogger(__name__).error(
                "Could not open %s; alarms and timers will not be saved over it", path)
            return
        try:
            self.alarms = [Alarm(**a) for a in data.get("alarms", []) if isinstance(a, dict)]
            self.timers = [Timer(**t) for t in data.get("timers", []) if isinstance(t, dict)]
            watch = data.get("stopwatch")
            if isinstance(watch, dict):
                self.stopwatch = Stopwatch(**watch)
        except TypeError:
            # A file from a newer/older version: start clean rather than crash.
            self.alarms, self.timers = [], []

    def save(self) -> None:
        path = alerts_path()
        if self.load_failed and os.path.exists(path):
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "alarms": [asdict(a) for a in self.alarms],
                        "timers": [asdict(t) for t in self.timers],
                        "stopwatch": asdict(self.stopwatch),
                    },
                    fh,
                    indent=2,
                )
        except OSError:
            pass

    # --- mutation ----------------------------------------------------------
    def add_alarm(self, alarm: Alarm) -> Alarm:
        self.alarms.append(alarm)
        self.alarms.sort(key=lambda a: (a.hour, a.minute))
        self.save()
        return alarm

    def remove_alarm(self, alarm_id: str) -> None:
        self.alarms = [a for a in self.alarms if a.id != alarm_id]
        self.save()

    def add_timer(self, timer: Timer) -> Timer:
        self.timers.append(timer)
        self.save()
        return timer

    def remove_timer(self, timer_id: str) -> None:
        self.timers = [t for t in self.timers if t.id != timer_id]
        self.save()

    def active_timer(self, now: datetime | None = None) -> Timer | None:
        """The running timer closest to finishing -- what the clock displays."""
        running = [t for t in self.timers if t.running and not t.fired]
        if not running:
            return None
        return min(running, key=lambda t: t.seconds_left(now))

    def next_alarm(self, now: datetime | None = None) -> tuple[Alarm, datetime] | None:
        now = now or datetime.now()
        upcoming = []
        for alarm in self.alarms:
            when = alarm.next_occurrence(now)
            if when:
                upcoming.append((alarm, when))
        return min(upcoming, key=lambda pair: pair[1]) if upcoming else None

    # --- firing ------------------------------------------------------------
    def due(self, now: datetime, events=(), lead_minutes: float = 5.0) -> list[Fired]:
        """Everything that should pop right now, de-duplicated."""
        fired: list[Fired] = []
        with self._lock:
            changed = False

            for alarm in self.alarms:
                if alarm.should_fire(now):
                    alarm.mark_fired(now)
                    changed = True
                    fired.append(
                        Fired(
                            kind="alarm",
                            title=alarm.label or "Alarm",
                            detail=alarm.time_text(),
                            key="alarm:%s:%s" % (alarm.id, alarm.last_fired),
                            source_id=alarm.id,
                        )
                    )

            for timer in self.timers:
                if timer.should_fire(now):
                    timer.fired = True
                    timer.ends_at = ""
                    timer.remaining = 0.0
                    changed = True
                    fired.append(
                        Fired(
                            kind="timer",
                            title=timer.label or "Timer",
                            detail="%s is up" % format_duration(timer.total_seconds),
                            key="timer:%s:%s" % (timer.id, now.strftime("%Y%m%d%H%M")),
                            source_id=timer.id,
                        )
                    )

            for event in events:
                if getattr(event, "all_day", False):
                    # A holiday does not "start in 5 minutes" at midnight.
                    continue
                minutes = event.minutes_until(now)
                if not (0 <= minutes <= lead_minutes):
                    continue
                key = "meeting:%s:%s" % (event.entry_id, event.start.isoformat())
                if key in self._notified:
                    continue
                self._notified.add(key)
                detail = "%s · starts %s" % (
                    event.start.strftime("%H:%M"),
                    "now" if minutes < 1 else "in %d min" % round(minutes),
                )
                if event.location:
                    detail += " · %s" % event.location[:40]
                fired.append(
                    Fired(
                        kind="meeting",
                        title=event.subject,
                        detail=detail,
                        join_url=event.join_url,
                        key=key,
                        source_id=event.entry_id,
                    )
                )

            if changed:
                self.save()
        return fired

    def forget_old_notifications(self, keep: int = 200) -> None:
        if len(self._notified) > keep:
            self._notified = set(list(self._notified)[-keep:])
