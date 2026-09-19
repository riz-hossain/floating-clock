"""Read a calendar from an ICS feed -- Google Calendar's "secret address".

Every Google calendar exposes a private .ics URL (Settings -> Integrate
calendar -> Secret address in iCal format). Pointing the clock at one needs no
OAuth app, no client secret and no consent screen, and it works the same for a
personal calendar and for each company you belong to.

The parser is deliberately small: ICS is a line-folded key:value format, and
the only genuinely hard part is recurrence, which dateutil already does
properly. What is handled here is what Google actually emits -- floating,
UTC and TZID-stamped times, all-day dates, RRULE, EXDATE, and the
RECURRENCE-ID overrides that a "change just this occurrence" edit produces.

The secret address is a bearer credential: anyone holding the URL can read the
calendar. It is stored in the settings file like any other setting, and is
never logged -- failures report the calendar's display name instead.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

USER_AGENT = "FloatingClock/1.0 (+calendar-sync)"
FETCH_TIMEOUT = 20.0
# A runaway RRULE (no COUNT, no UNTIL) would otherwise expand forever.
MAX_OCCURRENCES = 400
DEFAULT_DURATION = timedelta(hours=1)


class IcsError(Exception):
    """The feed could not be fetched or made sense of."""


# --- the wire format ---------------------------------------------------- #

def unfold(text: str) -> list[str]:
    """Undo RFC 5545 line folding.

    A continuation is any line starting with a space or tab; it joins the
    previous line with the leading whitespace removed. Google folds at 75
    octets, so long summaries and URLs arrive split.
    """
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def parse_line(line: str) -> tuple[str, dict[str, str], str]:
    """Split "NAME;PARAM=x:value" into its three parts."""
    head, _, value = line.partition(":")
    pieces = head.split(";")
    name = pieces[0].upper()
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, _, param = piece.partition("=")
        params[key.upper()] = param.strip('"')
    return name, params, value


def parse_components(text: str, wanted: str = "VEVENT") -> list[dict]:
    """Collect each component block as {name: [(params, value), ...]}.

    Properties are kept as lists because EXDATE and ATTENDEE legitimately
    repeat, and dropping the repeats would silently resurrect deleted
    occurrences.
    """
    found: list[dict] = []
    current: dict | None = None
    depth = 0
    for line in unfold(text):
        name, params, value = parse_line(line)
        if name == "BEGIN" and value.upper() == wanted:
            current, depth = {}, 1
            continue
        if current is None:
            continue
        if name == "BEGIN":
            # A VALARM nested inside the VEVENT: skip it whole.
            depth += 1
            continue
        if name == "END":
            depth -= 1
            if depth == 0:
                found.append(current)
                current = None
            continue
        if depth == 1:
            current.setdefault(name, []).append((params, value))
    return found


def first(component: dict, name: str, default: str = "") -> str:
    values = component.get(name)
    return _unescape(values[0][1]) if values else default


# --- times -------------------------------------------------------------- #

def _zone(tzid: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tzid)
    except Exception:
        # An unknown or Windows-style TZID: treat it as floating rather than
        # dropping the event, which is the more useful failure.
        return None


def parse_datetime(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    """Return (local naive datetime, is_all_day).

    Everything is normalised to naive local time because that is what the rest
    of the clock compares against datetime.now().
    """
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        parsed = datetime.strptime(value, "%Y%m%d")
        return parsed, True

    match = re.fullmatch(r"(\d{8}T\d{6})(Z?)", value)
    if not match:
        raise IcsError("unrecognised date-time %r" % value)
    stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")

    if match.group(2) == "Z":
        return stamp.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None), False
    tzid = params.get("TZID", "")
    if tzid:
        zone = _zone(tzid)
        if zone is not None:
            return stamp.replace(tzinfo=zone).astimezone().replace(tzinfo=None), False
    # Floating time: already local by definition.
    return stamp, False


def parse_duration(value: str) -> timedelta:
    """RFC 5545 durations, e.g. PT1H30M, P2D, -PT15M."""
    match = re.fullmatch(
        r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip()
    )
    if not match:
        raise IcsError("unrecognised duration %r" % value)
    sign, weeks, days, hours, minutes, seconds = match.groups()
    total = timedelta(
        weeks=int(weeks or 0), days=int(days or 0), hours=int(hours or 0),
        minutes=int(minutes or 0), seconds=int(seconds or 0),
    )
    return -total if sign == "-" else total


# --- fetching ----------------------------------------------------------- #

def check_address(url: str) -> str:
    """Why `url` cannot be a calendar feed, or "" when it may well be one.

    Cheap and offline: the pasted link is most often the calendar's settings
    page rather than the secret address, and that is worth saying plainly.
    """
    url = (url or "").strip()
    if not url:
        return "Paste the calendar's secret iCal address."
    if not re.match(r"(https?|webcal)://", url, re.I):
        return "The address must start with https://"
    lower = url.lower()
    if "calendar.google.com" in lower and "/ical/" not in lower:
        return ("That is a Google Calendar share link or page, not the secret address. "
                "The secret address contains /ical/ and ends in basic.ics: on the "
                "calendar's settings page scroll to the last section, Integrate "
                "calendar, and copy its second box. If there is no second box, a "
                "Workspace admin must allow it first at "
                "admin.google.com/ac/appsettings/435070579839/sharing (External sharing "
                "options for primary calendars: Share all information).")
    if "calendar.google.com" in lower and "/public/" in lower:
        return ("That is the public address; use the secret address in iCal format "
                "a little further down the same page.")
    return ""


def fetch(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    """GET an ICS feed. Raises IcsError with nothing secret in the message."""
    if not re.match(r"https?://", url, re.I):
        raise IcsError("the address must start with http:// or https://")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise IcsError("the address was rejected (%d) -- has it been reset?" % exc.code)
        if exc.code == 404:
            raise IcsError("no calendar at that address (404)")
        raise IcsError("the calendar server returned %d" % exc.code) from None
    except urllib.error.URLError as exc:
        raise IcsError("could not reach the calendar (%s)" % exc.reason) from None
    except Exception as exc:
        raise IcsError("could not read the calendar (%s)" % exc) from None
    if "BEGIN:VCALENDAR" not in body.upper():
        raise IcsError("that address did not return a calendar")
    return body


# --- recurrence --------------------------------------------------------- #

def _localise_until(rule: str) -> str:
    """Strip the Z off an UNTIL so it matches a naive DTSTART.

    dateutil refuses to mix a naive DTSTART with a UTC UNTIL, and every time
    here is already normalised to naive local, so the UNTIL has to come along.
    """
    def replace(match: re.Match) -> str:
        stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
        local = stamp.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        return "UNTIL=" + local.strftime("%Y%m%dT%H%M%S")

    return re.sub(r"UNTIL=(\d{8}T\d{6})Z", replace, rule, flags=re.I)


def _occurrences(rule: str, start: datetime, window_start: datetime,
                 window_end: datetime) -> list[datetime]:
    """Every start time this rule produces inside the window."""
    from dateutil.rrule import rrulestr

    try:
        parsed = rrulestr(_localise_until(rule), dtstart=start)
    except Exception as exc:
        log.debug("unparsable RRULE %r: %s", rule, exc)
        return [start] if window_start <= start <= window_end else []
    out: list[datetime] = []
    for moment in parsed:
        if moment > window_end or len(out) >= MAX_OCCURRENCES:
            break
        if moment >= window_start:
            out.append(moment)
    return out


def _exdates(component: dict) -> set[datetime]:
    dropped: set[datetime] = set()
    for params, value in component.get("EXDATE", []):
        for piece in value.split(","):
            if not piece.strip():
                continue
            try:
                dropped.add(parse_datetime(piece, params)[0])
            except IcsError:
                continue
    return dropped


def _span(component: dict) -> tuple[datetime, datetime, bool]:
    """The event's start, end and all-day flag."""
    starts = component.get("DTSTART")
    if not starts:
        raise IcsError("event has no start")
    params, value = starts[0]
    start, all_day = parse_datetime(value, params)

    ends = component.get("DTEND")
    if ends:
        end = parse_datetime(ends[0][1], ends[0][0])[0]
    elif component.get("DURATION"):
        end = start + parse_duration(component["DURATION"][0][1])
    else:
        # An all-day VEVENT with no DTEND is one day; a timed one is an hour.
        end = start + (timedelta(days=1) if all_day else DEFAULT_DURATION)
    return start, end, all_day


def to_events(text: str, window_start: datetime, window_end: datetime,
              source: str = "") -> list["object"]:
    """Turn an ICS document into Event objects overlapping the window."""
    from .outlook import Event, find_join_url

    components = parse_components(text)
    overrides: dict[tuple[str, datetime], dict] = {}
    masters: list[dict] = []
    for component in components:
        recurrence_id = component.get("RECURRENCE-ID")
        uid = first(component, "UID")
        if recurrence_id:
            try:
                moment = parse_datetime(recurrence_id[0][1], recurrence_id[0][0])[0]
            except IcsError:
                continue
            overrides[(uid, moment)] = component
        else:
            masters.append(component)

    events: list[Event] = []
    for component in masters:
        if first(component, "STATUS").upper() == "CANCELLED":
            continue
        try:
            start, end, all_day = _span(component)
        except IcsError as exc:
            log.debug("skipping event: %s", exc)
            continue

        uid = first(component, "UID")
        rules = component.get("RRULE")
        if rules:
            # Reach back by the event's own length so a long meeting already
            # under way is still found by a window that starts after it began.
            starts = _occurrences(
                rules[0][1], start, window_start - (end - start), window_end
            )
        else:
            starts = [start] if start <= window_end and end >= window_start else []

        skip = _exdates(component)
        for moment in starts:
            if moment in skip:
                continue
            instance = overrides.get((uid, moment))
            if instance is not None:
                if first(instance, "STATUS").upper() == "CANCELLED":
                    continue
                try:
                    moment, finish, all_day = _span(instance)
                except IcsError:
                    continue
                body = instance
            else:
                finish = moment + (end - start)
                body = component
            events.append(Event(
                subject=first(body, "SUMMARY") or "(no subject)",
                start=moment,
                end=finish,
                location=first(body, "LOCATION"),
                organizer=re.sub(r"^mailto:", "", first(body, "ORGANIZER"), flags=re.I),
                all_day=all_day,
                join_url=find_join_url(
                    first(body, "X-GOOGLE-CONFERENCE"),
                    first(body, "LOCATION"),
                    first(body, "DESCRIPTION"),
                ),
                entry_id="%s@%s" % (uid, moment.isoformat()),
                source=source,
            ))
    events.sort(key=lambda e: e.start)
    return events


def read_calendar(url: str, window_start: datetime, window_end: datetime,
                  source: str = "", timeout: float = FETCH_TIMEOUT) -> list["object"]:
    """Fetch and parse one feed. Raises IcsError, never leaking the URL."""
    return to_events(fetch(url, timeout), window_start, window_end, source)
