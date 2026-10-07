"""Offline checks for reading a calendar a masjid's page links to.

Waterloo Masjid runs no timetable plug-in and draws its times with a script,
so reading the page gave five guesses for one day -- while the page links the
very calendar the masjid publishes, a year of exact iqama times. These checks
pin down which links count as that calendar and which do not.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-linked-calendar.py
"""

from __future__ import annotations

import sys

from floating_clock import prayer

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


HOME = "https://waterloomasjid.com/main/"

# The shape of the real page: a webcal button for Apple, a share link for
# Google, and the ordinary furniture of a website around them.
PAGE = """
<html><head><link rel="stylesheet" href="/main/style.css"></head>
<body>
  <a href="/main/index.php/prayers">Prayers</a>
  <img src="/main/images/logo.png">
  <p>Subscribe: <a href="https://calendar.google.com/calendar/u/6?cid=ZjYwMjQxYTZi">Google Calendar</a></p>
  <p>Apple: <a href="webcal://calendar.google.com/calendar/ical/f60241a6%40group.calendar.google.com/public/basic.ics">Apple Calendar</a></p>
  <a href="/main/masjid_media/yearly_timetable.pdf">Yearly timetable</a>
</body></html>
"""

print("what counts as a linked calendar")
found = prayer.linked_calendars(PAGE, HOME)
check("the webcal link is found", len(found) == 1, "found %r" % (found,))
check("webcal is read as https",
      found and found[0].startswith("https://calendar.google.com/calendar/ical/"),
      "got %r" % (found[0] if found else None,))
check("the Google share link is not mistaken for a feed",
      not any("cid=" in url for url in found))
check("the PDF timetable is not mistaken for a feed",
      not any(url.endswith(".pdf") for url in found))
check("ordinary links are left alone",
      not any("style.css" in url or "logo.png" in url for url in found))

print()
print("relative and escaped addresses")
relative = prayer.linked_calendars(
    '<a href="/feeds/iqamah.ics">calendar</a>', "https://example.org/prayers/")
check("a relative address is made absolute",
      relative == ["https://example.org/feeds/iqamah.ics"], "got %r" % (relative,))
escaped = prayer.linked_calendars(
    '<a href="https://example.org/ical?id=7&amp;fmt=ics">c</a>', HOME)
check("&amp; in a link is undone", escaped == ["https://example.org/ical?id=7&fmt=ics"],
      "got %r" % (escaped,))

print()
print("pages with nothing to offer")
check("a page with no calendar gives none",
      prayer.linked_calendars("<html><body>No times here</body></html>", HOME) == [])
check("an empty page is not an error", prayer.linked_calendars("", HOME) == [])

print()
print("a page listing many calendars")
many = "".join('<a href="https://example.org/%d/basic.ics">c</a>' % n for n in range(9))
check("no more than a handful are tried",
      len(prayer.linked_calendars(many, HOME)) == prayer.MAX_LINKED_CALENDARS,
      "got %d" % len(prayer.linked_calendars(many, HOME)))
check("the same address twice counts once",
      prayer.linked_calendars(
          '<a href="https://e.org/a.ics">x</a><a href="https://E.org/A.ics">y</a>', HOME
      ) == ["https://e.org/a.ics"])

print()
print("the page is read once and looked through twice")
calls: list[str] = []


def fake_page(home: str) -> str:
    calls.append(home)
    return PAGE


original = prayer._page_html
prayer._page_html = fake_page
try:
    # _embedded_page takes the page it was given rather than fetching again.
    prayer._embedded_page(HOME, PAGE)
    check("a page handed in is not fetched again", calls == [], "fetched %r" % (calls,))
    prayer._embedded_page(HOME)
    check("a page not handed in is fetched", calls == [HOME], "fetched %r" % (calls,))
finally:
    prayer._page_html = original

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all linked-calendar checks passed")
