"""Sign in with Google, and read Google calendars through the Calendar API.

Google accepts no password for calendar access, only its own sign-in page.
This is the desktop ("installed app") flow: the app opens the sign-in page in
the browser, listens on a loopback port for Google to send the user back
with a code, and swaps the code for tokens. PKCE ties the two halves
together so nothing else on the machine can use the code. The refresh
token, which is what lets the clock keep reading, goes into the vault with
the calendar; the app never sees a password.

Needs a client id and secret from a one-time registration in the Google
Cloud console (see SIGN-IN-SETUP.md). For a desktop app Google treats the
secret as non-confidential, so it can live in the settings file.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = "https://www.googleapis.com/auth/calendar.readonly openid email"
TIMEOUT = 20.0
SIGN_IN_TIMEOUT = 240.0
MAX_EVENTS = 250

DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>Floating Clock</title>
<body style="font-family:system-ui;background:#1c1d28;color:#e9e9f2;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center"><h1 style="font-weight:600">Signed in</h1>
<p style="color:#9a9ab0">You can close this window and go back to Floating Clock.</p></div>"""


class GoogleError(Exception):
    """Sign-in or reading failed; the message is safe to show."""


@dataclass(frozen=True)
class Calendar:
    id: str
    name: str
    colour: str = ""
    primary: bool = False


# --- PKCE and URLs -----------------------------------------------------------
def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str,
             login_hint: str = "") -> str:
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPES, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


# --- HTTP ---------------------------------------------------------------------
def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("ascii")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "FloatingClock/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise GoogleError("Google refused the token request (%d): %s" % (exc.code, detail)) from None
    except urllib.error.URLError as exc:
        raise GoogleError("could not reach Google (%s)" % exc.reason) from None


def _get_json(url: str, access_token: str) -> dict:
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + access_token, "User-Agent": "FloatingClock/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise GoogleError("the sign-in has expired: add the calendar again") from None
        raise GoogleError("Google returned %d" % exc.code) from None
    except urllib.error.URLError as exc:
        raise GoogleError("could not reach Google (%s)" % exc.reason) from None


# --- the sign-in ---------------------------------------------------------------
class _Catcher(http.server.BaseHTTPRequestHandler):
    """Receives Google's redirect on the loopback port."""

    result: dict = {}
    event: threading.Event | None = None

    def do_GET(self) -> None:   # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        _Catcher.result = {k: v[0] for k, v in query.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(DONE_PAGE.encode("utf-8"))
        if _Catcher.event is not None:
            _Catcher.event.set()

    def log_message(self, *_args) -> None:
        pass


def sign_in(client_id: str, client_secret: str, login_hint: str = "",
            open_browser=webbrowser.open, timeout: float = SIGN_IN_TIMEOUT) -> dict:
    """Run the browser sign-in. Returns the token record to keep:
    {refresh_token, access_token, expires_at, email}."""
    if not client_id:
        raise GoogleError("no Google client id is configured")
    server = http.server.HTTPServer(("127.0.0.1", 0), _Catcher)
    port = server.server_address[1]
    redirect_uri = "http://127.0.0.1:%d/" % port
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    _Catcher.result = {}
    _Catcher.event = threading.Event()
    thread = threading.Thread(target=server.serve_forever, name="floating-clock-oauth", daemon=True)
    thread.start()
    try:
        open_browser(auth_url(client_id, redirect_uri, state, challenge, login_hint))
        if not _Catcher.event.wait(timeout):
            raise GoogleError("the sign-in was not completed in time")
    finally:
        server.shutdown()
        server.server_close()
    answer = _Catcher.result
    if answer.get("state") != state:
        raise GoogleError("the sign-in response did not match the request")
    if "error" in answer:
        raise GoogleError("Google said: %s" % answer["error"])
    code = answer.get("code")
    if not code:
        raise GoogleError("Google sent no code back")
    return exchange(client_id, client_secret, code, verifier, redirect_uri)


def exchange(client_id: str, client_secret: str, code: str, verifier: str,
             redirect_uri: str) -> dict:
    tokens = _post_form(TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "code_verifier": verifier, "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    record = _record(tokens)
    if not record.get("refresh_token"):
        raise GoogleError("Google gave no refresh token; remove the app's access at "
                          "myaccount.google.com/permissions and sign in again")
    record["email"] = _email_of(tokens, record["access_token"])
    return record


def _record(tokens: dict, refresh_token: str = "") -> dict:
    expires_in = int(tokens.get("expires_in", 3600))
    return {
        "refresh_token": tokens.get("refresh_token") or refresh_token,
        "access_token": tokens.get("access_token", ""),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)).isoformat(),
    }


def _email_of(tokens: dict, access_token: str) -> str:
    id_token = tokens.get("id_token", "")
    if id_token.count(".") == 2:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        try:
            return str(json.loads(base64.urlsafe_b64decode(payload)).get("email", ""))
        except Exception:
            pass
    try:
        return str(_get_json(USERINFO_URL, access_token).get("email", ""))
    except GoogleError:
        return ""


def refresh(client_id: str, client_secret: str, record: dict) -> dict:
    """A fresh access token when the stored one has run out."""
    try:
        expires_at = datetime.fromisoformat(record.get("expires_at", ""))
    except ValueError:
        expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    if record.get("access_token") and expires_at > datetime.now(timezone.utc):
        return record
    tokens = _post_form(TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": record["refresh_token"], "grant_type": "refresh_token",
    })
    fresh = _record(tokens, refresh_token=record["refresh_token"])
    fresh["email"] = record.get("email", "")
    return fresh


# --- reading ---------------------------------------------------------------------
def list_calendars(access_token: str) -> list[Calendar]:
    data = _get_json(API + "/users/me/calendarList?minAccessRole=reader", access_token)
    found = []
    for item in data.get("items", []):
        if item.get("hidden"):
            continue
        found.append(Calendar(
            id=item.get("id", ""), name=item.get("summaryOverride") or item.get("summary", ""),
            colour=item.get("backgroundColor", ""), primary=bool(item.get("primary")),
        ))
    found.sort(key=lambda c: (not c.primary, c.name.lower()))
    return found


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat()


def _local(value: dict) -> tuple[datetime, bool]:
    """A Google start/end object -> (naive local datetime, all_day)."""
    if "dateTime" in value:
        moment = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        return moment.astimezone().replace(tzinfo=None), False
    day = datetime.strptime(value["date"], "%Y-%m-%d")
    return day, True


def to_events(items: list, window_start: datetime, window_end: datetime, source: str,
              own_email: str = "") -> list:
    from .outlook import Event, event_domains, find_join_url

    own_domain = own_email.rsplit("@", 1)[-1].lower() if "@" in own_email else ""
    events = []
    for item in items:
        if item.get("status") == "cancelled":
            continue
        try:
            start, all_day = _local(item["start"])
            end, _ = _local(item["end"])
        except (KeyError, ValueError):
            continue
        if end <= window_start or start >= window_end:
            continue
        organizer = item.get("organizer", {}) or {}
        attendees = [a.get("email", "") for a in item.get("attendees", []) or []]
        join = item.get("hangoutLink", "")
        for entry in (item.get("conferenceData", {}) or {}).get("entryPoints", []) or []:
            if entry.get("entryPointType") == "video" and entry.get("uri"):
                join = entry["uri"]
                break
        if not join:
            join = find_join_url(item.get("location", "") or "", item.get("description", "") or "")
        events.append(Event(
            subject=item.get("summary") or "(no subject)", start=start, end=end,
            location=item.get("location", "") or "",
            organizer=organizer.get("displayName") or organizer.get("email", ""),
            all_day=all_day, join_url=join, entry_id=item.get("id", ""), source=source,
            domains=event_domains(organizer.get("email", ""), attendees, own_domain),
        ))
    return events


def read_calendar(access_token: str, calendar_id: str, window_start: datetime,
                  window_end: datetime, source: str = "", own_email: str = "") -> list:
    params = urllib.parse.urlencode({
        "singleEvents": "true", "orderBy": "startTime", "maxResults": MAX_EVENTS,
        "timeMin": _stamp(window_start), "timeMax": _stamp(window_end),
    })
    url = "%s/calendars/%s/events?%s" % (API, urllib.parse.quote(calendar_id, safe=""), params)
    data = _get_json(url, access_token)
    return to_events(data.get("items", []), window_start, window_end, source, own_email)
