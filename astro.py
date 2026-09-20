"""Where the sun is, so a masjid's page can be checked and completed.

Two things a page cannot always give the clock, and the sun can:

- **Maghrib.** A great many masjids write it as "Sunset" or "5 minutes after
  sunset" rather than as a time, because that is what it is: Maghrib is called
  when the sun sets and the iqama follows a few minutes later. With where the
  masjid is and what day it is, that is a time.
- **A check on everything else.** A page can be perfectly formatted and a whole
  season out of date -- a table of June times sitting on the site in September,
  headed "Today's board". Nothing in the table gives it away; the sun does.
  Maghrib cannot be an hour after sunset in September, whatever the table says.

This is the NOAA solar calculation, good to a minute or two, which is all either
purpose needs. It does not compute Fajr or Isha (those depend on a calculation
method every masjid chooses for itself); the checks on them are deliberately
loose.

Times come back as minutes after local midnight, on whatever clock the given UTC
offset describes. The clock assumes the masjid is in this machine's own time
zone, as everything else in it does.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

# Maghrib's adhan falls a little after the geometric sunset, and calendars
# agree on how much. Measured against 70 Canadian masjids on MAWAQIT, whose
# adhan is calculated the same way.
ADHAN_AFTER_SUNSET = 1.0


def _rad(x: float) -> float:
    return math.radians(x)


def _deg(x: float) -> float:
    return math.degrees(x)


def _solar(lon: float, day: date, utc_offset_hours: float):
    """(the sun's declination in degrees, solar noon in minutes after local midnight)."""
    # Julian day at local noon, then centuries since J2000.
    jd = day.toordinal() + 1721424.5 + 0.5 - utc_offset_hours / 24.0
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (math.sin(_rad(m)) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(_rad(2 * m)) * (0.019993 - 0.000101 * t)
         + math.sin(_rad(3 * m)) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    apparent = true_long - 0.00569 - 0.00478 * math.sin(_rad(omega))
    mean_obliq = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    obliq = mean_obliq + 0.00256 * math.cos(_rad(omega))
    decl = _deg(math.asin(math.sin(_rad(obliq)) * math.sin(_rad(apparent))))
    y = math.tan(_rad(obliq / 2.0)) ** 2
    eq_time = 4.0 * _deg(
        y * math.sin(2 * _rad(l0)) - 2 * e * math.sin(_rad(m))
        + 4 * e * y * math.sin(_rad(m)) * math.cos(2 * _rad(l0))
        - 0.5 * y * y * math.sin(4 * _rad(l0)) - 1.25 * e * e * math.sin(2 * _rad(m)))
    return decl, 720.0 - 4.0 * lon - eq_time + utc_offset_hours * 60.0


def _hour_angle(lat: float, decl: float, altitude: float):
    """Degrees of the sun's travel from noon to when it stands at `altitude`, or None."""
    cosine = ((math.sin(_rad(altitude)) - math.sin(_rad(lat)) * math.sin(_rad(decl)))
              / (math.cos(_rad(lat)) * math.cos(_rad(decl))))
    if not -1.0 <= cosine <= 1.0:
        return None
    return _deg(math.acos(cosine))


def sun_times(lat: float, lon: float, day: date, utc_offset_hours: float):
    """(sunrise, solar noon, sunset) in minutes after local midnight, or None.

    None where the sun does not rise or does not set that day (far north, near
    midsummer), which is where these checks give way to the page.
    """
    decl, noon = _solar(lon, day, utc_offset_hours)
    hour_angle = _hour_angle(lat, decl, -0.833)
    if hour_angle is None:
        return None
    return noon - 4.0 * hour_angle, noon, noon + 4.0 * hour_angle


def dawn(lat: float, lon: float, day: date, utc_offset_hours: float, depression: float = 20.0):
    """When the sun is `depression` degrees below the horizon before sunrise, in minutes
    after local midnight; None where it never gets that low (a northern summer).

    Fajr cannot begin before this on any calculation method -- the steepest in
    use is 20 degrees -- so an iqama earlier than it is not Fajr's.
    """
    decl, noon = _solar(lon, day, utc_offset_hours)
    hour_angle = _hour_angle(lat, decl, -depression)
    return None if hour_angle is None else noon - 4.0 * hour_angle


def local_offset_hours(when: datetime | None = None) -> float:
    """This machine's UTC offset, in hours, at that moment (DST included)."""
    moment = (when or datetime.now()).astimezone()
    offset = moment.utcoffset()
    return offset.total_seconds() / 3600.0 if offset is not None else 0.0


def sun_today(lat, lon, day: date | None = None, offset_hours: float | None = None):
    """(sunrise, noon, sunset, dawn) for a day, or None if it cannot be known.

    On this machine's clock unless the masjid's own UTC offset is given. It
    should be whenever it is known: a masjid in Edmonton followed from an
    Eastern machine has its sun two hours from where this machine's clock
    puts it, and every honest table would look impossible.
    """
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-66.0 <= lat <= 66.0 and -180.0 <= lon <= 180.0):
        return None
    day = day or date.today()
    if offset_hours is None:
        offset_hours = local_offset_hours(datetime(day.year, day.month, day.day, 12))
    times = sun_times(lat, lon, day, offset_hours)
    if times is None:
        return None
    return times + (dawn(lat, lon, day, offset_hours),)


# --- Maghrib written as words ---------------------------------------------------
_SUNSET = re.compile(r"sun\s?set")
_ADHAN = re.compile(r"adh?aa?n|ath?aa?n|azaa?n")
_OFFSET = re.compile(r"(?:\+\s*(\d{1,2})|(\d{1,2})\s*(?:min|mins|minute|minutes)\b)")


def maghrib_from_words(text: str, sunset: float) -> int | None:
    """Maghrib's iqama, in minutes after midnight, from how a page describes it.

    "Sunset", "At sunset", "3 minutes after sunset", "Sunset + 5",
    "5 min after Adhan". None for anything that says neither, so that a page
    which is not talking about Maghrib's timing at all is not read as though
    it were.
    """
    low = " ".join(str(text or "").lower().split())
    by_sunset, by_adhan = bool(_SUNSET.search(low)), bool(_ADHAN.search(low))
    if not (by_sunset or by_adhan):
        return None
    # The adhan follows the sun a minute or so later; "after adhan" counts from it.
    base = sunset + (ADHAN_AFTER_SUNSET if by_adhan and not by_sunset else 0.0)
    m = _OFFSET.search(low)
    if m:
        return int(round(base + int(m.group(1) or m.group(2))))
    return int(round(base))


# --- is this table the right season? -----------------------------------------------
# How far from the sun each iqama may fall, in minutes. Wide on purpose: these
# catch a page a season out of date, not a masjid's own choices.
def check(times: dict, sun) -> str:
    """"" when the five iqamas are plausible for that sun, else what is wrong.

    `times` maps prayer name to minutes after midnight. `sun` is sun_today().
    """
    if sun is None:
        return ""
    sunrise, noon, sunset = sun[:3]
    first_light = sun[3] if len(sun) > 3 else None

    def hm(minutes: float) -> str:
        return "%02d:%02d" % divmod(int(round(minutes)), 60)

    # (window, the fact about the sun that the window hangs on)
    windows = {
        "Fajr": ((max(sunrise - 230, first_light if first_light is not None else -1e9), sunrise - 5),
                 "sunrise is %s and dawn no earlier than %s" % (hm(sunrise), hm(first_light))
                 if first_light is not None else "sunrise is " + hm(sunrise)),
        "Dhuhr": ((noon - 5, noon + 150), "solar noon is " + hm(noon)),
        "Asr": ((noon + 80, sunset - 5), "noon is %s and sunset %s" % (hm(noon), hm(sunset))),
        "Maghrib": ((sunset - 3, sunset + 50), "sunset is " + hm(sunset)),
        "Isha": ((sunset + 35, min(sunset + 330, 23 * 60 + 59)), "sunset is " + hm(sunset)),
    }
    for prayer, ((low, high), why) in windows.items():
        value = times.get(prayer)
        if value is None:
            continue
        if not low <= value <= high:
            return "%s at %s is not possible on this day here (%s)" % (prayer, hm(value), why)
    return ""
