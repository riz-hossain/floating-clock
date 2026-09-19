"""CalDAV: find a person's calendars from their address, then read them.

The open door to iCloud, Zoho, Fastmail, Yahoo, Nextcloud and most other
calendar servers. Discovery follows RFC 6764: a `_caldavs._tcp` SRV record
or the `/.well-known/caldav` path leads to the current user's principal,
the principal names the calendar home, and the home lists the calendars.
Reading is a single REPORT per calendar for the window the clock cares
about, and the VEVENTs that come back go through the same ICS parser as a
Google feed.

Only an app password and the server address ever leave the machine, over
HTTPS. Error messages name the server, never the password.
"""

from __future__ import annotations

import base64
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from . import dnsq, ics

log = logging.getLogger(__name__)

USER_AGENT = "FloatingClock/1.0 (+calendar-sync)"
TIMEOUT = 20.0
MAX_REDIRECTS = 5
NS = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:caldav", "A": "http://apple.com/ns/ical/"}

PROPFIND_PRINCIPAL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>'
)
PROPFIND_HOME = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
    '<D:prop><C:calendar-home-set/></D:prop></D:propfind>'
)
PROPFIND_CALENDARS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav" '
    'xmlns:A="http://apple.com/ns/ical/">'
    '<D:prop><D:resourcetype/><D:displayname/><C:supported-calendar-component-set/>'
    '<A:calendar-color/></D:prop></D:propfind>'
)
REPORT_EVENTS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
    '<D:prop><D:getetag/><C:calendar-data/></D:prop>'
    '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">'
    '<C:time-range start="%s" end="%s"/></C:comp-filter></C:comp-filter></C:filter>'
    '</C:calendar-query>'
)


class CalDavError(Exception):
    """Discovery or reading failed; the message is safe to show."""


@dataclass(frozen=True)
class Calendar:
    name: str
    url: str          # absolute URL of the collection
    colour: str = ""  # "#rrggbb" when the server says


# --- transport -------------------------------------------------------------
class _Server:
    """One signed-in server: sends WebDAV verbs and follows redirects by hand,
    since urllib turns a redirected PROPFIND into a GET."""

    def __init__(self, username: str, password: str, opener=None) -> None:
        token = base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")
        self.headers = {
            "Authorization": "Basic " + token,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/xml; charset=utf-8",
        }
        self.opener = opener or self._open

    @staticmethod
    def _open(request):
        # A no-redirect opener: 3xx comes back as an HTTPError we read Location from.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None

        return urllib.request.build_opener(_NoRedirect).open(request, timeout=TIMEOUT)

    def request(self, method: str, url: str, body: str = "", depth: str = "0"):
        """(status, final_url, text). Raises CalDavError on 401/403/network."""
        for _hop in range(MAX_REDIRECTS + 1):
            headers = dict(self.headers, Depth=depth)
            request = urllib.request.Request(
                url, data=body.encode("utf-8") if body else None, headers=headers, method=method,
            )
            try:
                with self.opener(request) as response:
                    return response.status, response.geturl(), response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    target = exc.headers.get("Location")
                    if not target:
                        raise CalDavError("%s redirected nowhere" % _host(url)) from None
                    url = urllib.parse.urljoin(url, target)
                    continue
                if exc.code in (401, 403):
                    raise CalDavError("%s rejected the sign-in (%d): check the address "
                                      "and the app password" % (_host(url), exc.code)) from None
                if exc.code == 404:
                    raise CalDavError("nothing at %s (404)" % _host(url)) from None
                if exc.code == 405:
                    raise CalDavError("%s does not speak CalDAV here" % _host(url)) from None
                body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
                return exc.code, url, body_text
            except urllib.error.URLError as exc:
                raise CalDavError("could not reach %s (%s)" % (_host(url), exc.reason)) from None
            except CalDavError:
                raise
            except Exception as exc:
                raise CalDavError("could not talk to %s (%s)" % (_host(url), exc)) from None
        raise CalDavError("%s redirected too many times" % _host(url))


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


# --- XML -----------------------------------------------------------------------
def _parse(text: str):
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise CalDavError("the server sent something that is not CalDAV (%s)" % exc) from None


def _href(node) -> str:
    found = node.find("D:href", NS)
    return (found.text or "").strip() if found is not None else ""


def principal_href(text: str) -> str:
    """current-user-principal from a PROPFIND response, or ""."""
    root = _parse(text)
    node = root.find(".//D:current-user-principal", NS)
    return _href(node) if node is not None else ""


def home_href(text: str) -> str:
    root = _parse(text)
    node = root.find(".//C:calendar-home-set", NS)
    return _href(node) if node is not None else ""


def calendars_in(text: str, base: str) -> list[Calendar]:
    """Every VEVENT-capable calendar collection in a Depth:1 PROPFIND."""
    root = _parse(text)
    found: list[Calendar] = []
    for response in root.findall("D:response", NS):
        href = _href(response)
        if not href:
            continue
        is_calendar = response.find(".//D:resourcetype/C:calendar", NS) is not None
        if not is_calendar:
            continue
        components = response.findall(".//C:supported-calendar-component-set/C:comp", NS)
        if components and not any(c.get("name", "").upper() == "VEVENT" for c in components):
            continue
        name_node = response.find(".//D:displayname", NS)
        name = (name_node.text or "").strip() if name_node is not None else ""
        colour_node = response.find(".//A:calendar-color", NS)
        colour = (colour_node.text or "").strip()[:7] if colour_node is not None else ""
        url = urllib.parse.urljoin(base, href)
        found.append(Calendar(name or url.rstrip("/").rsplit("/", 1)[-1], url, colour))
    return found


def calendar_data(text: str) -> list[str]:
    """Every calendar-data blob in a REPORT response."""
    root = _parse(text)
    return [(node.text or "") for node in root.iter("{urn:ietf:params:xml:ns:caldav}calendar-data")
            if node.text and "BEGIN:VCALENDAR" in node.text.upper()]


# --- discovery -------------------------------------------------------------------
def starting_points(email: str, base: str = "", srv_lookup=dnsq.srv) -> list[str]:
    """Where to knock, best first: a known base, the SRV record, then the
    domain's well-known path and the usual host names."""
    domain = (email or "").rsplit("@", 1)[-1].strip().lower() if "@" in (email or "") else ""
    points: list[str] = []
    if base:
        points.append(base.rstrip("/") + "/.well-known/caldav")
    if domain:
        for host, port in srv_lookup("_caldavs._tcp", domain):
            points.append("https://%s%s/.well-known/caldav" % (host, "" if port in (0, 443) else ":%d" % port))
        for host in (domain, "caldav." + domain, "calendar." + domain, "mail." + domain):
            points.append("https://%s/.well-known/caldav" % host)
    seen: list[str] = []
    for point in points:
        if point not in seen:
            seen.append(point)
    return seen


def discover(email: str, password: str, base: str = "", server=None,
             srv_lookup=dnsq.srv) -> list[Calendar]:
    """The user's calendars, or raise CalDavError with a message worth showing."""
    server = server or _Server(email, password)
    errors: list[str] = []
    for point in starting_points(email, base, srv_lookup):
        try:
            _status, url, text = server.request("PROPFIND", point, PROPFIND_PRINCIPAL, "0")
            principal = principal_href(text)
            if not principal:
                errors.append("%s did not say who you are" % _host(url))
                continue
            principal_url = urllib.parse.urljoin(url, principal)
            _status, url, text = server.request("PROPFIND", principal_url, PROPFIND_HOME, "0")
            home = home_href(text)
            if not home:
                errors.append("%s has no calendar home" % _host(url))
                continue
            home_url = urllib.parse.urljoin(url, home)
            _status, url, text = server.request("PROPFIND", home_url, PROPFIND_CALENDARS, "1")
            calendars = calendars_in(text, url)
            if calendars:
                return calendars
            errors.append("%s has no calendars" % _host(url))
        except CalDavError as exc:
            errors.append(str(exc))
            if "rejected the sign-in" in str(exc):
                raise
    raise CalDavError(errors[0] if errors else "no calendar server was found for that address")


# --- reading ------------------------------------------------------------------------
def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_calendar(url: str, username: str, password: str, window_start: datetime,
                  window_end: datetime, source: str = "", server=None) -> list:
    """Events in the window from one calendar collection."""
    server = server or _Server(username, password)
    body = REPORT_EVENTS % (_stamp(window_start), _stamp(window_end))
    status, _final, text = server.request("REPORT", url, body, "1")
    if status >= 400:
        raise CalDavError("%s returned %d" % (_host(url), status))
    events: list = []
    for blob in calendar_data(text):
        try:
            events.extend(ics.to_events(blob, window_start, window_end, source))
        except ics.IcsError as exc:
            log.debug("skipping an unreadable event from %s: %s", _host(url), exc)
    return events
