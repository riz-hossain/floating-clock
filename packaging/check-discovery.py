"""Offline checks for finding a masjid and for what is done when it has no times.

    * OpenStreetMap: a place into a point, a point into the masjids mapped near
      it, kept a month, politely, and with another server tried when one is busy
    * merging what MAWAQIT, the bundled directory and the map each know
    * offering something for a choice: exact, read off a page, a neighbour's, none
    * the settings that remember where a masjid is and whose times these are
    * the loader handing the position along and saying when times are borrowed

No network and no browser, so it cannot flake.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-discovery.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
from datetime import datetime, timedelta

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import dpt, ics, masjids, mawaqit, osm, prayer, scrape  # noqa: E402
from floating_clock import settings as cfg  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


class Reply:
    def __init__(self, body) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self, size: int = -1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


osm._MIN_GAP = 0.0                                   # nobody is being kept waiting here

# --- OpenStreetMap ---------------------------------------------------------------------
print("OpenStreetMap")

asked: list[str] = []
agents: list[str] = []


def nominatim(request, timeout=None):
    asked.append(request.full_url)
    agents.append(request.get_header("User-agent") or "")
    if "Nowhere" in request.full_url:
        return Reply([])
    if "Masjid" in request.full_url:
        return Reply([{"lat": "51.05", "lon": "-114.07", "display_name": "Calgary Central Masjid",
                       "type": "place_of_worship", "extratags": {"religion": "muslim"}}])
    return Reply([{"lat": "51.0447", "lon": "-114.0719", "display_name": "Calgary, Alberta, Canada",
                   "type": "city", "extratags": {}}])


place = osm.geocode("Calgary", opener=nominatim)
check("a city becomes a point", place and abs(place["lat"] - 51.0447) < 1e-6 and "Calgary" in place["label"], str(place))
check("that is not itself a masjid", place and place["is_masjid"] is False)
again = osm.geocode("  calgary ", opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked twice")))
check("and is remembered, so the same place is not asked for twice", again == place)
check("the map service is told who is asking", agents and all("FloatingClock" in a for a in agents), str(agents))
check("a masjid's name is recognised as a masjid",
      osm.geocode("Masjid Calgary", opener=nominatim)["is_masjid"] is True)
check("a place that is nowhere is None", osm.geocode("Nowhere at all", opener=nominatim) is None)
before = len(asked)
osm.geocode("Nowhere at all", opener=nominatim)
check("and that is remembered too", len(asked) == before)


def offline(*a, **k):
    raise urllib.error.URLError("no route")


try:
    osm.geocode("Somewhere unseen", opener=offline)
    check("an unreachable service is an error, not silence", False, "no error raised")
except osm.OsmError as exc:
    check("an unreachable service says so", "could not look" in str(exc), str(exc))

elements = {"elements": [
    {"type": "node", "lat": 51.05, "lon": -114.07,
     "tags": {"name": "Masjid One", "amenity": "place_of_worship", "religion": "muslim",
              "website": "masjidone.example", "phone": "+1 403 555 0100", "addr:housenumber": "12",
              "addr:street": "Main St", "addr:city": "Calgary"}},
    {"type": "way", "center": {"lat": 51.06, "lon": -114.08}, "tags": {"name": "Masjid Two", "building": "mosque"}},
    {"type": "node", "lat": 51.05001, "lon": -114.07001, "tags": {"name": "Masjid One", "religion": "muslim"}},
    {"type": "node", "lat": 51.07, "lon": -114.09, "tags": {"religion": "muslim"}},          # no name
    {"type": "node", "lat": 51.08, "lon": -114.10, "tags": {"name": "Prayer Room", "amenity": "prayer_room"}},
]}
calls: list[str] = []


def overpass(request, timeout=None):
    calls.append(request.full_url)
    return Reply(elements)


found = osm.mosques_near(51.0447, -114.0719, 20, opener=overpass)
names = [m["name"] for m in found]
check("masjids near a point", names == ["Masjid One", "Masjid Two", "Prayer Room"], str(names))
one = found[0]
check("with the website, given a scheme when it was left off", one["website"] == "http://masjidone.example", one["website"])
check("the phone and the street", one["phone"].startswith("+1") and one["address"] == "12 Main St")
check("a way is placed at its centre", abs(found[1]["latitude"] - 51.06) < 1e-9)
check("the same masjid mapped twice is listed once", names.count("Masjid One") == 1)
check("a nameless one cannot be offered", all(m["name"] for m in found))
check("a prayer room is marked as one", found[2]["type"] == "prayer_room")
time.sleep(0.2)                                  # let the slower servers' answers land
asked_first = len(calls)
osm.mosques_near(51.0447, -114.0719, 20, opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked twice")))
check("what was found is kept, so the same area is not asked for twice", asked_first == len(osm.OVERPASS)
      and len(calls) == asked_first, "%d then %d" % (asked_first, len(calls)))

busy = {"n": 0}


def one_busy(request, timeout=None):
    busy["n"] += 1
    if "overpass-api.de" in request.full_url:
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
    return Reply(elements)


got = osm.mosques_near(45.0, -75.0, 20, opener=one_busy)
check("a busy server does not stop the others answering", len(got) == 3 and busy["n"] >= 2, str(busy))
check("and each server is asked once, not pressed", busy["n"] == len(osm.OVERPASS), str(busy))


def all_busy(request, timeout=None):
    raise urllib.error.HTTPError(request.full_url, 504, "Gateway Timeout", {}, None)


try:
    osm.mosques_near(10.0, 10.0, 20, opener=all_busy)
    check("every server busy is an error", False, "no error raised")
except osm.OsmError as exc:
    check("every server busy says to try again", "busy" in str(exc), str(exc))

check("the credit the licence asks for is on hand", "OpenStreetMap" in osm.ATTRIBUTION)

# --- merging ------------------------------------------------------------------------------------
print("merging what the sources know")

MW = {"name": "Masjid Al-Noor", "slug": "masjid-al-noor-calgary", "city": "Calgary", "latitude": 51.05,
      "longitude": -114.07, "site": "", "iqama": False, "source": "mawaqit"}
MAP = {"name": "Al Noor Masjid", "latitude": 51.0508, "longitude": -114.0709, "website": "http://alnoor.example",
       "source": "osm"}
FAR = {"name": "Masjid Two", "latitude": 51.06, "longitude": -114.08, "website": "http://two.example", "source": "osm"}

merged = masjids._merge([MW], [], [MAP, FAR])
check("the same masjid in two sources is one entry", len(merged) == 2, str([m["name"] for m in merged]))
check("MAWAQIT's entry wins, since it may carry times itself", merged[0]["slug"] == "masjid-al-noor-calgary")
check("and takes the website the map knew and it did not", merged[0].get("website") == "http://alnoor.example")
check("a masjid MAWAQIT switched off can still be read from its own site",
      masjids.addresses_for(merged[0]) == ["http://alnoor.example"], str(masjids.addresses_for(merged[0])))
check("and is described that way", "its website" in masjids.describe(merged[0]), masjids.describe(merged[0]))
on = dict(MW, iqama=True)
check("one that publishes times has its MAWAQIT page first, its site after",
      masjids.addresses_for(dict(on, website="http://alnoor.example")) ==
      [mawaqit.page_url("masjid-al-noor-calgary"), "http://alnoor.example"])
check("two entries on the same spot are one masjid, whatever each calls it",
      masjids._same_place({"name": "Masjid Al-Noor", "latitude": 51.05, "longitude": -114.07},
                          {"name": "Prayer Hall", "latitude": 51.0503, "longitude": -114.0702}))
check("two masjids down one street are two",
      not masjids._same_place({"name": "Masjid Bilal", "latitude": 51.05, "longitude": -114.07},
                              {"name": "Dar ul Hikmah", "latitude": 51.0536, "longitude": -114.07}))
check("and one name in two cities is two",
      not masjids._same_place({"name": "Masjid Al-Noor", "latitude": 51.05, "longitude": -114.07},
                              {"name": "Masjid Al-Noor", "latitude": 43.65, "longitude": -79.38}))
check("an address that is not a web address is not read", masjids.addresses_for({"website": "javascript:alert(1)"}) == [])

# --- searching -----------------------------------------------------------------------------------
print("searching")

real = (osm.geocode, osm.mosques_near, mawaqit.search)
bundled = masjids._directory
masjids._directory = [{"name": "Bundled Masjid", "city": "Calgary", "province": "Alberta", "latitude": 51.03,
                       "longitude": -114.05, "website": "http://bundled.example"}]       # only what is set here
osm.geocode = lambda text, opener=None: {"lat": 51.0447, "lon": -114.0719, "label": "Calgary", "is_masjid": False}
osm.mosques_near = lambda lat, lon, radius_km=20.0, opener=None: [dict(FAR), dict(MAP)]
mawaqit.search = lambda word="", lat=None, lon=None, radius_km=20, **k: [dict(MW)]
rows, trouble = masjids.search("Calgary")
check("a town gives every source's masjids, merged",
      sorted(r["name"] for r in rows) == ["Bundled Masjid", "Masjid Al-Noor", "Masjid Two"],
      str([r["name"] for r in rows]))
check("nearest first, each with how far", rows[0]["km"] <= rows[1]["km"] and rows[0]["km"] is not None, str(rows))
check("nothing went wrong", trouble == "", trouble)

rows, trouble = masjids.search("Masjid Two")
check("an exact name is ahead of what is merely near", rows[0]["name"] == "Masjid Two", str([r["name"] for r in rows]))


def down(*a, **k):
    raise osm.OsmError("the map service is busy")


osm.mosques_near = down
rows, trouble = masjids.search("Calgary")
check("one source down narrows the answer rather than emptying it",
      sorted(r["name"] for r in rows) == ["Bundled Masjid", "Masjid Al-Noor"], str([r["name"] for r in rows]))
check("and says which", trouble.startswith("map: ") and "busy" in trouble, trouble)

rows, trouble = masjids.search("Calgary", online=False)
check("offline it is the bundled directory alone, and no complaint", trouble == "" and all(r["source"] == "directory" for r in rows))
osm.geocode, osm.mosques_near, mawaqit.search = real
masjids._directory = bundled

# --- offering something for a choice -------------------------------------------------------------------
print("offering something for a choice")


def fake_prayers(exact=True, how=""):
    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return [prayer.Prayer(n, now + timedelta(hours=h, minutes=m)) for n, (h, m) in zip(
        prayer.DAILY, ((6, 15), (13, 45), (17, 45), (19, 28), (21, 0)))]


def reading(exact=True, how=""):
    ps = fake_prayers()
    return {"prayers": ps, "status": "ok", "source": "" if exact else "scrape", "how": how, "exact": exact,
            "times": masjids._day_times(ps)}


NOTHING = {"prayers": [], "status": "Could not read the prayer times: no page prints its times", "source": "",
           "how": "", "exact": False, "times": []}
tried: list[str] = []
outcomes: dict = {}


def fake_inspect(address, where=None):
    tried.append(address)
    return outcomes.get(address, NOTHING)


real_inspect = masjids.inspect
masjids.inspect = fake_inspect
from floating_clock import astro  # noqa: E402

real_local_offset = astro.local_offset_hours
astro.local_offset_hours = lambda when=None: -4.0               # Eastern Daylight Time, whatever runs this

target = {"name": "Erin Centre", "latitude": 43.77, "longitude": -80.06, "website": "http://erin.example"}
near = {"name": "Near Masjid", "latitude": 43.80, "longitude": -80.06, "website": "http://near.example"}
nearer = {"name": "Nearer Masjid", "latitude": 43.78, "longitude": -80.06, "website": "http://nearer.example"}
far = {"name": "Far Masjid", "latitude": 44.50, "longitude": -80.06, "website": "http://far.example"}
bare = {"name": "Bare Masjid", "latitude": 43.771, "longitude": -80.06}
rows = [target, near, nearer, far, bare]

outcomes = {"http://erin.example": reading(exact=True)}
got = masjids.propose(target, rows)
check("a masjid that reads from a data feed is offered as it is", got["kind"] == "exact" and got["address"] == "http://erin.example")
check("without looking at its neighbours", tried == ["http://erin.example"], str(tried))
check("with the five times it will show", [n for n, _t in got["times"]] == list(prayer.DAILY), str(got["times"]))
check("and where it is, for the settings", got["latitude"] == 43.77 and got["longitude"] == -80.06)

tried.clear()
outcomes = {"http://erin.example": reading(exact=False, how="guessed")}
got = masjids.propose(target, rows)
check("one read off a web page is offered to be shown first", got["kind"] == "read" and got["how"] == "guessed")

tried.clear()
outcomes = {"http://near.example": reading(exact=False, how="labelled"), "http://nearer.example": reading(exact=True),
            "http://far.example": reading(exact=True)}
progress: list[str] = []
got = masjids.propose(target, rows, progress=progress.append)
check("with nothing readable, the nearest masjid that has something is offered", got["kind"] == "proxy"
      and got["name"] == "Nearer Masjid", "%s %s" % (got["kind"], got["name"]))
check("and it says whose times they replace", got["asked"] == "Erin Centre")
check("nearer ones are tried before farther ones, and the search stops at the first that works",
      tried == ["http://erin.example", "http://nearer.example"], str(tried))
check("and the neighbour's own position is what is kept, since that is where its page is about",
      got["latitude"] == 43.78)
check("what is being tried is said as it goes", any("Nearer Masjid" in p for p in progress), str(progress))

tried.clear()
outcomes = {"http://far.example": reading(exact=True)}
got = masjids.propose(target, rows)
check("a masjid twenty-five kilometres off is too far to stand in", got["kind"] == "none" and "http://far.example" not in tried,
      "%s %s" % (got["kind"], tried))
check("and the answer says nothing near had times", "around it" in got["status"], got["status"])

tried.clear()
got = masjids.propose(bare, rows)
check("a masjid with no website at all still gets the nearest that has one", tried and got["kind"] in ("proxy", "none"))

tried.clear()
outcomes = {"http://nearer.example": reading(exact=True)}
clock = iter([0.0, 500.0, 500.0, 500.0, 500.0, 500.0, 500.0])
got = masjids.propose(target, rows, clock=lambda: next(clock))
check("the search for a neighbour gives up when its time is used", got["kind"] == "none", got["kind"])

print("a listing and the masjid's own page")


def said(source, how, hhmm):
    """A reading whose five times are given, as one source or another."""
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ps = [prayer.Prayer(n, day + timedelta(hours=int(t[:2]), minutes=int(t[3:]))) for n, t in zip(prayer.DAILY, hhmm)]
    return {"prayers": ps, "status": "ok", "source": source, "how": how, "exact": source != "scrape",
            "times": masjids._day_times(ps)}


LISTING = ("06:45", "14:30", "16:32", "19:06", "20:36")          # what mawaqit.net held for Ottawa South
PAGE = ("05:30", "13:30", "17:30", "19:12", "20:45")             # what the masjid's own page said
both = {"name": "Both Masjid", "slug": "both-masjid-x", "iqama": True, "website": "http://both.example",
        "latitude": 43.77, "longitude": -80.06}
listing_address = mawaqit.page_url("both-masjid-x")

outcomes = {listing_address: said("mawaqit", "", LISTING), "http://both.example": said("scrape", "labelled", LISTING)}
got = masjids.propose(both, [both])
check("a listing and a page that agree change nothing", got["kind"] == "exact" and "disagrees" not in got, str(got.get("kind")))

outcomes = {listing_address: said("mawaqit", "", LISTING), "http://both.example": said("scrape", "labelled", PAGE)}
got = masjids.propose(both, [both])
check("where they differ and the page reads with certainty, the page is what is offered",
      got["kind"] == "read" and got["address"] == "http://both.example", "%s %s" % (got["kind"], got["address"]))
check("and the listing is named as the dissenter", got.get("disagrees", {}).get("who") == "mawaqit.net"
      and "Fajr" in got["disagrees"]["prayers"], str(got.get("disagrees")))
text = masjids.confirmation(got)
check("in words a person can act on", "Mawaqit.net lists different times" in text and "Fajr 6:45 AM" in text
      and "Fajr 5:30 AM" in text, text)

outcomes = {listing_address: said("mawaqit", "", LISTING), "http://both.example": said("scrape", "guessed", PAGE)}
got = masjids.propose(both, [both])
check("a page that was only guessed at does not displace the listing", got["kind"] == "exact"
      and got["address"] == listing_address, "%s %s" % (got["kind"], got["address"]))
check("but is mentioned, since the two disagree", got.get("disagrees", {}).get("who") == "the masjid's own website"
      and "reads differently" in masjids.confirmation(got), masjids.confirmation(got))

outcomes = {listing_address: said("mawaqit", "", LISTING)}
got = masjids.propose(both, [both])
check("a page that cannot be read leaves the listing alone", got["kind"] == "exact" and "disagrees" not in got)

check("a few minutes' difference is not a disagreement",
      masjids.differing([("Fajr", "05:30"), ("Isha", "20:45")], [("Fajr", "05:38"), ("Isha", "20:41")]) == [])
check("ten and more is", masjids.differing([("Fajr", "05:30")], [("Fajr", "05:45")]) == ["Fajr"])
check("a Friday's Jumu'ah is not compared with Dhuhr",
      masjids.differing([("Jumuah", "13:15"), ("Asr", "17:30")], [("Dhuhr", "13:45"), ("Asr", "17:30")]) == [])
outcomes = {}

check("a masjid in another time zone is not offered, and it says why",
      masjids.propose({"name": "Calgary Centre", "latitude": 51.05, "longitude": -114.07,
                       "website": "http://calgary.example"}, rows)["kind"] == "none"
      and "different time zone" in masjids.propose({"name": "Calgary Centre", "latitude": 51.05,
                                                    "longitude": -114.07}, rows)["status"])
check("nor is anything even tried for it", "http://calgary.example" not in tried)
check("a masjid whose position is not known is not accused of it", masjids.zone_problem({"name": "x"}) == "")
astro.local_offset_hours = real_local_offset
masjids.inspect = real_inspect

text = masjids.confirmation({"kind": "exact", "name": "Erin Centre", "source": "mawaqit",
                             "times": [("Fajr", "05:30"), ("Isha", "20:00")]})
check("times from a data feed are shown too, and said to be a listing that can be out of date",
      "from mawaqit.net" in text and "Fajr 5:30 AM" in text and "out of date" in text, text)
text = masjids.confirmation({"kind": "exact", "name": "Erin Centre", "source": "mawaqit", "times": [("Fajr", "05:30")],
                             "warning": "Fajr at 06:45 is not possible on this day here"})
check("with the sun's objection, when it has one", "Note: Fajr at 06:45 is not possible" in text, text)
OTTAWA = (45.42, -75.70)
sun = astro.sun_today(OTTAWA[0], OTTAWA[1])
fits = [("Fajr", sun[0] - 90), ("Dhuhr", sun[1] + 30), ("Asr", (sun[1] + sun[2]) / 2), ("Maghrib", sun[2] + 5),
        ("Isha", sun[2] + 90)]
hm = lambda m: "%02d:%02d" % divmod(int(round(m)), 60)   # noqa: E731
check("times that fit the sun draw no objection",
      masjids._sun_warning(OTTAWA, [(n, hm(m)) for n, m in fits]) == "", masjids._sun_warning(OTTAWA, [(n, hm(m)) for n, m in fits]))
late = [(n, hm(m)) for n, m in fits]
late[0] = ("Fajr", hm(sun[0] - 2))
check("Fajr two minutes before sunrise does",
      "Fajr" in masjids._sun_warning(OTTAWA, late), masjids._sun_warning(OTTAWA, late))
check("and no position, no objection", masjids._sun_warning(None, late) == "")
text = masjids.confirmation({"kind": "read", "name": "Erin Centre", "times": [("Fajr", "05:30"), ("Isha", "20:00")]})
check("a reading is shown as a 12-hour clock, with a warning it may be wrong",
      "Fajr 5:30 AM" in text and "Isha 8:00 PM" in text and "check" in text, text)
check("or on a 24-hour one when that is how the clock is set",
      "Isha 20:00" in masjids.confirmation({"kind": "read", "name": "x", "times": [("Isha", "20:00")]}, True))
text = masjids.confirmation({"kind": "proxy", "name": "Nearer Masjid", "asked": "Erin Centre", "km": 1.1,
                             "times": [("Fajr", "06:00")]})
check("a neighbour's times are labelled as the neighbour's, not the masjid's",
      "Erin Centre publishes no times" in text and "Nearer Masjid's times, not Erin Centre's" in text and "1.1 km" in text, text)

settings = dict(cfg.DEFAULTS)
masjids.apply(settings, {"kind": "proxy", "address": "http://nearer.example", "name": "Nearer Masjid",
                         "asked": "Erin Centre", "latitude": 43.78, "longitude": -80.06})
check("an accepted choice is written into the settings", settings["prayer_ics_url"] == "http://nearer.example"
      and settings["prayer_lat"] == 43.78 and settings["prayer_proxy_for"] == "Erin Centre")
masjids.apply(settings, {"kind": "read", "address": "http://erin.example", "name": "Erin Centre",
                         "asked": "left over", "latitude": 43.77, "longitude": -80.06})
check("and choosing a masjid's own times ends the borrowing", settings["prayer_proxy_for"] == "")
masjids.forget_place(settings)
check("an address changed by hand forgets which masjid it was", settings["prayer_lat"] is None
      and settings["prayer_masjid_name"] == "" and settings["prayer_proxy_for"] == "")

# --- the settings ------------------------------------------------------------------------------------------
print("the settings that remember where a masjid is")

data = dict(cfg.DEFAULTS, prayer_lat="43.4", prayer_lon=-80.5, prayer_masjid_name="  Waterloo  ")
data = cfg.sanitise(data)
check("a position is kept as numbers", data["prayer_lat"] == 43.4 and data["prayer_lon"] == -80.5)
check("and names are trimmed", data["prayer_masjid_name"] == "Waterloo")
data = cfg.sanitise(dict(cfg.DEFAULTS, prayer_lat="north", prayer_lon=500))
check("nonsense is no position at all", data["prayer_lat"] is None and data["prayer_lon"] is None)
check("and by default there is none", cfg.DEFAULTS["prayer_lat"] is None and cfg.DEFAULTS["prayer_proxy_for"] == "")

# --- the reader is reached, in order ----------------------------------------------------------------------------
print("reading a site's own page, last")

seen: dict = {}
real_parts = (dpt.resolve, dpt.fetch, prayer._embedded_page, ics.fetch, scrape.fetch, prayer._renderer)
dpt.resolve = lambda address, **k: address


def no_api(home, **k):
    raise dpt.NoApi("no plugin")


def no_calendar(address, **k):
    raise RuntimeError("not a calendar")


def scraped(home, **k):
    seen.update(k, home=home)
    return json.dumps({"source": "scrape", "name": "Erin Centre", "asof": datetime.now().strftime("%Y-%m-%d"),
                       "how": "labelled", "iqamah": {"Fajr": "06:15", "Dhuhr": "13:45", "Asr": "17:45",
                                                     "Maghrib": "19:28", "Isha": "21:00"}})


dpt.fetch, prayer._embedded_page, ics.fetch, scrape.fetch = no_api, (lambda home: ""), no_calendar, scraped
prayer._renderer = lambda: "the browser"
folder = tempfile.mkdtemp(prefix="floating-clock-check-")
loaded, status = prayer.load(folder, "http://erin.example", force=True, where=(43.77, -80.06))
check("a site with no plugin and no calendar is read as a page", seen.get("home") == "http://erin.example", str(seen))
check("with where the masjid is, for the sun", seen.get("where") == (43.77, -80.06), str(seen))
check("and a browser to hand for pages that need one", seen.get("render") == "the browser")
check("its five times reach the clock", len(loaded) >= 1 and loaded[0].name in prayer.DAILY, str(loaded))
check("and are said to have been read from a page", "read from its web page" in status, status)


def fails(home, **k):
    raise scrape.NoTimes("its timetable is posted as an image, which the clock cannot read yet")


scrape.fetch = fails
loaded, status = prayer.load(tempfile.mkdtemp(prefix="floating-clock-check-"), "http://erin.example", force=True)
check("a site that cannot be read says why, and what would work",
      not loaded and "image" in status and "mawaqit.net" in status, status)


def stale_plugin(home, **k):
    raise dpt.DptError("that masjid's timetable stops at 2024-12-31; it needs updating on their website")


def read_page(sun_checked):
    def fetch(home, **k):
        seen["scraped"] = seen.get("scraped", 0) + 1
        return json.dumps({"source": "scrape", "name": "Erin Centre", "asof": datetime.now().strftime("%Y-%m-%d"),
                           "how": "labelled", "sun_checked": sun_checked,
                           "iqamah": {"Fajr": "06:15", "Dhuhr": "13:45", "Asr": "17:45", "Maghrib": "19:28",
                                      "Isha": "21:00"}})
    return fetch


print("a plugin that is there but not usable")
dpt.fetch, prayer._embedded_page, ics.fetch = stale_plugin, (lambda home: ""), no_calendar
seen.clear()
scrape.fetch = read_page(True)
loaded, status = prayer.load(tempfile.mkdtemp(prefix="floating-clock-check-"), "http://erin.example", force=True)
check("does not end the search: the page may say more than the plugin", loaded and seen.get("scraped") == 1, status)

scrape.fetch = read_page(False)
loaded, status = prayer.load(tempfile.mkdtemp(prefix="floating-clock-check-"), "http://erin.example", force=True)
check("but a page nothing could check against the sun is not trusted over a stale plugin",
      not loaded and "stops at 2024-12-31" in status, status)

scrape.fetch = fails
loaded, status = prayer.load(tempfile.mkdtemp(prefix="floating-clock-check-"), "http://erin.example", force=True)
check("and if nothing else works, the message is the plugin's, which says what to fix",
      not loaded and "stops at 2024-12-31" in status and "mawaqit.net" not in status, status)

prayer._embedded_page = lambda home: "https://mawaqit.net/en/erin-centre-erin"
real_mawaqit_fetch = mawaqit.fetch
mawaqit.fetch = lambda page, **k: scraped("x")
loaded, status = prayer.load(tempfile.mkdtemp(prefix="floating-clock-check-"), "http://erin.example", force=True)
check("a mawaqit widget on the page is used when the plugin is not", bool(loaded), status)
mawaqit.fetch = real_mawaqit_fetch
prayer._embedded_page = lambda home: ""

print("a calendar with no prayers in it")
tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%dT120000Z")
CRLF = chr(13) + chr(10)
EVENTS_ONLY = CRLF.join(["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:1", "SUMMARY:Bake sale",
                         "DTSTART:" + tomorrow, "DTEND:" + tomorrow, "END:VEVENT", "END:VCALENDAR", ""])
ics.fetch = lambda address, **k: EVENTS_ONLY
dpt.fetch = no_api
seen.clear()
scrape.fetch = scraped
loaded, status = prayer.load(tempfile.mkdtemp(prefix="floating-clock-check-"), "http://erin.example", force=True)
check("an events calendar is not the timetable: the page is read instead", loaded and seen.get("home"), status)

print("a site that cannot be reached")


def unreachable(address, **k):
    raise dpt.DptError("could not reach the site (Name or service not known)")


dpt.resolve = unreachable
seen.clear()
scrape.fetch = scraped
loaded, status = prayer.load(tempfile.mkdtemp(prefix="floating-clock-check-"), "http://gone.example", force=True)
check("is an error at once", not loaded and "could not reach the site" in status, status)
check("and nothing further is tried", not seen)
dpt.resolve, dpt.fetch, prayer._embedded_page, ics.fetch, scrape.fetch, prayer._renderer = real_parts

print("probing a site")


class Answer:
    def __init__(self, url):
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


final, problem = dpt.probe("http://vanity.example", opener=lambda req, timeout=None: Answer("https://platform.example/x/"))
check("a site that answers says where it really lives", (final, problem) == ("https://platform.example/x/", ""), str((final, problem)))
final, problem = dpt.probe("http://vanity.example", opener=lambda req, timeout=None: (_ for _ in ()).throw(
    urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)))
check("one that refuses us has still answered", problem == "" and final == "http://vanity.example", str((final, problem)))
final, problem = dpt.probe("http://gone.example", opener=lambda req, timeout=None: (_ for _ in ()).throw(
    urllib.error.URLError("[Errno 11001] getaddrinfo failed")))
check("one that cannot be reached says why", "getaddrinfo" in problem, problem)
started = time.time()
final, problem = dpt.probe("http://slow.example", timeout=0.3, opener=lambda req, timeout=None: time.sleep(6))
check("one that does not answer is not waited for", problem == "no answer" and time.time() - started < 5.0,
      "%s after %.1fs" % (problem, time.time() - started))
try:
    dpt.resolve("http://gone.example", strict=True, opener=lambda req, timeout=None: (_ for _ in ()).throw(
        urllib.error.URLError("no route")))
    check("resolve can insist that a site answers", False, "no error raised")
except dpt.DptError as exc:
    check("resolve can insist that a site answers", "could not reach" in str(exc), str(exc))
check("and by default it does not", dpt.resolve("http://gone.example", opener=lambda req, timeout=None: (_ for _ in ()).throw(
    urllib.error.URLError("no route"))) == "http://gone.example")

# --- the loader -----------------------------------------------------------------------------------------------------
print("the loader")

given: dict = {}
real_load = prayer.load


def spy_load(base, url="", now=None, force=False, fetcher=None, where=None):
    given["where"], given["url"] = where, url
    return fake_prayers(), "Erin Centre (read from its web page) · updated just now."


prayer.load = spy_load
landed: list = []
loader = prayer.Loader(dict(cfg.DEFAULTS, prayer_ics_url="http://nearer.example", prayer_lat=43.78,
                            prayer_lon=-80.06, prayer_proxy_for="Erin Centre"),
                       tempfile.mkdtemp(prefix="floating-clock-check-"), lambda fn: fn(),
                       lambda prayers, status: landed.append(status))
loader.refresh()
deadline = time.time() + 10
while not landed and time.time() < deadline:
    time.sleep(0.05)
check("the masjid's position goes to the reader", given.get("where") == (43.78, -80.06), str(given))
check("times borrowed from a neighbour are called approximate", landed and landed[0].startswith("Approximate: Erin Centre"),
      str(landed))

landed.clear()
loader = prayer.Loader(dict(cfg.DEFAULTS, prayer_ics_url="http://erin.example"),
                       tempfile.mkdtemp(prefix="floating-clock-check-"), lambda fn: fn(),
                       lambda prayers, status: landed.append(status))
loader.refresh()
deadline = time.time() + 10
while not landed and time.time() < deadline:
    time.sleep(0.05)
check("with no position saved there is none to give", given.get("where") is None, str(given))
check("and its own times are not called approximate", landed and not landed[0].startswith("Approximate"), str(landed))
prayer.load = real_load

print()
if failures:
    print("%d discovery check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all discovery checks passed")
