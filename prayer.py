"""Iqama times, read from a masjid's own calendar.

Iqama is when the congregation actually stands up, and every mosque sets its
own -- it cannot be worked out from the sun the way the prayer windows can.
So the times have to come from the mosque itself. Waterloo Masjid publishes
its iqamah calendar as a public iCal feed, which is the same shape this app
already reads for calendars: the whole feature is a fetch, a cached copy and
a parse.

The cached copy is what makes it dependable. The feed carries a year at a
time, so a laptop that is offline, asleep for a week or on a plane still
knows today's times.

No Tk and no threads in here: the app runs `load()` on a worker and hands
the result back to its own loop.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import dpt, ics

log = logging.getLogger(__name__)

# Waterloo Masjid -- the Muslim Society of Waterloo & Wellington Counties --
# hands this link out on its own Prayers page for Apple Calendar. A year of
# times: five a day, with Jumaa in place of Dhuhr on Fridays.
WATERLOO_MASJID_ICS = (
    "https://calendar.google.com/calendar/ical/"
    "f60241a6b8e256eb5597c20afaaea5f919e48d9bedea33757bff6cb45a7930c0"
    "%40group.calendar.google.com/public/basic.ics"
)
SOURCE_NAME = "Waterloo Masjid"
SOURCE_WHERE = "213 Erb St W, Waterloo"

CACHE_NAME = "prayer-times.ics"
# The feed is a year long, so it needs looking at rarely; twice a day keeps a
# machine that is seldom awake from ever running out of times.
REFRESH_HOURS = 12.0
# How much of it to keep parsed. A week is enough to be right while offline
# for days, without holding a year of objects in memory.
WINDOW_DAYS = 8

ORDER = ("Fajr", "Dhuhr", "Jumuah", "Asr", "Maghrib", "Isha", "Eid")
# The prayers a routine can be hung on. Jumuah is not here: it replaces
# Dhuhr on a Friday and falls back to Dhuhr's trigger unless given its own.
DAILY = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")

# Amazon has no API for editing a routine, so the clock does not try. It
# fires a trigger URL instead -- the routine's "when" becomes that trigger
# rather than a time, and the times stay right all year on their own.
HOOK_TIMEOUT = 10.0
HOOK_AGENT = "FloatingClock/1.0 (+prayer-routine-trigger)"
# A moment reached later than this was slept through, and the azan must not
# play an hour late because a laptop woke up. The next one is left to fire
# on time instead.
HOOK_GRACE_S = 120.0
# A trigger that fails is tried again inside that window rather than lost:
# the network is often still coming back in the first minute after a wake.
HOOK_RETRY_GAPS = (15.0, 30.0, 45.0)

# The masjid's calendar is checked once a night, after midnight and well
# before Fajr, so a change to the times is picked up the day it applies...
NIGHTLY_AT = (2, 30)
# ...and again this often while a check is failing, or while there are no
# times at all -- a laptop waking to no network must not wait a whole day.
REFRESH_RETRY_MINUTES = 15.0
EMPTY_RETRY_MINUTES = 5.0
# What feeds call them -> the name shown here. Spellings vary by mosque, so
# match on any word of the title: "Jumaa Iqama", "Salat al-Fajr", "Isha'a".
_ALIASES = {
    "fajr": "Fajr", "fajir": "Fajr", "subh": "Fajr", "sobh": "Fajr",
    "dhuhr": "Dhuhr", "duhr": "Dhuhr", "zuhr": "Dhuhr", "zohr": "Dhuhr",
    "asr": "Asr", "assr": "Asr",
    "maghrib": "Maghrib", "magrib": "Maghrib",
    "isha": "Isha", "ishaa": "Isha", "eshaa": "Isha",
    "jumuah": "Jumuah", "jumaa": "Jumuah", "jumua": "Jumuah", "juma": "Jumuah",
    "jummah": "Jumuah", "jumah": "Jumuah", "friday": "Jumuah",
    "eid": "Eid",
}


@dataclass(frozen=True)
class Prayer:
    """One iqama: which prayer, and the moment the congregation stands."""

    name: str
    iqama: datetime

    @property
    def key(self) -> str:
        """Stable per prayer per day, so a reminder cannot fire twice."""
        return "prayer:%s:%s" % (self.name, self.iqama.strftime("%Y-%m-%dT%H:%M"))

    def minutes_until(self, now: datetime | None = None) -> float:
        return (self.iqama - (now or datetime.now())).total_seconds() / 60.0

    def time_text(self, use_24h: bool = False) -> str:
        if use_24h:
            return self.iqama.strftime("%H:%M")
        return self.iqama.strftime("%I:%M %p").lstrip("0").lower()


def normalise(summary: str) -> str:
    """'Asr Iqama' -> 'Asr'; '' for anything that is not a prayer.

    Mosque calendars carry lectures, fundraisers and classes too, and none of
    those should turn into a reminder to go and pray.
    """
    text = (summary or "").replace("'", "").replace("-", " ").replace("_", " ")
    for word in text.split():
        name = _ALIASES.get(word.strip(".,:").lower())
        if name:
            return name
    return ""


def parse(text: str, window_start: datetime, window_end: datetime) -> list[Prayer]:
    """Every iqama inside the window, in time order, from either kind of feed.

    The cache holds whatever was fetched, so what it holds says which reader
    to use: a mosque website's timetable arrives as JSON, a calendar as ICS.
    Content rather than a saved flag, so a cache left over from the other
    kind of address cannot be read with the wrong reader.
    """
    if text.lstrip()[:1] in ("{", "["):
        return [Prayer(name=name, iqama=when)
                for name, when in dpt.parse(text, window_start, window_end)]
    return parse_ics(text, window_start, window_end)


def parse_ics(text: str, window_start: datetime, window_end: datetime) -> list[Prayer]:
    """Every iqama in an ICS feed that falls inside the window."""
    found: list[Prayer] = []
    try:
        events = ics.to_events(text, window_start, window_end, source="prayer")
    except Exception:
        log.warning("Could not parse the prayer calendar", exc_info=True)
        return []
    for event in events:
        if getattr(event, "all_day", False):
            continue
        name = normalise(getattr(event, "subject", ""))
        if name:
            found.append(Prayer(name=name, iqama=event.start))
    found.sort(key=lambda p: (p.iqama, p.name))
    # A feed that carries both a master and its override would otherwise
    # list one prayer twice, and remind twice for it.
    out: list[Prayer] = []
    for prayer in found:
        if out and out[-1].name == prayer.name and out[-1].iqama == prayer.iqama:
            continue
        out.append(prayer)
    return out


def next_prayer(prayers, now: datetime | None = None) -> Prayer | None:
    """The next iqama still to come."""
    now = now or datetime.now()
    for prayer in prayers:
        if prayer.iqama > now:
            return prayer
    return None


def on_day(prayers, day) -> list[Prayer]:
    """The iqamas for one calendar day, for the settings page's list."""
    if isinstance(day, datetime):
        day = day.date()
    return [prayer for prayer in prayers if prayer.iqama.date() == day]


def due(prayers, now: datetime, lead_minutes: float, seen=()) -> list[Prayer]:
    """Prayers to remind about right now: inside the lead, not yet reminded.

    The same rule the meeting reminder uses, so "5 minutes before" means the
    same thing on the card as it does in the calendar.
    """
    ready = []
    for prayer in prayers:
        minutes = prayer.minutes_until(now)
        if 0 <= minutes <= lead_minutes and prayer.key not in seen:
            ready.append(prayer)
    return ready


# --- Alexa routine triggers ------------------------------------------------
def hook_for(name: str, hooks) -> str:
    """The trigger URL for one prayer.

    Friday's Jumuah stands in for Dhuhr, so it borrows Dhuhr's trigger unless
    it has been given one of its own -- otherwise the one prayer a week that
    is most attended would be the one that silently did nothing.
    """
    hooks = hooks or {}
    url = str(hooks.get(name) or "").strip()
    if not url and name == "Jumuah":
        url = str(hooks.get("Dhuhr") or "").strip()
    return url


def hooks_due(prayers, now: datetime, lead_minutes: float, hooks, seen=(),
              kind: str = "routine") -> list:
    """(prayer, value) for the entries whose moment has just come.

    A window rather than a threshold: the clock looks every second, but a PC
    that was asleep, or busy, must not fire a routine long after the fact.

    The value is whatever the table holds -- a trigger URL for a routine, a
    file or address for the speaker -- so one lookup serves both, `kind`
    only keeping their "already done" marks apart.
    """
    ready = []
    for item in prayers:
        url = hook_for(item.name, hooks)
        if not url or hook_key(item, kind) in seen:
            continue
        late = (now - (item.iqama - timedelta(minutes=lead_minutes))).total_seconds()
        if 0 <= late <= HOOK_GRACE_S:
            ready.append((item, url))
    return ready


def hook_key(item, kind: str = "routine") -> str:
    """Stable per prayer per day per kind, so neither fires twice.

    The prefix keeps a prayer's routine apart from its cast: both are due at
    their own moment and one must not mark the other as done. prune_keys
    reads the date off the end, so the prefix is free to say anything.
    """
    return "%s:%s" % (kind, item.key)


def check_hook(url: str) -> str:
    """"" when the address could be a trigger URL, else what is wrong with it.

    The trigger skills all hand out a plain https link; the commonest mistake
    is pasting the skill's page instead of the link it generated.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")):
        return "A trigger address starts with https:// -- that does not."
    if " " in url:
        return "That address has a space in it; paste the whole link."
    return ""


def fire_detail(url: str, timeout: float = HOOK_TIMEOUT, opener=None) -> tuple[str, str]:
    """(problem, reply): "" for problem when the call worked, and the start
    of whatever the trigger service said back, for the log.

    Never raises: a routine that cannot be reached is worth a line in the
    settings window and nothing more.
    """
    problem = check_hook(url)
    if problem:
        return problem, ""
    request = urllib.request.Request(url, headers={"User-Agent": HOOK_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            code = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 200)
            reply = _snippet(response)
    except Exception as exc:
        return _reason(exc), ""
    if code >= 400:
        return "the trigger answered %d" % code, reply
    return "", reply


def fire(url: str, timeout: float = HOOK_TIMEOUT, opener=None) -> str:
    """Call one trigger URL. "" when it worked, else what went wrong."""
    return fire_detail(url, timeout, opener)[0]


def fire_with_retries(url: str, gaps=HOOK_RETRY_GAPS, sleep=time.sleep,
                      attempt=fire_detail) -> tuple[str, int, str]:
    """(problem, tries, reply) -- the call, and up to len(gaps) more if it
    fails, all inside the grace window so a retry can never land late.

    A bad address is not retried: waiting will not make it a good one.
    """
    problem, reply, tries = "", "", 0
    for gap in (0.0,) + tuple(gaps):
        if gap:
            sleep(gap)
        tries += 1
        problem, reply = attempt(url)
        if not problem:
            return "", tries, reply
        if check_hook(url):
            break
    return problem, tries, reply


def next_hook(prayers, now: datetime, lead_minutes: float, hooks):
    """(prayer, moment) for the next routine that will fire, or None."""
    best = None
    for item in prayers:
        if not hook_for(item.name, hooks):
            continue
        moment = item.iqama - timedelta(minutes=lead_minutes)
        if moment > now and (best is None or moment < best[1]):
            best = (item, moment)
    return best


def prune_keys(keys, now: datetime, keep_hours: float = 36.0) -> set:
    """The remembered "already done" keys, minus those long past.

    Keys end in the moment they were for. Trimming them by count, as this
    used to, could drop one for a prayer still inside its window and let it
    fire twice; by date it cannot.
    """
    cutoff = now - timedelta(hours=keep_hours)
    kept = set()
    for key in keys:
        stamp = _key_time(key)
        if stamp is None or stamp >= cutoff:
            kept.add(key)
    return kept


def _key_time(key) -> datetime | None:
    text = str(key)
    for size, fmt in ((19, "%Y-%m-%dT%H:%M:%S"), (16, "%Y-%m-%dT%H:%M")):
        try:
            return datetime.strptime(text[-size:], fmt)
        except ValueError:
            continue
    return None


def _snippet(response) -> str:
    try:
        body = response.read(400)
    except Exception:
        return ""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    return " ".join(str(body).split())[:160]


# --- the cached copy -------------------------------------------------------
def cache_path(base_dir: str) -> str:
    return os.path.join(base_dir, CACHE_NAME)


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path: str, text: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        log.warning("Could not save the prayer calendar to %s", path, exc_info=True)


def age_hours(path: str, now: float | None = None) -> float | None:
    """How long ago the cached copy was written, or None if there isn't one."""
    try:
        return max(0.0, ((now or time.time()) - os.path.getmtime(path)) / 3600.0)
    except OSError:
        return None


def _ago(hours: float) -> str:
    if hours < 1.5:
        return "just now"
    if hours < 36:
        return "%d hours ago" % round(hours)
    return "%d days ago" % round(hours / 24)


def load(base_dir: str, url: str = "", now: datetime | None = None,
         force: bool = False, fetcher=None) -> tuple[list[Prayer], str]:
    """(prayers, status) -- from the saved copy while it is fresh, from the
    feed when it is not, and from the saved copy again when the feed cannot
    be reached. `status` is one plain sentence for the settings page."""
    now = now or datetime.now()
    url = (url or "").strip() or WATERLOO_MASJID_ICS
    fetcher = fetcher or _fetcher_for(url)
    path = cache_path(base_dir)
    text = _read(path)
    age = age_hours(path)
    stale = age is None or age >= REFRESH_HOURS
    note = ""

    if force or not text or stale:
        try:
            fetched = fetcher(url)
        except Exception as exc:
            if not text:
                return [], "Could not read the prayer times: %s" % _reason(exc)
            note = "Could not reach the calendar; using the copy saved %s." % _ago(age or 0.0)
            log.info("Prayer times: feed unreachable (%s); using the cache", _reason(exc))
        else:
            if fetched.strip():
                _write(path, fetched)
                text, age = fetched, 0.0
            elif not text:
                return [], "The prayer calendar came back empty."

    prayers = parse(text, now - timedelta(hours=18), now + timedelta(days=WINDOW_DAYS))
    if not prayers:
        return [], note or "No prayer times found in that calendar."
    if note:
        return prayers, note
    last = max(prayer.iqama for prayer in prayers)
    return prayers, "%s · updated %s · times through %s." % (
        source_label(url, text), _ago(age or 0.0), last.strftime("%a %d %b"),
    )


def _fetcher_for(url: str):
    """The reader for this address.

    A mosque website is tried as a timetable API first and as a calendar
    afterwards, because an ICS feed does not always end in .ics -- so the
    fallback costs one failed probe and saves a confusing error.
    """
    if dpt.looks_like_ics(url):
        return ics.fetch

    def fetch_site(address: str) -> str:
        try:
            return dpt.fetch(address)
        except dpt.NoApi as no_api:
            # No timetable API there at all, so it may still be a calendar.
            # A site that has one but nothing usable in it keeps its own
            # message, which says more than a calendar parser's would.
            try:
                return ics.fetch(address)
            except Exception:
                # Neither worked. "that address did not return a calendar" is
                # true but unhelpful for someone who pasted a mosque's
                # website; the first message tells them what to do next.
                raise dpt.NoApi(str(no_api)) from None

    return fetch_site


def source_label(url: str, text: str) -> str:
    """Whose times these are, for the one sentence the settings page shows."""
    if not (url or "").strip() or url.strip() == WATERLOO_MASJID_ICS:
        return SOURCE_NAME
    return dpt.source_name(text) or urllib.parse.urlsplit(url).netloc or SOURCE_NAME


def _reason(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:90]


# --- keeping the times current ---------------------------------------------
def checked_at(base_dir: str) -> datetime | None:
    """When the calendar was last fetched successfully: the saved copy is
    only ever rewritten by a fetch that worked."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(cache_path(base_dir)))
    except OSError:
        return None


def last_nightly(now: datetime) -> datetime:
    """The most recent nightly check slot at or before `now`."""
    slot = now.replace(hour=NIGHTLY_AT[0], minute=NIGHTLY_AT[1], second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def refresh_due(now: datetime, last_ok, last_try, have_prayers: bool) -> bool:
    """Whether to fetch the masjid's calendar again right now.

    Once a night after 02:30; straight away after a night the PC was off or
    asleep through that slot; every few minutes while there are no times at
    all; and never more often than the retry gap, so a feed that is down is
    asked politely rather than hammered.
    """
    gap = EMPTY_RETRY_MINUTES if not have_prayers else REFRESH_RETRY_MINUTES
    if last_try is not None and now - last_try < timedelta(minutes=gap):
        return False
    if not have_prayers or last_ok is None:
        return True
    return last_ok < last_nightly(now)


def diff(old, new, now: datetime | None = None, days: int = 7) -> list[str]:
    """What moved between two readings of the calendar, in plain words.

    Only prayers present in both are compared: each night's reading reaches
    one day further, and a day that was simply not loaded before is not a
    change anyone needs telling about.
    """
    now = now or datetime.now()
    start = now.date()
    end = start + timedelta(days=days)
    before = {(p.iqama.date(), p.name): p for p in old if start <= p.iqama.date() < end}
    changes = []
    for item in new:
        was = before.get((item.iqama.date(), item.name))
        if was is not None and was.iqama != item.iqama:
            changes.append("%s %s: %s \u2192 %s" % (
                item.name, item.iqama.strftime("%a %d %b"), was.time_text(), item.time_text(),
            ))
    return changes


# How often the loop is even allowed to consider a refresh. The check itself
# is cheap, but it reads the cache file's timestamp, and once every thirty
# seconds is plenty for something that happens nightly.
CHECK_EVERY = timedelta(seconds=30)


class Loader:
    """Keeps the iqama times current, on a worker, for either host.

    Both hosts need the same five things around load(): a read at startup, the
    nightly check at 02:30, a catch-up within a minute of starting after a
    night the PC was off or asleep, a retry every few minutes while there are
    no times at all, and a "fetch it again now" for the settings page.

    A timer is exactly what does not survive a sleep or a bad start, so none
    of this is on one: poll() is called from the host's own loop and decides
    for itself whether anything is due.

    All that differs between Tk and Qt is the hop back to the UI thread, which
    is what `to_ui` is for -- it is handed a callable and must run it there.
    """

    def __init__(self, settings: dict, base_dir, to_ui, on_ready) -> None:
        self.s = settings
        self.base_dir = base_dir          # str, or a callable returning one
        self.to_ui = to_ui                # to_ui(fn): run fn on the UI thread
        self.on_ready = on_ready          # on_ready(prayers, status)
        self.prayers: list = []
        self.status = ""
        self.loading = False
        self.last_try: datetime | None = None
        self.next_check: datetime | None = None

    def _dir(self) -> str:
        return self.base_dir() if callable(self.base_dir) else self.base_dir

    # --- the host's entry points -------------------------------------------
    def start(self) -> None:
        """Read the times at startup, fetching fresh ones when the saved copy
        predates last night's check."""
        stale = refresh_due(datetime.now(), checked_at(self._dir()), None, True)
        self.refresh(force=stale)

    def poll(self, now: datetime) -> None:
        """The nightly look at the masjid's calendar, and every catch-up."""
        if not self.s.get("prayer_enabled", True) or self.loading:
            return
        if self.next_check is not None and now < self.next_check:
            return
        self.next_check = now + CHECK_EVERY
        if refresh_due(now, checked_at(self._dir()), self.last_try, bool(self.prayers)):
            log.info("Checking %s's calendar for changes", SOURCE_NAME)
            self.refresh(force=True)

    def refresh(self, force: bool = True) -> None:
        """Read the times on a worker: a feed that is slow, or a masjid whose
        site is down, must never hold up the clock's own loop."""
        if not self.s.get("prayer_enabled", True):
            if self.prayers or self.status:
                self.prayers, self.status = [], ""
                self._to_ui(lambda: self.on_ready([], ""))
            return
        if self.loading:
            return
        self.loading = True
        self.last_try = datetime.now()
        url = str(self.s.get("prayer_ics_url", "") or "")
        base = self._dir()

        def work() -> None:
            found, status = None, ""
            try:
                found, status = load(base, url, force=force)
            except Exception:
                log.warning("Could not load the prayer times", exc_info=True)
            finally:
                # Always report back, even empty-handed: a load that never
                # says it has finished would block every later one.
                if not self._to_ui(lambda: self._done(found, status)):
                    self.loading = False

        threading.Thread(target=work, name="floating-clock-prayers",
                         daemon=True).start()

    # --- back on the UI thread ---------------------------------------------
    def _done(self, found, status: str) -> None:
        self.loading = False
        if found is None:
            return
        previous = list(self.prayers or ())
        changes = diff(previous, found) if previous and found else []
        for line in changes:
            log.info("Prayer time changed: %s", line)
        if changes:
            status = "%s  Changed: %s." % (status, "; ".join(changes[:3]))
        self.prayers, self.status = found, status
        log.info("Prayer times: %d loaded -- %s", len(found), status)
        self.on_ready(found, status)

    def _to_ui(self, fn) -> bool:
        """True when the UI thread took it; False when the window has gone."""
        try:
            self.to_ui(fn)
            return True
        except Exception:
            return False
