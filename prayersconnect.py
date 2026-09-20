"""Iqama times from a masjid's PrayersConnect page.

PrayersConnect (prayersconnect.com) carries masjids across many countries, and
its congregation times are maintained by the masjids themselves rather than
calculated -- each prayer says whether its time is a `record` somebody entered
or a `prediction` from an adjustment like "+10".

The page is a Next.js one and ships its data with it, in the `__NEXT_DATA__`
script the framework writes. So there is nothing to scrape and no key to ask
for: the times are already in the page anyone can open.

The one real limit is that a page carries **today** only, where mawaqit and
the WordPress plugin both hand over a year. The clock re-reads every night
anyway, so in normal use it makes no difference -- but a machine that has
been off for a day starts with nothing until it has been online a moment,
which the other two sources do not need.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

HOST = "prayersconnect.com"
FETCH_TIMEOUT = 25.0
USER_AGENT = "FloatingClock/1.0 (+prayer-times)"

# The site's own names for each prayer, and the clock's.
PRAYERS = (
    ("fajr", "Fajr"),
    ("dhuhr", "Dhuhr"),
    ("asr", "Asr"),
    ("maghrib", "Maghrib"),
    ("isha", "Isha"),
)

FRIDAY = 4


class PrayersConnectError(Exception):
    """Something went wrong reaching or reading prayersconnect.com."""


def looks_like(url: str) -> bool:
    return HOST in (url or "").strip().lower()


def _read(url: str, timeout: float = FETCH_TIMEOUT, opener=None) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            charset = getattr(response.headers, "get_content_charset",
                              lambda: None)() or "utf-8"
            return response.read().decode(charset, "replace")
    except urllib.error.HTTPError as exc:
        raise PrayersConnectError("prayersconnect.com returned %d" % exc.code) from None
    except urllib.error.URLError as exc:
        raise PrayersConnectError(
            "could not reach prayersconnect.com (%s)" % exc.reason) from None
    except Exception as exc:
        raise PrayersConnectError("could not read that page (%s)" % exc) from None


def _next_data(html: str) -> dict:
    """The JSON Next.js leaves in the page."""
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise PrayersConnectError("that page carries no timetable")
    try:
        return json.loads(match.group(1))
    except ValueError:
        raise PrayersConnectError("that page's timetable is unreadable") from None


def fetch(url: str, timeout: float = FETCH_TIMEOUT, opener=None) -> str:
    """Today's congregation times, as the text the clock caches."""
    page = _next_data(_read(url.strip(), timeout, opener))
    props = (page.get("props") or {}).get("pageProps") or {}
    mosque = props.get("mosque")
    if not isinstance(mosque, dict):
        raise PrayersConnectError("no masjid at that address")
    times = mosque.get("prayer_times")
    if not isinstance(times, dict):
        raise PrayersConnectError("that masjid has no times on prayersconnect yet")
    if not mosque.get("has_iqamah_data"):
        raise PrayersConnectError(
            "that masjid publishes prayer times but no congregation times")
    kept = {}
    for key, _name in PRAYERS:
        entry = times.get(key)
        if isinstance(entry, dict) and entry.get("iqamah") not in (None, "", "na"):
            kept[key] = str(entry["iqamah"])
    if not kept:
        raise PrayersConnectError("that masjid has no congregation times listed")
    return json.dumps({
        "source": "prayersconnect",
        "name": str(mosque.get("name") or ""),
        "timezone": str(mosque.get("timezone") or ""),
        "iqamah": kept,
        "jumuah": _jumuah(mosque),
    })


def _jumuah(mosque: dict):
    """Friday's congregation, when the page gives one."""
    for key in ("jumuah_times", "jumuah", "khutbah_times"):
        value = mosque.get(key)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                for inner in ("iqamah", "time", "starts_at"):
                    if first.get(inner):
                        return str(first[inner])
            elif first:
                return str(first)
        elif isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _local(stamp: str, zone: str = "") -> datetime | None:
    """An ISO instant as the masjid's own wall clock.

    The site answers in UTC. Every other reader here hands back the masjid's
    own clock -- what its website shows and what the plausibility screen is
    written for -- so this converts to the masjid's timezone, and only falls
    back to this machine's when that timezone is not available (Windows
    ships no timezone database of its own).
    """
    text = str(stamp or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    target = None
    if zone:
        try:
            from zoneinfo import ZoneInfo

            target = ZoneInfo(zone)
        except Exception:
            target = None
    return moment.astimezone(target).replace(tzinfo=None)


def parse(text: str, window_start: datetime, window_end: datetime) -> list:
    """(name, iqama) for today's congregations inside the window.

    One day only, which is all a page carries.
    """
    try:
        data = json.loads(text)
    except ValueError:
        log.warning("The cached prayersconnect timetable is not readable JSON")
        return []
    iqamah = data.get("iqamah") or {}
    zone = str(data.get("timezone") or "")
    jumuah = _local(data.get("jumuah"), zone)

    found = []
    for key, name in PRAYERS:
        when = _local(iqamah.get(key), zone)
        if when is None:
            continue
        if name == "Dhuhr" and when.weekday() == FRIDAY and jumuah:
            name, when = "Jumuah", jumuah
        if window_start <= when <= window_end:
            found.append((name, when))
    found.sort(key=lambda item: (item[1], item[0]))
    return found


def source_name(text: str) -> str:
    try:
        return str(json.loads(text).get("name") or "")
    except ValueError:
        return ""
