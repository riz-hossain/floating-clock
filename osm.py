"""Masjids near a place, from OpenStreetMap.

Someone travelling to a city they do not know searches "masjid near me" and gets
every one of them with a website to open. The nearest equivalent that is free,
needs no key and may be used by an app is OpenStreetMap: its Nominatim service
turns a city, an address or a postal code into a point, and its Overpass service
lists what is mapped near it -- for a masjid, its name, where it is, and very
often its website and phone.

It is a good list and not a complete one. In Calgary it holds twelve masjids
where the city has many more, and the one somebody would ask about first -- a
well-known centre -- may be missing altogether. So it is one source among
several, merged with the others, and never the only way in: an address can still
be pasted.

Both services are run by volunteers and both ask to be used lightly: an
identifying User-Agent, no more than about a request a second, and no bulk
queries. Nothing here runs unless somebody presses Search, results are kept on
disk for a month so the same place is not asked for twice, and a busy server
(HTTP 429 or 504) is answered by trying a mirror rather than trying harder.

Map data (c) OpenStreetMap contributors, under the Open Database Licence.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import settings as cfg

log = logging.getLogger(__name__)

ATTRIBUTION = "Map data © OpenStreetMap contributors"
USER_AGENT = "FloatingClock/1.0 (+https://github.com/riz-hossain/floating-clock; prayer-times)"

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

TIMEOUT = 25.0
CACHE_DAYS = 30
CACHE_NAME = "osm-cache.json"

# Nominatim asks for at most one request a second. Kept across searches, not
# just within one, so two quick presses of Search do not break it.
_MIN_GAP = 1.2
_lock = threading.Lock()
_last_call = [0.0]


class OsmError(Exception):
    """OpenStreetMap could not be reached or gave nothing usable."""


# --- the cache ----------------------------------------------------------------
def _cache_path() -> str:
    return os.path.join(cfg.config_dir(), CACHE_NAME)


def _load_cache() -> dict:
    try:
        with io.open(_cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    try:
        os.makedirs(cfg.config_dir(), exist_ok=True)
        # Only what is recent: a cache that only grows is a file nobody chose.
        cutoff = time.time() - CACHE_DAYS * 86400
        data = {k: v for k, v in data.items()
                if isinstance(v, dict) and v.get("at", 0) >= cutoff}
        with io.open(_cache_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except OSError:
        log.debug("could not save the map cache", exc_info=True)


def _cached(key: str):
    entry = _load_cache().get(key)
    if isinstance(entry, dict) and entry.get("at", 0) >= time.time() - CACHE_DAYS * 86400:
        return entry.get("value")
    return None


def _remember(key: str, value) -> None:
    data = _load_cache()
    data[key] = {"at": time.time(), "value": value}
    _save_cache(data)


# --- talking to the services -----------------------------------------------------
def _polite() -> None:
    with _lock:
        wait = _MIN_GAP - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _request(url: str, data: bytes | None = None, timeout: float = TIMEOUT, opener=None):
    request = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Accept-Language": "en"})
    with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


# --- a place, as a point ------------------------------------------------------------
def geocode(text: str, opener=None) -> dict | None:
    """{"lat", "lon", "label", "is_masjid"} for a city, address or postal code.

    None when nothing matches, or the service could not be reached. Whether the
    top match is itself a masjid matters: someone who typed a masjid's name has
    already found it, and the list should say so.
    """
    text = " ".join(str(text or "").split())
    if len(text) < 2:
        return None
    key = "geo:" + text.lower()
    hit = _cached(key)
    if hit is not None:
        return hit or None
    _polite()
    params = urllib.parse.urlencode({"q": text, "format": "jsonv2", "limit": 1,
                                     "addressdetails": 0, "extratags": 1})
    try:
        found = _request("%s?%s" % (NOMINATIM, params), opener=opener)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OsmError("could not look that place up (%s)" % exc) from None
    if not isinstance(found, list) or not found:
        _remember(key, {})
        return None
    top = found[0]
    result = {
        "lat": float(top["lat"]), "lon": float(top["lon"]),
        "label": str(top.get("display_name") or text)[:120],
        "is_masjid": top.get("type") in ("place_of_worship", "mosque")
                     or (top.get("extratags") or {}).get("religion") == "muslim",
        "website": str((top.get("extratags") or {}).get("website") or ""),
    }
    _remember(key, result)
    return result


# --- what is mapped near it ---------------------------------------------------------
def _query(lat: float, lon: float, radius_m: int) -> str:
    return ('[out:json][timeout:40];('
            'nwr["amenity"="place_of_worship"]["religion"="muslim"](around:%d,%.5f,%.5f);'
            'nwr["building"="mosque"](around:%d,%.5f,%.5f);'
            'nwr["amenity"="prayer_room"](around:%d,%.5f,%.5f);'
            ');out center tags;') % (radius_m, lat, lon, radius_m, lat, lon, radius_m, lat, lon)


def _clean(element: dict) -> dict | None:
    tags = element.get("tags") or {}
    lat = element.get("lat") if element.get("lat") is not None else (element.get("center") or {}).get("lat")
    lon = element.get("lon") if element.get("lon") is not None else (element.get("center") or {}).get("lon")
    name = str(tags.get("name") or tags.get("name:en") or tags.get("name:ar") or "").strip()
    if lat is None or lon is None or not name:
        return None
    site = str(tags.get("website") or tags.get("contact:website") or tags.get("url") or "").strip()
    if site and not site.lower().startswith(("http://", "https://")):
        site = "http://" + site
    street = " ".join(str(tags.get(k) or "") for k in ("addr:housenumber", "addr:street")).strip()
    return {
        "name": name,
        "latitude": float(lat),
        "longitude": float(lon),
        "website": site,
        "phone": str(tags.get("phone") or tags.get("contact:phone") or "").strip(),
        "city": str(tags.get("addr:city") or "").strip(),
        "province": str(tags.get("addr:province") or tags.get("addr:state") or "").strip(),
        "address": street,
        "type": "prayer_room" if tags.get("amenity") == "prayer_room" else "mosque",
        "source": "osm",
    }


def mosques_near(lat: float, lon: float, radius_km: float = 20.0, opener=None) -> list[dict]:
    """Masjids mapped within `radius_km` of a point. Raises OsmError if none of
    the servers answers; an empty list means the area is simply unmapped."""
    radius_m = int(max(1.0, min(radius_km, 50.0)) * 1000)
    key = "near:%.2f,%.2f,%d" % (lat, lon, radius_m // 1000)
    hit = _cached(key)
    if hit is not None:
        return hit
    body = urllib.parse.urlencode({"data": _query(lat, lon, radius_m)}).encode()
    last = "no answer"
    for server in OVERPASS:
        _polite()
        try:
            elements = _request(server, data=body, opener=opener).get("elements") or []
        except urllib.error.HTTPError as exc:
            last = "HTTP %d" % exc.code
            if exc.code in (429, 502, 503, 504):
                time.sleep(2.0)               # busy: the next mirror, not a harder push
                continue
            raise OsmError("the map service answered %s" % last) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = str(exc)[:60]
            continue
        seen, out = set(), []
        for element in elements:
            item = _clean(element)
            if item is None:
                continue
            marker = (item["name"].lower(), round(item["latitude"], 3), round(item["longitude"], 3))
            if marker in seen:
                continue
            seen.add(marker)
            out.append(item)
        _remember(key, out)
        return out
    raise OsmError("the map service is busy (%s); try again in a minute" % last)
