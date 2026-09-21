"""What the clock is doing while it works out a masjid's times, told as a timeline.

Choosing a masjid can take a minute or more. The clock reaches the site, looks for
a timetable feed, reads the page and the pages it links to, opens them in a browser
for the times a script draws in, and -- when the masjid publishes nothing -- does
all of that again for the masjids nearby. One line reading "Checking..." is no way
to sit through that: nobody can tell working from stuck, or why it is slow.

This is the record of it, in order. The code that does the work reports into a
Timeline (`timeline.current()`, from anywhere on the worker's thread); each window
polls `snapshot()` a few times a second and draws what it finds. Polling and not
callbacks is deliberate: the worker never has to reach the UI thread, which is the
very hop that once left the Qt picker stuck on "Searching..." (see qt/ui.py).

Nothing here imports a GUI toolkit, so all of it is tested without one. Nothing
here changes what is read or how: with no Timeline listening, every call is a
no-op, which is how the clock's own background reads run.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

log = logging.getLogger(__name__)

PENDING, RUNNING, DONE, SKIPPED, FAILED = "pending", "running", "done", "skipped", "failed"
FINISHED = (DONE, SKIPPED, FAILED)

# Every step a read can go through: what to call it, and how many seconds it usually
# takes. The seconds are what the bar is weighted by -- a step that takes half a
# minute is half a minute of bar -- and how far a running step pushes the bar
# before it is done.
STEPS = {
    "reach":    ("Reaching the website", 4.0),
    "feed":     ("Looking for a timetable feed", 3.0),
    "embed":    ("Looking for an embedded timetable", 3.0),
    "calendar": ("Looking for a calendar feed", 3.0),
    "home":     ("Reading the home page", 4.0),
    "pages":    ("Reading its prayer-time pages", 8.0),
    "browser":  ("Opening the pages in a browser", 25.0),
    "ask":      ("Asking mawaqit.net for its timetable", 6.0),
    "check":    ("Checking the times make sense", 1.0),
}
WEBSITE = ("reach", "feed", "embed", "calendar", "home", "pages", "browser", "check")
MAWAQIT = ("ask", "check")

# What trying one neighbouring masjid is worth on the bar, in the same seconds.
ATTEMPT_SECONDS = 10.0
# A running step may take the bar this far towards done, and no further: it is an
# estimate of a step, never a claim that it has finished.
CREEP = 0.9


class Cancelled(BaseException):
    """The person pressed Stop.

    Not an Exception, on purpose: the readers are full of `except Exception:` that
    mean "that page did not work, try the next", and a Stop caught by one of those
    would be taken for a page that failed and carried on past.
    """


@dataclass(frozen=True)
class Section:
    """One source of times, as planned: what to call it and what it will go through."""

    key: str
    label: str
    sub: str = ""                 # the host, or a hint, shown beside the label
    steps: tuple = ()             # step keys, for one of the masjid's own sources
    attempts: tuple = ()          # (key, name, "1.8 km away"), for the neighbours


class _Row:
    __slots__ = ("key", "kind", "label", "sub", "detail", "state", "level", "parent",
                 "expected", "started", "ended", "pinned")

    def __init__(self, key, kind, label, level, parent=None, sub="", expected=0.0):
        self.key = key
        self.kind = kind                  # "section" | "step" | "attempt"
        self.label = label
        self.sub = sub
        self.detail = ""
        self.state = PENDING
        self.level = level
        self.parent = parent              # a step's section key
        self.expected = expected
        self.started = None
        self.ended = None
        self.pinned = False               # a finished section that stays open


def _short(exc: BaseException) -> str:
    """One short line for what went wrong."""
    return (str(exc).strip()[:90]) or exc.__class__.__name__


# --- saying how it went ------------------------------------------------------
class _Handle:
    """What `with timeline.step(...) as s` gives back. Say how it went, or don't:

    leaving the block without a word is a success, and leaving it by an exception is
    a failure that names the exception and lets it carry on.
    """

    def __init__(self, line: "Timeline", row: _Row) -> None:
        self._line = line
        self._row = row

    def note(self, detail: str) -> None:
        """What it is doing right now, while it runs."""
        self._line._note(self._row, detail)

    def ok(self, detail: str | None = None) -> None:
        self._line._finish(self._row, DONE, detail)

    def fail(self, detail: str | None = None) -> None:
        self._line._finish(self._row, FAILED, detail)

    def skip(self, detail: str | None = None) -> None:
        self._line._finish(self._row, SKIPPED, detail)

    def __enter__(self) -> "_Handle":
        return self

    def __exit__(self, kind, exc, _tb) -> bool:
        if self._row.state not in FINISHED:
            if exc is None:
                self._line._finish(self._row, DONE, None)
            elif isinstance(exc, Cancelled):
                self._line._finish(self._row, FAILED, "stopped")
            else:
                self._line._finish(self._row, FAILED, _short(exc))
        self._line._leave(self._row)
        return False


class _Glimpse:
    """A step seen from inside a neighbour's attempt.

    A neighbour is one line, not a checklist of its own: all it shows of a step is
    what it is doing at the moment, in its detail.
    """

    def __init__(self, line: "Timeline", row: _Row, key: str, detail: str) -> None:
        self._line = line
        self._row = row
        self._label = STEPS.get(key, (key, 0.0))[0]
        self.note(detail)

    def note(self, detail: str) -> None:
        self._line._note(self._row, "%s%s" % (self._label, ": " + detail if detail else "..."))

    def ok(self, detail: str | None = None) -> None:
        pass

    def fail(self, detail: str | None = None) -> None:
        pass

    def skip(self, detail: str | None = None) -> None:
        pass

    def __enter__(self) -> "_Glimpse":
        return self

    def __exit__(self, kind, exc, _tb) -> bool:
        return False


class _Nothing:
    """Where a call lands when nobody is listening or the step is not on the plan."""

    def note(self, detail: str) -> None:
        pass

    def ok(self, detail: str | None = None) -> None:
        pass

    def fail(self, detail: str | None = None) -> None:
        pass

    def skip(self, detail: str | None = None) -> None:
        pass

    def __enter__(self) -> "_Nothing":
        return self

    def __exit__(self, kind, exc, _tb) -> bool:
        return False


_NOTHING = _Nothing()


# --- the timeline ------------------------------------------------------------
class Timeline:
    """The steps of one check, and how far along it is. Safe to read from any thread."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._rows: list[_Row] = []
        self._sections: dict[str, _Row] = {}
        self._steps: dict[tuple, _Row] = {}
        self._section: _Row | None = None        # the source being worked on
        self._attempt: _Row | None = None        # the neighbour being tried, inside it
        self._cancelled = threading.Event()
        self.title = ""
        self.started = clock()
        self.ended: float | None = None
        self.outcome = ""                        # "" while it runs, then found | none | stopped
        self.summary = ""

    # the plan
    def plan(self, title: str, sections) -> None:
        """Lay out what is going to be tried, so the whole of it is visible from the start."""
        with self._lock:
            self.title = title
            for spec in sections:
                section = _Row(spec.key, "section", spec.label, 0, sub=spec.sub)
                self._rows.append(section)
                self._sections[spec.key] = section
                for step in spec.steps:
                    label, expected = STEPS[step]
                    row = _Row(step, "step", label, 1, parent=spec.key, expected=expected)
                    self._rows.append(row)
                    self._steps[(spec.key, step)] = row
                for key, name, sub in spec.attempts:
                    row = _Row(key, "attempt", name, 1, parent=spec.key, sub=sub,
                               expected=ATTEMPT_SECONDS)
                    self._rows.append(row)
                    self._steps[(spec.key, key)] = row

    # stopping
    def cancel(self) -> None:
        """Ask the work to stop. It does, at the next step: a request in flight is not abandoned."""
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        """Raise Cancelled if the person has pressed Stop. The work calls this between steps."""
        if self._cancelled.is_set():
            raise Cancelled()

    # reporting: sections, neighbours and steps
    def section(self, key: str, detail: str = ""):
        """Begin one of the planned sources. Use as `with line.section("site") as s:`."""
        with self._lock:
            self.check()
            row = self._sections.get(key)
            if row is None:
                return _NOTHING
            row.state, row.started, row.detail = RUNNING, self._clock(), detail
            self._section = row
            return _Handle(self, row)

    def attempt(self, key: str):
        """Begin trying one planned neighbour."""
        with self._lock:
            self.check()
            row = self._lookup(key, attempts=True)
            if row is None:
                return _NOTHING
            row.state, row.started = RUNNING, self._clock()
            self._attempt = row
            return _Handle(self, row)

    def step(self, key: str, detail: str = ""):
        """Begin a step of whatever is being read. Use as `with line.step("reach") as s:`."""
        with self._lock:
            self.check()
            if self._attempt is not None:
                return _Glimpse(self, self._attempt, key, detail)
            row = self._lookup(key)
            if row is None or row.state in FINISHED:
                return _NOTHING                     # not planned, or already spoken for
            row.state, row.started, row.detail = RUNNING, self._clock(), detail
            return _Handle(self, row)

    # ...and the same without the block, for code that starts a step in one place and ends it in another
    def start(self, key: str, detail: str = "") -> None:
        self.step(key, detail)

    def note(self, key: str, detail: str) -> None:
        with self._lock:
            if self._attempt is not None:
                _Glimpse(self, self._attempt, key, detail)
                return
            row = self._lookup(key)
            if row is not None:
                self._note(row, detail)

    def done(self, key: str, detail: str | None = None) -> None:
        self._finish_key(key, DONE, detail)

    def fail(self, key: str, detail: str | None = None) -> None:
        self._finish_key(key, FAILED, detail)

    def skip(self, key: str, detail: str | None = None) -> None:
        self._finish_key(key, SKIPPED, detail)

    def finish(self, outcome: str, summary: str = "", open: str | None = None) -> None:
        """The check is over: found | none | stopped. Everything left undone is put away.

        `open` names the source that supplied the answer, which stays unfolded so that
        how the times were come by can still be read afterwards. When nothing was found
        there is no such source, and what is wanted is the opposite: every source that
        was tried stays unfolded, so that each step, and why it did not work, can be read
        -- except a source with a step or two, whose own line already says all there is.
        """
        with self._lock:
            if self.outcome:
                return
            self.outcome, self.summary, self.ended = outcome, summary, self._clock()
            for row in self._rows:
                if row.state == PENDING:
                    row.state = SKIPPED
                elif row.state == RUNNING:
                    row.state = FAILED if outcome == "stopped" else DONE
                    row.ended = self.ended
                if row.kind == "section" and (row.key == open or (
                        outcome == "none" and open is None and row.state == FAILED
                        and self._has_more_to_say(row))):
                    row.pinned = True
            self._section = self._attempt = None

    # internals ----------------------------------------------------------------
    def _has_more_to_say(self, section: _Row) -> bool:
        """Whether unfolding a source would tell more than its own line: it has neighbours
        each with a reason of its own, or more than a step or two."""
        children = [row for row in self._rows if row.parent == section.key]
        return any(row.kind == "attempt" for row in children) or len(children) > 2

    def _lookup(self, key: str, attempts: bool = False) -> _Row | None:
        if self._section is None:
            return None
        row = self._steps.get((self._section.key, key))
        if row is None or (row.kind == "attempt") != attempts:
            return None
        return row

    def _note(self, row: _Row, detail: str) -> None:
        with self._lock:
            if row.state not in FINISHED:
                row.detail = detail

    def _finish_key(self, key: str, state: str, detail: str | None) -> None:
        with self._lock:
            row = self._lookup(key)
            if row is not None:
                self._finish(row, state, detail)

    def _finish(self, row: _Row, state: str, detail: str | None) -> None:
        with self._lock:
            if row.state in FINISHED:
                return                              # the first word on how it went stands
            now = self._clock()
            if row.started is None:
                row.started = now
            row.state, row.ended = state, now
            if detail is not None:
                row.detail = detail
            log.debug("Timeline: %s %s %s (%.1f s) %s", row.kind, row.key, state,
                      now - row.started, row.detail)

    def _leave(self, row: _Row) -> None:
        with self._lock:
            if row.kind == "attempt":
                if self._attempt is row:
                    self._attempt = None
                return
            if row.kind != "section":
                return
            ok = row.state == DONE
            for child in self._rows:
                if child.parent != row.key:
                    continue
                if child.state == PENDING:
                    child.state = SKIPPED
                    if ok and child.kind == "step" and not child.detail:
                        child.detail = "not needed"
                elif child.state == RUNNING:
                    child.state = DONE if ok else FAILED
                    child.ended = self._clock()
            if self._section is row:
                self._section = None

    # reading it ---------------------------------------------------------------
    def snapshot(self) -> dict:
        """Everything a window needs to draw this, as plain data.

        {"title", "busy", "elapsed", "fraction", "outcome", "summary", "now", "rows"}
        where each row is {"key", "kind", "label", "sub", "detail", "state", "level",
        "seconds"}. A source's steps are listed while it runs, and after it only if it
        is the one that supplied the answer; the rest collapse to the line that says how
        it went.
        """
        with self._lock:
            now = self._clock()
            rows = []
            running = None
            for row in self._rows:
                if row.level:
                    parent = self._sections[row.parent]
                    if not (parent.state == RUNNING or parent.pinned):
                        continue
                if row.state == RUNNING:
                    running = row
                seconds = None
                if row.started is not None:
                    seconds = max(0.0, (row.ended if row.ended is not None else now) - row.started)
                rows.append({
                    "key": row.key if not row.level else "%s.%s" % (row.parent, row.key),
                    "kind": row.kind, "label": row.label, "sub": row.sub, "detail": row.detail,
                    "state": row.state, "level": row.level, "seconds": seconds,
                })
            end = self.ended if self.ended is not None else now
            return {
                "title": self.title, "busy": not self.outcome, "outcome": self.outcome,
                "summary": self.summary, "elapsed": max(0.0, end - self.started),
                "fraction": self._fraction(now), "rows": rows,
                "now": ("%s %s" % (running.label, running.detail)).strip() if running else "",
            }

    def _fraction(self, now: float) -> float:
        """How far along, 0..1: the seconds of work done over the seconds planned.

        The steps that have not run are counted as if they will, which is why a masjid
        whose first source answers jumps to full: the rest did not need doing. A step
        that is running counts for part of itself, more the longer it has run but never
        all of it, so a long step keeps the bar moving without claiming it is finished.
        """
        if self.outcome:
            return 1.0
        total = done = 0.0
        for row in self._rows:
            if not row.level:
                continue
            total += row.expected
            if row.state in FINISHED:
                done += row.expected
            elif row.state == RUNNING and row.started is not None:
                spent = max(0.0, now - row.started)
                done += row.expected * CREEP * (1.0 - math.exp(-spent / max(row.expected, 1.0)))
        return min(0.999, done / total) if total else 0.0


class _Idle(Timeline):
    """The timeline nobody is watching: everything is accepted and nothing is kept."""

    def plan(self, title, sections) -> None:
        pass

    def cancel(self) -> None:
        pass

    def finish(self, outcome, summary="", open=None) -> None:
        pass


class _Hushed(_Idle):
    """Keeps nothing, like _Idle, but still honours Stop for the timeline it stands in for."""

    def __init__(self, behind: Timeline) -> None:
        super().__init__()
        self._behind = behind

    def check(self) -> None:
        self._behind.check()


_IDLE = _Idle()
_local = threading.local()


def current() -> Timeline:
    """The timeline this thread is reporting into, or one that keeps nothing."""
    return getattr(_local, "line", None) or _IDLE


@contextmanager
def using(line: Timeline):
    """Report into `line` from this thread, for the length of the block."""
    before = getattr(_local, "line", None)
    _local.line = line
    try:
        yield line
    finally:
        _local.line = before


@contextmanager
def hushed():
    """Read on without reporting, for a read that happens inside a step of its own.

    An embedded widget is fetched with the same reader that reads the site, and that
    reader would write its "home page" and "pages" over the steps of the site around
    it. Stop still works inside.
    """
    with using(_Hushed(current())):
        yield


# --- how to say it -------------------------------------------------------------
def clock_text(seconds: float) -> str:
    """"0:07" or "1:05": the total elapsed, as a stopwatch."""
    whole = max(0, int(seconds))
    return "%d:%02d" % (whole // 60, whole % 60)


def span_text(seconds: float | None) -> str:
    """"0.4 s", "12 s" or "1:05" for one step; "" for one that has not started."""
    if seconds is None:
        return ""
    if seconds < 10:
        return "%.1f s" % seconds
    if seconds < 60:
        return "%d s" % round(seconds)
    return clock_text(seconds)
