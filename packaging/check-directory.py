"""Offline checks on the bundled masjid directory and the corrections made to it.

The directory is research, and research has mistakes the clock pays for: a
website that is another masjid's makes the clock show that masjid's times as
this one's. audit-directory-websites.py finds them on the network; this holds
what was found to the file, so it stays fixed.

    * every website is an http(s) address the clock will actually try
    * one website is not shared by masjids in different places -- unless it is
      an umbrella body with a page for each centre, and then no two centres
      share a page
    * the corrections in directory-overrides.json are well formed, and none of
      the mistakes they correct is in the shipped file
    * the audit notices a page that is somewhere else -- another state, another
      province's postal codes -- however well the name matches
    * make-directory.py applies them: the right address goes in, a cleared
      website leaves the masjid on the map, a school is dropped, a correction
      meant for an old mistake does not overrule a research file that has since
      been fixed, and the file's shape is what the clock reads

No network, and none of the research folder: the last is done on a made-up one.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-directory.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import urllib.parse

from floating_clock import masjids

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DIRECTORY = ROOT / "data" / "masjids.json"

# Umbrella bodies whose one website has a page for each of their centres, so that
# different masjids share a host and still not a page: the ones that have no masjid of
# their own to put on the front page, and the associations that have.
UMBRELLAS = {"ahmadiyya.ca", "centres.macnet.ca"}
ASSOCIATIONS = {"windsorislamicassociation.com", "miaonline.org"}
# One page shared by masjids farther apart than this is not one masjid listed twice, or a
# masjid and the musalla it runs: it is a mistake. (The app itself looks 25 km around.)
SAME_PAGE_KM = 25.0
# Two pages of one site for masjids this close are one masjid listed twice, a page spelt two ways.
SAME_PLACE_KM = 2.0
# MAC's old per-city pages now land on news articles; its centres are on centres.macnet.ca.
DEAD_PAGES = ("macnet.ca/local/",)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def host_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def page_of(url: str) -> str:
    parts = urllib.parse.urlparse(url.lower())
    return host_of(url) + parts.path.rstrip("/") + ("?" + parts.query if parts.query else "")


def label(entry: dict) -> str:
    return "%s (%s)" % (entry.get("name"), entry.get("city"))


data = json.load(io.open(DIRECTORY, encoding="utf-8"))
everyone = data["masjids"]
sites = [e for e in everyone if e.get("website")]
maker = load_script("make-directory")

# --- the shipped file ----------------------------------------------------------------
print("the shipped directory")
check("the file has the shape the clock reads", list(data) == ["source", "masjids"] and everyone,
      str(list(data)))
check("what the clock loads is this file", masjids.directory() == everyone,
      "the copy under data/ that the clock loads differs from the one that was checked")
check("every masjid has a name and a position in Canada",
      all(e.get("name") and 40.0 < float(e.get("latitude", 0)) < 84.0 and -142.0 < float(e.get("longitude", 0)) < -52.0
          for e in everyone),
      "; ".join(label(e) for e in everyone
                if not (e.get("name") and 40.0 < float(e.get("latitude", 0)) < 84.0
                        and -142.0 < float(e.get("longitude", 0)) < -52.0))[:200])
check("sorted by province, city and name, as make-directory writes it",
      everyone == sorted(everyone, key=lambda e: (e.get("province", ""), e.get("city", ""), e["name"])))
seen = {}
for e in everyone:
    seen.setdefault((e["name"].lower(), round(e["latitude"], 4), round(e["longitude"], 4)), []).append(e)
check("no masjid is listed twice at one spot", all(len(v) == 1 for v in seen.values()),
      "; ".join(label(v[0]) for v in seen.values() if len(v) > 1))

print("websites")
bad = []
for e in sites:
    url = e["website"]
    parts = urllib.parse.urlparse(url)
    if (parts.scheme not in ("http", "https") or "." not in parts.netloc or url != url.strip()
            or any(c.isspace() for c in url) or parts.fragment or len(url) > 300):
        bad.append("%s: %s" % (label(e), url))
check("every website is an http(s) address", not bad, "; ".join(bad)[:300])
check("and the clock has an address to try for each", all(masjids.addresses_for(e) for e in sites),
      "; ".join(label(e) for e in sites if not masjids.addresses_for(e))[:300])

by_host: dict = {}
for e in sites:
    by_host.setdefault(host_of(e["website"]), []).append(e)
apart, hosts = [], []
for host, group in sorted(by_host.items()):
    pages: dict = {}
    for e in group:
        pages.setdefault(page_of(e["website"]), []).append(e)
    for page, members in sorted(pages.items()):
        reach = max((masjids.distance_km(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
                     for a in members for b in members), default=0.0)
        if reach > SAME_PAGE_KM:
            apart.append("%s is the page of %s, %.0f km apart" % (page, " and ".join(label(e) for e in members), reach))
    together = all(masjids.distance_km(a["latitude"], a["longitude"], b["latitude"], b["longitude"]) <= SAME_PLACE_KM
                   for a in group for b in group)
    if len(pages) > 1 and host not in UMBRELLAS | ASSOCIATIONS and not together:
        hosts.append("%s: %s" % (host, "; ".join(label(e) for g in pages.values() for e in g)))
check("one page is not the page of masjids in different places", not apart, " | ".join(apart)[:600])
check("one website is not shared by masjids that are not one body's centres or one masjid twice", not hosts, " | ".join(hosts)[:600])
check("no umbrella body's front page stands for a centre",
      all(urllib.parse.urlparse(e["website"]).path.strip("/") for e in sites if host_of(e["website"]) in UMBRELLAS),
      "; ".join(label(e) for e in sites if host_of(e["website"]) in UMBRELLAS
                and not urllib.parse.urlparse(e["website"]).path.strip("/")))
check("no page that has moved away is filed", not any(any(dead in page_of(e["website"]) for dead in DEAD_PAGES) for e in sites),
      "; ".join(label(e) for e in sites if any(dead in page_of(e["website"]) for dead in DEAD_PAGES)))

# --- the corrections -----------------------------------------------------------------------
print("the corrections")
rules = maker.load_overrides()
check("there are corrections, and each is well formed", bool(rules))
keys = [(r["name"], r["city"], r.get("was")) for r in rules]
check("none is written twice", len(keys) == len(set(keys)))
check("each says what it replaces", all("was" in r for r in rules if r["action"] in ("website", "clear")),
      "; ".join(label(r) for r in rules if r["action"] in ("website", "clear") and "was" not in r))
check("a corrected website is not the one it replaces",
      all(r["website"] != r.get("was") for r in rules if r["action"] == "website"))
still = []
for r in rules:
    for e in everyone:
        if e["name"] == r["name"] and e.get("city") == r["city"] and r.get("was") and e.get("website") == r["was"]:
            still.append("%s still has %s" % (label(e), r["was"]))
check("no mistake that was corrected is in the shipped file", not still, "; ".join(still)[:300])

# --- applying them, on a made-up research folder ---------------------------------------------
print("applying them")


def record(name, city, site, kind="mosque", lat=43.7, lon=-79.4):
    return {"name": name, "type": kind, "address": "1 Main St", "city": city, "province": "Ontario",
            "country": "Canada", "latitude": lat, "longitude": lon, "phone": "+1-555-0100",
            "website": site, "verified": True, "description": "words the clock has no use for"}


with tempfile.TemporaryDirectory(prefix="floating-clock-directory-") as folder:
    folder = pathlib.Path(folder)
    (folder / "ontario").mkdir()
    (folder / "_meta").mkdir()
    (folder / "_meta" / "notes.json").write_text('{"mosques": [{"name": "Ignored"}]}', encoding="utf-8")
    (folder / "ontario" / "toronto.json").write_text(json.dumps({"mosques": [
        record("Alpha Masjid", "Toronto", "https://chicago.example"),
        record("Beta Masjid", "Toronto", "https://news.example/2022/story"),
        record("Gamma Boarding School", "Toronto", "https://school.example", lat=43.71),
        record("Delta Masjid", "Toronto", "https://fixed-since.example", lat=43.72),
        record("Twin Masjid", "Toronto", "https://one.example", lat=43.73),
        record("Twin Masjid", "Toronto", "https://two.example", lat=43.74),
        record("Plain Masjid", "Toronto", None, lat=43.75),
        record("Alpha Masjid", "Ottawa", "https://chicago.example", lat=45.4, lon=-75.7),
    ]}), encoding="utf-8")
    fixture_rules = [
        {"name": "Alpha Masjid", "city": "Toronto", "action": "website", "was": "https://chicago.example",
         "website": "https://alpha-toronto.example", "why": "Chicago's"},
        {"name": "Beta Masjid", "city": "Toronto", "action": "clear", "was": "https://news.example/2022/story",
         "why": "a news article"},
        {"name": "Gamma Boarding School", "city": "Toronto", "action": "remove", "was": "https://school.example",
         "why": "a school"},
        {"name": "Delta Masjid", "city": "Toronto", "action": "website", "was": "https://stale-mistake.example",
         "website": "https://delta.example", "why": "the mistake the research has since fixed"},
        {"name": "Twin Masjid", "city": "Toronto", "action": "clear", "was": "https://two.example",
         "why": "only one of two of that name"},
    ]
    real_print, sys.stdout = sys.stdout, io.StringIO()
    try:
        made = maker.distil(folder, fixture_rules)
    finally:
        said, sys.stdout = sys.stdout.getvalue(), real_print
    rows = {(r["name"], r["city"], r.get("website")): r for r in made["masjids"]}
    by_name = {}
    for r in made["masjids"]:
        by_name.setdefault((r["name"], r["city"]), []).append(r)

    check("a corrected website goes in", ("Alpha Masjid", "Toronto", "https://alpha-toronto.example") in rows)
    check("only for the city it was written for", ("Alpha Masjid", "Ottawa", "https://chicago.example") in rows)
    check("a cleared website leaves the masjid on the map", ("Beta Masjid", "Toronto") in by_name
          and "website" not in by_name[("Beta Masjid", "Toronto")][0])
    check("a school is dropped", ("Gamma Boarding School", "Toronto") not in by_name)
    check("a correction for an old mistake does not overrule a fixed source",
          ("Delta Masjid", "Toronto", "https://fixed-since.example") in rows)
    check("and it says so", "Delta Masjid" in said, said)
    check("`was` picks the one of two masjids that share a name",
          sorted((r.get("website") for r in by_name[("Twin Masjid", "Toronto")]), key=lambda w: w or "") == [None, "https://one.example"],
          str(by_name[("Twin Masjid", "Toronto")]))
    check("a masjid with no website is untouched", "website" not in by_name[("Plain Masjid", "Toronto")][0])
    check("the research folder's notes are left alone", "Ignored" not in str(made))
    check("the shape is unchanged: the same keys in the same order",
          all(list(r) == [k for k in maker.FIELDS if k in r] for r in made["masjids"]))
    check("and nothing else is carried", not any(k in r for r in made["masjids"] for k in ("phone", "address", "description")))
    check("sorted as before", made["masjids"] == sorted(made["masjids"], key=lambda r: (r["province"], r["city"], r["name"])))

    bad_rules = {
        "no city": {"name": "X", "action": "clear", "why": "w"},
        "an action nobody knows": {"name": "X", "city": "Y", "action": "delete", "why": "w"},
        "a website that is not an address": {"name": "X", "city": "Y", "action": "website", "website": "example.com", "why": "w"},
        "no reason": {"name": "X", "city": "Y", "action": "clear"},
    }
    for what, rule in bad_rules.items():
        path = folder / "bad.json"
        path.write_text(json.dumps({"overrides": [rule]}), encoding="utf-8")
        try:
            maker.load_overrides(path)
            check("refuses a correction with " + what, False, "accepted")
        except ValueError:
            check("refuses a correction with " + what, True)
    check("no corrections file is no corrections", maker.load_overrides(folder / "absent.json") == [])

# --- the audit: a page that is somewhere else ----------------------------------------------------
print("the audit notices a page that is somewhere else")
audit = load_script("audit-directory-websites")


def judged(entry: dict, html: str, research: dict | None = None, url: str = "https://masjid.example/") -> dict:
    got = {"final": url, "status": 200, "insecure": False, "html": html, "error": "", "type": "text/html", "alt": None}
    key = (entry["name"].strip().lower(), round(entry["latitude"], 4), round(entry["longitude"], 4))
    return audit.judge(dict(entry, website=url), {key: research} if research else {}, got, {audit.fold(entry["city"])})


def site(title: str, body: str) -> str:
    return "<html><head><title>%s</title></head><body><h1>%s</h1><p>%s</p></body></html>" % (title, title, body)


RICHMOND_HILL = {"name": "Richmond Hill Islamic Centre", "city": "Richmond Hill", "province": "Ontario",
                 "latitude": 43.85, "longitude": -79.43}
FAROOQ = {"name": "Masjid Al Farooq", "city": "Mississauga", "province": "Ontario", "latitude": 43.6, "longitude": -79.7}

new_york = judged(RICHMOND_HILL, site("Richmond Hill Islamic Centre",
                                      "Address: 125-09 Jamaica Avenue, Richmond Hill, NY 11418. Phone: (718) 847-0000"))
check("a masjid's own town in another state is not its place, however well the name matches",
      new_york["verdict"] == "WRONG PLACE?" and any("'Richmond Hill, NY'" in why for why in new_york["why"]), str(new_york))
right = judged(RICHMOND_HILL, site("Richmond Hill Islamic Centre", "Address: 1 Fieldstone Dr, Richmond Hill, ON L4S 2A4."))
check("and the same town in the right province is", right["verdict"] == "OK", "%s %s" % (right["verdict"], right["why"]))

edmonton = judged(FAROOQ, site("Masjid Al Farooq", "Serving Mississauga and beyond. Our address: 345 Woodvale Rd W, Edmonton, AB T6L 3Z7."))
check("another province's postal code, on a page that names the town in passing, is not this masjid's",
      edmonton["verdict"] == "WRONG PLACE?" and any("postal code of Alberta (T6L 3Z7)" in why for why in edmonton["why"]), str(edmonton))
check("and says whose it looks like", any("this masjid is in Ontario" in why for why in edmonton["why"]))

branch = judged(RICHMOND_HILL, site("Richmond Hill Islamic Centre",
                                    "1 Fieldstone Dr, Richmond Hill, ON L4S 2A4. Our sister masjid: 12 First St, Calgary, AB T2P 1J9."),
                research={"address": "1 Fieldstone Dr, Richmond Hill, ON L4S 2A4"})
check("but a page that prints the masjid's own address as well is a sister masjid, for a person to look at",
      branch["verdict"] == "CHECK" and any("Alberta" in why for why in branch["why"]), "%s %s" % (branch["verdict"], branch["why"]))

alberta = judged({"name": "Markaz Ul Islam", "city": "Edmonton", "province": "Alberta", "latitude": 53.5, "longitude": -113.5},
                 site("Markaz Ul Islam", "Serving Edmonton. 5315 Rue Lessard NW, Edmonton, AB T6S 1A7."))
check("a masjid in Alberta printing Alberta's postal code is not accused", alberta["verdict"] == "OK", "%s %s" % (alberta["verdict"], alberta["why"]))

check("what a page says is read as it is written: 'ny' in ordinary text is not a state",
      audit.elsewhere(RICHMOND_HILL, "Richmond Hill, ny is where it began, they say") == [])
check("nor is a postal-code-shaped word from another country", audit.elsewhere(RICHMOND_HILL, "Zip 11418 and W1A 1AA") == [])
check("a masjid whose province is not known is not accused of anything", audit.elsewhere({"city": "Richmond Hill"}, "Richmond Hill, NY T6L 3Z7") == [])
check("every first letter of a postal code that Canada uses belongs to a province",
      set(audit.POSTAL_PROVINCE) == set("ABCEGHJKLMNPRSTVXY") and set(audit.POSTAL_PROVINCE.values()) >= {"Ontario", "Alberta", "Quebec"})
check("and every province the directory holds has a code to compare with",
      all(audit.PROVINCE_CODE.get(m.get("province")) for m in json.load(io.open(DIRECTORY, encoding="utf-8"))["masjids"]),
      str({m.get("province") for m in json.load(io.open(DIRECTORY, encoding="utf-8"))["masjids"] if not audit.PROVINCE_CODE.get(m.get("province"))}))

print()
if failures:
    print("%d directory check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all directory checks passed")
