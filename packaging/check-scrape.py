"""Offline checks for reading a masjid's times off its web page, and for the sun.

Every layout here was met on a real masjid website while testing this reader,
and every refusal is a page that a careless reader would have got wrong: a
placeholder table, a June timetable on a September page, a week of different
days with no way to say which is today. The fixtures are small, made-up pages
in those shapes -- no network, so they cannot flake.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-scrape.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
from datetime import date, datetime

from floating_clock import astro, scrape

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


TODAY = date(2026, 9, 20)
WATERLOO = (43.4643, -80.5204, -4)          # and Eastern Daylight Time, whatever this machine says
FIVE = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


def read(html: str, today: date = TODAY, where=WATERLOO):
    return scrape.extract(html, today, where)


def times(found) -> str:
    return " ".join(found["iqamah"][p] for p in FIVE) if found else "nothing"


def page(body: str) -> str:
    return "<html><head><title>A Masjid</title></head><body>%s</body></html>" % body


GOOD = "06:15 13:45 17:45 19:28 21:00"

# --- the layouts that are read -------------------------------------------------
print("layouts that are read")

found = read(page("<p>Fajr 5:49 AM Iqama: 6:15 AM</p><p>Dhuhr 1:16 PM Iqama: 1:45 PM</p>"
                  "<p>Asr 5:34 PM Iqama: 5:45 PM</p><p>Maghrib 7:24 PM Iqama: 7:28 PM</p>"
                  "<p>Isha 8:43 PM Iqama: 9:00 PM</p>"))
check("one line to a prayer, the iqama labelled", times(found) == GOOD, times(found))
check("and that is the surest kind of reading", found and found["how"] == "labelled")

stack = "".join("<div>%s</div><div>Athan %s</div><div>Iqamah %s</div>" % row for row in (
    ("Fajr", "5:49", "6:15"), ("Dhuhr", "1:16", "1:45"), ("Asr", "5:34", "5:45"),
    ("Maghrib", "7:24", "7:28"), ("Isha", "8:43", "9:00")))
check("a stack of divs, adhan and iqama labelled", times(read(page(stack))) == GOOD)

table = ("<table><tr><th></th><th>Begins</th><th>Iqamah</th></tr>" + "".join(
    "<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % row for row in (
        ("Fajr", "5:49 am", "6:15 am"), ("Dhuhr", "1:16 pm", "1:45 pm"),
        ("Asr", "5:34 pm", "5:45 pm"), ("Maghrib", "7:24 pm", "7:28 pm"),
        ("Isha", "8:43 pm", "9:00 pm"))) + "</table>")
found = read(page(table))
check("a table whose columns are named in a header", times(found) == GOOD, times(found))
check("is a headed reading", found and found["how"] == "headed")

check("the iqama first and the adhan labelled after it",
      times(read(page("".join("<p>%s %s ATHAN: %s</p>" % row for row in (
          ("FAJR", "6:15 AM", "05:49 AM"), ("DHUHR", "1:45 PM", "01:16 PM"),
          ("ASR", "5:45 PM", "05:34 PM"), ("MAGHRIB", "7:28 PM", "07:24 PM"),
          ("ISHA", "9:00 PM", "08:43 PM")))))) == GOOD)

found = read(page("".join("<p>%s %s ATHAN:%s</p>" % row for row in (
    ("FAJR", "6:15 AM", "05:49 AM"), ("DHUHR", "1:45 PM", "01:16 PM"),
    ("ASR", "5:45 PM", "05:34 PM"), ("MAGHRIB", "7:28 PM", "07:24 PM"),
    ("ISHA", "9:00 PM", "08:43 PM")))))
check("a label with the time straight after its colon", times(found) == GOOD and found["how"] == "labelled",
      "%s (%s)" % (times(found), found and found["how"]))

pieces = "".join("<div>%s</div><div>%s</div><div>%s AM</div><div>Iqamah</div><div>%s</div><div>%s AM</div>" % row
                 for row in (("Fajr", "5", "49", "6", "15"),))
pieces += "".join("<div>%s</div><div>%s</div><div>%s PM</div><div>Iqamah</div><div>%s</div><div>%s PM</div>" % row
                  for row in (("Dhuhr", "1", "16", "1", "45"), ("Asr", "5", "34", "5", "45"),
                              ("Maghrib", "7", "24", "7", "28"), ("Isha", "8", "43", "9", "00")))
check("a time drawn as two elements, the hour and then the minutes, is one time",
      times(read(page(pieces))) == GOOD, times(read(page(pieces))))
check("but a number on its own and a number on the next line are not always a time",
      scrape.flatten("<div>5</div><div>48 people came</div><div>1</div><div>7</div>") ==
      ["5", "48 people came", "1", "7"])

across = ("<table><tr><td></td><td>Fajr</td><td>Dhuhr</td><td>Asr</td><td>Maghrib</td><td>Isha</td></tr>"
          "<tr><td>Adhan</td><td>5:49</td><td>1:16</td><td>5:34</td><td>7:24</td><td>8:43</td></tr>"
          "<tr><td>Iqama</td><td>6:15</td><td>1:45</td><td>5:45</td><td>7:28</td><td>9:00</td></tr></table>")
check("prayers across the top and a row each for adhan and iqama", times(read(page(across))) == GOOD,
      times(read(page(across))))

names_first = ("<h2>Prayer timing September 11 to 20</h2><div>Fajar</div><div>Dhuhr</div><div>Asr</div>"
               "<div>Maghrib</div><div>Isha'a</div><div>6:15 AM</div><div>1:45 PM</div>"
               "<div>5:45 PM</div><div>Sunset</div><div>9:00 PM</div>")
found = read(page(names_first))
sunset = astro.sun_today(WATERLOO[0], WATERLOO[1], TODAY, WATERLOO[2])[2]
check("all the names, then all the times in the same order",
      found is not None and found["iqamah"]["Fajr"] == "06:15" and found["iqamah"]["Isha"] == "21:00",
      times(found))
check("and Maghrib written as 'Sunset' is worked out from the sun",
      found is not None and found["iqamah"]["Maghrib"] == "%02d:%02d" % divmod(int(round(sunset)), 60)
      and found["computed"] == ["Maghrib"], times(found))

words = page("<h3>Salah timings from Sunday September 13th, 2026</h3><p>Fajr: 6:10 am</p><p>Zuhr: 1:50 pm</p>"
             "<p>Asr: 6:00 pm</p><p>Magrib: 3 Minutes after sunset</p><p>Isha: 9:10 pm</p>"
             "<h4>First Jumma Salat:</h4><p>Talk in English 1:20 pm</p><p>Arabic Khutba 1:40 pm</p>")
found = read(words)
check("one time each, Maghrib in words, Jumma after Isha ignored",
      found is not None and found["iqamah"]["Isha"] == "21:10"
      and found["iqamah"]["Maghrib"] == "%02d:%02d" % divmod(int(round(sunset)) + 3, 60), times(found))
check("and that is only a guess, said so", found and found["how"] == "guessed")

check("the Jumah spelling, and its Bayaan, end Isha's group",
      times(read(page("<p>Fajr: 6:30 am</p><p>Zuhr: 2:00 pm</p><p>Asr: 6:00 pm</p><p>Maghrib: 7:29 pm</p>"
                      "<p>Isha: 9:00 pm</p><p>Jumah English Bayaan: 4:00 PM</p><p>Jumah Arabic Khutba: 4:15 pm</p>"
                      "<p>Today's prayer times</p>"))) != "nothing")

sunset_row = ("<p>Iqamah times change on Monday 21 September</p><table><tr><th>Prayer</th><th>Begins</th>"
              "<th>Adhan</th><th>Iqamah</th></tr>"
              "<tr><td>Fajr</td><td>5:21 AM</td><td>6:00 AM</td><td>6:15 AM</td></tr>"
              "<tr><td>Sunrise</td><td>6:59 AM</td></tr>"
              "<tr><td>Dhuhr</td><td>1:08 PM</td><td>1:30 PM</td><td>1:35 PM</td></tr>"
              "<tr><td>Asr</td><td>5:27 PM</td><td>6:10 PM</td><td>6:15 PM</td></tr>"
              "<tr><td>Sunset</td><td>7:18 PM</td></tr>"
              "<tr><td>Maghrib</td><td>7:20 PM</td><td>7:21 PM</td><td>7:22 PM</td></tr>"
              "<tr><td>Isha</td><td>8:37 PM</td><td>9:10 PM</td><td>9:15 PM</td></tr></table>")
found = read(sunset_row)
check("a Sunset row among the five is not one of them", times(found) == "06:15 13:35 18:15 19:22 21:15",
      times(found))

# --- which day the times are for -------------------------------------------------------
print("which day the times are for")


def block(fajr: str, dhuhr: str, asr: str, maghrib: str, isha: str, head: str = "") -> str:
    return page("%s" % head + "".join("<p>%s %s</p>" % pair for pair in (
        ("Fajr", fajr), ("Dhuhr", dhuhr), ("Asr", asr), ("Maghrib", maghrib), ("Isha", isha))))


two = ("<h3>Today's prayer times</h3>" + block("Iqama 6:15", "Iqama 1:45", "Iqama 5:45", "Iqama 7:28",
                                              "Iqama 9:00")[len("<html><head><title>A Masjid</title></head><body>"):-len("</body></html>")]
       + "<h3>Next week</h3>" + "".join("<p>%s Iqama %s</p>" % pair for pair in (
           ("Fajr", "6:30"), ("Dhuhr", "1:30"), ("Asr", "5:15"), ("Maghrib", "7:10"), ("Isha", "8:45"))))
found = read(page(two))
check("of two different sets, the one that says today", times(found) == GOOD, times(found))

no_proof = page("<p>Men</p>" + "".join("<p>%s Iqama %s</p>" % pair for pair in (
    ("Fajr", "6:15"), ("Dhuhr", "1:45"), ("Asr", "5:45"), ("Maghrib", "7:28"), ("Isha", "9:00")))
                + "<p>Women</p>" + "".join("<p>%s Iqama %s</p>" % pair for pair in (
                    ("Fajr", "6:30"), ("Dhuhr", "2:00"), ("Asr", "6:00"), ("Maghrib", "7:30"), ("Isha", "9:15"))))
check("two different sets and nothing to choose between them is not guessed at", read(no_proof) is None,
      times(read(no_proof)))

week = "".join("<h4>%s</h4>%s" % (day, "".join("<p>%s Iqama %s</p>" % pair for pair in rows)) for day, rows in (
    ("Saturday, September 19, 2026", (("Fajr", "6:10"), ("Dhuhr", "1:40"), ("Asr", "5:40"), ("Maghrib", "7:30"), ("Isha", "8:55"))),
    ("Sunday, September 20, 2026", (("Fajr", "6:15"), ("Dhuhr", "1:45"), ("Asr", "5:45"), ("Maghrib", "7:28"), ("Isha", "9:00"))),
    ("Monday, September 21, 2026", (("Fajr", "6:20"), ("Dhuhr", "1:50"), ("Asr", "5:50"), ("Maghrib", "7:26"), ("Isha", "9:05")))))
check("a week of days: the one headed with today's date", times(read(page(week))) == GOOD, times(read(page(week))))
check("and a page whose days are all past says nothing", read(page(week), today=date(2026, 9, 27)) is None)

check("a board dated three days ago is not today's",
      read(page("<h4>Thursday, Sep 17, 2026</h4>" + "".join("<p>%s Iqama %s</p>" % pair for pair in (
          ("Fajr", "6:15"), ("Dhuhr", "1:45"), ("Asr", "5:45"), ("Maghrib", "7:28"), ("Isha", "9:00"))))) is None)

board = "".join("<p>%s Iqama %s</p>" % pair for pair in (
    ("Fajr", "6:15"), ("Dhuhr", "1:45"), ("Asr", "5:45"), ("Maghrib", "7:28"), ("Isha", "9:00")))
check("'from' a date in the past: in force until changed", times(read(page("<p>Timings from Sunday September 13th</p>" + board))) == GOOD)
check("'from' a date to come: not in force yet", read(page("<p>Timings from Monday September 28th</p>" + board)) is None)
check("'changes on' a date to come: still in force", times(read(page("<p>Iqamah times change on Monday 21 September</p>" + board))) == GOOD)
check("'changes on' a date gone by: out of date", read(page("<p>Iqamah times change on Monday 14 September</p>" + board)) is None)
check("a range of days that includes today", times(read(page("<p>Prayer timing September 11 to 20</p>" + board))) == GOOD)
check("a range that ended", read(page("<p>Prayer timing September 1 to 10</p>" + board)) is None)
check("an announcement's date on the page is not the timetable's",
      times(read(page("<p>Eid prayers will be held at 10:30am on Wednesday the 27th May, 2026 insha'Allah! Please come early.</p>"
                      + board))) == GOOD)
check("'September 2026' is a month, not the 20th", times(read(page("<h3>Prayer Times September 2026</h3>" + board))) == GOOD)

# --- pages that must not be read --------------------------------------------------------
print("pages that must not be read")

# One Vancouver association's home page: the iqama times need a branch chosen, and what is
# printed is the city's start times (Fajr 5:09 is first light). Counting the instruction as a
# heading read them as iqamas.
VANCOUVER = (49.28, -123.12, -7)


def start_times(line: str) -> str:
    return page("<h3>Salah Times</h3><div>Show Prayer Times for:</div><div>%s</div>"
                "<div>Your Local Branch e.g. Richmond</div>"
                "<div>Fajr</div><div>5:09 AM</div><div>Sunrise</div><div>6:55 AM</div>"
                "<div>Zuhr</div><div>1:15 PM</div><div>Asr</div><div>5:17 PM</div>"
                "<div>Maghrib</div><div>7:18 PM</div><div>Isha</div><div>8:41 PM</div>" % line)


POINTERS = ("For Current Iqama Times Select", "Select your branch for iqama times",
            "Choose a branch for iqama times", "Click here for the iqama times", "Tap for iqama times",
            "Press for iqama times", "Download the iqama times", "Subscribe for iqama times",
            "Confirm the iqama times", "Visit us for iqama times", "Contact the masjid for iqama",
            "For current iqama times see below", "For the latest iqamah times", "For updated iqama times")
for pointer in POINTERS:
    check("a list of start times under %r is the city's prayer times, not an iqama column" % pointer,
          read(start_times(pointer), TODAY, VANCOUVER) is None)
for heading in ("Iqama", "Iqamah Times", "Iqama Times for Current Week", "Jamaat (Iqama) Timings"):
    check("but the same list under the heading %r is what it says it is" % heading,
          times(read(start_times(heading), TODAY, VANCOUVER)) == "05:09 13:15 17:17 19:18 20:41")

# Times a masjid could keep on a September day -- so it is the round adhan times, and
# nothing about the season, that says this is a template still waiting for its numbers.
template = ("<table><tr><td></td><td>Fajr</td><td>Dhuhr</td><td>Asr</td><td>Maghrib</td><td>Isha</td></tr>"
            "<tr><td>Adhan</td><td>5:30</td><td>1:00</td><td>5:00</td><td>7:30</td><td>9:00</td></tr>"
            "<tr><td>Iqama</td><td>6:00</td><td>1:30</td><td>5:30</td><td>7:35</td><td>9:30</td></tr></table>")
check("a template of adhan times on the hour and half hour", read(page(template)) is None)
check("and the same table with real adhan times is a timetable",
      read(page(template.replace("5:30</td><td>1:00</td><td>5:00</td><td>7:30</td><td>9:00",
                                 "5:49</td><td>1:16</td><td>5:14</td><td>7:24</td><td>8:43"))) is not None)

june = page("".join("<p>%s Athan %s Iqama %s</p>" % row for row in (
    ("Fajr", "3:35 AM", "4:00 AM"), ("Dhuhr", "1:16 PM", "1:45 PM"), ("Asr", "5:34 PM", "6:15 PM"),
    ("Maghrib", "9:00 PM", "9:10 PM"), ("Isha", "10:30 PM", "10:45 PM"))))
check("a June timetable on a September page, well-formed in every other way", read(june) is None)
check("but the same page in June is fine", read(june, today=date(2026, 6, 21)) is not None)
check("and without a position there is no sun to check against, so it is not refused for that",
      scrape.extract(june, TODAY, None) is not None)

check("times a script fills in later are placeholders, not times",
      read(page("".join("<div>%s</div><div>12:00 am</div><div>12:00 am</div>" % n for n in FIVE))) is None)
check("and so are unfilled template tags",
      read(page("".join("<div>%s</div><div>[%s_start]</div>" % (n, n.lower()) for n in FIVE))) is None)

check("start times alone, none of them round, are not the congregation's",
      read(page("".join("<p>%s %s</p>" % row for row in (
          ("Fajr", "5:49 AM"), ("Dhuhr", "1:16 PM"), ("Asr", "5:34 PM"), ("Maghrib", "7:24 PM"),
          ("Isha", "8:43 PM"))))) is None)

check("an iqama before its own adhan is a column misread",
      read(page("".join("<p>%s Athan %s Iqamah %s</p>" % row for row in (
          ("Fajr", "6:15", "5:49"), ("Dhuhr", "1:45", "1:16"), ("Asr", "5:45", "5:34"),
          ("Maghrib", "7:28", "7:24"), ("Isha", "9:00", "8:43"))))) is None)

check("prayers out of order are not a timetable",
      read(page("".join("<p>%s Iqama %s</p>" % row for row in (
          ("Fajr", "6:15"), ("Dhuhr", "5:45"), ("Asr", "1:45"), ("Maghrib", "7:28"), ("Isha", "9:00"))))) is None)

check("Fajr before the first light of the day cannot be Fajr",
      read(page("".join("<p>%s Athan %s Iqama %s</p>" % row for row in (
          ("Fajr", "4:00 AM", "4:30 AM"), ("Dhuhr", "1:16 PM", "1:45 PM"), ("Asr", "5:34 PM", "5:45 PM"),
          ("Maghrib", "7:24 PM", "7:28 PM"), ("Isha", "8:43 PM", "9:00 PM"))))) is None)

check("a page that is not about prayer at all", read(page("<h1>Welcome</h1><p>Classes at 5:30 pm on Fridays.</p>")) is None)
check("hidden text is not read",
      read(page('<div style="display:none">' + board + "</div>")) is None)

# --- the sun ----------------------------------------------------------------------------------
print("the sun")


def hm(minutes: float) -> str:
    return "%02d:%02d" % divmod(int(round(minutes)), 60)


ny = astro.sun_times(40.7128, -74.0060, date(2026, 6, 21), -4)
check("New York's longest day: sunrise about 5:25 and sunset about 8:31",
      abs(ny[0] - 5 * 60 - 25) <= 3 and abs(ny[2] - 20 * 60 - 31) <= 3, "%s %s" % (hm(ny[0]), hm(ny[2])))
lon = astro.sun_times(51.5074, -0.1278, date(2026, 12, 21), 0)
check("London's shortest: sunrise about 8:04 and sunset about 3:53",
      abs(lon[0] - 8 * 60 - 4) <= 3 and abs(lon[2] - 15 * 60 - 53) <= 3, "%s %s" % (hm(lon[0]), hm(lon[2])))
check("no sunrise at all above the arctic circle in midwinter", astro.sun_times(78.0, 15.0, date(2026, 12, 21), 1) is None)
check("first light at twenty degrees is well before sunrise in September",
      astro.sun_times(43.46, -80.52, TODAY, -4)[0] - astro.dawn(43.46, -80.52, TODAY, -4) > 90)
check("and never comes in a northern summer", astro.dawn(53.5, -113.5, date(2026, 6, 21), -6) is None)

sun = astro.sun_today(*WATERLOO[:2], TODAY, WATERLOO[2])
check("a machine's own zone is used only when no offset is given",
      astro.sun_today(43.4643, -80.5204, TODAY, -6)[1] < sun[1] - 100)
check("Maghrib as words: 'sunset'", astro.maghrib_from_words("Maghrib: Sunset", sun[2]) == int(round(sun[2])))
check("'3 minutes after sunset'", astro.maghrib_from_words("3 Minutes after sunset", sun[2]) == int(round(sun[2] + 3)))
check("'Sunset + 5'", astro.maghrib_from_words("Sunset + 5", sun[2]) == int(round(sun[2] + 5)))
check("'5 min after Adhan'", astro.maghrib_from_words("5 min after Adhan", sun[2]) == int(round(sun[2] + 1 + 5)))
check("nothing about the sun or adhan says nothing", astro.maghrib_from_words("7:28 PM", sun[2]) is None)

# --- the whole fetch, on a made-up network ------------------------------------------------------------
print("getting the pages")


class Reply:
    def __init__(self, url: str, body: str, kind: str = "text/html") -> None:
        self._url, self._body, self._kind = url, body.encode("utf-8"), kind
        self.headers = self

    def get_content_type(self):
        return self._kind

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


home = page('<a href="/prayer-times/">Prayer Times</a><a href="https://www.facebook.com/x">Facebook</a>'
            '<iframe src="https://www.youtube.com/embed/abc"></iframe><p>Welcome</p>')
site = {"https://masjid.example/": home, "https://masjid.example/prayer-times/": page(board)}
got = json.loads(scrape.fetch("https://masjid.example/", TODAY, network(site), where=WATERLOO))
check("the home page has nothing, so the page it links to as prayer times is read",
      got["iqamah"]["Isha"] == "21:00" and got["page"].endswith("/prayer-times/"), str(got))
check("what is cached says what was read and how sure it was",
      got["source"] == "scrape" and got["asof"] == "2026-09-20" and got["how"] == "labelled")

parsed = scrape.parse(json.dumps(got), datetime(2026, 9, 19, 12), datetime(2026, 9, 28))
check("the cache reads back as today's five", [n for n, _w in parsed] == list(FIVE), str(parsed))
check("and nothing else -- a page shows one day", {w.date() for _n, w in parsed} == {TODAY})
check("a day gone by is outside the window", scrape.parse(json.dumps(got), datetime(2026, 9, 21), datetime(2026, 9, 28)) == [])
check("the source is named for the settings page", "A Masjid" in scrape.source_name(json.dumps(got)))

try:
    scrape.fetch("https://masjid.example/", TODAY, network({"https://masjid.example/": page("<p>Welcome</p>")}),
                 where=WATERLOO)
    check("a site with nothing on it is an error", False, "no error raised")
except scrape.NoTimes as exc:
    check("a site with nothing on it says so", "prints its congregation times" in str(exc), str(exc))

try:
    scrape.fetch("https://masjid.example/", TODAY, network(
        {"https://masjid.example/": page('<img src="/wp-content/uploads/prayer-timetable-sept.jpg">')}), where=WATERLOO)
    check("a timetable posted as a picture is an error", False, "no error raised")
except scrape.NoTimes as exc:
    check("a timetable posted as a picture says that it is one", "image" in str(exc), str(exc))

try:
    scrape.fetch("https://gone.example/", TODAY, network({}), where=WATERLOO)
    check("a site that is down is an error", False, "no error raised")
except scrape.ScrapeError as exc:
    check("a site that is down says it could not be opened", "could not open" in str(exc), str(exc))

# a page whose times a script writes in: nothing in what the server sends, everything once rendered
script = page('<div id="t"></div><script>document.getElementById("t").innerHTML="..."</script>')
asked = []


def renderer(url: str):
    asked.append(url)
    return url, page(board)


got = json.loads(scrape.fetch("https://masjid.example/", TODAY, network({"https://masjid.example/": script}),
                              where=WATERLOO, render=renderer))
check("a script-written page is read once a browser has drawn it", got["iqamah"]["Fajr"] == "06:15", str(got))
check("and a browser was only asked because the plain page had nothing", asked == ["https://masjid.example/"], str(asked))

asked.clear()
scrape.fetch("https://masjid.example/", TODAY, network(site), where=WATERLOO, render=renderer)
check("a browser is not started for a page that reads without one", asked == [])

# links: which pages and frames are worth following
subpages, frames = scrape.links(
    '<a href="/prayer-times">Prayer times</a><a href="/donate">Donate</a><a href="/about">About</a>'
    '<a href="https://other.example/prayer-times">Elsewhere</a><a href="/files/timetable.pdf">PDF</a>'
    '<iframe src="https://widget.example/times"></iframe>'
    '<iframe src="https://mawaqit.net/en/w/some-masjid"></iframe>'
    '<iframe src="https://www.youtube.com/embed/x"></iframe>', "https://masjid.example/")
check("a page called prayer times is followed", subpages == ["https://masjid.example/prayer-times"], str(subpages))
check("a widget frame is followed, and a video is not", frames == ["https://widget.example/times"], str(frames))
check("MAWAQIT is left to its own reader", not any("mawaqit" in f for f in frames))

print()
if failures:
    print("%d scrape check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all scrape checks passed")
