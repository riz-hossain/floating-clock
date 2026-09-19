"""From an email address to a calendar provider, and what it takes to connect.

Outlook's "just type your address" trick is two lookups: the domain itself
for the big consumer providers, and the domain's MX records for hosted mail
(a company on Google Workspace has google.com mail servers). Each provider
then has one of three doors:

* caldav    -- the open standard. An app password and a server address,
               which we can discover. Works today with nothing from anyone.
* google    -- OAuth only. Needs the app registered with Google; until then
               the calendar's secret iCal address is the way in.
* microsoft -- no CalDAV. Outlook on this PC is read directly; sign-in
               through Microsoft needs the app registered with them.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import dnsq


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    kind: str                # caldav | google | microsoft | unknown
    domain: str = ""         # the provider's own site, for its logo
    caldav_base: str = ""    # where discovery starts, "" to discover from the domain
    password_hint: str = ""  # where the user finds an app password


# Calendar's sharing settings in the Admin console; the number is Google's
# id for the Calendar service, the same for every organisation.
ADMIN_SHARING_URL = "https://admin.google.com/ac/appsettings/435070579839/sharing"
ADMIN_HELP = (
    "If you are the Workspace admin, you can allow it at admin.google.com/ac/appsettings/435070579839/sharing: click External sharing options for primary calendars, choose Share all information but outsiders cannot change calendars, and Save. The secret address then appears on the calendar page within a few minutes."
)

PROVIDERS = {
    "google": Provider(
        "google", "Google Calendar", "google", "google.com",
        password_hint="Google accepts no password for calendars, only its own "
                      "sign-in page. Without that, paste the calendar's secret iCal "
                      "address: on the calendar's settings page, the last section, "
                      "Integrate calendar, second box. If it is missing, a Workspace "
                      "admin has hidden it. %s" % ADMIN_HELP,
    ),
    "microsoft": Provider(
        "microsoft", "Microsoft 365 / Outlook.com", "microsoft", "microsoft.com",
        password_hint="Outlook on this PC is read automatically. Sign-in through "
                      "Microsoft is coming; Outlook.com can also publish a "
                      "calendar as an iCal link.",
    ),
    "icloud": Provider(
        "icloud", "iCloud Calendar", "caldav", "icloud.com",
        caldav_base="https://caldav.icloud.com",
        password_hint="Use an app-specific password from appleid.apple.com "
                      "(Sign-In and Security → App-Specific Passwords).",
    ),
    "zoho": Provider(
        "zoho", "Zoho Calendar", "caldav", "zoho.com",
        caldav_base="https://calendar.zoho.com",
        password_hint="Use an application-specific password from Zoho Accounts "
                      "(Security → App Passwords).",
    ),
    "fastmail": Provider(
        "fastmail", "Fastmail", "caldav", "fastmail.com",
        caldav_base="https://caldav.fastmail.com",
        password_hint="Use an app password from Fastmail Settings → Privacy & "
                      "Security → Integrations, with calendar access.",
    ),
    "yahoo": Provider(
        "yahoo", "Yahoo Calendar", "caldav", "yahoo.com",
        caldav_base="https://caldav.calendar.yahoo.com",
        password_hint="Use an app password from Yahoo Account Security → "
                      "Generate app password.",
    ),
    "aol": Provider(
        "aol", "AOL Calendar", "caldav", "aol.com",
        caldav_base="https://caldav.aol.com",
        password_hint="Use an app password from AOL Account Security.",
    ),
    "unknown": Provider(
        "unknown", "Your calendar server", "unknown",
        password_hint="If your provider supports CalDAV, an app password (or "
                      "your password) lets the clock find your calendars.",
    ),
}

_BY_DOMAIN = {
    "gmail.com": "google", "googlemail.com": "google",
    "outlook.com": "microsoft", "hotmail.com": "microsoft", "live.com": "microsoft",
    "msn.com": "microsoft", "hotmail.co.uk": "microsoft", "outlook.co.uk": "microsoft",
    "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud",
    "zoho.com": "zoho", "zohomail.com": "zoho", "zohomail.eu": "zoho", "zoho.eu": "zoho",
    "zohomail.in": "zoho", "zoho.in": "zoho",
    "fastmail.com": "fastmail", "fastmail.fm": "fastmail",
    "yahoo.com": "yahoo", "ymail.com": "yahoo", "yahoo.co.uk": "yahoo", "yahoo.ca": "yahoo",
    "aol.com": "aol",
}

# Hosted mail: the MX host says who runs the mailbox, whatever the domain.
_BY_MX = (
    ("google.com", "google"), ("googlemail.com", "google"),
    ("outlook.com", "microsoft"), ("protection.outlook.com", "microsoft"),
    ("zoho.com", "zoho"), ("zohomail.com", "zoho"), ("zoho.eu", "zoho"), ("zoho.in", "zoho"),
    ("icloud.com", "icloud"), ("messagingengine.com", "fastmail"), ("fastmail.com", "fastmail"),
    ("yahoodns.net", "yahoo"),
)


@dataclass(frozen=True)
class Detection:
    email: str
    domain: str
    provider: Provider
    mx: tuple[str, ...] = ()
    hosted: bool = False     # the provider was found through MX, not the domain


def google_settings_url(email: str) -> str:
    """The settings page of a Google calendar, where its secret iCal address
    lives under "Integrate calendar". Google names the page after the
    calendar id, which for a person's own calendar is the address, base64."""
    import base64

    address = (email or "").strip().lower()
    if "@" not in address:
        return "https://calendar.google.com/calendar/u/0/r/settings"
    token = base64.urlsafe_b64encode(address.encode("utf-8")).decode("ascii").rstrip("=")
    return "https://calendar.google.com/calendar/u/0/r/settings/calendar/" + token


def domain_of(email: str) -> str:
    email = (email or "").strip().lower()
    return email.rsplit("@", 1)[1] if "@" in email else ""


def detect(email: str, mx_lookup=dnsq.mx) -> Detection:
    """Work out who hosts the calendar behind an email address."""
    domain = domain_of(email)
    if not domain:
        return Detection(email, "", PROVIDERS["unknown"])
    key = _BY_DOMAIN.get(domain)
    if key:
        return Detection(email, domain, PROVIDERS[key])
    hosts = tuple(mx_lookup(domain) or ())
    for host in hosts:
        for suffix, key in _BY_MX:
            if host == suffix or host.endswith("." + suffix):
                return Detection(email, domain, PROVIDERS[key], hosts, hosted=True)
    return Detection(email, domain, PROVIDERS["unknown"], hosts)
