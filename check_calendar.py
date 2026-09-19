"""Live check of the Outlook connection: `python -m floating_clock.check_calendar`.

Prints what the clock can actually see right now and when the next reminder
would fire. Use it when meetings are not showing up -- it separates "Outlook
is unreachable" from "the query returned nothing" from "nothing is scheduled",
which otherwise all look identical on the clock face.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

from . import alerts, outlook, settings as cfg

OK = "  [ok]  "
BAD = "  [!!]  "
INFO = "         "


def _line(text: str = "") -> None:
    print(text)


def check(hours: float = 24.0) -> int:
    _line()
    _line("Floating Clock - Outlook calendar check")
    _line("=" * 52)
    problems = 0

    try:
        import pythoncom  # noqa: F401
        import win32com.client
    except ImportError:
        _line(BAD + "pywin32 is not installed.  pip install pywin32")
        return 1
    _line(OK + "pywin32 is installed")

    import pythoncom

    pythoncom.CoInitialize()
    try:
        try:
            app = win32com.client.Dispatch("Outlook.Application")
            namespace = app.GetNamespace("MAPI")
            _line(OK + "connected to Outlook %s" % app.Version)
        except Exception as exc:
            _line(BAD + "cannot reach Outlook over COM: %s" % exc)
            _line(INFO + "The classic Outlook desktop app must be installed.")
            _line(INFO + "The new Outlook (the Store app) has no COM interface.")
            return 1

        try:
            folder = namespace.GetDefaultFolder(outlook.OL_FOLDER_CALENDAR)
            store = getattr(getattr(folder, "Store", None), "DisplayName", "?")
            _line(OK + "default calendar: %s  (%s)" % (folder.Name, store))
            _line(INFO + "%d items in the folder" % folder.Items.Count)
        except Exception as exc:
            _line(BAD + "cannot open the calendar folder: %s" % exc)
            return 1

        now = datetime.now()
        literal = outlook._restrict_literal(now)
        _line(OK + "filter literal: %r" % literal)
        if literal.count(":") > 1:
            problems += 1
            _line(BAD + "literal contains seconds -- Outlook will match nothing")

        try:
            events = outlook.fetch_events(hours_ahead=hours)
        except outlook.CalendarUnavailable as exc:
            _line(BAD + "read failed: %s" % exc)
            return 1

        _line(OK + "%d meeting(s) in the next %g hours" % (len(events), hours))
        if not events:
            _line(INFO + "Nothing scheduled, or everything ahead is further out.")
            _line(INFO + "Try a wider window:  python -m floating_clock.check_calendar 168")

        _line()
        _line("  When       Ends   In         Join  Subject")
        _line("  " + "-" * 62)
        with_links = 0
        for event in events[:20]:
            if event.join_url:
                with_links += 1
            _line(
                "  %-10s %-6s %-10s %-5s %s" % (
                    event.start.strftime("%a %H:%M"),
                    event.end.strftime("%H:%M"),
                    "live" if event.is_live(now) else outlook.describe_gap(
                        event.minutes_until(now)
                    ),
                    "yes" if event.join_url else "-",
                    event.subject[:34],
                )
            )
        if events:
            _line()
            _line(OK + "%d of %d have a join link" % (with_links, len(events)))

        settings = cfg.load()
        lead = float(settings["meeting_lead_minutes"])
        _line()
        _line("  Reminder lead time: %g minutes" % lead)
        if not settings["calendar_enabled"]:
            problems += 1
            _line(BAD + "the calendar is switched OFF in settings")
            _line(INFO + "Right-click the clock > Outlook calendar, or Settings > Meetings")
        else:
            _line(OK + "the calendar is switched on")

        upcoming = [e for e in events if e.start > now]
        if upcoming:
            nxt = upcoming[0]
            fires_at = nxt.start - timedelta(minutes=lead)
            _line(
                OK + "next reminder: %s at %s (%s from now) for %r"
                % (
                    fires_at.strftime("%a %d %b"),
                    fires_at.strftime("%H:%M"),
                    outlook.describe_gap((fires_at - now).total_seconds() / 60)[3:],
                    nxt.subject[:40],
                )
            )
            # Prove the scheduler agrees, by asking it at that moment.
            scheduler = alerts.Scheduler()
            scheduler._notified.clear()
            fired = scheduler.due(fires_at + timedelta(seconds=1), [nxt], lead_minutes=lead)
            if any(f.kind == "meeting" for f in fired):
                _line(OK + "the scheduler fires for it at that moment")
            else:
                problems += 1
                _line(BAD + "the scheduler did NOT fire at that moment")
        else:
            _line(INFO + "No future meetings in this window to remind about.")
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    _line()
    _line("=" * 52)
    if problems:
        _line("  %d problem(s) found." % problems)
    else:
        _line("  All good.")
    _line()
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    hours = 24.0
    if argv:
        try:
            hours = float(argv[0])
        except ValueError:
            print("usage: python -m floating_clock.check_calendar [hours]")
            return 2
    return check(hours)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
