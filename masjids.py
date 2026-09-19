"""Finding a masjid, so nobody has to go hunting for a web address.

Two places are searched and the answers merged:

- **MAWAQIT**, live. Several thousand masjids, searchable by name, by town or
  by a point on the map, and the ones it has usually carry a year of times.
- **The bundled directory**, offline. A few hundred Canadian masjids with
  their coordinates and websites, distilled from research done for the
  LiveAzan project. MAWAQIT is thin in Canada -- four within fifty kilometres
  of Waterloo against eleven in the city itself -- so this fills the gap, and
  it answers instantly with no network at all.

A masjid found here still has to be turned into something the clock can read
times from, which is what `address_for` does: a MAWAQIT page when there is
one, otherwise the masjid's own website, which prayer.py will try to read as
a timetable. Some entries have neither, and the picker says so rather than
saving an address that will quietly never work.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import sys
import unicodedata

from . import mawaqit

log = logging.getLogger(__name__)

DIRECTORY_NAME = "masjids.json"

# How far apart two entries can be and still be judged the same masjid. A
# building is tens of metres across; different masjids on the same street are
# rarely closer than this.
SAME_PLACE_KM = 0.25

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


# --- the two together -------------------------------------------------------
def search(text: str = "", lat=None, lon=None, radius_km: float = 50.0,
           limit: int = 40, online: bool = True) -> tuple[list[dict], str]:
    """(masjids, trouble) from both sources, nearest or best match first.

    Trouble is the live search's complaint, if it had one -- the bundled
    results still come back, because being offline should narrow the answer
    rather than empty it.
    """
    results = search_directory(text, lat, lon, radius_km, limit)
    trouble = ""
    if online:
        try:
            live = mawaqit.search(word=text, lat=lat, lon=lon,
                                  radius_km=int(radius_km))
        except mawaqit.MawaqitError as exc:
            trouble = str(exc)
            live = []
        except Exception as exc:                 # a search must never crash
            trouble = str(exc)[:90] or exc.__class__.__name__
            live = []
        results = _merge(results, live)

    if lat is not None and lon is not None:
        results.sort(key=lambda r: distance_km(lat, lon, r.get("latitude"),
                                               r.get("longitude")))
    return results[:limit], trouble


def _merge(bundled: list, live: list) -> list:
    """Live entries first, with the bundled ones that are not the same place.

    MAWAQIT wins a tie because it is the one that might carry times; the
    bundled entry only knows where the masjid is.
    """
    out = list(live)
    for entry in bundled:
        same = False
        for other in out:
            if (_fold(entry.get("name")) == _fold(other.get("name"))
                    or distance_km(entry.get("latitude"), entry.get("longitude"),
                                   other.get("latitude"),
                                   other.get("longitude")) <= SAME_PLACE_KM):
                same = True
                break
        if not same:
            out.append(entry)
    return out


def address_for(entry: dict) -> str:
    """What to put in the masjid box for this one, or "" when there is nothing.

    A MAWAQIT page is preferred: it carries a year of congregation times. A
    website is a guess -- prayer.py will try to read a timetable off it, and
    say so plainly when it cannot.
    """
    if not isinstance(entry, dict):
        return ""
    slug = str(entry.get("slug") or "").strip()
    if slug:
        return mawaqit.page_url(slug)
    return str(entry.get("website") or entry.get("site") or "").strip()


def describe(entry: dict) -> str:
    """One line for the list: where it is, and whether times will come."""
    where = ", ".join(part for part in (str(entry.get("city") or "").strip(),
                                        str(entry.get("province") or "").strip())
                      if part)
    if not where:
        where = str(entry.get("localisation") or "").strip()
    if entry.get("slug"):
        note = "times from mawaqit.net"
    elif address_for(entry):
        note = "times from its website, if it publishes them"
    else:
        note = "no website on record -- no times"
    return "%s%s%s" % (where, "  ·  " if where else "", note)
