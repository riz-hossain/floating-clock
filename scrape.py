"""Reading a masjid's iqama times off an ordinary web page.

The last resort. MAWAQIT, PrayersConnect and the WordPress plugin hand over
structured data; this is for the masjid whose site simply prints its times.
Nothing here can be exact the way those are, so the whole design is about not
being wrong. A page is read only when it says which times are the
congregation's, and whatever is read still has to pass checks that a genuine
timetable passes and a misreading does not:

- the five run in order, and each is where that prayer can fall;
- where both an adhan and an iqama are printed, the iqama follows its adhan;
- a table whose adhan times are all on the hour or half hour is a template
  waiting to be filled in, not a timetable;
- a date printed beside the times must be today's;
- and, when the masjid's position is known, the sun must agree: a table of June
  times on a September page is well-formed in every respect and impossible in
  one (Maghrib cannot be two hours after sunset).

What it reads is what is in the HTML the server sends. Times that a script
fills in afterwards are not there -- the page holds a "-" or a "12:00 am" --
and those masjids have to be reached some other way.

Layouts seen on real masjid sites, all handled:

    Fajr 5:37 AM  Iqama: 6:00 AM          one line, labelled
    Fajr / Athan 5:49 / Iqamah 6:15       a stack, labelled
    | Fajr | 5:45 am | 6:15 am |          a table, columns named in a header
    FAJR 6:15 AM  ATHAN: 05:41 AM         the iqama first, the adhan labelled
    Fajr / 5:46 AM / 6:15 AM              a stack, columns named above it
    Fajr: 6:10 am ... Magrib: 3 minutes   one time each, Maghrib in words
        after sunset

The last is the one that needs care. A single time per prayer under no label is
either the adhan or the iqama and the page does not say -- which is exactly the
mistake that shows Fajr an hour early. It is accepted only when the times look
chosen by a person: nearly all on a multiple of five minutes, which calculated
adhan times are not.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from . import astro

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 12.0
MAX_BYTES = 1_500_000
DEADLINE_S = 40.0
RENDER_DEADLINE_S = 70.0      # the whole of the browser-rendered attempt
USER_AGENT = "FloatingClock/1.0 (+prayer-times)"

DAILY = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")

# Where each iqama can fall on a masjid's own clock, minutes after midnight.
# The same windows prayer.screen() applies; repeated here so that a page with
# nonsense in it is refused at once rather than after being cached.
WINDOW = {
    "Fajr": (2 * 60 + 30, 8 * 60 + 15),
    "Dhuhr": (11 * 60 + 30, 15 * 60),
    "Asr": (13 * 60 + 30, 19 * 60 + 45),
    "Maghrib": (16 * 60, 22 * 60 + 45),
    "Isha": (17 * 60 + 30, 23 * 60 + 59),
}

# How sure a reading is, best first. Whether one is accepted, and which of two
# readings of the same page wins, rests on this.
LABELLED, HEADED, GUESSED = 3, 2, 1
HOW = {LABELLED: "labelled", HEADED: "headed", GUESSED: "guessed"}


class ScrapeError(Exception):
    """The page could not be read for times."""


class NoTimes(ScrapeError):
    """Nothing on the page reads as a masjid's iqama times."""


# --- turning HTML into lines -------------------------------------------------
_SKIP = {"script", "style", "noscript", "template", "svg", "head", "select", "option",
         "button", "iframe", "canvas", "video", "audio", "object", "embed"}
_VOID = {"br", "img", "input", "hr", "meta", "link", "area", "base", "col", "source",
         "track", "wbr", "param"}
_BLOCK = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section",
          "article", "header", "footer", "tr", "table", "dl", "dt", "dd", "main", "nav",
          "figure", "form", "blockquote", "aside", "tbody", "thead", "tfoot", "caption",
          "address", "details", "summary", "label", "legend", "fieldset"}


class _Flat(HTMLParser):
    """Visible text, one entry per block, with table cells kept apart.

    A page's own markup decides where a line ends. That is what lets a stack of
    <div>s -- name, then adhan, then iqama -- read the same as a table row.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._cur: list[str] = []
        self._stack: list[tuple[str, bool]] = []
        self._hidden = 0

    def _flush(self) -> None:
        text = " ".join(" ".join(self._cur).split())
        if text:
            self.lines.append(text)
        self._cur = []

    @staticmethod
    def _is_hidden(attrs) -> bool:
        for key, value in attrs:
            if key == "hidden" or (key == "aria-hidden" and value == "true"):
                return True
            if key == "style" and value and re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden", value, re.I):
                return True
        return False

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "br":
            self._flush()
            return
        if tag in _VOID:
            return
        hide = tag in _SKIP or self._is_hidden(attrs)
        self._stack.append((tag, hide))
        if hide:
            self._hidden += 1
        elif tag in ("td", "th"):
            self._cur.append(" | ")
        elif tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag) -> None:
        if tag in _VOID:
            return
        # Close back to the matching tag, tolerating the unclosed ones real
        # pages are full of.
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                for _t, hide in self._stack[index:]:
                    if hide:
                        self._hidden = max(0, self._hidden - 1)
                del self._stack[index:]
                break
        if not self._hidden and tag in _BLOCK:
            self._flush()

    def handle_data(self, data) -> None:
        if not self._hidden and data.strip():
            self._cur.append(data.strip())

    def close(self) -> None:
        super().close()
        self._flush()


_HOUR_ALONE = re.compile(r"^\d{1,2}$")
_MINUTES_ALONE = re.compile(r"^(\d{2})(?:\s*[ap]\.?\s?m\.?)?$", re.I)


def _join_split_times(lines: list[str]) -> list[str]:
    """"5" then "48 AM" on two lines is 5:48 AM.

    Some widgets draw the hour and the minutes as separate elements, which a
    person sees as one time and a reader of the text sees as two numbers.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and _HOUR_ALONE.match(lines[i]) and int(lines[i]) <= 24:
            rest = _MINUTES_ALONE.match(lines[i + 1])
            if rest and int(rest.group(1)) <= 59:
                out.append("%s:%s" % (lines[i], lines[i + 1]))
                i += 2
                continue
        out.append(lines[i])
        i += 1
    return out


def flatten(html: str) -> list[str]:
    parser = _Flat()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        log.debug("could not fully parse a page", exc_info=True)
    return _join_split_times(parser.lines)


def page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not match:
        return ""
    import html as _html

    return " ".join(_html.unescape(match.group(1)).split())[:80]


# --- recognising the pieces --------------------------------------------------
_APOS = "['’`]?"
_NAMES = {
    "Fajr": r"fajr|fajar|fajir|fadjr|subh|sobh",
    "Dhuhr": (r"dhuhr|dhur|duhr|duhur|zuhr|zohr|zuhur|dhohr|zhuhr|dhuhur|dohr|thuhr|"
              r"zuhar|zohar|dhuhar|duhar"),
    "Asr": r"asr|asar|" + _APOS + r"asr",
    "Maghrib": r"maghrib|magrib|maghreb|magreb|mughrib|mughreb|maghrb|maghib|magharib",
    "Isha": r"isha" + _APOS + r"a|isha|ishaa|esha|eshaa|ishá",
}
_NAME_RE = {p: re.compile(r"(?<![a-z])(?:%s)(?![a-z])" % pat, re.I) for p, pat in _NAMES.items()}
# Rows that are not one of the five and end whatever came before them.
_BOUNDARY_RE = re.compile(
    r"(?<![a-z])(?:sunrise|sun rise|shuruq|shurooq|shurouq|shuruk|chourouk|imsak|"
    r"ju+m+[u`\u2019\u02bf']*a+[`\u2019']?a*h?|khut?b[aeh]{1,3}|khotb[ae]h?|"
    r"friday|fri|eid|"
    r"taraweeh|tarawih|tahajjud|ishraq|duha|zawal|qiyam|sehri|suhoor|iftar)(?![a-z])", re.I)
# "Sunset | 7:18 PM" as a row of its own in a timetable is a row that is not one of
# the five, like Sunrise. "Maghrib: Sunset" is Maghrib's own answer, and is not.
_SUNSET_ROW = re.compile(r"^[\s|]*sun\s?set(?![a-z])", re.I)
_IQAMA_RE = re.compile(
    r"(?<![a-z])(?:iq[aā]{1,2}m[aā]?h?t?|jam[aā]{1,2}[ʿ']?[aā]?t|"
    r"jam[aā]?" + _APOS + r"?ah|jam[aā]{1,2}h|congregation)(?![a-z])", re.I)
_ADHAN_RE = re.compile(
    r"(?<![a-z])(?:a[dt]h[aā]{1,2}n|azaan|azan|adan|begins?|starts?|beginning|"
    r"call to prayer)(?![a-z])", re.I)
_TIME_RE = re.compile(
    r"(?<![\d.])(?<!\d:)(\d{1,2}):(\d{2})(?![\d:])(?:\s*([ap])\.?\s?m\b\.?)?", re.I)


class _Tok:
    __slots__ = ("kind", "line", "pos", "value")

    def __init__(self, kind, line, pos, value=None):
        self.kind, self.line, self.pos, self.value = kind, line, pos, value


def _tokens(lines: list[str]) -> list[_Tok]:
    out: list[_Tok] = []
    for number, text in enumerate(lines):
        found: list[_Tok] = []
        for prayer, rx in _NAME_RE.items():
            for m in rx.finditer(text):
                found.append(_Tok("name", number, m.start(), prayer))
        for m in _BOUNDARY_RE.finditer(text):
            found.append(_Tok("bound", number, m.start()))
        if _SUNSET_ROW.match(text) and _TIME_RE.search(text):
            found.append(_Tok("bound", number, 0))
        for m in _IQAMA_RE.finditer(text):
            found.append(_Tok("label", number, m.start(), "iqama"))
        for m in _ADHAN_RE.finditer(text):
            found.append(_Tok("label", number, m.start(), "adhan"))
        for m in _TIME_RE.finditer(text):
            hour, minute = int(m.group(1)), int(m.group(2))
            if minute > 59 or hour > 24:
                continue
            marker = (m.group(3) or "").lower() or None
            found.append(_Tok("time", number, m.start(), (hour, minute, marker)))
        found.sort(key=lambda t: t.pos)
        out.extend(found)
    return out


def _minutes(value, prayer: str):
    """A time as minutes after midnight, on the prayer's own clock, or None.

    A page that writes "6:15" without am or pm is common, and each prayer has
    only one reading that lands in its window, so that decides it.
    """
    hour, minute, marker = value
    if marker == "p":
        hour = hour % 12 + 12
    elif marker == "a":
        hour = hour % 12
    total = hour * 60 + minute
    low, high = WINDOW[prayer]
    if marker is None:
        for guess in (total, total + 12 * 60):
            if low <= guess <= high:
                return guess
        return None
    return total if low <= total <= high else None


# --- finding a block of five prayers ------------------------------------------
_NEAR = 16          # lines from one prayer's name to the next
_SPAN = 6           # lines a prayer's own times can be spread over
_HEADER = 6         # lines above the first name that can hold column headings
_HEADING_CHARS = 40  # a line longer than this is prose, not a column heading
_ABOVE = 5           # lines above a block a heading may sit in, before a day picker is walked back over
_PICKER_CHARS = 44   # a day tab is short; a paragraph that happens to hold a date is not one
_PICKER_GAP = 2      # two lines with no date end a strip of tabs; a widget puts a Hijri date under each
# An instruction is not a heading either. "For Current Iqama Times Select [your branch]" points at
# iqama times that are somewhere else, and the list under it is the city's prayer times: counting
# it as the "iqama column" read start times as iqamas (one Vancouver association's home page, in a
# check of the reader's own confident readings).
_POINTER = re.compile(
    r"\b(?:select|choose|click|tap|press|download|subscribe|confirm|visit|contact)\b"
    r"|\bfor\s+(?:the\s+)?(?:current|latest|updated)\s+iqam", re.I)


def _chains(tokens) -> list[list[int]]:
    """Runs of the five prayers' names, in order, with no other prayer between.

    "No other prayer between" is what picks the table over a "Next prayer:
    FAJR" strip above it: the strip's Fajr has the table's Fajr after it, so
    it is not followed directly by Dhuhr.
    """
    names = [i for i, t in enumerate(tokens) if t.kind == "name"]
    # A prayer written twice in a row ("Dhuhr / Zuhr") is one row.
    seq: list[int] = []
    for i in names:
        if seq and tokens[seq[-1]].value == tokens[i].value \
                and tokens[i].line - tokens[seq[-1]].line <= 1:
            continue
        seq.append(i)
    found = []
    for k in range(len(seq) - 4):
        run = seq[k:k + 5]
        if [tokens[i].value for i in run] != list(DAILY):
            continue
        if all(tokens[b].line - tokens[a].line <= _NEAR for a, b in zip(run, run[1:])):
            found.append(run)
    return found


def _group(tokens, index, stop) -> list:
    """The labels and times that belong to the prayer named at tokens[index]."""
    name = tokens[index]
    items = []
    for j in range(index + 1, stop):
        t = tokens[j]
        if t.line - name.line > _SPAN or t.kind in ("name", "bound"):
            break
        items.append(t)
    return items


def _group_text(lines, tokens, index, stop) -> str:
    """The words that follow one prayer's name, up to the next one."""
    name = tokens[index]
    end_line = tokens[stop].line if stop < len(tokens) else min(len(lines) - 1, name.line + _SPAN)
    end_pos = tokens[stop].pos if stop < len(tokens) else None
    pieces = []
    for n in range(name.line, min(end_line, name.line + _SPAN) + 1):
        text = lines[n]
        start = name.pos if n == name.line else 0
        stop_at = end_pos if (n == end_line and end_pos is not None) else len(text)
        pieces.append(text[start:stop_at])
    return " ".join(pieces)


def _headers(tokens, lines, first) -> list:
    """Column labels sitting directly above the first prayer, in reading order.

    Walked back from the name over labels only: the first time, prayer name or
    unrelated row stops it. That keeps a strip's "IQAMAH" further up the page
    from being counted as one of this table's columns.

    Two labels are one column only when they share a cell: "Athan / Adhan" is
    one heading said twice, "Begins | Adhan | Iqamah" is three.

    A heading is short. A sentence that happens to contain "Iqamah" -- "Confirm
    that Waterloo Masjid Iqamah appears in your calendars" -- is not one, and
    counting it made the first time in every row look like the iqama. An
    instruction ("For Current Iqama Times Select ...") is not one either.
    """
    name = tokens[first]
    run = []
    for t in reversed(tokens[:first]):
        if name.line - t.line > _HEADER or t.kind in ("time", "name", "bound"):
            break
        if (t.kind == "label" and len(lines[t.line]) <= _HEADING_CHARS
                and not _POINTER.search(lines[t.line])):
            run.append(t)
    run.reverse()
    out, previous = [], None
    for t in run:
        same_cell = (previous is not None and previous.line == t.line
                     and "|" not in lines[t.line][previous.pos:t.pos]
                     and previous.value == t.value)
        if not same_cell:
            out.append(t.value)
        previous = t
    return out


def _round_times(times) -> int:
    return sum(1 for m in times if m % 5 == 0)


def _iqama_of(items, header, prayer, words, sun):
    """(iqama, adhan, quality, kind) for one prayer, or None.

    kind is "time" for one read off the page, "single" for a lone unlabelled
    time (accepted only if the block as a whole looks human-set) and
    "computed" for Maghrib worked out from the sun.
    """
    times, label = [], None
    for t in items:
        if t.kind == "label":
            label = t.value
        elif t.kind == "time":
            times.append((t.value, label))
            label = None

    def mins(raw):
        return _minutes(raw, prayer) if raw is not None else None

    if not times:
        if prayer == "Maghrib" and sun is not None:
            value = astro.maghrib_from_words(words, sun[2])
            if value is not None:
                return value, None, LABELLED, "computed"
        return None

    labelled = [(v, l) for v, l in times if l]
    iq = next((v for v, l in labelled if l == "iqama"), None)
    ad = next((v for v, l in labelled if l == "adhan"), None)
    plain = [v for v, _l in times]

    if iq is not None:                                   # "Iqama: 6:00 AM"
        if ad is None and len(times) == 2:
            ad = next((v for v, l in times if l is None), None)
        got = mins(iq)
        return (got, mins(ad), LABELLED, "time") if got is not None else None
    if ad is not None and len(times) == 2:               # "6:15 AM  ATHAN: 05:41"
        other = next((v for v, l in times if l is None), None)
        if other is not None and mins(other) is not None:
            return mins(other), mins(ad), LABELLED, "time"
    if header and "iqama" in header and len(plain) == len(header):
        cols = plain                                      # a table's own columns
        got = mins(cols[header.index("iqama")])
        # Of several adhan-like columns ("Begins", "Adhan") the last is the call.
        at = len(header) - 1 - header[::-1].index("adhan") if "adhan" in header else None
        adhan = mins(cols[at]) if at is not None else None
        return (got, adhan, HEADED, "time") if got is not None else None
    if header == ["adhan"] and len(plain) == 2:           # only "Athan" is named
        first, second = mins(plain[0]), mins(plain[1])
        if first is not None and second is not None and 0 <= second - first <= 90:
            return second, first, GUESSED, "time"
        return None
    if not header and len(plain) == 2:                    # two times, nothing says which
        first, second = mins(plain[0]), mins(plain[1])
        if first is not None and second is not None and 0 <= second - first <= 90:
            return second, first, GUESSED, "time"
        return None
    if not header and not labelled and len(plain) == 1:   # one time, nothing says what
        got = mins(plain[0])
        return (got, None, GUESSED, "single") if got is not None else None
    return None


def _accept(result, adhans, kinds, worst, sun):
    """The quality of a reading that passes every check, or None.

    The checks are the same for every layout, which is the point of keeping
    them here: however the page was laid out, what comes out has to be a
    timetable a masjid could actually have.
    """
    run_times = [result[p] for p in DAILY]
    if any(a >= b for a, b in zip(run_times, run_times[1:])):
        return None                                      # out of order
    for prayer, adhan in adhans.items():
        gap = result[prayer] - adhan
        # An iqama before its adhan is a misread column. Maghrib may share the
        # minute; the rest are at most a little over an hour on.
        if gap < 0 or gap > 90 or (gap == 0 and prayer != "Maghrib"):
            return None

    # One time apiece and no label: only if a person plainly chose them.
    singles = [p for p in DAILY if kinds[p] == "single"]
    if singles:
        if any(kinds[p] == "time" for p in DAILY):
            return None                                  # a mix says the page is not uniform
        # Maghrib is prayed at sunset, which is no round number; a page that
        # lists the other four by a person's hand and Maghrib by the sun is the
        # ordinary way to write it down.
        explicit = [result[p] for p in singles if p != "Maghrib"]
        if len(explicit) < 4 or _round_times(explicit) < len(explicit):
            return None
        worst = GUESSED

    # A table of round adhan times is a template still waiting for its numbers.
    if len(adhans) >= 4 and all(a % 30 == 0 for a in adhans.values()):
        return None

    if sun is not None and astro.check(result, sun):
        return None                                      # the wrong season, or a placeholder
    return worst


def _read_block(tokens, lines, run, sun):
    """One candidate: five prayers' iqamas, or None if they do not add up."""
    header = _headers(tokens, lines, run[0])
    result, adhans, kinds = {}, {}, {}
    worst = LABELLED
    for k, prayer in enumerate(DAILY):
        stop = run[k + 1] if k + 1 < len(run) else len(tokens)
        items = _group(tokens, run[k], stop)
        words = _group_text(lines, tokens, run[k], stop)
        found = _iqama_of(items, header, prayer, words, sun)
        if found is None:
            return None
        minutes, adhan, quality, kind = found
        result[prayer], kinds[prayer] = minutes, kind
        if adhan is not None:
            adhans[prayer] = adhan
        worst = min(worst, quality)
    worst = _accept(result, adhans, kinds, worst, sun)
    if worst is None:
        return None
    return {"minutes": result, "quality": worst, "kinds": kinds,
            "lines": (tokens[run[0]].line, tokens[run[-1]].line),
            "today": _says_today(lines, tokens[run[0]].line)}


def _says_today(lines, first: int) -> bool:
    """Whether the block is introduced as today's: "Today", "Today's prayer times".

    A weekly calendar prints seven days' times one after another, and the only
    thing that says which is today's is a word like this above it.
    """
    return bool(re.search(r"\b(?:today|current(?:ly)?)\b",
                          " ".join(lines[max(0, first - 4): first + 1]), re.I))


def _read_horizontal(tokens, lines, run, sun):
    """Prayers across the top, one row of times per kind beneath.

        |       | Fajr     | Dhuhr    | Asr      | Maghrib  | Isha     |
        | Adhan | 05:51 AM | 01:15 PM | 04:40 PM | 07:25 PM | 08:43 PM |
        | Iqama | 06:15 AM | 01:45 PM | 06:00 PM | 07:30 PM | 09:00 PM |

    The transpose of the layout everything else here reads, and as common: a
    timetable narrow enough for a phone is drawn this way.
    """
    head = tokens[run[0]].line
    columns = [t for t in tokens if t.line == head and t.kind in ("name", "bound")
               and tokens[run[0]].pos <= t.pos <= tokens[run[-1]].pos]
    prayer_at = [i for i, t in enumerate(columns) if t.kind == "name"]
    rows = {}
    for n in range(head + 1, min(len(lines), head + 9)):
        row = [t for t in tokens if t.line == n]
        if any(t.kind in ("name", "bound") for t in row):
            break                                        # the next section
        times = [t for t in row if t.kind == "time"]
        if len(times) not in (5, len(columns)):
            continue
        label = next((t.value for t in row if t.kind == "label" and t.pos < times[0].pos), None)
        picked = [times[i] for i in prayer_at] if len(times) == len(columns) else times
        rows.setdefault(label, (n, picked))
    if "iqama" not in rows:
        return None
    n_iqama, iq_times = rows["iqama"]
    result, adhans = {}, {}
    for prayer, t in zip(DAILY, iq_times):
        minutes = _minutes(t.value, prayer)
        if minutes is None:
            return None
        result[prayer] = minutes
    if "adhan" in rows:
        for prayer, t in zip(DAILY, rows["adhan"][1]):
            adhan = _minutes(t.value, prayer)
            if adhan is not None:
                adhans[prayer] = adhan
    kinds = {p: "time" for p in DAILY}
    worst = _accept(result, adhans, kinds, LABELLED, sun)
    if worst is None:
        return None
    last = max(n for n, _ in rows.values())
    return {"minutes": result, "quality": worst, "kinds": kinds, "lines": (head, last),
            "today": _says_today(lines, head)}


def _read_columns(tokens, lines, run, sun):
    """All five names first, then their times in the same order.

        Fajr / Dhuhr / Asr / Maghrib / Isha          one name to a line
        6:15 AM / 1:30 PM / 5:30 PM / Sunset / 9:15 PM   then one time to a line

    What a widget laid out in columns comes to once it is flattened. One list of
    times is the iqamas if it says so or if nothing says otherwise (and then it
    must look chosen by a person); two lists -- begins, then iqama -- are taken
    only when each is named just above it, or the page names its columns.
    """
    if any(t.kind == "time" for t in tokens[run[0]:run[-1] + 1]):
        return None                                        # times between the names: not this layout
    first, last = tokens[run[0]].line, tokens[run[-1]].line
    by_line: dict = {}
    for t in tokens:
        if first <= t.line <= last + 24:
            by_line.setdefault(t.line, []).append(t)

    def entry(n: int, k: int):
        if n >= len(lines):
            return None
        row = by_line.get(n, [])
        if any(t.kind in ("name", "bound") for t in row):
            return None
        times = [t for t in row if t.kind == "time"]
        if len(times) == 1:
            return "time", times[0].value
        if not times and k == 3 and sun is not None:
            got = astro.maghrib_from_words(lines[n], sun[2])
            if got is not None:
                return "computed", got
        return None

    def read_list(start: int):
        got = [entry(start + k, k) for k in range(5)]
        return got if all(got) else None

    def label_of(n: int):
        row = by_line.get(n, [])
        if row and not any(t.kind in ("time", "name", "bound") for t in row):
            return next((t.value for t in row if t.kind == "label"), None)
        return None

    lists, labels = [], []
    n, skipped = last + 1, 0
    while len(lists) < 2 and n <= last + 24:
        label = label_of(n)
        start = n + 1 if label is not None else n
        got = read_list(start)
        if got is not None:
            lists.append(got)
            labels.append(label)
            n = start + 5
        elif lists or skipped >= 2:
            break
        else:
            skipped += 1                                   # a caption between the names and the times
            n += 1
    if not lists:
        return None
    head = _headers(tokens, lines, run[0])
    if len(lists) == 2:
        names_of = labels if all(labels) else (head if len(head) == 2 else None)
        if not names_of or "iqama" not in names_of:
            return None
        pick, other = names_of.index("iqama"), 1 - names_of.index("iqama")
        chosen, adhan_list = lists[pick], lists[other]
        quality = LABELLED if all(labels) else HEADED
    else:
        chosen, adhan_list = lists[0], None
        if labels[0] == "adhan":
            return None                                    # only the call to prayer is printed
        quality = LABELLED if labels[0] == "iqama" else GUESSED

    result, adhans, kinds = {}, {}, {}
    for k, prayer in enumerate(DAILY):
        kind, value = chosen[k]
        minutes = value if kind == "computed" else _minutes(value, prayer)
        if minutes is None:
            return None
        result[prayer] = minutes
        kinds[prayer] = "computed" if kind == "computed" else ("single" if quality == GUESSED else "time")
        if adhan_list is not None and adhan_list[k][0] == "time":
            a = _minutes(adhan_list[k][1], prayer)
            if a is not None:
                adhans[prayer] = a
    worst = _accept(result, adhans, kinds, quality, sun)
    if worst is None:
        return None
    return {"minutes": result, "quality": worst, "kinds": kinds,
            "lines": (first, n - 1), "today": _says_today(lines, first)}


def blocks(lines: list[str], sun=None) -> list[dict]:
    """Every run of the five prayers on the page that reads as iqama times."""
    tokens = _tokens(lines)
    found = []
    for run in _chains(tokens):
        across = tokens[run[-1]].line - tokens[run[0]].line <= 1
        block = (_read_horizontal(tokens, lines, run, sun) if across
                 else _read_block(tokens, lines, run, sun))
        if block is None and not across:
            block = _read_columns(tokens, lines, run, sun)
        if block:
            found.append(block)
    return found


# --- what days the page says these times are for -----------------------------------
_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_ORD = r"(?:st|nd|rd|th)?"
_TO = r"\s*(?:to|-|\u2013|\u2014|through|thru)\s*"
# The (?![\d:]) matters: without it "September 2026" reads as September 20, which
# on the 20th makes every day of a calendar headed by its month look like today,
# and "21 September 5:30 AM" reads as September 5.
_RANGE_MD = re.compile(_MON + r"\s+(\d{1,2})" + _ORD + _TO + r"(?:" + _MON + r"\s+)?(\d{1,2})(?![\d:])" + _ORD, re.I)
_RANGE_DM = re.compile(r"(\d{1,2})" + _ORD + _TO + r"(\d{1,2})" + _ORD + r"\s+" + _MON, re.I)
_DATE_MD = re.compile(_MON + r"\s+(\d{1,2})(?![\d:])" + _ORD + r"(?:,?\s+(\d{4}))?", re.I)
_DATE_DM = re.compile(r"(\d{1,2})" + _ORD + r"\s+" + _MON + r"(?:,?\s+(\d{4}))?", re.I)
_ISO = re.compile(r"(?<!\d)(20\d\d)-(\d{2})-(\d{2})(?!\d)")

# The words in front of a date that say what it is a date of. "Salah timings from
# September 13th" is in force from then until it is changed; "times change on
# Monday 21 September" is in force until then; a bare "Thursday, Sep 17" heads
# the times of that one day.
_FROM_CUE = re.compile(
    r"\b(?:from|effective|starting|starts?|since|as of|updated|w\.?e\.?f\.?|begin(?:s|ning)?)\b[^.\d]{0,24}$", re.I)
_UNTIL_CUE = re.compile(
    r"\b(?:until|till|through|thru|(?:next|upcoming)(?: time)? changes?|changes?|ends?|expires?)\b[^.\d]{0,24}$", re.I)
_ANY_CUE = re.compile(
    r"\b(?:from|effective|starting|since|as of|updated|until|till|through|changes?|w\.?e\.?f)\b", re.I)
_DATE_LINE_CHARS = 80      # a longer sentence is prose, and its date is not this table's
_FROM_VALID_DAYS = 200     # "from March 8" is not a reason to trust a September page


def _closest(month: int, day: int, today: date) -> date | None:
    """The date with that month and day nearest to today: a page rarely prints the year."""
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best


def _line_dates(text: str, where: str, today: date) -> list:
    """(kind, start, end, where) for each date on one line.

    kind is "day" (heads one day's times), "range" (a span of days), "from" or
    "until".
    """
    if len(text) > 120 or (len(text) > _DATE_LINE_CHARS and not _ANY_CUE.search(text)):
        return []
    out = []
    masked = list(text)

    def blank(m) -> None:
        for i in range(m.start(), m.end()):
            masked[i] = " "

    def kind_of(start: int) -> str:
        prefix = text[max(0, start - 40): start]
        if _UNTIL_CUE.search(prefix):
            return "until"
        return "from" if _FROM_CUE.search(prefix) else "day"

    for m in _RANGE_MD.finditer(text):
        m1 = _MONTHS[m.group(1)[:3].lower()]
        m2 = _MONTHS[m.group(3)[:3].lower()] if m.group(3) else m1
        start = _closest(m1, int(m.group(2)), today)
        end = date(start.year, m2, int(m.group(4))) if start and _valid(start.year, m2, int(m.group(4))) else None
        if start and end:
            if end < start:
                end = date(end.year + 1, end.month, end.day)
            out.append(("range", start, end, where))
            blank(m)
    for m in _RANGE_DM.finditer("".join(masked)):
        m1 = _MONTHS[m.group(3)[:3].lower()]
        start = _closest(m1, int(m.group(1)), today)
        if start and _valid(start.year, m1, int(m.group(2))):
            out.append(("range", start, date(start.year, m1, int(m.group(2))), where))
            blank(m)
    rest = "".join(masked)
    for m in _ISO.finditer(rest):
        if _valid(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            out.append((kind_of(m.start()), d, d, where))
            blank(m)
    for rx, mon, day, year in ((_DATE_DM, 2, 1, 3), (_DATE_MD, 1, 2, 3)):
        for m in rx.finditer("".join(masked)):
            month = _MONTHS[m.group(mon)[:3].lower()]
            if m.group(year):
                d = date(int(m.group(year)), month, int(m.group(day))) if _valid(
                    int(m.group(year)), month, int(m.group(day))) else None
            else:
                d = _closest(month, int(m.group(day)), today)
            if d is not None:
                out.append((kind_of(m.start()), d, d, where))
                blank(m)
    return out


def _valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def _heading_from(lines: list[str], first: int, today: date) -> int:
    """Where a block's heading may start.

    A few lines above it, normally. But a widget that prints today's times under a
    strip of day tabs -- Sunday, Monday ... Saturday, each on its own line with its
    Hijri date beneath -- puts today's own date a dozen lines up, and reading only
    the near end of that strip makes a widget showing today look like next
    Saturday's and refuses it. So the window is walked back over a run of short
    lines that hold no times and no prayer names, which is what such a strip is.
    """
    start = max(0, first - _ABOVE)
    since_date = 0
    while start > 0 and since_date <= _PICKER_GAP:
        text = lines[start - 1]
        if len(text) > _PICKER_CHARS or _TIME_RE.search(text):
            break
        if any(_NAME_RE[p].search(text) for p in DAILY):
            break
        since_date = 0 if _line_dates(text, "above", today) else since_date + 1
        start -= 1
    return start


def _dates_near(lines: list[str], first: int, last: int, today: date) -> list:
    """What the page says about which days the block at lines first..last is for.

    Above it, and on its own lines, a date heads the block. Below it, a date as
    likely heads the next block, so only a "changes on" says anything there.
    """
    found = []
    for lo, hi, where in ((_heading_from(lines, first, today), first, "above"),
                          (first, last + 1, "inside"),
                          (last + 1, last + 4, "below")):
        for n in range(lo, min(hi, len(lines))):
            found.extend(_line_dates(lines[n], where, today))
    return found


def _fresh(evidence: list, today: date):
    """(verdict, exact): whether the page says these are today's times.

    The verdict is True when something says they are, False when something says
    they are not, and None when nothing says -- or when two things disagree.
    `exact` is whether a date printed is today's to the day: a page's yesterday
    and tomorrow are near enough to be believed on their own, and no use for
    choosing between the days of a week.
    """
    good = bad = exact = False
    one_day = timedelta(days=1)
    for kind, start, end, where in evidence:
        if kind == "until":
            left = (start - today).days
            if left > 60:
                continue                                   # too far off to say anything
            ok = left >= -1
        elif where == "below":
            continue                                       # heads the next block as often as this one
        elif kind == "range":
            ok = start - one_day <= today <= end + one_day
            exact = exact or start <= today <= end
        elif kind == "from":
            ok = 0 <= (today - start).days <= _FROM_VALID_DAYS
        else:
            ok = abs((start - today).days) <= 1
            exact = exact or start == today
        good = good or ok
        bad = bad or not ok
    return (good if good != bad else None), (exact and good and not bad)


# --- where the masjid is, when the page says ------------------------------------
_COORD_FORMS = (
    re.compile(r'"latitude"\s*:\s*"?(-?\d{1,2}\.\d+)"?\s*,\s*"longitude"\s*:\s*"?(-?\d{1,3}\.\d+)"?', re.I),
    re.compile(r'name=["\'](?:geo\.position|ICBM)["\'][^>]*content=["\'](-?\d{1,2}\.\d+)\s*[;,]\s*(-?\d{1,3}\.\d+)', re.I),
    re.compile(r'!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)'),
    re.compile(r'[?&@](?:q=|ll=)?(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})'),
    re.compile(r'latitude[^0-9\-]{0,30}(-?\d{1,2}\.\d{3,})[^0-9\-]{1,40}(-?\d{1,3}\.\d{3,})', re.I),
)


def find_coordinates(html: str):
    """(lat, lon) the page gives for the masjid, or None.

    A fallback for when the picker has not supplied a position; used only to
    check and complete a reading, never to decide one. A wrong position can
    only make a good page fail its checks, not a bad one pass them.
    """
    for rx in _COORD_FORMS:
        m = rx.search(html)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -66 <= lat <= 66 and -180 <= lon <= 180 and (abs(lat) > 1 or abs(lon) > 1):
                return lat, lon
    return None


# --- the whole of one page ----------------------------------------------------
def extract(html: str, today: date | None = None, where=None) -> dict | None:
    """The iqama times on one page, or None. Never a guess dressed as a reading.

    `where` is the masjid's (latitude, longitude), or with its UTC offset in
    hours as a third value when that is not this machine's. Without it the
    sun-based checks are skipped and Maghrib written as "sunset" cannot be read.

    {"iqamah": {"Fajr": "06:15", ...}, "quality": 1..3, "how": "labelled",
     "computed": ["Maghrib"], "sun_checked": True}
    """
    today = today or date.today()
    coords = where or find_coordinates(html)
    offset = coords[2] if coords and len(coords) > 2 else None
    sun = astro.sun_today(coords[0], coords[1], today, offset) if coords else None
    lines = flatten(html)
    candidates = blocks(lines, sun)
    if not candidates:
        return None
    usable = []
    for block in candidates:
        seen = _dates_near(lines, block["lines"][0], block["lines"][1], today)
        state, exact = _fresh(seen, today)
        if state is False and not block["today"]:
            continue                                    # dated, and not today
        # 2: it says today, to the day. 1: what it says is consistent with today.
        proof = 2 if (block["today"] or exact) else (1 if state is True else 0)
        usable.append((block["quality"], proof, block))
    if not usable:
        return None
    if len({tuple(u[2]["minutes"][p] for p in DAILY) for u in usable}) > 1:
        # Several different sets of times on one page -- a week's calendar, a
        # men's and a women's hall, two branches. Quality is no way to choose
        # between them: the wrong column can look better than the right row,
        # and did. Only evidence that one is today's settles it, and the best
        # evidence must point at one set alone.
        strongest = max(u[1] for u in usable)
        usable = [u for u in usable if u[1] == strongest] if strongest else []
        if len({tuple(u[2]["minutes"][p] for p in DAILY) for u in usable}) != 1:
            return None
    best = max(u[0] for u in usable)
    top = [u for u in usable if u[0] == best]
    block = top[0][2]
    return {
        "iqamah": {p: "%02d:%02d" % divmod(block["minutes"][p], 60) for p in DAILY},
        "quality": block["quality"],
        "how": HOW[block["quality"]],
        "computed": [p for p in DAILY if block["kinds"][p] == "computed"],
        "sun_checked": sun is not None,
    }


# --- getting the pages ---------------------------------------------------------
_PRAYER_LINK = ("prayer", "salah", "salat", "namaz", "timing", "iqama", "iqamah",
                "jamaat", "schedule", "timetable", "times")
_NOT_A_TIMETABLE = ("youtube.", "youtu.be", "google.com/maps", "maps.google", "facebook.",
                    "instagram.", "vimeo.", "twitter.", "donorbox", "paypal", "eventbrite",
                    "calendly", "stripe.", "zoom.us", "gstatic", "doubleclick", "recaptcha",
                    "tiktok", "spotify", "soundcloud", "typeform", "mailchimp",
                    "constantcontact", "wa.me", "whatsapp",
                    # Read by their own readers, which know which column is which; as
                    # a page they show the adhan and the iqama in ways this reader
                    # cannot tell apart.
                    "mawaqit.net", "prayersconnect.com")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self.iframes: list[str] = []
        self._href = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._href, self._text = a["href"], []
        elif tag == "iframe" and (a.get("src") or a.get("data-src")):
            self.iframes.append(a.get("src") or a.get("data-src"))

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.anchors.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def links(html: str, base: str) -> tuple[list[str], list[str]]:
    """(pages that look like a prayer-times page, iframes worth reading)."""
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:
        pass
    host = urllib.parse.urlsplit(base).netloc.lower().replace("www.", "")
    scored, seen = [], set()
    for href, text in parser.anchors:
        url = urllib.parse.urljoin(base, href.split("#")[0])
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        if parts.netloc.lower().replace("www.", "") != host:
            continue
        if re.search(r"\.(jpe?g|png|gif|svg|webp|pdf|zip|docx?|mp[34])(\?|$)", parts.path, re.I):
            continue
        blob = (parts.path + " " + text).lower()
        score = sum(2 if k in parts.path.lower() else 1 for k in _PRAYER_LINK if k in blob)
        if ("donat" in blob or "event" in blob) and "prayer" not in blob:
            score -= 2
        key = url.rstrip("/")
        if score > 0 and key not in seen and key != base.rstrip("/"):
            seen.add(key)
            scored.append((score, url))
    scored.sort(key=lambda pair: -pair[0])
    subpages = [u for _s, u in scored[:3]]
    frames = []
    for src in parser.iframes:
        url = urllib.parse.urljoin(base, src)
        if url.startswith("http") and not any(k in url.lower() for k in _NOT_A_TIMETABLE):
            frames.append(url)
    return subpages, frames[:2]


def _get(url: str, timeout: float = FETCH_TIMEOUT, opener=None) -> tuple[str, str]:
    """(final url, html). Raises ScrapeError."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            kind = ""
            try:
                kind = response.headers.get_content_type()
            except Exception:
                pass
            if kind and not kind.startswith(("text/", "application/xhtml")):
                raise ScrapeError("not a web page")
            charset = getattr(response.headers, "get_content_charset", lambda: None)() or "utf-8"
            body = response.read(MAX_BYTES).decode(charset, "replace")
            return getattr(response, "geturl", lambda: url)() or url, body
    except ScrapeError:
        raise
    except Exception as exc:
        raise ScrapeError(str(exc)[:80] or exc.__class__.__name__) from None


def _read_site(home_url: str, home: str, today: date, opener, deadline: float,
               started: float, where):
    """(page url, reading) from a site's home page, else from the best of the pages
    it links to that look like a prayer-times page and the frames it embeds."""
    reading = extract(home, today, where)
    if reading is not None:
        return home_url, reading
    subpages, frames = links(home, home_url)

    def grab(url):
        if time.time() - started > deadline:
            return url, None
        try:
            return url, _get(url, opener=opener)
        except ScrapeError:
            return url, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(grab, subpages + frames))
    best = None
    coords = where or find_coordinates(home)
    for _url, got in pages:
        if not got:
            continue
        final, body = got
        found = extract(body, today, coords)
        if found and (best is None or found["quality"] > best[1]["quality"]):
            best = (final, found)
    return best


def _read_rendered(site_url: str, static_home: str, today: date, where, render,
                   started: float, deadline: float):
    """Like _read_site, but every page is opened in a real browser first, so that
    what a script writes into it is there to read.

    ((page url, reading) or None, the home page as the browser drew it or None). The
    second is how a caller tells a site that is down from a site with nothing on it."""

    def draw(url):
        if time.time() - started > deadline:
            return url, None
        try:
            return render(url)
        except Exception as exc:
            log.info("Could not render %s: %s", url, exc)
            return url, None

    home_url, html = draw(site_url)
    if not html:
        return None, None
    coords = where or find_coordinates(html) or find_coordinates(static_home)
    reading = extract(html, today, coords)
    if reading is not None:
        return (home_url, reading), html
    subpages, frames = links(html, home_url)
    if static_home:
        more_pages, more_frames = links(static_home, site_url)
        subpages += [u for u in more_pages if u not in subpages]
        frames += [u for u in more_frames if u not in frames]
    wanted = []
    for url in frames + subpages:                    # a frame is the widget itself
        if url.rstrip("/") not in {w.rstrip("/") for w in wanted} and url.rstrip("/") != home_url.rstrip("/"):
            wanted.append(url)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        pages = list(pool.map(draw, wanted[:4]))
    best = None
    for final, body in pages:
        if not body:
            continue
        found = extract(body, today, coords)
        if found and (best is None or found["quality"] > best[1]["quality"]):
            best = (final, found)
    return (best if best else None), html


_FILE_HINT = re.compile(
    r"(?:prayer|salah|salat|iqama|timetable|timing|schedule)[^\"'<>]{0,60}\.(?:jpe?g|png|webp|gif|pdf)", re.I)


def _posted_as_file(html: str) -> str:
    """"an image", "a PDF" or "" -- for the sentence that says why nothing was read."""
    found = _FILE_HINT.search(html or "")
    if not found:
        return ""
    return "a PDF" if found.group(0).lower().endswith(".pdf") else "an image"


def fetch(site_url: str, today: date | None = None, opener=None,
          deadline: float = DEADLINE_S, where=None, render=None) -> str:
    """Read a masjid's iqama times off its site, as the text the clock caches.

    The home page first; then, only if that has nothing, the pages it links to
    that look like a prayer-times page, and the frames it embeds. If a `render`
    function -- render(url) -> (final url, html) -- is given and the plain pages
    hold nothing, the same is done again through a real browser, for the site
    whose times are written in by a script. Raises NoTimes when none of it
    yields a reading worth trusting.
    """
    today = today or date.today()
    started = time.time()
    home_url, home, trouble = site_url, "", None
    try:
        home_url, home = _get(site_url, opener=opener)
    except ScrapeError as exc:
        trouble = exc

    found = _read_site(home_url, home, today, opener, deadline, started, where) if home else None
    title = page_title(home) if home else ""
    drawn_html = None
    if found is None and render is not None:
        found, drawn_html = _read_rendered(site_url, home, today, where, render, started,
                                           RENDER_DEADLINE_S)
        title = title or (page_title(drawn_html) if drawn_html else "")
    if found is None:
        if trouble is not None and not drawn_html:
            raise ScrapeError("could not open the site (%s)" % trouble) from None
        posted = _posted_as_file(home or drawn_html or "")
        if posted:
            raise NoTimes("its timetable is posted as %s, which the clock cannot read yet" % posted)
        raise NoTimes("no page on that site prints its congregation times in a form "
                      "the clock can read")
    page, reading = found
    return json.dumps({
        "source": "scrape",
        "name": title or urllib.parse.urlsplit(site_url).netloc,
        "page": page,
        "asof": today.isoformat(),
        "how": reading["how"],
        "quality": reading["quality"],
        "computed": reading["computed"],
        "sun_checked": reading["sun_checked"],
        "iqamah": reading["iqamah"],
    })


# --- reading the cache ----------------------------------------------------------
def parse(text: str, window_start: datetime, window_end: datetime) -> list:
    """(name, iqama) for the day the page was read, inside the window.

    One day only, like PrayersConnect: a page shows today's times and nothing
    about next week's. The clock reads again every night.
    """
    try:
        data = json.loads(text)
        day = date.fromisoformat(str(data["asof"]))
        times = data["iqamah"]
    except (ValueError, KeyError, TypeError):
        return []
    found = []
    for name in DAILY:
        raw = str(times.get(name) or "")
        m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
        if not m:
            continue
        when = datetime(day.year, day.month, day.day, int(m.group(1)), int(m.group(2)))
        if window_start <= when <= window_end:
            found.append((name, when))
    found.sort(key=lambda item: (item[1], item[0]))
    return found


def source_name(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    return "%s (read from its web page)" % (data.get("name") or "")


def sun_checked(text: str) -> bool:
    """Whether the reading in a cache was checked against the sun."""
    try:
        return bool(json.loads(text).get("sun_checked"))
    except ValueError:
        return False


def how(text: str) -> str:
    """"labelled", "headed" or "guessed" -- how sure the reading was."""
    try:
        return str(json.loads(text).get("how") or "")
    except ValueError:
        return ""
