"""The day timeline: where you are in your day, and where the meetings sit.

The bar spans a full 24 hours anchored on the hour your day starts, and
`work_end_fraction` says where the working part of it stops so the renderer
can shade the rest. Nothing can fall outside the bar.

Pure arithmetic, no drawing and no COM, so the awkward cases -- a day starting
at 05:00, a meeting at 01:30, the hours before the day has begun -- can be
tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Slack when deciding whether two blocks touch. Back-to-back meetings read as
# one booked stretch; blocks that merely land close together are the
# renderer's problem, since only it knows how many pixels apart they are.
MERGE_DISTANCE = 0.0008
DAY_MINUTES = 24 * 60

# Most urgent first; used when merging overlapping marks.
STATE_ORDER = ("live", "next", "ahead", "done")


@dataclass(frozen=True)
class Mark:
    """One block on the timeline: where a meeting starts, and how long it runs.

    A duration rather than a point, so a two-hour block reads as four times
    the half-hour one next to it -- the shape of the day at a glance.
    """

    position: float        # 0..1, where it starts
    end_position: float    # 0..1, where it ends
    state: str             # live | next | ahead | done
    subject: str = ""
    start: datetime | None = None
    count: int = 1         # meetings merged into this block
    colour: str = ""       # the organisation's colour, "" for the accent
    end: datetime | None = None
    org: str = ""          # the organisation's name, for the hover detail

    @property
    def width(self) -> float:
        return max(0.0, self.end_position - self.position)


def _minutes(hour: float) -> int:
    return int(round(float(hour) * 60)) % DAY_MINUTES


def day_window(now: datetime, start_hour: float) -> tuple[datetime, datetime]:
    """The 24 hours your current day covers, beginning at `start_hour`.

    Always a full day. A bar spanning only working hours would silently drop
    anything scheduled outside them, and the 06:30 or 23:00 meeting is exactly
    the one you cannot afford to lose. Anything past the end of your working
    day lands in the tail of the bar; the next cycle begins at your start hour
    again, so it reads as tomorrow.

    Hours are fractional, so a day can start at 05:30 as easily as at 05:00.
    """
    start_minutes = _minutes(start_hour)
    anchor = now.replace(
        hour=start_minutes // 60, minute=start_minutes % 60, second=0, microsecond=0
    )
    if now < anchor:
        # Before today's start hour you are still inside yesterday's day.
        anchor -= timedelta(days=1)
    return anchor, anchor + timedelta(minutes=DAY_MINUTES)


def work_end_fraction(start_hour: float, end_hour: float) -> float:
    """Where the working day ends, as a 0..1 position along the 24-hour bar.

    1.0 for a round-the-clock day, so callers need no special case for
    "there are no off-hours to shade".
    """
    span = (_minutes(end_hour) - _minutes(start_hour)) % DAY_MINUTES
    return 1.0 if span == 0 else span / DAY_MINUTES


def progress(now: datetime, window: tuple[datetime, datetime]) -> float:
    start, end = window
    span = (end - start).total_seconds()
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (now - start).total_seconds() / span))


def _state(event, now: datetime, is_next: bool) -> str:
    if event.is_live(now):
        return "live"
    if event.end <= now:
        return "done"
    return "next" if is_next else "ahead"


def marks(events, now: datetime, window: tuple[datetime, datetime],
          colour_for=None, label_for=None) -> tuple[Mark, ...]:
    """One block per meeting inside the window, overlaps merged, in time order.

    `colour_for(event)` names the organisation's colour for a block, so the
    bar can show whose meetings fill the day; None leaves every block in
    the accent.
    """
    start, end = window
    span = (end - start).total_seconds()
    if span <= 0:
        return ()

    # All-day entries -- a holiday, "out of office", a conference -- would
    # paint the whole bar and say nothing about when you are actually busy.
    # The panel lists them under their own heading instead.
    events = [e for e in events if not getattr(e, "all_day", False)]
    ahead = [e for e in events if e.start > now]
    next_event = min(ahead, key=lambda e: e.start) if ahead else None

    def fraction(moment: datetime) -> float:
        return max(0.0, min(1.0, (moment - start).total_seconds() / span))

    found: list[Mark] = []
    for event in events:
        # Half-open: a meeting exactly on the rollover belongs to the day it
        # opens, not to both the one ending and the one beginning.
        if not (start <= event.start < end):
            continue
        found.append(
            Mark(
                position=fraction(event.start),
                end_position=fraction(min(event.end, end)),
                state=_state(event, now, event is next_event),
                subject=event.subject,
                start=event.start,
                colour=(colour_for(event) or "") if colour_for else "",
                end=event.end,
                org=(label_for(event) or "") if label_for else "",
            )
        )
    found.sort(key=lambda m: (m.position, m.end_position))

    # Genuinely overlapping meetings cannot be stacked on a bar this thin, so
    # they become one block spanning the union: that stretch of the day is
    # booked either way. Back-to-back meetings only touch, and stay separate --
    # two half-hours in a row should not read as a single hour.
    merged: list[Mark] = []
    for mark in found:
        if merged and mark.position < merged[-1].end_position - MERGE_DISTANCE:
            previous = merged[-1]
            keep = min((previous, mark), key=lambda m: STATE_ORDER.index(m.state))
            merged[-1] = Mark(
                position=previous.position,
                end_position=max(previous.end_position, mark.end_position),
                state=keep.state,
                subject=keep.subject,
                start=keep.start,
                count=previous.count + 1,
                colour=keep.colour,
                end=max(previous.end, mark.end) if previous.end and mark.end else keep.end,
                org=keep.org,
            )
            continue
        merged.append(mark)
    return tuple(merged)


def remaining(events, now: datetime, window: tuple[datetime, datetime]) -> int:
    """Meetings still to come inside the window -- the 'how many left' number."""
    _start, end = window
    return sum(1 for e in events if e.start > now and e.start < end)
