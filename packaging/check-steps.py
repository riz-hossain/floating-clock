"""Offline checks that the real reading code reports what it is doing.

The timeline (check-timeline.py) is only worth having if what it shows is what the
clock is really doing. These drive the actual code -- the page reader, the way a
masjid's site is tried, the check made on what comes back, and the choice of what to
offer -- on made-up networks, and read the timeline afterwards:

    * each step of reading a site: reach, feed, embed, calendar, home, pages, browser, check
    * the ones that are not needed are put away, and the ones not possible say why
    * an embedded widget is read without writing over the site's own steps
    * a masjid's own sources, then its neighbours, one line each
    * Stop, pressed in the middle of a browser, stops there and is not mistaken for a bad page
    * the answer is the same with a timeline watching and without one

No network and no browser, so it cannot flake.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-steps.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
from datetime import date, datetime, timedelta

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import astro, dpt, ics, masjids, mawaqit, prayer, scrape, timeline  # noqa: E402
from floating_clock.timeline import DONE, FAILED, PENDING, SKIPPED, Cancelled, Section, Timeline  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


TODAY = date(2026, 9, 20)
WATERLOO = (43.4643, -80.5204, -4)
HOME = "https://masjid.example/"


class Reply:
    def __init__(self, url: str, body: str) -> None:
        self._url, self._body = url, body.encode("utf-8")
        self.headers = self

    def get_content_type(self):
        return "text/html"

    def get_content_charset(self):
        return "utf-8"

    def read(self, size: int = -1):
        return self._body if size is None or size < 0 else self._body[:size]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def network(pages: dict):
    def opener(request, timeout=None):
        url = request.full_url
        if url in pages:
            return Reply(url, pages[url])
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)
    return opener


def page(body: str) -> str:
    return "<html><head><title>A Masjid</title></head><body>%s</body></html>" % body


BOARD = "".join("<p>%s Iqama %s</p>" % pair for pair in (
    ("Fajr", "6:15"), ("Dhuhr", "1:45"), ("Asr", "5:45"), ("Maghrib", "7:28"), ("Isha", "9:00")))


class Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


WEBSITE = Section("site", "The masjid's own website", "masjid.example", timeline.WEBSITE)


def watched(work, sections=None, open_="site"):
    """Run `work()` as the masjid's own website is read, and hand back what the timeline shows.

    (timeline, what work returned, what it raised). Finished with the website left
    unfolded so that its steps can be read.
    """
    line = Timeline(Clock())
    line.plan("Test Masjid", sections or [WEBSITE])
    result = error = None
    with timeline.using(line):
        try:
            with line.section("site") as source:
                result = work()
                source.ok("read")
        except BaseException as exc:                 # noqa: BLE001
            error = exc
    line.finish("found" if error is None else "none", "", open=open_)
    return line, result, error


def rows(line: Timeline) -> dict:
    return {r["key"]: (r["state"], r["detail"]) for r in line.snapshot()["rows"]}


# --- reading the page itself -----------------------------------------------------------------
print("reading a site's pages")
line, got, err = watched(lambda: scrape.fetch(HOME, TODAY, network({HOME: page(BOARD)}), where=WATERLOO))
r = rows(line)
check("times on the home page are read from it", err is None and r["site.home"][0] == DONE
      and "found the times" in r["site.home"][1], str(r))
check("so no further page is read", r["site.pages"][0] == SKIPPED and r["site.browser"][0] == SKIPPED, str(r))

site = {HOME: page('<a href="/prayer-times/">Prayer Times</a><p>Welcome</p>'), HOME + "prayer-times/": page(BOARD)}
midway: list = []


def looking(pages, line_holder):
    """A network that notes what the timeline says while the sub-page is being fetched."""
    inner = network(pages)

    def opener(request, timeout=None):
        if request.full_url.endswith("prayer-times/") and line_holder:
            midway.append(rows(line_holder[0]).get("site.pages"))
        return inner(request, timeout)
    return opener


holder: list = []
line = Timeline(Clock())
line.plan("Test Masjid", [WEBSITE])
holder.append(line)
with timeline.using(line):
    with line.section("site") as source:
        scrape.fetch(HOME, TODAY, looking(site, holder), where=WATERLOO)
        source.ok("read")
line.finish("found", "", open="site")
r = rows(line)
check("while a page is being read the step says which one", midway and midway[0][0] == "running"
      and "/prayer-times" in midway[0][1], str(midway))
check("a home page with none says so", r["site.home"] == (DONE, "no times on it"), str(r["site.home"]))
check("the page it links to is read, and named", r["site.pages"][0] == DONE
      and "/prayer-times" in r["site.pages"][1], str(r["site.pages"]))
check("no browser is needed for a page that reads without one", r["site.browser"][0] == SKIPPED, str(r["site.browser"]))

site = {HOME: page('<a href="/prayer-times/">Prayer Times</a>'), HOME + "prayer-times/": page("<p>Welcome</p>")}
line, got, err = watched(lambda: scrape.fetch(HOME, TODAY, network(site), where=WATERLOO))
r = rows(line)
check("a page that is read and has nothing says how many had nothing",
      r["site.pages"][0] == DONE and "none of its 1 page" in r["site.pages"][1], str(r["site.pages"]))
check("and with no browser to try, that is said too, and why",
      r["site.browser"][0] == SKIPPED and "no browser" in r["site.browser"][1], str(r["site.browser"]))
check("the read fails as it always did", isinstance(err, scrape.NoTimes), repr(err))

script = page('<div id="t"></div><script>document.getElementById("t").innerHTML="..."</script>')
asked: list = []


def renderer(url: str):
    asked.append(url)
    return url, page(BOARD)


line, got, err = watched(lambda: scrape.fetch(HOME, TODAY, network({HOME: script}), where=WATERLOO, render=renderer))
r = rows(line)
check("nothing to follow on the home page is said, not skipped in silence",
      r["site.pages"][0] == SKIPPED and "links to a prayer-times page" in r["site.pages"][1], str(r["site.pages"]))
check("a script-drawn page is read in a browser, and says where the times were",
      r["site.browser"][0] == DONE and "found the times" in r["site.browser"][1], str(r["site.browser"]))

line, got, err = watched(lambda: scrape.fetch("https://gone.example/", TODAY, network({}), where=WATERLOO))
r = rows(line)
check("a site that cannot be opened fails its first step, with the reason",
      r["site.home"][0] == FAILED and "could not open" in r["site.home"][1], str(r["site.home"]))
check("and what could not be reached is not claimed to have been read", r["site.pages"][0] in (SKIPPED, PENDING), str(r["site.pages"]))

# Stop, pressed while the browser is working, is not a page that did not read
line = Timeline(Clock())
line.plan("Test Masjid", [WEBSITE])


def stopping_renderer(url: str):
    line.cancel()
    return url, page('<a href="/prayer-times/">Prayer Times</a>')      # a page to go on to, and so a step to stop at


stopped = None
with timeline.using(line):
    try:
        with line.section("site"):
            scrape.fetch(HOME, TODAY, network({HOME: script, HOME + "prayer-times/": page(BOARD)}), where=WATERLOO,
                         render=stopping_renderer)
    except Cancelled:
        stopped = True
    except Exception as exc:                          # noqa: BLE001
        stopped = "as an ordinary failure: %r" % exc
check("Stop in the middle of a browser stops the read, and is not swallowed as a bad page", stopped is True, str(stopped))

# --- the check made on what comes back ------------------------------------------------------
print("checking what came back")
text = scrape.fetch(HOME, TODAY, network({HOME: page(BOARD)}), where=WATERLOO)
tmp = tempfile.mkdtemp(prefix="floating-clock-check-")
line, got, err = watched(lambda: prayer.load(tmp, HOME, now=datetime(2026, 9, 20, 12), force=True, fetcher=lambda u: text))
r = rows(line)
check("times that pass are said to have passed, and how", r["site.check"][0] == DONE
      and "plausible" in r["site.check"][1], str(r["site.check"]))
check("and a reading that was checked against the sun says so", not scrape.sun_checked(text) or "sunrise" in r["site.check"][1],
      str(r["site.check"]))
bad = json.dumps({"source": "scrape", "name": "x", "page": "p", "asof": "2026-09-20", "how": "labelled", "quality": 5,
                  "computed": False, "sun_checked": False,
                  "iqamah": {"Fajr": "13:00", "Dhuhr": "13:45", "Asr": "17:45", "Maghrib": "19:28", "Isha": "05:00"}})
line, got, err = watched(lambda: prayer.load(tmp, HOME, now=datetime(2026, 9, 20, 12), force=True, fetcher=lambda u: bad))
r = rows(line)
check("times that are impossible fail it, with the reason", r["site.check"][0] == FAILED
      and "do not look like prayer times" in r["site.check"][1], str(r["site.check"]))
one = json.dumps(dict(json.loads(bad), iqamah={"Fajr": "13:00", "Dhuhr": "13:45", "Asr": "17:45",
                                              "Maghrib": "19:28", "Isha": "21:00"}))
line, got, err = watched(lambda: prayer.load(tmp, HOME, now=datetime(2026, 9, 20, 12), force=True, fetcher=lambda u: one))
r = rows(line)
check("one bad time among good ones is said, not passed over as a clean pass",
      r["site.check"][0] == DONE and "do not look like prayer times" in r["site.check"][1], str(r["site.check"]))

# --- how a site is tried ------------------------------------------------------------------------
print("how a site is tried")
real = (dpt.resolve, dpt.fetch, ics.fetch, scrape.fetch, prayer._embedded_page, prayer._renderer, mawaqit.fetch)


def restore() -> None:
    dpt.resolve, dpt.fetch, ics.fetch, scrape.fetch, prayer._embedded_page, prayer._renderer, mawaqit.fetch = real


def no_plugin(home, resolved=False):
    raise dpt.NoApi("no plugin here")


def not_a_calendar(address):
    raise RuntimeError("not a calendar")


dpt.resolve = lambda address, strict=False: address
dpt.fetch = no_plugin
ics.fetch = not_a_calendar
prayer._embedded_page = lambda home: ""
prayer._renderer = lambda: None
scrape.fetch = lambda site_url, **kw: "READ FROM THE PAGE"

fetch = prayer._fetcher_for(HOME)
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("a plain site goes through every step, in order, and is read off its page",
      got == "READ FROM THE PAGE" and all(r["site." + k][0] == DONE for k in ("reach", "feed", "embed", "calendar")), str(r))
check("the address it reached is named", r["site.reach"][1] == "reached masjid.example", str(r["site.reach"]))
check("each says what it found: nothing", "no timetable plug-in" in r["site.feed"][1] and r["site.embed"][1] == "none found",
      str((r["site.feed"], r["site.embed"])))

dpt.fetch = lambda home, resolved=False: "FROM THE PLUGIN"
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("a site with a timetable plug-in stops there", got == "FROM THE PLUGIN" and r["site.feed"][0] == DONE
      and "plug-in" in r["site.feed"][1], str(r["site.feed"]))
check("and the steps after it are put away as not needed",
      all(r["site." + k][0] == SKIPPED for k in ("embed", "calendar", "home", "pages", "browser")), str(r))

dpt.fetch = lambda home, resolved=False: (_ for _ in ()).throw(dpt.DptError("its times stop at last year"))
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("a plug-in that is there but no use is said so at its own step", r["site.feed"][0] == DONE
      and "not a usable one" in r["site.feed"][1], str(r["site.feed"]))
check("and the search goes on past it: the embed, the calendar and the page are all tried",
      all(r["site." + k][0] == DONE for k in ("embed", "calendar")), str(r))
check("and the plug-in's complaint is what is raised, since a page read beside a stale plug-in is not trusted",
      isinstance(err, dpt.DptError) and "last year" in str(err), repr(err))
dpt.fetch = no_plugin

prayer._embedded_page = lambda home: "https://mawaqit.net/en/some-masjid-name"
mawaqit.fetch = lambda url: "FROM MAWAQIT"
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("an embedded mawaqit.net page is found, read, and named", got == "FROM MAWAQIT" and r["site.embed"][0] == DONE
      and "mawaqit.net" in r["site.embed"][1] and "read the times" in r["site.embed"][1], str(r["site.embed"]))
check("and the calendar and the page are not read after it", r["site.calendar"][0] == SKIPPED and r["site.home"][0] == SKIPPED)

def broken_mawaqit(url):
    raise RuntimeError("timed out")


mawaqit.fetch = broken_mawaqit
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("an embedded page that does not read is said not to, and the site's own page is tried",
      "did not read" in r["site.embed"][1] and got == "READ FROM THE PAGE", str((r["site.embed"], got)))

# a widget read with the same reader that reads the site must not write over the site's steps
prayer._embedded_page = lambda home: "https://widgets.example/prayer"


def widget_read(site_url, **kw):
    inner = timeline.current()
    inner.start("home", "the widget's page")
    inner.done("home", "found the times on the widget")
    return "FROM THE WIDGET"


scrape.fetch = widget_read
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("the site's own home-page step is not overwritten by the widget's read",
      got == "FROM THE WIDGET" and "widget" not in r["site.home"][1], str(r["site.home"]))


def widget_then_stop(site_url, **kw):
    timeline.current().check()          # what the reader does between pages
    raise AssertionError("Stop was not seen")


prayer._embedded_page = lambda home: "https://widgets.example/prayer"
scrape.fetch = widget_then_stop
line = Timeline(Clock())
line.plan("Test Masjid", [WEBSITE])
stopped = None
with timeline.using(line):
    try:
        with line.section("site"):
            line.cancel()
            fetch_stopped = prayer._fetcher_for(HOME)
            timeline.current()._cancelled.clear()          # reach the widget first, then stop inside it
            real_hushed = timeline.hushed

            def stop_inside():
                line.cancel()
                return real_hushed()

            timeline.hushed = stop_inside
            try:
                fetch_stopped(HOME)
            finally:
                timeline.hushed = real_hushed
    except Cancelled:
        stopped = True
    except BaseException as exc:                              # noqa: BLE001
        stopped = "swallowed or changed: %r" % exc
check("a Stop that arrives inside an embedded widget's read is not taken for a widget that failed to read",
      stopped is True, str(stopped))
line.finish("stopped", "", open="site")
r = rows(line)
check("it is the step that was running that says it was stopped, and not one that carried on past it",
      r["site.embed"] == (FAILED, "stopped"), str(r["site.embed"]))
scrape.fetch = lambda site_url, **kw: "READ FROM THE PAGE"
prayer._embedded_page = lambda home: ""

dpt.resolve = lambda address, strict=False: (_ for _ in ()).throw(dpt.NoApi("that address could not be reached"))
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("a site that cannot be reached fails at the first step, with the reason",
      isinstance(err, dpt.NoApi) and r["site.reach"][0] == FAILED and "could not be reached" in r["site.reach"][1], str(r))
check("and nothing after it is claimed", all(r["site." + k][0] in (SKIPPED, PENDING) for k in ("feed", "embed", "calendar")), str(r))

dpt.resolve = lambda address, strict=False: "https://mawaqit.net/en/masjid-abc-123"
mawaqit.fetch = lambda url: "FROM MAWAQIT"
line, got, err = watched(lambda: fetch(HOME))
r = rows(line)
check("a domain that turns out to be a mawaqit.net page is read as one", got == "FROM MAWAQIT"
      and "mawaqit.net page" in r["site.feed"][1], str(r["site.feed"]))
dpt.resolve = lambda address, strict=False: address

mawaqit_line, got, err = watched(lambda: prayer._fetcher_for(mawaqit.page_url("some-masjid-abc"))(mawaqit.page_url("some-masjid-abc")),
                                 sections=[Section("site", "mawaqit.net", "", timeline.MAWAQIT)])
r = rows(mawaqit_line)
check("a mawaqit.net listing is one step: asking for it", got == "FROM MAWAQIT" and r["site.ask"][0] == DONE, str(r))
restore()

# --- what to offer, and from whom ---------------------------------------------------------------
print("what to offer")
real_inspect = masjids.inspect
real_offset = astro.local_offset_hours
astro.local_offset_hours = lambda when=None: -4.0


def prayers_for():
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return [prayer.Prayer(n, day + timedelta(hours=h, minutes=m))
            for n, (h, m) in zip(prayer.DAILY, ((6, 15), (13, 45), (17, 45), (19, 28), (21, 0)))]


def reading(source="", how="", exact=True):
    ps = prayers_for()
    return {"prayers": ps, "status": "ok", "source": source, "how": how, "exact": exact,
            "times": masjids._day_times(ps)}


NOTHING = {"prayers": [], "status": "Could not read the prayer times: its timetable is posted as an image", "source": "",
           "how": "", "exact": False, "times": []}
outcomes: dict = {}
masjids.inspect = lambda address, where=None: outcomes.get(address, NOTHING)

target = {"name": "Erin Centre", "latitude": 43.77, "longitude": -80.06, "website": "http://erin.example",
          "slug": "erin-centre-abc", "iqama": True}
near = {"name": "Near Masjid", "latitude": 43.80, "longitude": -80.06, "website": "http://near.example"}
nearer = {"name": "Nearer Masjid", "latitude": 43.78, "longitude": -80.06, "website": "http://nearer.example"}
rows_ = [target, near, nearer]
MAWAQIT = mawaqit.page_url("erin-centre-abc")
SITE = "http://erin.example"


def timeline_of(entry, table, **kw):
    global outcomes
    outcomes = table
    line = Timeline(Clock())
    found = masjids.propose(entry, rows_, trail=line, **kw)
    return line, found


line, found = timeline_of(target, {MAWAQIT: reading("mawaqit"), SITE: reading("scrape", "labelled", exact=False)})
snap = line.snapshot()
keys = [r["key"] for r in snap["rows"]]
check("every source is laid out before it is tried, then the neighbours", "mawaqit" in keys and "site" in keys and "nearby" in keys, str(keys))
check("the masjid's name heads it", snap["title"] == "Erin Centre")
by = {r["key"]: r for r in snap["rows"]}
check("a mawaqit.net listing that has the times is a found source", by["mawaqit"]["state"] == DONE, str(by["mawaqit"]))
check("and it is then compared with the masjid's own website", by["site"]["state"] == DONE
      and "agrees with mawaqit.net" in by["site"]["detail"], str(by["site"]))
check("the neighbours are put away, as they were not needed", by["nearby"]["state"] == SKIPPED, str(by["nearby"]))
check("the check ends found, and the bar is full", snap["outcome"] == "found" and snap["fraction"] == 1.0 and not snap["busy"])
check("the source that supplied the times stays open to read", any(k.startswith("mawaqit.") for k in keys), str(keys))
check("and what it offers is what it always offered", found["kind"] == "exact" and found["address"] == MAWAQIT, str(found["kind"]))

line, found = timeline_of(target, {MAWAQIT: reading("mawaqit"), SITE: reading("scrape", "labelled", exact=False)})
outcomes[SITE] = dict(reading("scrape", "labelled", exact=False), times=[("Fajr", "05:00")] + masjids._day_times(prayers_for())[1:])
line, found = timeline_of(target, outcomes)
by = {r["key"]: r for r in line.snapshot()["rows"]}
check("a website that disagrees says where, and is what is offered when it is sure",
      found["kind"] == "read" and "Fajr" in by["site"]["detail"] and "page is what is offered" in by["site"]["detail"], str(by["site"]))
check("and then it is the website that stays open, since it supplied the answer",
      any(r["key"].startswith("site.") for r in line.snapshot()["rows"]))

line, found = timeline_of(target, {MAWAQIT: NOTHING, SITE: reading("scrape", "labelled", exact=False)})
by = {r["key"]: r for r in line.snapshot()["rows"]}
check("a listing with nothing is a failed source, with the reason", by["mawaqit"]["state"] == FAILED
      and "posted as an image" in by["mawaqit"]["detail"], str(by["mawaqit"]))
check("and the masjid's own website is then read as a source of its own", by["site"]["state"] == DONE
      and "read the times off the page" in by["site"]["detail"], str(by["site"]))

line, found = timeline_of(target, {NOTHING["status"]: NOTHING, "http://nearer.example": reading("mawaqit")})
by = {r["key"]: r for r in line.snapshot()["rows"]}
check("with nothing of its own, the neighbours are tried, nearest first", found["kind"] == "proxy", str(found["kind"]))
check("a neighbour that has times is a found line", by["nearby.n0"]["state"] == DONE, str({k: v["state"] for k, v in by.items()}))
check("and the nearby group says whose times are used", "Nearer Masjid" in by["nearby"]["detail"], str(by["nearby"]))
check("the second neighbour was never needed", by["nearby.n1"]["state"] == SKIPPED)

line, found = timeline_of(target, {})
by = {r["key"]: r for r in line.snapshot()["rows"]}
check("when nothing has times, every source and every neighbour failed and says why", found["kind"] == "none"
      and by["mawaqit"]["state"] == FAILED and by["site"]["state"] == FAILED and by["nearby"]["state"] == FAILED, str({k: v["state"] for k, v in by.items()}))
check("each neighbour that was tried is listed with why", by["nearby.n0"]["state"] == FAILED
      and "posted as an image" in by["nearby.n0"]["detail"], str(by.get("nearby.n0")))
check("the check ends with nothing, and says so", line.snapshot()["outcome"] == "none" and "Nor did the masjids" in line.snapshot()["summary"],
      line.snapshot()["summary"])

bare = {"name": "Bare Masjid", "latitude": 43.771, "longitude": -80.06}
line, found = timeline_of(bare, {"http://nearer.example": reading("mawaqit")})
keys = [r["key"] for r in line.snapshot()["rows"]]
check("a masjid with no address of its own goes straight to its neighbours", found["kind"] == "proxy"
      and "mawaqit" not in keys and "site" not in keys, str(keys))

line, found = timeline_of({"name": "Calgary Centre", "latitude": 51.05, "longitude": -114.07}, {})
check("a masjid in another time zone is refused with nothing to show, and the check ends", found["kind"] == "none"
      and line.snapshot()["rows"] == [] and line.snapshot()["outcome"] == "none", str(line.snapshot()["rows"]))

# Stop
print("Stop")
line = Timeline(Clock())


def stops(address, where=None):
    line.cancel()
    timeline.current().check()
    return NOTHING


masjids.inspect = stops
found = masjids.propose(target, rows_, trail=line)
check("pressing Stop ends the check where it is, with nothing offered", found["kind"] == "none" and found["status"] == "Stopped.", str(found))
check("and the timeline says it was stopped", line.snapshot()["outcome"] == "stopped", str(line.snapshot()["outcome"]))
check("and nothing is left spinning", all(r["state"] != "running" for r in line.snapshot()["rows"]), str(line.snapshot()["rows"]))

def raises(address, where=None):
    raise RuntimeError("the disk is full")


masjids.inspect = raises
line = Timeline(Clock())
try:
    masjids.propose(target, rows_, trail=line)
    passed_on = False
except RuntimeError:
    passed_on = True
check("a failure that is not Stop still reaches the caller, and the timeline is closed", passed_on
      and line.snapshot()["outcome"] == "none" and "disk is full" in line.snapshot()["summary"], line.snapshot()["summary"])

# the answer does not depend on being watched
masjids.inspect = lambda address, where=None: outcomes.get(address, NOTHING)
outcomes = {MAWAQIT: reading("mawaqit")}
watched_answer = masjids.propose(target, rows_, trail=Timeline(Clock()))
plain_answer = masjids.propose(target, rows_)
check("what is offered is the same with a timeline watching and without one", watched_answer == plain_answer,
      "%s vs %s" % (watched_answer, plain_answer))

masjids.inspect = real_inspect
astro.local_offset_hours = real_offset

print()
if failures:
    print("%d step check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all step checks passed")
