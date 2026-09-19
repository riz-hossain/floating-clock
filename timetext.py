"""Times written the way the clock says them.

Both hosts need these and neither should reach into the other's UI module to
get them: the Qt host used to import clock_text from settings_ui and _span
from app, which dragged Tk onto macOS for two lines of string formatting.
"""

from __future__ import annotations

from datetime import datetime


def clock_text(moment: datetime, use_24h: bool, today=None) -> str:
    """Time of day, prefixed with the weekday when it is not today's.

    Ten meetings ahead can run into next week, and a bare "9:00 am" three
    rows down would read as this morning. Callers with nothing to compare
    against leave `today` out and get the bare time.
    """
    if use_24h:
        text = moment.strftime("%H:%M")
    else:
        text = moment.strftime("%I:%M %p").lstrip("0").lower()
    if today is not None and moment.date() != today:
        return "%s %s" % (moment.strftime("%a"), text)
    return text


def span(seconds: float) -> str:
    """'4h 10m' / '25m' for the hover detail's distances."""
    minutes = max(0, int(round(seconds / 60)))
    hours, minutes = divmod(minutes, 60)
    return "%dh %dm" % (hours, minutes) if hours else "%dm" % minutes


def parse_clock_time(text: str) -> tuple[int, int] | None:
    """Accepts '9', '9:05', '0905', '9:05 pm', '21:05'."""
    text = (text or "").strip().lower().replace(".", ":")
    if not text:
        return None
    meridiem = ""
    for marker in ("am", "pm"):
        if text.endswith(marker):
            meridiem = marker
            text = text[: -len(marker)].strip()
            break
    try:
        if ":" in text:
            hour_text, minute_text = text.split(":", 1)
            hour, minute = int(hour_text), int(minute_text or 0)
        elif len(text) == 4 and text.isdigit():
            hour, minute = int(text[:2]), int(text[2:])
        else:
            hour, minute = int(text), 0
    except ValueError:
        return None
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute
