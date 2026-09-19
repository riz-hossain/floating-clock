"""Persisted user settings."""

from __future__ import annotations

import json
import shutil
import logging
import os
import time

APP_NAME = "FloatingClock"

MIN_OPACITY = 0.10
MAX_OPACITY = 1.00
OPACITY_STEP = 0.05
MIN_FONT = 14
MAX_FONT = 200

DEFAULTS: dict = {
    "x": None,
    "y": None,
    "opacity": 0.92,
    "font_size": 52,
    "font_family": "Segoe UI Light",
    "theme": "Midnight",
    # Dialog and menu chrome: "auto" follows Windows' light/dark app mode
    "ui_mode": "auto",
    "use_24h": False,
    "show_seconds": True,
    "show_date": True,
    "seconds_bar": True,
    "tabular_digits": True,
    "click_through": False,
    "topmost": True,
    "hover_boost": True,
    "lock_position": False,
    # Hidden from the desktop, reachable from the tray icon. The peek still
    # runs while minimized: it is the whole point of keeping the clock around.
    "minimized": False,
    # Time and the next thing only: no date, no bar, one footer line.
    "compact": False,
    # Meetings, alarms and timers
    # On by default: on Windows this is Outlook on this PC, which needs no
    # sign-in; elsewhere it is whatever calendars have been added.
    "calendar_enabled": True,
    "show_next_meeting": True,
    "meeting_lead_minutes": 5,
    "calendar_poll_seconds": 120,
    "calendar_hours_ahead": 14,
    "alerts_sound": True,
    "show_timer": True,
    # A meeting about to start: the clock flies to the middle of the screen
    # and shakes there. Louder than a toast, which a full-screen window hides,
    # and for the one minute when being late still costs something. Clicking
    # the clock stops it.
    "nudge_enabled": True,
    "nudge_lead_minutes": 1,
    "nudge_shake_seconds": 5.0,
    # Iqama times from a masjid's own calendar (prayer.py), with a reminder
    # this many minutes before each one. The URL is blank for Waterloo
    # Masjid, which is built in; any other iqamah iCal feed works too.
    "prayer_enabled": True,
    "prayer_lead_minutes": 5,
    "prayer_ics_url": "",
    "prayer_show_minutes": 60,
    # No assistant lets you edit a routine's time from outside, so the clock
    # fires a trigger URL instead: the routine's "when" becomes that trigger
    # rather than a time, and it stays right all year. One URL per prayer.
    # Alexa reaches these through a trigger skill, Google through Home
    # Assistant or IFTTT; the clock only opens an address. See
    # ROUTINES.md.
    "prayer_routines_enabled": False,
    "prayer_routines_lead_minutes": 10,
    "prayer_routines_hooks": {},
    # Google Home has no webhook starter at all, so for Google the clock skips
    # routines and plays the adhan on the speaker itself over the network
    # (cast.py). The audio is the user's own file or URL -- nothing is
    # shipped or licensed, the same rule sounds.py follows. One default for
    # every prayer, overridable per prayer because Fajr's adhan differs.
    "prayer_cast_enabled": False,
    "prayer_cast_device": "",
    "prayer_cast_volume": 0.6,
    "prayer_cast_lead_minutes": 10,
    "prayer_cast_media_default": "",
    "prayer_cast_media": {},
    # Progress bar: "day" spans 24h from day_start_hour and dots each meeting,
    # "meeting" fills toward the next one, "seconds" tracks the passing minute.
    "bar_mode": "day",
    "day_start_hour": 8.0,
    "day_end_hour": 20.0,
    # Periodic "peek": glide to the middle of the screen, then glide back
    "peek_enabled": True,
    "peek_sound": True,      # a soft whoosh as the card sets off and returns
    "peek_interval_minutes": 30,
    "peek_hold_seconds": 2.0,
    "peek_zoom": 1.25,
    "peek_travel_ms": 620,
    # Extra calendars read from their ICS address, one per organisation. Each
    # entry is {id, name, domain, ics_url, colour, icon, enabled}; see orgs.py.
    "calendars": [],
    # Sign in with Google needs the app registered once in the Google Cloud
    # console (SIGN-IN-SETUP.md); the ids from that registration go here.
    "google_client_id": "",
    "google_client_secret": "",
    "show_org_marks": True,
    # Give every company seen in an Outlook meeting (by the organiser's email
    # domain) an entry of its own, with its website's icon as the mark.
    "auto_org_marks": True,
}


# Set this to point the clock at another folder for its settings, icons and
# log. The test suite sets it to a scratch directory, so no test can ever
# touch the real settings -- one once did, and took two calendars with it.
HOME_ENV = "FLOATING_CLOCK_HOME"


def config_dir() -> str:
    override = os.environ.get(HOME_ENV)
    if override:
        return override
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def rescue_path() -> str:
    """Flag file a second launch drops to un-stick the running instance."""
    return os.path.join(os.path.dirname(settings_path()), "rescue.flag")


def request_rescue() -> None:
    path = rescue_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("rescue")
    except OSError:
        pass


# The first sign-in after a Windows update can hold the settings folder shut
# for a few seconds. Starting on the defaults in that moment would switch the
# Alexa triggers off and drop every added calendar -- so wait it out, and if
# it never opens, run on defaults without ever saving them over the real file.
LOAD_ATTEMPTS = 10
LOAD_PAUSE_S = 1.5
LOAD_FAILED = False
_sleep = time.sleep


def _read_saved(path: str, attempts: int = LOAD_ATTEMPTS, pause: float = LOAD_PAUSE_S,
                sleep=None):
    """The saved dict; {} when there is no file; None when there is one that
    will not open.

    A file that opens but is not valid JSON is copied aside before being
    treated as empty, so whatever was in it can still be recovered.
    """
    sleep = sleep or _sleep
    for attempt in range(max(1, attempts)):
        try:
            # utf-8-sig: Notepad and PowerShell write a BOM, and plain utf-8
            # would choke on it -- silently discarding every saved setting.
            with open(path, "r", encoding="utf-8-sig") as fh:
                saved = json.load(fh)
            return saved if isinstance(saved, dict) else {}
        except FileNotFoundError:
            return {}
        except ValueError:
            aside = "%s.unreadable-%s.json" % (path[:-5] if path.endswith(".json") else path,
                                               time.strftime("%Y%m%d-%H%M%S"))
            try:
                shutil.copyfile(path, aside)
            except OSError:
                pass
            logging.getLogger(__name__).error(
                "%s is not valid JSON; kept a copy at %s and started on defaults", path, aside)
            return {}
        except OSError:
            if attempt + 1 < attempts:
                sleep(pause)
    return None


def load() -> dict:
    global LOAD_FAILED
    data = dict(DEFAULTS)
    saved = _read_saved(settings_path())
    if saved is None:
        LOAD_FAILED = True
        logging.getLogger(__name__).error(
            "Could not open %s; running on defaults and not saving over it", settings_path())
    else:
        LOAD_FAILED = False
        data.update({k: v for k, v in saved.items() if k in DEFAULTS})
        # The trigger URLs were "Alexa" until Google joined them and the
        # feature turned out to be assistant-agnostic all along. Carry the
        # old names over: load() keeps only keys in DEFAULTS, so a settings
        # file written before the rename would otherwise lose every URL.
        _carry_over(saved, data)
    # Never restore click-through: it makes the window ignore the mouse, so a
    # restart has to be a guaranteed way back regardless of what was saved.
    data["click_through"] = False
    return sanitise(data)


# old key -> new key, for settings files written before the rename.
_RENAMED = {
    "prayer_alexa_enabled": "prayer_routines_enabled",
    "prayer_alexa_lead_minutes": "prayer_routines_lead_minutes",
    "prayer_alexa_hooks": "prayer_routines_hooks",
}


def _carry_over(saved: dict, data: dict) -> None:
    """Fill new keys from their old names, when the file predates the rename.

    Only when the file has nothing under the new name: someone who has
    already saved once since upgrading should not have a stale Alexa key
    overwrite what they set.
    """
    for old, new in _RENAMED.items():
        if old in saved and new not in saved:
            data[new] = saved[old]


def sanitise(data: dict) -> dict:
    try:
        data["opacity"] = min(MAX_OPACITY, max(MIN_OPACITY, float(data["opacity"])))
    except (TypeError, ValueError):
        data["opacity"] = DEFAULTS["opacity"]
    try:
        data["font_size"] = min(MAX_FONT, max(MIN_FONT, int(data["font_size"])))
    except (TypeError, ValueError):
        data["font_size"] = DEFAULTS["font_size"]
    for key in (
        "use_24h", "show_seconds", "show_date", "seconds_bar", "tabular_digits",
        "click_through", "topmost", "hover_boost", "lock_position",
        "minimized", "compact", "calendar_enabled", "show_next_meeting", "alerts_sound", "show_timer",
        "peek_enabled", "peek_sound", "show_org_marks", "auto_org_marks",
        "nudge_enabled", "prayer_enabled", "prayer_routines_enabled",
        "prayer_cast_enabled",
    ):
        data[key] = bool(data[key])
    try:
        data["meeting_lead_minutes"] = min(60, max(1, int(data["meeting_lead_minutes"])))
        data["calendar_poll_seconds"] = min(3600, max(30, int(data["calendar_poll_seconds"])))
        data["calendar_hours_ahead"] = min(72, max(1, int(data["calendar_hours_ahead"])))
        data["peek_interval_minutes"] = min(240, max(1, int(data["peek_interval_minutes"])))
        data["peek_hold_seconds"] = min(10.0, max(0.3, float(data["peek_hold_seconds"])))
        data["peek_zoom"] = min(2.0, max(1.0, float(data["peek_zoom"])))
        data["peek_travel_ms"] = min(3000, max(120, int(data["peek_travel_ms"])))
        data["nudge_lead_minutes"] = min(30, max(0, int(data["nudge_lead_minutes"])))
        data["nudge_shake_seconds"] = min(30.0, max(0.5, float(data["nudge_shake_seconds"])))
        data["prayer_lead_minutes"] = min(60, max(0, int(data["prayer_lead_minutes"])))
        data["prayer_show_minutes"] = min(720, max(0, int(data["prayer_show_minutes"])))
        data["prayer_routines_lead_minutes"] = min(
            60, max(0, int(data["prayer_routines_lead_minutes"])))
        data["prayer_cast_lead_minutes"] = min(
            60, max(0, int(data["prayer_cast_lead_minutes"])))
        data["prayer_cast_volume"] = min(1.0, max(0.0, float(data["prayer_cast_volume"])))
        # Quarter-hour resolution: enough for "my day ends at 11:30pm"
        # without pretending the boundary is precise to the minute.
        for key in ("day_start_hour", "day_end_hour"):
            data[key] = min(23.75, max(0.0, round(float(data[key]) * 4) / 4))
    except (TypeError, ValueError):
        for key in ("meeting_lead_minutes", "calendar_poll_seconds", "calendar_hours_ahead",
                    "peek_interval_minutes", "peek_hold_seconds", "peek_zoom",
                    "peek_travel_ms", "day_start_hour", "day_end_hour",
                    "nudge_lead_minutes", "nudge_shake_seconds",
                    "prayer_lead_minutes", "prayer_show_minutes",
                    "prayer_routines_lead_minutes", "prayer_cast_lead_minutes",
                    "prayer_cast_volume"):
            data[key] = DEFAULTS[key]
    if not isinstance(data["font_family"], str):
        data["font_family"] = DEFAULTS["font_family"]
    if not isinstance(data["theme"], str):
        data["theme"] = DEFAULTS["theme"]
    for key in ("google_client_id", "google_client_secret", "prayer_ics_url",
                "prayer_cast_device", "prayer_cast_media_default"):
        data[key] = str(data.get(key) or "").strip()
    if data["ui_mode"] not in ("auto", "dark", "light"):
        data["ui_mode"] = DEFAULTS["ui_mode"]
    if data["bar_mode"] not in ("day", "meeting", "seconds"):
        data["bar_mode"] = DEFAULTS["bar_mode"]
    # One entry per prayer, and nothing else: a hand-edited file must not put
    # a stray key or a number in front of the firing code.
    from .prayer import ORDER as PRAYER_NAMES

    for key in ("prayer_routines_hooks", "prayer_cast_media"):
        table = data.get(key)
        data[key] = {
            name: str(table[name]).strip()
            for name in PRAYER_NAMES
            if isinstance(table, dict) and str(table.get(name) or "").strip()
        } if isinstance(table, dict) else {}
    if not isinstance(data["calendars"], list):
        data["calendars"] = []
    else:
        # Round-trip through orgs so a hand-edited file cannot put a malformed
        # entry in front of the poller.
        from .orgs import from_config, to_config

        data["calendars"] = to_config(from_config(data["calendars"]))
    return data


BACKUP_NAME = "settings.before-removal.json"


def connected_calendars(data) -> list:
    """The saved entries that read a calendar, as opposed to company marks."""
    out = []
    for entry in (data or {}).get("calendars", []) or []:
        if isinstance(entry, dict) and (entry.get("ics_url") or entry.get("caldav_url")):
            out.append(entry)
    return out


def keep_backup(path: str, data: dict) -> str:
    """Before a save with fewer connected calendars than the file on disk,
    copy that file aside and say so in the log.

    A calendar the user typed in must never vanish without a trace. If one
    does, the list as it was is one file away, and the log says when.
    Returns the backup's path, or "" when nothing was lost."""
    try:
        with open(path, encoding="utf-8") as fh:
            previous = json.load(fh)
    except (OSError, ValueError):
        return ""
    before, after = connected_calendars(previous), connected_calendars(data)
    if len(after) >= len(before):
        return ""
    backup = os.path.join(os.path.dirname(path), BACKUP_NAME)
    try:
        shutil.copyfile(path, backup)
    except OSError:
        return ""
    kept = {entry.get("id") for entry in after}
    lost = [str(entry.get("name") or entry.get("id")) for entry in before
            if entry.get("id") not in kept]
    logging.getLogger(__name__).warning(
        "Saving with %d fewer connected calendar(s) (%s); the previous settings are in %s",
        len(before) - len(after), ", ".join(lost), backup,
    )
    return backup


def save(data: dict) -> None:
    path = settings_path()
    if LOAD_FAILED and os.path.exists(path):
        # What is in memory is the defaults, not the user's settings; writing
        # it out would replace the real file with nothing.
        logging.getLogger(__name__).warning(
            "Not saving: %s could not be read at startup", path)
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        keep_backup(path, data)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


def reset() -> str:
    path = settings_path()
    try:
        os.remove(path)
        return "Settings reset."
    except FileNotFoundError:
        return "No saved settings to reset."
    except OSError as exc:
        return "Could not reset settings: %s" % exc
