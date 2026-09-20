"""Iqama times from MAWAQIT, and the search that finds a masjid in the first place.

MAWAQIT (mawaqit.net) is a waqf project carrying several thousand masjids,
most densely in France but reaching well beyond it. Two things make it worth
reading:

- Its search takes a **name or a town** (`?word=`) or a **point on the map**
  (`?lat=&lon=&radius=`), so someone can find their masjid without hunting
  for a web address.
- Every masjid's page carries a whole year of times, so once read, the clock
  knows today's iqama with the network down.

The year lives in two tables. `calendar` is the adhan, six times a day --
fajr, shuruq, dhuhr, asr, maghrib, isha. `iqamaCalendar` is the congregation,
five -- the same minus shuruq. That gap matters: an iqama entry can be an
offset like "+10" rather than a time, and it is added to *its own* prayer's
adhan, which is not the entry at the same index. Line them up wrongly and
every prayer of the year is quietly at the wrong time.

The official prayer-times API needs a key; none of this does. The pages are
the ones anybody can read.
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

HOST = "https://mawaqit.net"
SEARCH_PATH = "/api/2.0/mosque/search"
FETCH_TIMEOUT = 25.0
USER_AGENT = "FloatingClock/1.0 (+prayer-times)"

# Without a radius the search answers with a single masjid, which reads as
# "there is only one near you" when there are a dozen.
DEFAULT_RADIUS_KM = 50

# iqamaCalendar's five, and which of calendar's six each one follows.
# Shuruq (index 1) is sunrise, not a prayer, so nothing points at it.
IQAMA_ORDER = (
    ("Fajr", 0),
    ("Dhuhr", 2),
    ("Asr", 3),
    ("Maghrib", 4),
    ("Isha", 5),
)

FRIDAY = 4


class MawaqitError(Exception):
    """Something went wrong reaching or reading mawaqit.net."""


# --- addresses --------------------------------------------------------------
def looks_like(url: str) -> bool:
    return "mawaqit.net" in (url or "").strip().lower()


def slug_from(url: str) -> str:
    """The masjid's slug out of any mawaqit address.

    Handles /en/<slug>, /fr/m/<slug> and a bare slug, since people paste
    whichever of those their browser happened to show.
    """
    text = (url or "").strip()
    if not text:
        return ""
    if "mawaqit.net" not in text.lower():
        return text.strip("/")
    path = urllib.parse.urlsplit(text if "//" in text else "//" + text).path
    parts = [p for p in path.split("/") if p]
    # Drop a leading language code, and the /m/ (masjid) or /w/ (widget) some
    # links carry -- a masjid's own site usually embeds the widget form.
    while parts and (len(parts[0]) == 2 or parts[0] in ("m", "w")):
        parts.pop(0)
    return parts[0] if parts else ""


def page_url(slug: str) -> str:
    return "%s/en/%s" % (HOST, slug.strip("/"))


# --- talking to the site ----------------------------------------------------
def _open(url: str, timeout: float, opener=None):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return (opener or urllib.request.urlopen)(request, timeout=timeout)


def _read(url: str, timeout: float = FETCH_TIMEOUT, opener=None) -> str:
    try:
        with _open(url, timeout, opener) as response:
            charset = getattr(response.headers, "get_content_charset",
                              lambda: None)() or "utf-8"
            return response.read().decode(charset, "replace")
    except urllib.error.HTTPError as exc:
        raise MawaqitError("mawaqit.net returned %d" % exc.code) from None
    except urllib.error.URLError as exc:
        raise MawaqitError("could not reach mawaqit.net (%s)" % exc.reason) from None
    except Exception as exc:
        raise MawaqitError("could not read mawaqit.net (%s)" % exc) from None


def search(word: str = "", lat: float | None = None, lon: float | None = None,
           radius_km: int = DEFAULT_RADIUS_KM, timeout: float = FETCH_TIMEOUT,
           opener=None) -> list[dict]:
    """Masjids matching a name or town, or lying near a point.

    Each one comes back as a plain dict: name, slug, city, latitude,
    longitude, site. Whatever else the API sends is dropped, so the callers
    do not grow a dependency on fields that may not last.
    """
    query = {}
    if word:
        query["word"] = word
    if lat is not None and lon is not None:
        query["lat"], query["lon"] = "%.6f" % lat, "%.6f" % lon
        query["radius"] = str(int(radius_km))
    if not query:
        return []
    url = "%s%s?%s" % (HOST, SEARCH_PATH, urllib.parse.urlencode(query))
    try:
        payload = json.loads(_read(url, timeout, opener))
    except ValueError:
        raise MawaqitError("mawaqit.net sent back something unreadable") from None
    if not isinstance(payload, list):
        return []
    found = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        found.append({
            "name": str(item.get("name") or item.get("label") or "").strip(),
            "slug": str(item["slug"]),
            "city": str(item.get("localisation") or "").strip(),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "site": str(item.get("site") or "").strip(),
            # A masjid can switch its congregation times off. What is left in
            # its table then is a leftover, not something it publishes.
            "iqama": item.get("iqamaEnabled") is not False,
            "source": "mawaqit",
        })
    return found


def _conf_data(html: str) -> dict:
    """The confData object a masjid's page carries its whole year in.

    Brace-matched rather than regexed to the closing brace: the object holds
    announcement text, and any of that may contain braces or quotes.
    """
    marker = re.search(r"confData\s*=\s*", html)
    if not marker:
        raise MawaqitError("that mawaqit page carries no timetable")
    try:
        start = html.index("{", marker.end())
    except ValueError:
        raise MawaqitError("that mawaqit page carries no timetable") from None
    depth, i, in_string, escaped = 0, start, False, False
    while i < len(html):
        char = html[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    try:
        return json.loads(html[start:i + 1])
    except ValueError:
        raise MawaqitError("that mawaqit page's timetable is unreadable") from None


def fetch(url_or_slug: str, timeout: float = FETCH_TIMEOUT, opener=None) -> str:
    """A masjid's whole year, as the text the clock caches."""
    slug = slug_from(url_or_slug)
    if not slug:
        raise MawaqitError("that does not look like a mawaqit address")
    conf = _conf_data(_read(page_url(slug), timeout, opener))
    calendar = conf.get("calendar")
    iqama_calendar = conf.get("iqamaCalendar")
    if not isinstance(calendar, list) or not isinstance(iqama_calendar, list):
        raise MawaqitError("that masjid has no timetable on mawaqit yet")
    if conf.get("iqamaEnabled") is False:
        raise MawaqitError(
            "that masjid has switched its congregation times off on mawaqit, so "
            "there is nothing for the clock to follow")
    if start_is_iqama(calendar, iqama_calendar):
        raise MawaqitError(
            "that masjid's mawaqit page lists prayer start times but no "
            "congregation times, so there is nothing for the clock to follow")
    return json.dumps({
        "source": "mawaqit",
        "slug": slug,
        "name": conf.get("name") or slug,
        "calendar": calendar,
        "iqamaCalendar": iqama_calendar,
        "jumua": conf.get("jumua"),
        "jumua2": conf.get("jumua2"),
        "jumua3": conf.get("jumua3"),
        "jumuaAsDuhr": bool(conf.get("jumuaAsDuhr")),
    })


def start_is_iqama(calendar, iqama_calendar) -> bool:
    """Whether the iqama table just says "at the adhan" all year.

    Four of five prayers at +0 (or at the very minute of the adhan) on nearly
    every day is a table nobody filled in. One at +0 -- Maghrib -- is normal.
    """
    days = unset = 0
    for month, table in enumerate(iqama_calendar, 1):
        if not isinstance(table, dict):
            continue
        for day, entries in table.items():
            if not isinstance(entries, (list, tuple)) or len(entries) < 5:
                continue
            days += 1
            adhan = _month_day(calendar, month, int(day)) if str(day).isdigit() else None
            same = 0
            for slot, (_name, index) in enumerate(IQAMA_ORDER):
                entry = str(entries[slot]).strip()
                if _offset(entry) == 0 or entry in ("0", "+0", "-0"):
                    same += 1
                elif (isinstance(adhan, (list, tuple)) and index < len(adhan)
                      and _clock(entry) and _clock(entry) == _clock(adhan[index])):
                    same += 1
            if same >= 4:
                unset += 1
    return days > 0 and unset / days >= 0.9


# --- reading it -------------------------------------------------------------
def _clock(value) -> tuple[int, int] | None:
    """(hour, minute) from '06:15'. None for anything that is not a time."""
    match = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _offset(value) -> int | None:
    """The minutes in '+10', or None when this is not an offset at all."""
    match = re.match(r"^\s*([+-]\d{1,3})\s*$", str(value or ""))
    return int(match.group(1)) if match else None


def _month_day(table, month: int, day: int):
    """One day's row out of a twelve-month table, whatever shape it is in.

    The months are a list; the days inside are usually a dict keyed by the
    day number as a string, and occasionally a plain list.
    """
    if not isinstance(table, list) or not 1 <= month <= len(table):
        return None
    entry = table[month - 1]
    if isinstance(entry, dict):
        return entry.get(str(day)) or entry.get(day)
    if isinstance(entry, list) and 1 <= day <= len(entry):
        return entry[day - 1]
    return None


def parse(text: str, window_start: datetime, window_end: datetime) -> list:
    """(name, iqama) for every congregation inside the window, in time order.

    Plain tuples rather than Prayer objects, so this module stays free of the
    one that calls it.
    """
    try:
        data = json.loads(text)
    except ValueError:
        log.warning("The cached mawaqit timetable is not readable JSON")
        return []
    calendar = data.get("calendar")
    iqama_calendar = data.get("iqamaCalendar")
    if not isinstance(iqama_calendar, list):
        return []
    jumuah = _clock(data.get("jumua"))
    as_duhr = bool(data.get("jumuaAsDuhr"))

    found = []
    day = window_start.date()
    last = window_end.date()
    while day <= last:
        adhan = _month_day(calendar, day.month, day.day)
        iqama = _month_day(iqama_calendar, day.month, day.day)
        if not isinstance(iqama, (list, tuple)):
            day += timedelta(days=1)
            continue
        friday = day.weekday() == FRIDAY
        for slot, (name, adhan_index) in enumerate(IQAMA_ORDER):
            if slot >= len(iqama):
                break
            when = _moment(day, iqama[slot], adhan, adhan_index)
            if when is None:
                continue
            # Friday's Jumuah stands in for Dhuhr -- unless the masjid says it
            # keeps Dhuhr's time, which some do.
            if friday and name == "Dhuhr" and jumuah and not as_duhr:
                name = "Jumuah"
                when = datetime(day.year, day.month, day.day, jumuah[0], jumuah[1])
            if window_start <= when <= window_end:
                found.append((name, when))
        day += timedelta(days=1)
    found.sort(key=lambda item: (item[1], item[0]))
    return found


def _moment(day: date, entry, adhan, adhan_index: int) -> datetime | None:
    """One prayer's congregation time, from a clock entry or an offset."""
    clock = _clock(entry)
    if clock:
        return datetime(day.year, day.month, day.day, clock[0], clock[1])
    minutes = _offset(entry)
    if minutes is None:
        return None
    if not isinstance(adhan, (list, tuple)) or adhan_index >= len(adhan):
        return None
    base = _clock(adhan[adhan_index])
    if not base:
        return None
    return (datetime(day.year, day.month, day.day, base[0], base[1])
            + timedelta(minutes=minutes))


def source_name(text: str) -> str:
    """Whose times these are, for the one sentence the settings page shows."""
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    return str(data.get("name") or data.get("slug") or "")
