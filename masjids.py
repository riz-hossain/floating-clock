"""Finding a masjid, so nobody has to go hunting for a web address.

Somebody in a city they do not know searches "masjid near me", opens each
website and reads the times off it; and where a masjid has no website, or
nothing on it, walks over to the next one and takes its times, knowing they
are near enough. This does the same, in that order.

Three places are searched and the answers merged:

- **OpenStreetMap**, live. What is mapped near a place, with a website more
  often than not. Free, keyless and complete enough to be the main list --
  though not complete: a well-known centre can be missing altogether.
- **MAWAQIT**, live. Several thousand masjids, searchable by name, town or a
  point on the map; the ones it has carry a year of times.
- **The bundled directory**, offline. A few hundred Canadian masjids with
  their coordinates and websites, distilled from research done for the
  LiveAzan project. It answers instantly with no network at all.

A masjid found here still has to be turned into times, which `addresses_for`
and `propose` do: its MAWAQIT page when there is one, then its own website,
which prayer.py reads as a timetable if it can. And when nothing readable
comes of that, `propose` looks at the masjids around it, nearest first, and
offers the first one whose times can be read -- labelled as a neighbour's,
never passed off as the masjid's own.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import logging
import math
import os
import re
import sys
import time
import unicodedata

from . import mawaqit, osm

log = logging.getLogger(__name__)

DIRECTORY_NAME = "masjids.json"

# How far apart two entries can be and still be judged the same masjid. A
# building is tens of metres across; different masjids on the same street are
# rarely closer than this.
SAME_PLACE_KM = 0.25
# Farther than that, but this near and sharing a distinctive word in the name:
# maps and directories put the same building a few hundred metres apart.
NEAR_KM = 0.8
# How far around a place somebody typed to look.
SEARCH_RADIUS_KM = 20.0
# How far away, and how many, and for how long, a neighbour's times are sought
# when a masjid has none of its own.
NEIGHBOUR_KM = 25.0
NEIGHBOURS_TRIED = 5
NEIGHBOUR_BUDGET_S = 100.0

_directory: list | None = None


# --- the bundled directory --------------------------------------------------
def directory_path() -> str:
    """Where the bundled file sits, frozen or from source."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "data", DIRECTORY_NAME)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "data", DIRECTORY_NAME)


def directory() -> list:
    """The bundled masjids, read once.

    A missing or damaged file is not fatal: the live search still works, and
    a clock that cannot search offline is better than one that will not
    start.
    """
    global _directory
    if _directory is not None:
        return _directory
    path = directory_path()
    try:
        data = json.load(io.open(path, encoding="utf-8"))
        _directory = list(data.get("masjids") or [])
    except (OSError, ValueError):
        log.warning("Could not read the bundled masjid directory at %s", path,
                    exc_info=True)
        _directory = []
    return _directory


# --- matching ---------------------------------------------------------------
def _fold(text: str) -> str:
    """Lowercased and stripped of accents, for comparing what people type.

    Someone looking for "Mosquée de Paris" should not have to produce the
    accent, and someone who does should not be punished for it.
    """
    plain = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return plain.casefold().strip()


def distance_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance. Plenty exact at the scale of a city."""
    try:
        lat1, lon1, lat2, lon2 = (float(lat1), float(lon1), float(lat2), float(lon2))
    except (TypeError, ValueError):
        return float("inf")
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return radius * 2 * math.asin(min(1.0, math.sqrt(a)))


def _matches(entry: dict, needle: str) -> bool:
    if not needle:
        return False
    hay = " ".join(str(entry.get(k) or "") for k in ("name", "city", "province"))
    return needle in _fold(hay)


# Words that say "masjid" and nothing about which one.
_COMMON = {"masjid", "mosque", "mosquee", "islamic", "islam", "centre", "center", "muslim",
           "association", "society", "community", "cultural", "the", "and", "canada",
           "canadian", "inc", "musalla", "musallah", "prayer", "hall", "jame", "jamia",
           "jami", "trust", "foundation", "education", "educational", "organization"}


def _words(name: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", _fold(name)) if w not in _COMMON and len(w) > 2}


def _same_place(a: dict, b: dict) -> bool:
    """Whether two entries, from different sources, are one masjid."""
    gap = distance_km(a.get("latitude"), a.get("longitude"),
                      b.get("latitude"), b.get("longitude"))
    name_a, name_b = _fold(a.get("name")), _fold(b.get("name"))
    if name_a and name_a == name_b and (gap <= 3.0 or gap == float("inf")):
        return True
    if gap <= SAME_PLACE_KM:
        return True
    return gap <= NEAR_KM and bool(_words(name_a) & _words(name_b))


def _enrich(kept: dict, other: dict) -> None:
    """Give `kept` what only `other` knew: the website MAWAQIT lacks, or the
    position the directory lacks."""
    for key in ("website", "site", "phone", "latitude", "longitude", "city",
                "province", "address"):
        if not kept.get(key) and other.get(key):
            kept[key] = other[key]


def _merge(*groups: list) -> list:
    """The groups as one list, earlier groups winning a tie.

    MAWAQIT is listed first because it is the one that might carry times
    itself; the others only know where the masjid is and what its website is,
    and give that to the entry that won.
    """
    out: list = []
    for group in groups:
        for entry in group:
            twin = next((o for o in out if _same_place(o, entry)), None)
            if twin is None:
                out.append(dict(entry))
            else:
                _enrich(twin, entry)
    return out


def search_directory(text: str = "", lat=None, lon=None, radius_km: float = 50.0,
                     limit: int = 40) -> list[dict]:
    """The bundled masjids matching a name or town, or lying near a point."""
    needle = _fold(text)
    found = []
    for entry in directory():
        if lat is not None and lon is not None:
            gap = distance_km(lat, lon, entry.get("latitude"), entry.get("longitude"))
            if gap > radius_km:
                continue
        elif not _matches(entry, needle):
            continue
        row = dict(entry)
        row["source"] = "directory"
        found.append(row)
    return found[:limit]


# --- searching --------------------------------------------------------------
def _complaint(source: str, exc: Exception) -> str:
    return "%s: %s" % (source, str(exc)[:90] or exc.__class__.__name__)


def _concurrently(jobs: dict, timeout: float = 25.0) -> dict:
    """{name: (value, error)} for jobs that are run at the same time.

    Each source is a different server and each is slow in its own way; asked one
    after another they add up to a wait nobody would sit through. One that has
    not answered in `timeout` seconds is given up on -- it is left to finish in
    the background, and what it finds is kept for the next time.
    """
    out: dict = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(jobs)))
    try:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        concurrent.futures.wait(list(futures.values()), timeout=timeout)
        for name, future in futures.items():
            if not future.done():
                out[name] = (None, TimeoutError("still looking; try again in a minute"))
                continue
            try:
                out[name] = (future.result(), None)
            except Exception as exc:                 # a search must never crash
                out[name] = (None, exc)
    finally:
        pool.shutdown(wait=False)
    return out


def search(text: str = "", lat=None, lon=None, radius_km: float = 50.0,
           limit: int = 60, online: bool = True) -> tuple[list[dict], str]:
    """(masjids, trouble) for a town, an address, a postal code or a name.

    A place typed in is turned into a point, and every source is asked what
    lies around it; a name is looked for in the sources that can search by
    name. Whatever answers is merged, nearest first, with an exact name match
    ahead of the rest. Trouble is what went wrong, if anything -- the results
    that did come back are still returned, because being offline or having
    one service busy should narrow the answer rather than empty it.
    """
    text = " ".join(str(text or "").split())
    point = (float(lat), float(lon)) if lat is not None and lon is not None else None
    bundled = search_directory(text, lat, lon, radius_km, 500)
    if not online:
        return _rank(bundled, text, point)[:limit], ""

    troubles: list[str] = []

    def note(source: str, error) -> None:
        if error is not None and not any(t.startswith(source + ":") for t in troubles):
            troubles.append(_complaint(source, error))

    # First, where is it, and who is called that -- at once.
    first: dict = {}
    if text:
        first["mawaqit-name"] = lambda: mawaqit.search(word=text)
        if point is None:
            first["place"] = lambda: osm.geocode(text)
    got = _concurrently(first)
    mawaqit_rows = list(got.get("mawaqit-name", (None, None))[0] or [])
    note("mawaqit.net", got.get("mawaqit-name", (None, None))[1])
    place, error = got.get("place", (None, None))
    note("map", error)
    around: list = []
    if point is None and place:
        point = (place["lat"], place["lon"])
    if point is not None and place:
        around = search_directory("", point[0], point[1], SEARCH_RADIUS_KM, 500)

    # Then what lies around that point, from the two that can say.
    osm_rows: list = []
    if point is not None:
        reach = min(radius_km, SEARCH_RADIUS_KM)
        second = _concurrently({
            "mawaqit-near": lambda: mawaqit.search(lat=point[0], lon=point[1], radius_km=int(reach)),
            "map": lambda: osm.mosques_near(point[0], point[1], reach),
        })
        mawaqit_rows += list(second["mawaqit-near"][0] or [])
        note("mawaqit.net", second["mawaqit-near"][1])
        osm_rows = list(second["map"][0] or [])
        note("map", second["map"][1])

    merged = _merge(mawaqit_rows, bundled, around, osm_rows)
    return _rank(merged, text, point)[:limit], "; ".join(troubles)


def _rank(rows: list, text: str, point) -> list:
    """An exact name match first, then nearest first; each carries its distance."""
    needle = _fold(text)
    if point is not None:
        for row in rows:
            gap = distance_km(point[0], point[1], row.get("latitude"), row.get("longitude"))
            row["km"] = round(gap, 1) if gap != float("inf") else None

    def key(row):
        named = 0 if needle and needle in _fold(row.get("name")) else 1
        gap = row.get("km")
        return (named, gap if gap is not None else 1e9)

    return sorted(rows, key=key) if point is not None or needle else rows


# --- what to read for one masjid ------------------------------------------------
def addresses_for(entry: dict) -> list[str]:
    """The addresses to try for this masjid, best first.

    Its MAWAQIT page carries a year of congregation times and is exact; its own
    website is the fallback, read as a timetable if it has one. A masjid that
    has switched off congregation times on MAWAQIT can still print them on
    its own site, so that does not close the door on the website.
    """
    if not isinstance(entry, dict):
        return []
    found: list[str] = []
    slug = str(entry.get("slug") or "").strip()
    if slug and entry.get("iqama") is not False:
        found.append(mawaqit.page_url(slug))
    site = str(entry.get("website") or entry.get("site") or "").strip()
    if site.lower().startswith(("http://", "https://")) and site not in found:
        found.append(site)
    return found


def address_for(entry: dict) -> str:
    """The first address to try, or "" when there is nothing to read from."""
    found = addresses_for(entry)
    return found[0] if found else ""


# The sun's own time is longitude / 15 hours from Greenwich, and a time zone never
# strays from that by more than about two hours -- so a masjid this far off this
# computer's clock is in another time zone.
ZONE_SLACK_HOURS = 2.5


def zone_problem(entry: dict) -> str:
    """"" when a masjid is on this computer's time zone, else a sentence saying it is not.

    The clock shows every time on this computer's clock, so a masjid in another
    zone would have its prayers shown hours from when they are.
    """
    spot = where_of(entry)
    if spot is None:
        return ""
    from . import astro

    if abs(astro.local_offset_hours() - spot[1] / 15.0) <= ZONE_SLACK_HOURS:
        return ""
    return ("%s looks to be in a different time zone from this computer, and the clock shows every "
            "time on this computer's clock, so its prayers would appear hours from when they are"
            % (str(entry.get("name") or "That masjid")))


def where_of(entry: dict):
    """(latitude, longitude) of a masjid, or None."""
    try:
        lat, lon = float(entry.get("latitude")), float(entry.get("longitude"))
    except (TypeError, ValueError):
        return None
    return (lat, lon) if -66.0 <= lat <= 66.0 and -180.0 <= lon <= 180.0 else None


def _day_times(prayers) -> list:
    """[(name, "HH:MM")] for today if the feed has it complete, else the first day that does."""
    from datetime import datetime

    from . import prayer

    by_day: dict = {}
    for item in prayers:
        by_day.setdefault(item.iqama.date(), {})[item.name] = item.iqama
    today = datetime.now().date()
    for day in sorted(by_day, key=lambda d: (d < today, d)):
        names = by_day[day]
        row = []
        for wanted in prayer.DAILY:
            name = wanted if wanted in names else ("Jumuah" if wanted == "Dhuhr" and "Jumuah" in names else "")
            if name:
                row.append((name, names[name].strftime("%H:%M")))
        if len(row) == len(prayer.DAILY):
            return row
    return []


def inspect(address: str, where=None) -> dict:
    """Read an address exactly as the clock will, and say what came of it.

    {"prayers": [...], "status": str, "source": "mawaqit" | "scrape" | ... | "",
     "how": "labelled" | "headed" | "guessed" | "", "exact": bool, "times": [(name, "HH:MM")]}

    Into a scratch folder, so nothing is saved and the times already in use are
    untouched. The picker runs this before it commits a choice: most masjid
    websites publish nothing the clock can read, and saving one of those would
    replace a setup that works with one that shows no times. "exact" is true
    of anything that came from a data feed and false of what was read off a
    page, which is worth showing to a person before it is kept.
    """
    import shutil
    import tempfile

    from . import prayer, scrape

    folder = tempfile.mkdtemp(prefix="floating-clock-verify-")
    try:
        prayers, status = prayer.load(folder, address, force=True, where=where)
        text = prayer._read(prayer.cache_path(folder)) if prayers else ""
        source = prayer._cached_source(text) if text else ""
        return {
            "prayers": prayers, "status": status, "source": source,
            "how": scrape.how(text) if source == "scrape" else "",
            "exact": bool(prayers) and source != "scrape",
            "times": _day_times(prayers) if prayers else [],
        }
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def verify(address: str, where=None) -> tuple[list, str]:
    """(prayers, status) read exactly as the clock will read them."""
    got = inspect(address, where)
    return got["prayers"], got["status"]


# --- offering something for a choice -------------------------------------------------
def _neighbours(entry: dict, rows: list, radius_km: float = NEIGHBOUR_KM) -> list:
    """The masjids around this one that have something to read, nearest first."""
    here = where_of(entry)
    if here is None:
        return []
    out = []
    for other in rows or ():
        if other is entry or not addresses_for(other):
            continue
        spot = where_of(other)
        if spot is None:
            continue
        gap = distance_km(here[0], here[1], spot[0], spot[1])
        if gap <= radius_km and _fold(other.get("name")) != _fold(entry.get("name")):
            out.append((gap, other))
    out.sort(key=lambda pair: pair[0])
    return [dict(other, km=round(gap, 1)) for gap, other in out]


def _sun_warning(spot, times: list) -> str:
    """What the sun says against these times, or "".

    A data feed is not refused on this -- a masjid can keep an odd schedule -- but
    a listing that says Fajr is two minutes before sunrise is worth a person's
    second look, and it is the kind of thing a stale listing does.
    """
    if spot is None or len(times) != 5:
        return ""
    from . import astro

    sun = astro.sun_today(spot[0], spot[1])
    if sun is None:
        return ""
    minutes = {("Dhuhr" if name == "Jumuah" else name): int(hhmm[:2]) * 60 + int(hhmm[3:])
               for name, hhmm in times}
    return astro.check(minutes, sun)


def _proposal(entry: dict, address: str, got: dict, kind: str) -> dict:
    spot = where_of(entry)
    return {
        "kind": kind, "address": address, "name": str(entry.get("name") or "").strip(),
        "times": got["times"], "status": got["status"], "how": got["how"],
        "source": got["source"], "asked": "", "km": entry.get("km"),
        "latitude": spot[0] if spot else None, "longitude": spot[1] if spot else None,
        "warning": _sun_warning(spot, got["times"]),
    }


def propose(entry: dict, rows: list, progress=None, clock=time.time) -> dict:
    """What to offer for this choice. Slow: run it on a worker.

    {"kind": "exact" | "read" | "proxy" | "none", "address", "name", "times",
     "status", "how", "km", "asked", "latitude", "longitude"}

    "exact"  the times came from a data feed.
    "read"   they were read off a web page; show them and ask.
    "proxy"  the masjid asked for publishes nothing the clock can read, and
             these are the nearest masjid's that does; show them, say whose
             they are, and ask.
    "none"   nothing was found, and `status` says why.

    `rows` is the list the person was choosing from, which is where the
    neighbours come from. `progress(text)` is told what is being tried.
    """
    say = progress or (lambda _text: None)
    name = str(entry.get("name") or "that masjid")
    spot = where_of(entry)
    zone = zone_problem(entry)
    if zone:
        return {"kind": "none", "address": "", "name": name, "times": [], "status": zone + ".",
                "how": "", "source": "", "asked": "", "km": None, "latitude": None, "longitude": None}
    reasons: list[str] = []
    for address in addresses_for(entry):
        say("Checking %s…" % name)
        got = inspect(address, spot)
        if got["prayers"]:
            return _proposal(entry, address, got, "exact" if got["exact"] else "read")
        reasons.append(got["status"].replace("Could not read the prayer times: ", ""))

    started = clock()
    for other in _neighbours(entry, rows)[:NEIGHBOURS_TRIED]:
        if clock() - started > NEIGHBOUR_BUDGET_S:
            break
        say("Nothing readable for %s. Trying %s, %.1f km away…" % (
            name, other.get("name") or "the next masjid", other.get("km") or 0.0))
        for address in addresses_for(other):
            got = inspect(address, where_of(other))
            if got["prayers"]:
                found = _proposal(other, address, got, "proxy")
                found["asked"] = name
                return found

    if not addresses_for(entry):
        why = "%s has no website on record, and no masjid near it has times the clock can read." % name
    else:
        why = "%s: %s" % (name, reasons[0] if reasons else "nothing the clock can read")
        why += " Nor did the masjids around it."
    return {"kind": "none", "address": "", "name": name, "times": [], "status": why,
            "how": "", "source": "", "asked": "", "km": None, "latitude": None, "longitude": None}


def clock_text(times: list, use_24h: bool = False) -> str:
    """"Fajr 6:15 AM · Dhuhr 1:45 PM · ..." for a proposal's times."""
    parts = []
    for name, hhmm in times:
        hour, minute = int(hhmm[:2]), int(hhmm[3:])
        if use_24h:
            shown = "%02d:%02d" % (hour, minute)
        else:
            shown = "%d:%02d %s" % (hour % 12 or 12, minute, "AM" if hour < 12 else "PM")
        parts.append("%s %s" % (name, shown))
    return " · ".join(parts)


def confirmation(proposal: dict, use_24h: bool = False) -> str:
    """What to say before keeping a proposal, which a person has to vouch for.

    Everything is shown, not only what was read off a page: a feed can be out of
    date too, and the person who prays there is the one who can tell.
    """
    times = clock_text(proposal.get("times") or [], use_24h)
    name = proposal.get("name") or "that masjid"
    note = (" Note: %s." % proposal["warning"]) if proposal.get("warning") else ""
    if proposal.get("kind") == "exact":
        source = {"mawaqit": "mawaqit.net", "prayersconnect": "PrayersConnect"}.get(
            proposal.get("source"), "its published timetable")
        return ("%s's times, from %s: %s.%s  Check they match what the masjid announces -- a listing "
                "can be out of date. Press Use this masjid again to keep them." % (name, source, times, note))
    if proposal.get("kind") == "proxy":
        km = proposal.get("km")
        return ("%s publishes no times the clock can read. The nearest masjid that does is %s%s: "
                "%s.  These are %s's times, not %s's, and may differ from what %s announces. "
                "Press Use this masjid again to keep them.%s" % (
                    proposal.get("asked") or "That masjid", name,
                    " (%.1f km away)" % km if km else "", times, name,
                    proposal.get("asked") or "it", proposal.get("asked") or "it", note))
    return ("Read from %s's web page: %s.%s  A page can be out of date or laid out in a way "
            "that fools a reader, so check these against the masjid. Press Use this masjid "
            "again to keep them." % (name, times, note))


def apply(settings: dict, proposal: dict) -> None:
    """Put an accepted proposal into the settings: the address, and where and whose it is."""
    settings["prayer_ics_url"] = proposal["address"]
    settings["prayer_lat"] = proposal.get("latitude")
    settings["prayer_lon"] = proposal.get("longitude")
    settings["prayer_masjid_name"] = proposal.get("name") or ""
    settings["prayer_proxy_for"] = (proposal.get("asked") or "") if proposal.get("kind") == "proxy" else ""


def forget_place(settings: dict) -> None:
    """Drop what says which masjid the address is, when the address is changed by hand."""
    settings["prayer_lat"] = None
    settings["prayer_lon"] = None
    settings["prayer_masjid_name"] = ""
    settings["prayer_proxy_for"] = ""


def describe(entry: dict) -> str:
    """One line for the list: where it is, and whether times will come."""
    where = ", ".join(part for part in (str(entry.get("city") or "").strip(),
                                        str(entry.get("province") or "").strip())
                      if part)
    if not where:
        where = str(entry.get("localisation") or "").strip()
    if not where:
        where = str(entry.get("address") or "").strip()
    if entry.get("slug") and entry.get("iqama") is not False:
        note = "times from mawaqit.net"
    elif address_for(entry):
        note = "times from its website, if it publishes them"
    elif entry.get("iqama") is False:
        note = "does not publish congregation times on mawaqit"
    else:
        note = "no website on record -- a nearby masjid's times can stand in"
    away = " (%.1f km)" % entry["km"] if entry.get("km") is not None else ""
    return "%s%s%s%s" % (where, "  ·  " if where else "", note, away)
