"""Offline checks for the masjid readers: no network, so safe for CI.

Each check pins down something a test against real masjids turned up, and
that every earlier check had let through:

  * a vanity domain that redirects to the platform hosting the masjid
  * a plugin whose "iqama" columns are only its start times (Port Elgin)
  * a plugin sending impossible times (a misconfigured site in London)
  * a MAWAQIT masjid that switched congregation times off
  * MAWAQIT's iqama offsets, which belong to a different table's indices
  * a masjid site embedding a MAWAQIT widget

    PYTHONPATH=<folder holding floating_clock> python packaging/check-readers.py
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from floating_clock import dpt, masjids, mawaqit, prayer, prayersconnect

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


# --- a stand-in for the network ---------------------------------------------
class Reply:
    def __init__(self, url: str, body) -> None:
        self._url = url
        self._body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.status = 200
        self.headers = self

    def get_content_charset(self):
        return "utf-8"

    def read(self, size: int = -1):
        return self._body if size is None or size < 0 else self._body[:size]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def network(routes: dict):
    """An opener answering from `routes`: url -> (final url, body).

    Anything else is a 404, like a site with no such route.
    """
    def opener(request, timeout=None, **_kw):
        url = getattr(request, "full_url", request)
        if url in routes:
            final, body = routes[url]
            return Reply(final, body)
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)
    return opener


def plugin_row(day, begins, jamahs) -> dict:
    return {"d_date": day.strftime("%Y-%m-%d"),
            "fajr_begins": begins[0], "fajr_jamah": jamahs[0],
            "zuhr_begins": begins[1], "zuhr_jamah": jamahs[1],
            "asr_mithl_1": begins[2], "asr_mithl_2": begins[2], "asr_jamah": jamahs[2],
            "maghrib_begins": begins[3], "maghrib_jamah": jamahs[3],
            "isha_begins": begins[4], "isha_jamah": jamahs[4]}


def a_year(begins, jamahs) -> list[dict]:
    first = datetime(datetime.now().year, 1, 1)
    return [plugin_row(first + timedelta(days=i), begins, jamahs) for i in range(365)]


BEGINS = ("05:31:00", "13:30:00", "17:39:00", "19:31:00", "20:51:00")
REAL = ("06:15:00", "13:45:00", "18:00:00", "19:32:00", "21:00:00")

# ---------------------------------------------------------------------------
print("vanity domains")
TODAY = "/wp-json/dpt/v1/prayertime?filter=today"
YEAR = "/wp-json/dpt/v1/prayertime?filter=year"
row = plugin_row(datetime.now(), BEGINS, REAL)
routes = {
    "http://kitchener.test": ("https://platform.test/kitchener/", "<html></html>"),
    "https://platform.test/kitchener" + TODAY:
        ("https://platform.test/kitchener" + TODAY, json.dumps([row])),
    "https://platform.test/kitchener" + YEAR:
        ("https://platform.test/kitchener" + YEAR, json.dumps([a_year(BEGINS, REAL)])),
}
opener = network(routes)
check("resolve follows the redirect to the real home",
      dpt.resolve("http://kitchener.test", opener=opener) == "https://platform.test/kitchener/")
check("discover finds the API on the platform, not the vanity domain",
      dpt.discover("http://kitchener.test", opener=opener) == "https://platform.test/kitchener")
text = dpt.fetch("http://kitchener.test", opener=opener)
now = datetime.now()
got = dpt.parse(text, now - timedelta(hours=18), now + timedelta(days=3))
check("and a year of times comes back through it", len(got) >= 10, "got %d" % len(got))

# a path on a multisite must never fall back to the bare domain
routes = {
    "https://platform.test/ccv/": ("https://platform.test/ccv/fr/accueil/", "<html></html>"),
    "https://platform.test" + TODAY: ("https://platform.test" + TODAY, json.dumps([row])),
}
check("a centre with no API is not handed the network root's times",
      dpt.discover("https://platform.test/ccv/", opener=network(routes)) == "")

# ---------------------------------------------------------------------------
print("times that are not iqama")
check("start times posing as iqama are recognised (Port Elgin)",
      dpt.congregation_unset(a_year(BEGINS, BEGINS)))
check("a real timetable is not",
      not dpt.congregation_unset(a_year(BEGINS, REAL)))
maghrib_only = ("06:15:00", "13:45:00", "18:00:00", BEGINS[3], "21:00:00")
check("Maghrib at its start time alone is ordinary",
      not dpt.congregation_unset(a_year(BEGINS, maghrib_only)))

routes = {
    "https://unset.test" + TODAY: ("https://unset.test" + TODAY, json.dumps([row])),
    "https://unset.test" + YEAR: ("https://unset.test" + YEAR,
                                  json.dumps([a_year(BEGINS, BEGINS)])),
}
try:
    dpt.fetch("https://unset.test", opener=network(routes), resolved=True)
    check("fetch refuses a masjid with no congregation times", False, "it did not")
except dpt.DptError as exc:
    check("fetch refuses a masjid with no congregation times", "start times" in str(exc), str(exc))


def times(*pairs):
    return [prayer.Prayer(n, datetime(2026, 9, d, h, m)) for n, d, h, m in pairs]


london = times(("Fajr", 19, 0, 57), ("Dhuhr", 19, 8, 9), ("Asr", 19, 11, 11),
               ("Maghrib", 19, 14, 12), ("Isha", 19, 15, 17),
               ("Fajr", 20, 0, 57), ("Dhuhr", 20, 8, 9), ("Asr", 20, 11, 11),
               ("Maghrib", 20, 14, 12), ("Isha", 20, 15, 17),
               ("Jumuah", 25, 13, 30))
kept, complaint = prayer.screen(london)
check("impossible times are refused whole, not trimmed to a lone Jumuah",
      kept == [] and "do not look like prayer times" in complaint, "kept %d" % len(kept))

good = times(("Fajr", 19, 6, 15), ("Dhuhr", 19, 13, 45), ("Asr", 19, 18, 0),
             ("Maghrib", 19, 19, 30), ("Isha", 19, 21, 0))
check("a normal day passes untouched", prayer.screen(good) == (good, ""))
june = times(("Fajr", 21, 3, 40), ("Dhuhr", 21, 13, 45), ("Asr", 21, 18, 30),
             ("Maghrib", 21, 21, 35), ("Isha", 21, 23, 30))
check("a June day in the far north passes", prayer.screen(june)[0] == june)
december = times(("Fajr", 21, 6, 45), ("Dhuhr", 21, 12, 15), ("Asr", 21, 14, 40),
                 ("Maghrib", 21, 16, 50), ("Isha", 21, 18, 20))
check("a December day passes", prayer.screen(december)[0] == december)
shuffled = times(("Fajr", 19, 6, 15), ("Dhuhr", 19, 13, 45), ("Asr", 19, 12, 30),
                 ("Maghrib", 19, 19, 30), ("Isha", 19, 21, 0))
check("a day whose prayers run out of order is dropped",
      prayer.screen(shuffled)[0] == [] or len(prayer.screen(shuffled)[0]) < 5)

# ---------------------------------------------------------------------------
print("mawaqit")


def month_table(entries):
    return [{str(d): entries for d in range(1, 32)} for _ in range(12)]


ADHAN = ["06:19", "07:32", "13:49", "17:09", "19:58", "21:43"]        # six, with shuruq


def page(conf: dict) -> str:
    return "<html><script>let confData = %s;</script></html>" % json.dumps(conf)


def conf(iqama, **extra) -> dict:
    base = {"name": "Test Masjid", "calendar": month_table(ADHAN),
            "iqamaCalendar": month_table(iqama), "iqamaEnabled": True, "jumua": "13:30"}
    base.update(extra)
    return base


slug = "test-masjid-paris-france"
url = mawaqit.page_url(slug)


def fetch_mawaqit(c):
    return mawaqit.fetch(slug, opener=network({url: (url, page(c))}))


cached = fetch_mawaqit(conf(["+10", "+10", "+10", "+0", "+10"]))
monday = datetime(2026, 9, 21)
rows = dict(mawaqit.parse(cached, monday, monday + timedelta(hours=23)))
expected = {"Fajr": "06:29", "Dhuhr": "13:59", "Asr": "17:19", "Maghrib": "19:58", "Isha": "21:53"}
got = {n: w.strftime("%H:%M") for n, w in rows.items()}
check("offsets apply to their own prayer's adhan, across the shuruq gap",
      got == expected, "%s != %s" % (got, expected))

absolute = fetch_mawaqit(conf(["06:15", "13:45", "18:00", "19:32", "21:00"]))
got = {n: w.strftime("%H:%M") for n, w in mawaqit.parse(absolute, monday, monday + timedelta(hours=23))}
check("absolute times are taken as they are",
      got == {"Fajr": "06:15", "Dhuhr": "13:45", "Asr": "18:00", "Maghrib": "19:32", "Isha": "21:00"},
      str(got))

for label, c in (("congregation times switched off", conf(["+10"] * 5, iqamaEnabled=False)),
                 ("an iqama table that only says +0", conf(["+0"] * 5))):
    try:
        fetch_mawaqit(c)
        check("refuses " + label, False, "it did not")
    except mawaqit.MawaqitError as exc:
        check("refuses " + label, "nothing for the clock to follow" in str(exc), str(exc))

check("Maghrib at +0 alone is ordinary",
      not mawaqit.start_is_iqama(month_table(ADHAN), month_table(["+10", "+10", "+10", "+0", "+10"])))
check("a widget link's slug is found",
      mawaqit.slug_from("https://mawaqit.net/fr/w/islamic-society-of-belleville?showonly5prayers=1")
      == "islamic-society-of-belleville")
check("and so is a masjid link's",
      mawaqit.slug_from("https://mawaqit.net/en/m/islamic-centre-of-kingston-k7l") ==
      "islamic-centre-of-kingston-k7l")

# ---------------------------------------------------------------------------
print("the picker")
off = {"name": "Off", "slug": "off-masjid", "iqama": False, "source": "mawaqit"}
on = {"name": "On", "slug": "on-masjid", "iqama": True, "source": "mawaqit"}
check("a masjid with iqama switched off offers nothing to save", masjids.address_for(off) == "")
check("and says why", "does not publish" in masjids.describe(off))
check("one that publishes them is offered", masjids.address_for(on).endswith("/en/on-masjid"))
check("a site with no website offers nothing",
      masjids.address_for({"name": "x", "source": "directory"}) == "")

# ---------------------------------------------------------------------------
print("sites that embed a platform we read")
real_urlopen = urllib.request.urlopen


def serve(html):
    urllib.request.urlopen = lambda req, timeout=None, **k: Reply("https://m.test/", html)


try:
    serve('<iframe src="https://mawaqit.net/fr/w/islamic-society-of-belleville?x=1"></iframe>')
    check("a single embedded mawaqit widget is found",
          prayer._embedded_page("https://m.test/") ==
          "https://mawaqit.net/en/islamic-society-of-belleville")
    serve('<a href="https://mawaqit.net/en/a-masjid-one">x</a>'
          '<a href="https://mawaqit.net/en/a-masjid-two">y</a>')
    check("two different masjids linked means none is followed",
          prayer._embedded_page("https://m.test/") == "")
    serve('<a href="https://prayersconnect.com/mosques/12345-some-masjid">x</a>')
    check("a prayersconnect page is found too",
          prayer._embedded_page("https://m.test/") ==
          "https://prayersconnect.com/mosques/12345-some-masjid")
    serve('<a href="https://masjidbox.com/prayer-times/Umulqura">Prayer Times</a>'
          '<script src="https://masjidbox.com/widgets/loader.js"></script>')
    check("a masjidbox page is found too, and only its prayer-times page",
          prayer._embedded_page("https://m.test/") == "https://masjidbox.com/prayer-times/umulqura")
    serve('<a href="https://masjidbox.com/prayer-times/one-masjid">x</a>'
          '<a href="https://masjidbox.com/prayer-times/another-masjid">y</a>')
    check("two masjidbox pages linked means none is followed", prayer._embedded_page("https://m.test/") == "")
    serve("<html>nothing here</html>")
    check("a page that embeds nothing yields nothing", prayer._embedded_page("https://m.test/") == "")
finally:
    urllib.request.urlopen = real_urlopen

# ---------------------------------------------------------------------------
print("prayersconnect")
try:
    from zoneinfo import ZoneInfo

    ZoneInfo("America/Toronto")
    have_zones = True
except Exception:
    have_zones = False
if have_zones:
    local = prayersconnect._local("2026-09-19T21:05:00Z", "America/Edmonton")
    check("an instant is shown on the masjid's own clock, wherever this machine is",
          local == datetime(2026, 9, 19, 15, 5), str(local))
else:
    print("  skip  no timezone database on this machine")

# ---------------------------------------------------------------------------
print("a source that keeps failing")
check("the first retry waits five minutes",
      prayer.retry_gap(1, False) == 5.0, str(prayer.retry_gap(1, False)))
check("then ten, then twenty",
      (prayer.retry_gap(2, False), prayer.retry_gap(3, False)) == (10.0, 20.0))
check("and never more than six hours apart", prayer.retry_gap(40, False) == 360.0)
check("times already in hand are retried more gently still",
      prayer.retry_gap(1, True) == 15.0)

ago = datetime.now() - timedelta(minutes=7)
check("seven minutes on, a first failure is retried",
      prayer.refresh_due(datetime.now(), None, ago, False, failures=1))
check("but a third in a row is not -- it waits twenty",
      not prayer.refresh_due(datetime.now(), None, ago, False, failures=3))

import tempfile

loader = prayer.Loader({"prayer_enabled": True}, tempfile.mkdtemp(),
                       to_ui=lambda fn: fn(), on_ready=lambda *_: None)
for _ in range(3):
    loader._done([], "nothing")
check("the loader counts failures in a row", loader.failures == 3, str(loader.failures))
loader._done([prayer.Prayer("Fajr", datetime.now() + timedelta(hours=1))], "read")
check("and starts again from zero once it reads something", loader.failures == 0)

real_load = prayer.load
prayer.load = lambda *a, **k: ([], "Could not read the prayer times: nothing")
try:
    loader.failures = 5
    loader.refresh()                       # somebody pressed Refresh or changed the address
    import time as _time

    deadline = _time.time() + 5
    while loader.loading and _time.time() < deadline:
        _time.sleep(0.02)
    check("a person asking is not made to wait out the last failure",
          loader.failures == 1, "failures %d" % loader.failures)
finally:
    prayer.load = real_load

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all reader checks passed")
