"""Reads the next meetings from classic Outlook desktop over COM.

Kept self-contained so the clock installs as a standalone app, but the awkward
parts (locale-dependent Restrict literals, recurrence expansion) follow the
same approach as outlook_tracker/outlook/com_client.py in this repo.

Everything here returns plain dataclasses -- no COM object escapes the module,
and no COM call happens outside the poller thread that initialised the
apartment.
"""

from __future__ import annotations

import json
import logging
import sys
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

OL_FOLDER_CALENDAR = 9
# Items arrive in Start order, so a clip lands on the meetings still ahead --
# exactly the ones the panel is for. Room for a fortnight of a busy calendar.
ITEM_CAP = 300
ATTENDEE_CAP = 40
# How far ahead the organisation scan looks, and how often (in polls) it runs:
# an invite for a fortnight out should get its logo today, not the morning
# it enters the clock's 14-hour window.
DOMAIN_SCAN_DAYS = 14.0
DOMAIN_SCAN_EVERY = 15
# Mailbox providers: a meeting with someone @gmail.com says nothing about
# which organisation it belongs to.
FREEMAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "pm.me", "mail.com", "gmx.com",
    "gmx.de", "yandex.com", "zoho.com",
})
BODY_SCAN_CHARS = 4000
# The panel needs a wider view than the clock face. Ten meetings ahead can
# span a week on a light calendar, and the last two can sit the far side of
# a long weekend. Reading that much costs about half a second, once every
# couple of minutes on the poller thread, so the window is generous and the
# panel decides what is worth showing.
POLL_PAST_MINUTES = 4 * 24 * 60.0
POLL_AHEAD_HOURS = 7 * 24.0

JOIN_PATTERNS = (
    re.compile(r"https://teams\.microsoft\.com/l/meetup-join/\S+", re.I),
    re.compile(r"https://teams\.live\.com/meet/\S+", re.I),
    re.compile(r"https://[\w.-]*zoom\.us/j/\S+", re.I),
    re.compile(r"https://meet\.google\.com/[\w-]+", re.I),
    re.compile(r"https://[\w.-]*webex\.com/\S+", re.I),
)
# Outlook wraps links in angle brackets and HTML bodies in quotes.
TRAILING_JUNK = '>",\'  \t\r\n'


class CalendarUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Event:
    subject: str
    start: datetime
    end: datetime
    location: str = ""
    organizer: str = ""
    all_day: bool = False
    join_url: str = ""
    entry_id: str = ""
    # Which calendar this came from -- "" for Outlook, otherwise the id of the
    # configured feed, so the panel can badge the row with that org's mark.
    source: str = ""
    # Email domains of the other organisations in the meeting, organiser
    # first: what an Outlook meeting has instead of a source. See
    # event_domains().
    domains: tuple[str, ...] = ()

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def minutes_until(self, now: datetime | None = None) -> float:
        return (self.start - (now or datetime.now())).total_seconds() / 60.0

    def is_live(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        return self.start <= now < self.end


def _to_datetime(value) -> datetime | None:
    """A COM date as the naive local time Outlook itself displays.

    pywin32 hands COM dates over as datetimes whose fields are the local
    wall clock -- exactly what the calendar shows -- but stamped with a UTC
    tzinfo. Anything that honours that tzinfo (timestamp(), astimezone())
    therefore shifts every meeting by the UTC offset: a 9:00 meeting in
    Toronto came out as 5:00. Only the fields are trusted here.
    """
    if value is None:
        return None
    try:
        return datetime(
            value.year, value.month, value.day,
            value.hour, value.minute, value.second,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _restrict_literal(moment: datetime) -> str:
    """Format a datetime for an Outlook Restrict clause.

    Outlook parses these with the machine's regional settings, so a hard-coded
    US month/day order silently transposes dates on dd/MM locales and errors
    outright on some others. Ask Windows for the local format instead.

    Two details are load-bearing, and both fail silently -- Outlook returns an
    empty collection rather than an error when it cannot parse a literal:

    * TIME_NOSECONDS. The locale's default time format includes seconds, and
      "9:40:23 AM" is rejected outright where "9:40 AM" is accepted.
    * The UTC stamp. pywin32 converts a naive datetime from local time to UTC
      on its way to a SYSTEMTIME, which shifts the literal by the UTC offset
      and slides the whole query window. Declaring the value as UTC makes that
      conversion a no-op, so the wall-clock time we asked for is what Outlook
      sees.
    """
    try:
        import win32api

        stamped = moment.replace(tzinfo=timezone.utc)
        # 0x0001 = DATE_SHORTDATE; 0x0002 = TIME_NOSECONDS.
        return "%s %s" % (
            win32api.GetDateFormat(0, 0x0001, stamped),
            win32api.GetTimeFormat(0, 0x0002, stamped),
        )
    except Exception:
        return f"{moment:%m/%d/%Y %I:%M %p}"


def find_join_url(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        for pattern in JOIN_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0).rstrip(TRAILING_JUNK)
    return ""


def domain_of(address: str) -> str:
    """'Riz <riz@ZeuZ.ai>' -> 'zeuz.ai'; '' when there is no address in it."""
    address = (address or "").strip().lower()
    if "@" not in address:
        return ""
    host = address.rsplit("@", 1)[1].strip("<> \t\"'")
    host = host.split(">")[0].split(" ")[0]
    return host if "." in host else ""


def event_domains(organizer: str, attendees, own_domain: str = "") -> tuple[str, ...]:
    """The organisations in a meeting, by email domain, organiser first.

    Your own domain says nothing (every meeting has you in it), and neither
    does a mailbox provider, so both are dropped. Order is kept: the
    organiser's company is the meeting's company, and after that the first
    guest's is the best guess.
    """
    own = (own_domain or "").strip().lower()
    found: list[str] = []
    for address in [organizer, *attendees]:
        domain = domain_of(address)
        if not domain or domain == own or domain in FREEMAIL or domain in found:
            continue
        found.append(domain)
    return tuple(found)


def _smtp_address(entry) -> str:
    """The SMTP address behind an Outlook AddressEntry, or ""."""
    if entry is None:
        return ""
    try:
        kind = str(getattr(entry, "Type", "") or "").upper()
        if kind == "EX":
            # An Exchange entry's Address is an X.500 path; the SMTP one is
            # a hop further.
            user = entry.GetExchangeUser()
            if user is not None:
                return str(getattr(user, "PrimarySmtpAddress", "") or "")
        return str(getattr(entry, "Address", "") or "")
    except Exception:
        return ""


def _item_domains(item, own_domain: str) -> tuple[str, ...]:
    organizer = ""
    try:
        organizer = _smtp_address(item.GetOrganizer())
    except Exception:
        pass
    attendees: list[str] = []
    try:
        recipients = item.Recipients
        for index in range(1, min(int(recipients.Count), ATTENDEE_CAP) + 1):
            attendees.append(_smtp_address(recipients.Item(index).AddressEntry))
    except Exception:
        pass
    return event_domains(organizer, attendees, own_domain)


def fetch_domains(days_ahead: float = DOMAIN_SCAN_DAYS) -> list[str]:
    """The leading organisation of every meeting in the next `days_ahead`.

    Reads only the people in each item, not the body, so it stays cheap
    enough to run over two weeks of calendar. Must run on a COM-initialised
    thread. Ordered by first appearance, no repeats.
    """
    import win32com.client

    app = win32com.client.Dispatch("Outlook.Application")
    namespace = app.GetNamespace("MAPI")
    own_domain = ""
    try:
        own_domain = domain_of(_smtp_address(namespace.CurrentUser.AddressEntry))
    except Exception:
        pass
    now = datetime.now()
    items = namespace.GetDefaultFolder(OL_FOLDER_CALENDAR).Items
    items.Sort("[Start]")
    items.IncludeRecurrences = True
    items = items.Restrict(
        "[Start] >= '%s' AND [Start] <= '%s'"
        % (_restrict_literal(now), _restrict_literal(now + timedelta(days=days_ahead)))
    )
    found: list[str] = []
    seen = 0
    for item in items:
        seen += 1
        if seen > ITEM_CAP:
            break
        try:
            domains = _item_domains(item, own_domain)
        except Exception:
            continue
        if domains and domains[0] not in found:
            found.append(domains[0])
    return found


def fetch_events(
    hours_ahead: float = 14.0,
    include_past_minutes: float = 240.0,
    keep_finished: bool = False,
) -> list[Event]:
    """Read calendar items around now. Must run on a COM-initialised thread.

    Finished meetings are dropped unless `keep_finished` is set: the clock
    face only ever wants what is current or ahead, while the meetings panel
    also lists the ones that have just wrapped up.
    """
    try:
        import win32com.client
    except ImportError as exc:
        raise CalendarUnavailable("pywin32 is not installed (pip install pywin32)") from exc

    try:
        app = win32com.client.Dispatch("Outlook.Application")
        namespace = app.GetNamespace("MAPI")
    except Exception as exc:
        raise CalendarUnavailable(
            "Could not connect to Outlook. The classic Outlook desktop app must "
            "be installed -- the new Outlook does not support COM automation."
        ) from exc

    now = datetime.now()
    window_start = now - timedelta(minutes=include_past_minutes)
    window_end = now + timedelta(hours=hours_ahead)

    own_domain = ""
    try:
        own_domain = domain_of(_smtp_address(namespace.CurrentUser.AddressEntry))
    except Exception:
        log.debug("Could not read the signed-in address", exc_info=True)

    try:
        items = namespace.GetDefaultFolder(OL_FOLDER_CALENDAR).Items
        # Order matters: sort by Start ascending first, then ask for
        # recurrences, or occurrences of recurring meetings never materialise.
        items.Sort("[Start]")
        items.IncludeRecurrences = True
        # Filter on [Start] alone. Restricting on [End] as well returns nothing
        # once IncludeRecurrences is on, so meetings already under way are
        # caught by reaching back with window_start and filtering in Python.
        items = items.Restrict(
            "[Start] >= '%s' AND [Start] <= '%s'"
            % (_restrict_literal(window_start), _restrict_literal(window_end))
        )
    except Exception as exc:
        raise CalendarUnavailable("Could not read the Outlook calendar: %s" % exc) from exc

    events: list[Event] = []
    for item in items:
        if len(events) >= ITEM_CAP:
            break
        try:
            start = _to_datetime(getattr(item, "Start", None))
            end = _to_datetime(getattr(item, "End", None))
            if start is None or end is None:
                continue
            subject = str(getattr(item, "Subject", "") or "(no subject)")
            location = str(getattr(item, "Location", "") or "")
            body = str(getattr(item, "Body", "") or "")[:BODY_SCAN_CHARS]
            events.append(
                Event(
                    subject=subject,
                    start=start,
                    end=end,
                    location=location,
                    organizer=str(getattr(item, "Organizer", "") or ""),
                    all_day=bool(getattr(item, "AllDayEvent", False)),
                    join_url=find_join_url(location, body),
                    entry_id=str(getattr(item, "EntryID", "") or ""),
                    domains=_item_domains(item, own_domain),
                )
            )
        except Exception:
            log.warning("Skipping unreadable calendar item", exc_info=True)

    # Reaching back caught long meetings still under way; drop the ones that
    # have already finished unless the caller asked to keep them.
    if not keep_finished:
        events = [e for e in events if e.end > now]
    events.sort(key=lambda e: e.start)
    return events


class CalendarPoller(threading.Thread):
    """Polls Outlook on its own COM apartment and publishes plain snapshots.

    COM is apartment-threaded: the objects created here can only be touched
    here, so the thread owns every call and hands results back over a queue.
    """

    def __init__(
        self,
        interval: float = 120.0,
        hours_ahead: float = POLL_AHEAD_HOURS,
        past_minutes: float = POLL_PAST_MINUTES,
        use_outlook: bool | None = None,
    ) -> None:
        super().__init__(daemon=True, name="floating-clock-calendar")
        self.interval = interval
        # Outlook is a Windows COM server; elsewhere the poller reads only the
        # configured feeds and never touches COM.
        self.use_outlook = (sys.platform == "win32") if use_outlook is None else use_outlook
        self.hours_ahead = hours_ahead
        self.past_minutes = past_minutes
        self.results: queue.Queue[tuple[list[Event], str]] = queue.Queue()
        # Organisations seen across the next fortnight, refreshed every
        # DOMAIN_SCAN_EVERY polls; the owner turns them into marks.
        self.domains: queue.Queue[list[str]] = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.enabled = True
        # Extra ICS feeds, as orgs.Org records. Replaced wholesale by the UI;
        # a plain attribute swap is atomic enough for a list this small.
        self.feeds: list = []

    def refresh_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        if not self.use_outlook:
            self._run_feeds_only()
            return
        try:
            import pythoncom
        except ImportError:
            self.results.put(([], "pywin32 is not installed (pip install pywin32)"))
            return

        pythoncom.CoInitialize()
        try:
            failures = 0
            polls = 0
            while not self._stop.is_set():
                if self.enabled:
                    events, problems, failed = self._collect()
                    failures = failures + 1 if failed else 0
                    self.results.put((events, "; ".join(problems)))
                    if not failed and self.use_outlook and polls % DOMAIN_SCAN_EVERY == 0:
                        try:
                            self.domains.put(fetch_domains())
                        except Exception:
                            log.debug("Organisation scan failed", exc_info=True)
                    polls += 1
                # Back off when Outlook is unreachable so we are not hammering
                # a COM server that is closed or mid-restart.
                delay = self.interval * min(8, 2 ** failures) if failures else self.interval
                self._wake.wait(timeout=delay)
                self._wake.clear()
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


    def _run_feeds_only(self) -> None:
        """The loop without COM: feeds only, no apartment to initialise."""
        while not self._stop.is_set():
            if self.enabled:
                events, problems, _failed = self._collect()
                self.results.put((events, "; ".join(problems)))
            self._wake.wait(timeout=self.interval)
            self._wake.clear()

    def _collect(self) -> tuple[list[Event], list[str], bool]:
        """Gather every source. One failing must not silence the others.

        A single try around the lot would mean an Outlook that is closed hides
        the Google feeds too, and the clock would show an empty day when it
        actually knows about four meetings.
        """
        events: list[Event] = []
        problems: list[str] = []
        outlook_failed = False
        if not self.use_outlook:
            events, problems = self._feeds(events, problems)
            events.sort(key=lambda event: event.start)
            return events, problems, False
        try:
            # Finished meetings are kept: the card filters them out for
            # itself, and the panel needs the history.
            events.extend(
                fetch_events(self.hours_ahead, self.past_minutes, keep_finished=True)
            )
        except CalendarUnavailable as exc:
            outlook_failed = True
            problems.append(str(exc))
        except Exception as exc:  # COM can fail in creative ways
            outlook_failed = True
            log.warning("Calendar poll failed", exc_info=True)
            problems.append("Outlook error: %s" % exc)

        events, problems = self._feeds(events, problems)
        events.sort(key=lambda event: event.start)
        return events, problems, outlook_failed

    def _feeds(self, events: list, problems: list) -> tuple[list, list]:
        if self.feeds:
            from . import ics

            now = datetime.now()
            window_start = now - timedelta(minutes=self.past_minutes)
            window_end = now + timedelta(hours=self.hours_ahead)
            for feed in self.feeds:
                if not getattr(feed, "enabled", True):
                    continue
                caldav_url = getattr(feed, "caldav_url", "")
                ics_url = getattr(feed, "ics_url", "")
                if getattr(feed, "kind", "") == "caldav" and caldav_url:
                    events.extend(self._read_caldav(feed, window_start, window_end, problems))
                    continue
                if getattr(feed, "kind", "") == "google" and caldav_url:
                    events.extend(self._read_google(feed, window_start, window_end, problems))
                    continue
                if not ics_url:
                    continue
                try:
                    events.extend(ics.read_calendar(
                        ics_url, window_start, window_end, source=feed.id
                    ))
                except ics.IcsError as exc:
                    # Named by calendar, never by address: the URL is a secret.
                    problems.append("%s: %s" % (feed.name, exc))
                except Exception as exc:
                    log.warning("Feed %s failed", feed.id, exc_info=True)
                    problems.append("%s: could not be read" % feed.name)
        return events, problems

    def _read_google(self, feed, window_start, window_end, problems: list) -> list:
        """One Google calendar, through the tokens the sign-in left in the vault.

        The calendar id rides in caldav_url; the refresh token is renewed on
        its own and written back so the next poll starts with a live one.
        """
        from . import google_oauth, settings as cfg, vault

        stored = vault.read(feed.id)
        if stored is None:
            problems.append("%s: its sign-in is missing -- add the calendar again" % feed.name)
            return []
        username, raw = stored
        try:
            record = json.loads(raw)
        except ValueError:
            problems.append("%s: its sign-in is unreadable -- add the calendar again" % feed.name)
            return []
        client = getattr(self, "google_client", None) or (
            cfg.load().get("google_client_id", ""), cfg.load().get("google_client_secret", ""))
        try:
            fresh = google_oauth.refresh(client[0], client[1], record)
            if fresh is not record:
                vault.store(feed.id, username, json.dumps(fresh))
            return google_oauth.read_calendar(
                fresh["access_token"], feed.caldav_url, window_start, window_end,
                source=feed.id, own_email=fresh.get("email") or username,
            )
        except google_oauth.GoogleError as exc:
            problems.append("%s: %s" % (feed.name, exc))
        except Exception:
            log.warning("Google calendar %s failed", feed.id, exc_info=True)
            problems.append("%s: could not be read" % feed.name)
        return []

    @staticmethod
    def _read_caldav(feed, window_start, window_end, problems: list) -> list:
        """One CalDAV calendar, signed in with the password from the vault."""
        from . import caldav, vault

        stored = vault.read(feed.id)
        if stored is None:
            problems.append("%s: its password is missing -- add the calendar again" % feed.name)
            return []
        username, password = stored
        try:
            return caldav.read_calendar(
                feed.caldav_url, feed.account or username, password,
                window_start, window_end, source=feed.id,
            )
        except caldav.CalDavError as exc:
            problems.append("%s: %s" % (feed.name, exc))
        except Exception:
            log.warning("CalDAV calendar %s failed", feed.id, exc_info=True)
            problems.append("%s: could not be read" % feed.name)
        return []


def describe_ago(minutes: float) -> str:
    """Human phrasing for how long ago a meeting finished."""
    minutes = max(0.0, minutes)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return "%dm ago" % round(minutes)
    hours, mins = divmod(int(round(minutes)), 60)
    if hours < 24:
        return "%dh %dm ago" % (hours, mins) if mins else "%dh ago" % hours
    return "%dd ago" % (hours // 24)


def describe_gap(minutes: float) -> str:
    """Human phrasing for how far away a meeting is."""
    if minutes < 0:
        return "now"
    if minutes < 1:
        return "in <1m"
    if minutes < 60:
        return "in %dm" % round(minutes)
    hours, mins = divmod(int(round(minutes)), 60)
    if hours < 24:
        return "in %dh %dm" % (hours, mins) if mins else "in %dh" % hours
    return "in %dd" % (hours // 24)
