"""Iqama times from a mosque's own website.

A great many mosque sites run the same WordPress plugin -- "Daily Prayer Time
for Mosques" -- and it publishes a small JSON API alongside the page people
actually read. So the clock does not have to scrape anything: given the
address of the mosque's site, it asks that API for the year's timetable and
reads the congregation times straight out of it.

    https://example.org/mosque/  ->  .../wp-json/dpt/v1/prayertime?filter=year

The plugin calls the congregation time *jamah* (jama'ah) and the call to
prayer *begins*; the clock wants the former, since the iqama is the moment
people stand. Friday is the exception: the year's timetable has no Jumuah in
it, because the plugin keeps Jumuah as one or more fixed times rather than a
per-day row, and those come back only from the "today" call. So a refresh
asks for both, and caches the pair.

A year is 130 KB and arrives in about a second, which buys something better
than a smaller request would: a clock that has not been online for a week
still knows today's times.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

API_PATH = "wp-json/dpt/v1/prayertime"
FETCH_TIMEOUT = 25.0
USER_AGENT = "FloatingClock/1.0 (+prayer-times)"

# The plugin's field for each prayer's congregation time, and what the clock
# calls it. Zuhr is Dhuhr here; the rest line up.
JAMAH_FIELDS = (
    ("fajr_jamah", "Fajr"),
    ("zuhr_jamah", "Dhuhr"),
    ("asr_jamah", "Asr"),
    ("maghrib_jamah", "Maghrib"),
    ("isha_jamah", "Isha"),
)

FRIDAY = 4

# The plugin's five (start, congregation) column pairs. Asr has two start
# columns, one per juristic school, and a congregation may follow either.
_PAIRS = (
    ("fajr_begins", ("fajr_jamah",)),
    ("zuhr_begins", ("zuhr_jamah",)),
    ("asr_mithl_1", ("asr_jamah",)),
    ("maghrib_begins", ("maghrib_jamah",)),
    ("isha_begins", ("isha_jamah",)),
)


class DptError(Exception):
    """Something went wrong reaching or reading the mosque's timetable."""


class NoApi(DptError):
    """This address has no timetable API at all.

    Kept apart from the rest so the caller can try reading it as a calendar
    instead. A site that *has* the API but nothing usable in it is a
    different matter, and saying so beats a calendar parser's complaint.
    """


# --- finding the API --------------------------------------------------------
def candidates(site_url: str) -> list[str]:
    """The addresses worth trying, nearest first.

    A mosque on a multisite lives under a path -- /icwaterloo/ -- and its API
    hangs off that, not off the domain. Someone pasting the address of the
    prayer-times *page* should still work, so each parent path is tried in
    turn.

    The bare domain is deliberately not one of them once a path is given. On
    a multisite the domain is a *different* mosque, and a centre whose own
    timetable is missing would otherwise be handed its neighbour's times with
    no sign anything was wrong. Someone whose mosque really is the whole site
    can paste its home page, and the error says so.
    """
    parts = urllib.parse.urlsplit(site_url.strip())
    if not parts.scheme or not parts.netloc:
        return []
    root = "%s://%s" % (parts.scheme, parts.netloc)
    segments = [seg for seg in parts.path.split("/") if seg]
    if not segments:
        return [root]
    out = []
    while segments:
        out.append("%s/%s" % (root, "/".join(segments)))
        segments.pop()
    seen, unique = set(), []
    for base in out:
        if base not in seen:
            seen.add(base)
            unique.append(base)
    return unique


def api_url(base: str, which: str = "year") -> str:
    return "%s/%s?filter=%s" % (base.rstrip("/"), API_PATH, which)


def _get(url: str, timeout: float = FETCH_TIMEOUT, opener=None):
    return _get_from(url, timeout, opener)[0]


def _get_from(url: str, timeout: float = FETCH_TIMEOUT, opener=None):
    """(payload, final url) -- the address included, because a redirect can
    land on a different site and its answer must not be taken for this one's."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    open_it = opener or urllib.request.urlopen
    with open_it(request, timeout=timeout) as response:
        charset = getattr(response.headers, "get_content_charset", lambda: None)() or "utf-8"
        payload = json.loads(response.read().decode(charset, "replace"))
        final = getattr(response, "geturl", lambda: url)() or url
        return payload, final


def _same_site(base: str, final_url: str) -> bool:
    """Whether an answer came from the site that was asked.

    On a multisite every mosque is a path under one domain, so a redirect
    that drops the path lands on somebody else's timetable. Times that are
    confidently wrong are worse here than no times at all.
    """
    asked, got = urllib.parse.urlsplit(base), urllib.parse.urlsplit(final_url)
    if asked.netloc != got.netloc:
        return False
    wanted = asked.path.strip("/")
    return not wanted or got.path.strip("/").startswith(wanted)


def _rows(payload) -> list[dict]:
    """The day records, whichever way this filter nested them.

    "today" answers with a list of one record; "year" with a list holding a
    list of records. Both shapes are flattened to plain rows here so nothing
    downstream has to know which call it came from.
    """
    rows: list[dict] = []
    stack = [payload]
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack = list(item) + stack
        elif isinstance(item, dict) and "d_date" in item:
            rows.append(item)
    return rows


def resolve(site_url: str, timeout: float = FETCH_TIMEOUT, opener=None) -> str:
    """Where an address really lives once its redirects are followed.

    Masjids very often own a vanity domain that simply redirects to the page
    on whatever platform hosts them: kitchenermasjid.com lands on
    centres.macnet.ca/kitchenermasjid/. The timetable's API hangs off the
    real home, not off the domain somebody remembered, so that is where
    discovery has to start -- and the cross-site guard below is right to
    refuse a *probe* that wanders off, but must not refuse a home page that
    the masjid itself sent us to.
    """
    request = urllib.request.Request(site_url, headers={"User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            return getattr(response, "geturl", lambda: site_url)() or site_url
    except Exception:
        return site_url                   # unreachable: let the probes say so


def discover(site_url: str, opener=None, resolved: bool = False) -> str:
    """The API base for this mosque's site, or "" when it has no such API.

    Walking up the path stops at the first address that answers, even when
    it answers with nothing. On a multisite the parent path is a different
    mosque, and quietly showing its times instead would be worse than
    showing none: the whole point is to be right about when to pray.
    """
    home = site_url if resolved else resolve(site_url, opener=opener)
    starts = [home] if home == site_url else [home, site_url]
    tried = set()
    for start in starts:
        for base in candidates(start):
            if base in tried:
                continue
            tried.add(base)
            try:
                payload, final = _get_from(api_url(base, "today"), opener=opener)
            except Exception:
                continue                  # nothing of ours here; try the parent
            if not _same_site(base, final):
                continue                  # redirected off this mosque's path
            if not isinstance(payload, list):
                continue
            if _rows(payload):
                log.info("Prayer times: found a timetable at %s", base)
            else:
                log.info("Prayer times: %s has a timetable API but nothing in it", base)
            return base
    return ""


def congregation_unset(rows) -> bool:
    """Whether the "congregation" columns are really just the start times.

    A masjid whose plugin was installed but never given congregation times
    publishes jamah == begins for every prayer of the year. The values are
    plausible, in order and wrong -- Fajr's iqama an hour before the masjid's
    own website says it is -- so no range check can catch them, and an Alexa
    routine set to fire at "iqama" would call the azan an hour early.

    Maghrib is often called at the same minute the sun sets, so one match in
    five is ordinary. Four or more, on nearly every day, is not.
    """
    days = unset = 0
    for row in rows:
        days += 1
        same = 0
        for begin, jamahs in _PAIRS:
            start = str(row.get(begin) or "")[:5]
            if not start:
                continue
            if begin == "asr_mithl_1":
                starts = {start, str(row.get("asr_mithl_2") or "")[:5]}
            else:
                starts = {start}
            if any(str(row.get(j) or "")[:5] in starts for j in jamahs):
                same += 1
        if same >= 4:
            unset += 1
    return days > 0 and unset / days >= 0.9


def looks_like_ics(url: str) -> bool:
    """A calendar feed rather than a website, judged by the address alone.

    Only used to skip the API probe for something that is obviously an ICS
    link; anything unclear is probed and falls back to ICS anyway.
    """
    text = (url or "").strip().lower()
    return (text.endswith(".ics") or "/ical" in text or "format=ical" in text
            or text.startswith("webcal://"))


# --- fetching ---------------------------------------------------------------
def fetch(site_url: str, timeout: float = FETCH_TIMEOUT, opener=None,
          resolved: bool = False) -> str:
    """The whole timetable, as the text the clock caches.

    Raises DptError with nothing secret in the message.
    """
    base = discover(site_url, opener=opener, resolved=resolved)
    if not base:
        raise NoApi(
            "that site does not publish a timetable the clock can read. Try the "
            "masjid's home page, or ask them for an iqamah iCal address")
    try:
        year = _rows(_get(api_url(base, "year"), timeout, opener))
    except urllib.error.HTTPError as exc:
        raise DptError("the mosque's site returned %d" % exc.code) from None
    except urllib.error.URLError as exc:
        raise DptError("could not reach the mosque's site (%s)" % exc.reason) from None
    except ValueError:
        raise DptError("the mosque's timetable came back unreadable") from None
    except Exception as exc:
        raise DptError("could not read the timetable (%s)" % exc) from None
    if not year:
        raise DptError("that masjid has not put a timetable on its site yet")
    # A plugin left behind with an old timetable in it is common enough to be
    # worth naming: "no prayer times found" would send someone looking for a
    # fault in the clock instead of at the masjid's website.
    covered = [row.get("d_date") for row in year if row.get("d_date")]
    newest = max(str(d)[:10] for d in covered) if covered else ""
    if newest and newest < datetime.now().strftime("%Y-%m-%d"):
        raise DptError("that masjid's timetable stops at %s; it needs updating "
                       "on their website" % newest)

    if congregation_unset(year):
        raise DptError(
            "that masjid's site lists prayer start times but no congregation "
            "(iqama) times, so there is nothing for the clock to follow")

    # Jumuah is not in the year's rows -- the plugin keeps it as fixed times
    # rather than a per-day value -- so it takes the second call.
    jumuah: list[str] = []
    try:
        payload = _get(api_url(base, "today"), timeout, opener)
        for row in _rows(payload):
            times = row.get("jumuah")
            if isinstance(times, list) and times:
                jumuah = [str(t) for t in times]
                break
    except Exception:
        log.debug("Could not read the Jumuah times", exc_info=True)

    return json.dumps({"api": base, "days": year, "jumuah": jumuah})


# --- reading ----------------------------------------------------------------
def _time(value) -> tuple[int, int] | None:
    """(hour, minute) from '06:15:00', '6:15' or '13:30'."""
    text = str(value or "").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour == 0 and minute == 0:
        return None          # the plugin's way of saying "not set"
    return hour, minute


def parse(text: str, window_start: datetime, window_end: datetime) -> list:
    """(name, iqama) for every congregation time inside the window.

    Plain tuples rather than Prayer objects so this module stays free of the
    one that calls it.
    """
    try:
        data = json.loads(text)
    except ValueError:
        log.warning("The cached timetable is not readable JSON")
        return []
    if isinstance(data, list):                      # a cache from a bare API dump
        days, jumuah = _rows(data), []
    else:
        days, jumuah = _rows(data.get("days")), list(data.get("jumuah") or [])

    first_jumuah = None
    for value in jumuah:
        first_jumuah = _time(value)
        if first_jumuah:
            break

    found = []
    for row in days:
        try:
            day = date.fromisoformat(str(row.get("d_date"))[:10])
        except (TypeError, ValueError):
            continue
        friday = day.weekday() == FRIDAY
        for field, name in JAMAH_FIELDS:
            # On Friday the Jumuah congregation stands in for Dhuhr, and it
            # is the one most people come to, so it must not be missed out
            # just because the year's rows have no column for it.
            if friday and name == "Dhuhr" and first_jumuah:
                name, clock = "Jumuah", first_jumuah
            else:
                clock = _time(row.get(field))
            if not clock:
                continue
            when = datetime(day.year, day.month, day.day, clock[0], clock[1])
            if window_start <= when <= window_end:
                found.append((name, when))
    found.sort(key=lambda item: (item[1], item[0]))
    return found


def source_name(text: str) -> str:
    """The site the cached timetable came from, for the settings page."""
    try:
        base = json.loads(text).get("api") or ""
    except Exception:
        return ""
    host = urllib.parse.urlsplit(base).netloc
    path = urllib.parse.urlsplit(base).path.strip("/")
    return "%s/%s" % (host, path) if path else host


def covers_to(text: str) -> datetime | None:
    """The last day the cached timetable has, so a caller can say so."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    days = _rows(data if isinstance(data, list) else data.get("days"))
    stamps = []
    for row in days:
        try:
            stamps.append(date.fromisoformat(str(row.get("d_date"))[:10]))
        except (TypeError, ValueError):
            continue
    if not stamps:
        return None
    last = max(stamps)
    return datetime(last.year, last.month, last.day) + timedelta(days=1)
