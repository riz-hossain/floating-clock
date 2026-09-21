"""Where this computer is, for "masjids near me".

Two ways to ask, in order, and the person is told which one answered:

  1. Windows' own location service, which knows within a hundred metres or so when
     it is on. It is asked with the same call any desktop program uses, through
     PowerShell, and it says plainly when location is switched off or not allowed
     for desktop apps.
  2. The computer's internet address, which any website can see and which places it
     to about the town. That is one request to a public service that answers with a
     latitude and longitude; it sends nothing but the request itself. It is only
     asked when Windows could not say, or on a system that has no such service, and
     what it says is called approximate wherever it is shown.

Nothing is kept: the position is used for one search and forgotten, and it is never
written to the settings.

Nothing here imports a toolkit, and both ways are injectable, so all of it is tested
without a window, without PowerShell and without a network.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, replace

from . import timeline

log = logging.getLogger(__name__)

USER_AGENT = "FloatingClock/1.0 (masjids near me)"
WINDOWS_TIMEOUT_S = 15.0        # the script itself gives up after 9, and PowerShell takes a second to start
WEB_TIMEOUT_S = 6.0
CREATE_NO_WINDOW = 0x08000000   # so that asking Windows does not flash a console
# How far out an address can place a computer, in km: to the town, not the street.
INTERNET_ACCURACY_KM = 25.0

# Public services that answer "where is the caller" in JSON with a latitude and longitude, no key and
# over https. Tried in order until one gives an answer that makes sense.
SERVICES = (
    "https://ipwho.is/",
    "https://get.geojs.io/v1/ip/geo.json",
    "https://ipapi.co/json/",
)

# GeoCoordinateWatcher is what .NET programs on Windows ask. It starts, and the first position can be a
# moment behind it, so the script waits for one; then it prints its state, and where, if it knows.
_SCRIPT = """
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default)
$until = (Get-Date).AddSeconds(9)
[void]$w.TryStart($false, [TimeSpan]::FromSeconds(5))
while ($w.Position.Location.IsUnknown -and $w.Status -ne 'Disabled' -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 250 }
$l = $w.Position.Location
'state ' + $w.Status + ' ' + $w.Permission
if (-not $l.IsUnknown) { [string]::Format([Globalization.CultureInfo]::InvariantCulture, 'where {0} {1} {2}', $l.Latitude, $l.Longitude, $l.HorizontalAccuracy) }
"""
_WHERE = re.compile(r"^where (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (\S+)\s*$", re.M)
_STATE = re.compile(r"^state (\S+) (\S+)\s*$", re.M)

LOCATION_SETTINGS = "Settings > Privacy & security > Location"


class Unavailable(Exception):
    """Where this computer is could not be worked out. The message says why, for a person."""


@dataclass(frozen=True)
class Where:
    latitude: float
    longitude: float
    source: str                        # "windows" or "internet"
    place: str = ""                    # "Kitchener, Ontario", when the source says
    accuracy_km: float | None = None
    why_not: str = ""                  # when it is the internet address: why Windows would not say


def valid(latitude: float, longitude: float) -> bool:
    """A position on the earth, and not the (0, 0) that a service gives when it does not know."""
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0 and not (latitude == 0.0 and longitude == 0.0)


def describe(where: Where) -> str:
    """How this position was worked out, and how far to trust it: the tail of a sentence."""
    if where.source == "windows":
        return "this computer's location, from Windows"
    place = where.place or "your area"
    return "%s, worked out from your internet address, so it may be a few kilometres out" % place


# --- Windows -----------------------------------------------------------------------------
def from_windows(run=subprocess.run) -> Where:
    """Windows' location service. Raises Unavailable, with the reason, when it cannot say."""
    # The script goes in on standard input. That needs no quoting, no file left on disk, and none of
    # the flags that security software takes for a sign of malware: an encoded command, or a bypassed
    # execution policy (which only ever applies to script files, and so is not wanted here).
    try:
        done = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"], input=_SCRIPT,
                   capture_output=True, text=True, timeout=WINDOWS_TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise Unavailable("Windows did not answer in time") from None
    except (OSError, ValueError) as exc:
        raise Unavailable("the clock could not ask Windows (%s)" % (str(exc)[:60] or exc.__class__.__name__)) from None
    out = done.stdout or ""
    found = _WHERE.search(out)
    if found:
        try:
            latitude, longitude = float(found.group(1)), float(found.group(2))
        except ValueError:
            raise Unavailable("Windows gave a position that makes no sense") from None
        if not valid(latitude, longitude):
            raise Unavailable("Windows gave a position that makes no sense")
        accuracy = None
        try:
            metres = float(found.group(3))
            if metres == metres and metres >= 0:                   # not NaN
                accuracy = metres / 1000.0
        except ValueError:
            pass
        return Where(latitude, longitude, "windows", "", accuracy)
    state = _STATE.search(out)
    if state and state.group(2).lower() == "denied":
        raise Unavailable("Windows is not letting desktop apps see where this computer is; turn that on in %s, "
                          "including \"Let desktop apps access your location\"" % LOCATION_SETTINGS)
    if state and state.group(1).lower() == "disabled":
        raise Unavailable("location is switched off in Windows; turn it on in %s" % LOCATION_SETTINGS)
    raise Unavailable("Windows could not tell where this computer is")


# --- the internet address -------------------------------------------------------------------
def _get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=WEB_TIMEOUT_S) as response:
        return json.loads(response.read(20_000).decode("utf-8", "replace"))


def _parse(data) -> Where | None:
    """A position out of what one of the services said, or None if it did not say one."""
    if not isinstance(data, dict) or data.get("success") is False:
        return None
    try:
        latitude, longitude = float(data.get("latitude")), float(data.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not valid(latitude, longitude):
        return None
    city = str(data.get("city") or "").strip()
    region = str(data.get("region") or data.get("region_name") or "").strip()
    return Where(latitude, longitude, "internet", ", ".join(part for part in (city, region) if part), INTERNET_ACCURACY_KM)


def from_internet(get=_get_json, services=SERVICES) -> Where:
    """The computer's internet address, placed by a public service. Raises Unavailable if none can."""
    line = timeline.current()
    for url in services:
        line.check()                                    # a Stop is heard between one service and the next
        try:
            data = get(url)
        except Exception as exc:                        # one service being down is what the others are for
            log.info("Near me: %s did not answer: %s", url, exc)
            continue
        got = _parse(data)
        if got is not None:
            return got
        log.info("Near me: %s answered with nothing usable", url)
    raise Unavailable("no location service answered; check the internet connection")


# --- both, in order --------------------------------------------------------------------------
def locate(windows=None, internet=None, platform: str | None = None) -> Where:
    """Where this computer is: Windows if it will say, else the internet address.

    Reports each try to the timeline that is listening, with what came of it. Raises
    Unavailable, with why, if neither can say; and if it is the internet address that
    answers when Windows would not, says on the position why Windows would not.
    """
    line = timeline.current()
    windows, internet = windows or from_windows, internet or from_internet
    platform = sys.platform if platform is None else platform
    windows_said = ""
    if platform.startswith("win"):
        with line.step("locate") as step:
            try:
                got = windows()
            except Unavailable as exc:
                got, windows_said = None, str(exc)
                step.fail(windows_said)
            else:
                step.ok("found it%s" % (" to within about %d m" % round(got.accuracy_km * 1000)
                                        if got.accuracy_km is not None else ""))
        if got is not None:
            line.skip("internet", "not needed")
            return got
    else:
        line.skip("locate", "this system has no location service that the clock can ask")
    with line.step("internet") as step:
        try:
            got = internet()
        except Unavailable as exc:
            got, internet_said = None, str(exc)
            step.fail(internet_said)
        else:
            step.ok("about %s" % (got.place or "where you are"))
    if got is None:
        raise Unavailable("%s. Nor could it be worked out from your internet address: %s" % (
            windows_said[:1].upper() + windows_said[1:], internet_said) if windows_said else internet_said)
    return replace(got, why_not=windows_said) if windows_said else got
