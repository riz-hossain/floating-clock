"""Kids' screen time on this PC: time per app, a daily limit per game, the
websites visited, and a report emailed to a parent.

Everything is counted only while the clock runs, so closing the clock is the
obvious way round a limit. The report therefore says when the clock was not
running while the PC was on -- that is the one thing it must not miss.

The counting, the limits, the gaps and the report are plain functions over
plain data, checked offline by packaging/check-kids.py. Only the few calls at
the bottom of this file touch Windows.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import secrets
import shutil
import smtplib
import sqlite3
import sys
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from email.message import EmailMessage
from glob import glob
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

POLL_S = 5
IDLE_S = 300            # no keyboard or mouse this long: nobody is at the PC
WARN_AT_MIN = (10, 2)   # a toast this many minutes before a game closes
GAP_S = 120             # a pause shorter than this is not worth reporting
LATE_START_S = 600      # the clock came up this long after Windows did
SAVE_EVERY_S = 60
SAME_BOOT_S = 120       # two boot times this close are the same boot
REPORT_DAYS = 7         # at most this many days in one email
TOP_APPS = 15
TOP_SITES = 40

# What the report calls a program, when its file name says little.
FRIENDLY = {
    "robloxplayerbeta.exe": "Roblox",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "explorer.exe": "File Explorer / desktop",
    "javaw.exe": "Minecraft (Java)",
    "minecraft.windows.exe": "Minecraft",
    "discord.exe": "Discord",
    "steam.exe": "Steam",
}


def friendly(exe: str) -> str:
    return FRIENDLY.get(exe, exe[:-4] if exe.endswith(".exe") else exe)


# --- settings --------------------------------------------------------------
def parse_limits(text) -> dict[str, int]:
    """'RobloxPlayerBeta.exe=60, Minecraft=30' -> {'robloxplayerbeta.exe': 60, ...}.

    Minutes a day; 0 means the game may not run at all. Anything unreadable is
    skipped rather than guessed at.
    """
    limits: dict[str, int] = {}
    for part in str(text or "").replace(";", ",").replace("\n", ",").split(","):
        name, sep, minutes = part.partition("=")
        name = name.strip().lower()
        if not sep or not name:
            continue
        try:
            value = int(float(minutes.strip()))
        except ValueError:
            continue
        if not name.endswith(".exe"):
            name += ".exe"
        limits[name] = max(0, value)
    return limits


def hash_pin(pin: str, salt: str = "") -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("ascii"), 100_000)
    return "%s$%s" % (salt, digest.hex())


def pin_matches(pin: str, stored: str) -> bool:
    salt, sep, _digest = str(stored or "").partition("$")
    return bool(sep and salt) and secrets.compare_digest(hash_pin(pin, salt), stored)


# --- one day's numbers, on disk ---------------------------------------------
def kids_dir(base: str) -> str:
    return os.path.join(base, "kids")


def day_path(base: str, day: date) -> str:
    return os.path.join(kids_dir(base), "%s.json" % day.isoformat())


def load_day(base: str, day: date) -> dict:
    try:
        with open(day_path(base, day), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key, empty in (("apps", {}), ("games", {}), ("runs", []), ("warned", []), ("blocked", 0)):
        data.setdefault(key, empty)
    return data


def save_day(base: str, day: date, data: dict) -> None:
    path = day_path(base, day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(temp, path)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _at(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


# --- counting ----------------------------------------------------------------
class Monitor:
    """Counts the foreground program every few seconds and holds games to
    their daily limit.

    `poll` returns what the clock should say: ("warn", exe, minutes_left) and
    ("stopped", exe, limit_minutes) after a game was closed. The Windows calls
    are passed in, so the counting can be checked anywhere.
    """

    def __init__(self, base: str, now: datetime, *, foreground, idle_seconds, kill,
                 boot_time: datetime | None = None, last_shutdown: datetime | None = None,
                 pid: int | None = None) -> None:
        self.base = base
        self.foreground = foreground
        self.idle_seconds = idle_seconds
        self.kill = kill
        self.boot = boot_time
        self.pid = os.getpid() if pid is None else pid
        self.day = now.date()
        self.data = load_day(base, self.day)
        self.last: datetime | None = None
        self.saved = now
        self.run = self._new_run(now)
        if last_shutdown is not None:
            # Read once, at start: the previous boot's end, so a clock closed
            # in the evening still shows when that PC was shut down after it.
            self.run["prev_shutdown"] = _iso(last_shutdown)

    def _new_run(self, start: datetime) -> dict:
        run = {"start": _iso(start), "end": _iso(start), "pid": self.pid}
        if self.boot is not None:
            run["boot"] = _iso(self.boot)
        self.data["runs"].append(run)
        return run

    def save(self) -> None:
        try:
            save_day(self.base, self.day, self.data)
        except OSError:
            log.warning("Could not save today's screen time", exc_info=True)

    def poll(self, now: datetime, limits: dict[str, int]) -> list[tuple]:
        said: list[tuple] = []
        if now.date() != self.day:
            # Midnight: the old day ends here, the new one starts with this
            # same run, so there is no gap at 00:00.
            self.run["end"] = _iso(datetime.combine(self.day, dtime(23, 59, 59)))
            self.save()
            self.day = now.date()
            self.data = load_day(self.base, self.day)
            self.run = self._new_run(datetime.combine(self.day, dtime(0, 0)))
            said.append(("new_day", self.day - timedelta(days=1)))
        elapsed = 0.0
        if self.last is not None:
            elapsed = (now - self.last).total_seconds()
            if elapsed > GAP_S or elapsed < 0:
                # Asleep, or the clock was frozen: this pause is not use.
                self.run["end"] = _iso(self.last)
                self.run = self._new_run(now)
                elapsed = 0.0
        self.last = now
        self.run["end"] = _iso(now)

        exe = self.foreground() if elapsed else ""
        if exe and self.idle_seconds() < IDLE_S:
            apps = self.data["apps"]
            apps[exe] = apps.get(exe, 0) + elapsed
            if exe in limits:
                games = self.data["games"]
                games[exe] = games.get(exe, 0) + elapsed

        for game, minutes in limits.items():
            left = minutes * 60 - self.data["games"].get(game, 0)
            for warn in WARN_AT_MIN:
                key = "%s:%d" % (game, warn)
                if minutes > warn and 0 < left <= warn * 60 and key not in self.data["warned"]:
                    self.data["warned"].append(key)
                    said.append(("warn", game, max(1, round(left / 60))))
            if left <= 0 and self.kill(game):
                self.data["blocked"] = self.data.get("blocked", 0) + 1
                said.append(("stopped", game, minutes))

        if (now - self.saved).total_seconds() >= SAVE_EVERY_S:
            self.saved = now
            self.save()
        return said

    def used_minutes(self, game: str) -> int:
        return int(self.data["games"].get(game, 0) // 60)


# --- when the clock was not watching ----------------------------------------
def gaps(runs: list[dict]) -> list[tuple[datetime, datetime, str]]:
    """The stretches when the PC was on but the clock was not running.

    "closed": the clock stopped and came back within the same Windows session.
    "late": Windows started this long before the clock did.
    Sleep (same process, a pause) and the PC being off are left out.
    """
    found = []
    ordered = sorted((r for r in runs if _at(r.get("start")) and _at(r.get("end"))),
                     key=lambda r: r["start"])
    previous = None
    for run in ordered:
        if previous is not None and run.get("pid") == previous.get("pid"):
            previous = run                                  # asleep, or midnight
            continue
        start, boot = _at(run["start"]), _at(run.get("boot", ""))
        shut = _at(run.get("prev_shutdown", ""))
        end = _at(previous["end"]) if previous is not None else None
        prev_boot = _at(previous.get("boot", "")) if previous is not None else None
        if end and shut and end <= shut <= start:
            # Windows shut down in between. The clock may have been closed
            # before that, which only the shutdown time can show. Checked
            # before the boot time: with Fast Startup a shutdown keeps it.
            if (shut - end).total_seconds() >= GAP_S:
                found.append((end, shut, "closed"))
        elif end and boot and prev_boot and abs((boot - prev_boot).total_seconds()) < SAME_BOOT_S:
            if (start - end).total_seconds() >= GAP_S:
                found.append((end, start, "closed"))
        if (boot and (shut is None or boot > shut) and (end is None or boot > end)
                and (start - boot).total_seconds() >= LATE_START_S):
            found.append((boot, start, "late"))
        previous = run
    return found


# --- browser history ---------------------------------------------------------
_WEBKIT_EPOCH_US = 11_644_473_600 * 1_000_000   # 1601-01-01 to 1970-01-01


def history_files() -> list[tuple[str, str, str]]:
    """(browser, kind, path) for every profile on this Windows account."""
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    found = []
    for name, root in (("Chrome", r"Google\Chrome\User Data"),
                       ("Edge", r"Microsoft\Edge\User Data"),
                       ("Brave", r"BraveSoftware\Brave-Browser\User Data")):
        for path in glob(os.path.join(local, root, "*", "History")):
            found.append((name, "chromium", path))
    for path in glob(os.path.join(roaming, r"Mozilla\Firefox\Profiles", "*", "places.sqlite")):
        found.append(("Firefox", "firefox", path))
    return found


def read_visits(path: str, kind: str, start: datetime, end: datetime) -> list[tuple[datetime, str, str]]:
    """(when, url, title) for visits between two local times.

    A running browser holds its history open, so this reads a copy.
    """
    folder = tempfile.mkdtemp(prefix="fc-history-")
    try:
        copy = os.path.join(folder, "h.sqlite")
        shutil.copyfile(path, copy)
        if os.path.exists(path + "-wal"):
            shutil.copyfile(path + "-wal", copy + "-wal")
        lo, hi = int(start.timestamp() * 1_000_000), int(end.timestamp() * 1_000_000)
        if kind == "chromium":
            sql = ("SELECT v.visit_time - ?, u.url, u.title FROM visits v JOIN urls u ON u.id = v.url "
                   "WHERE v.visit_time BETWEEN ? AND ?")
            args = (_WEBKIT_EPOCH_US, lo + _WEBKIT_EPOCH_US, hi + _WEBKIT_EPOCH_US)
        else:
            sql = ("SELECT v.visit_date, p.url, p.title FROM moz_historyvisits v "
                   "JOIN moz_places p ON p.id = v.place_id WHERE v.visit_date BETWEEN ? AND ?")
            args = (lo, hi)
        with sqlite3.connect(copy) as db:
            rows = db.execute(sql, args).fetchall()
        return [(datetime.fromtimestamp(us / 1_000_000), url or "", title or "")
                for us, url, title in rows]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def site_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def summarize_sites(visits) -> list[tuple[str, int, str, datetime, datetime]]:
    """(site, visits, a page title, first, last), busiest first."""
    sites: dict[str, list] = {}
    for when, url, title in visits:
        site = site_of(url)
        if not site:
            continue
        entry = sites.setdefault(site, [0, "", when, when])
        entry[0] += 1
        entry[1] = entry[1] or title
        entry[2], entry[3] = min(entry[2], when), max(entry[3], when)
    return sorted(((s, *v) for s, v in sites.items()), key=lambda row: (-row[1], row[0]))


def day_sites(day: date) -> tuple[list, list[str]]:
    """The day's sites from every browser, and a line for each that would not read."""
    start = datetime.combine(day, dtime(0, 0))
    visits, trouble = [], []
    for browser, kind, path in history_files():
        try:
            visits += read_visits(path, kind, start, start + timedelta(days=1))
        except Exception as exc:     # a locked or odd file must not lose the report
            trouble.append("%s: %s" % (browser, exc.__class__.__name__))
    return summarize_sites(visits), trouble


# --- the report --------------------------------------------------------------
def _minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    return "%d h %02d min" % divmod(minutes, 60) if minutes >= 60 else "%d min" % minutes


def due_days(base: str, reported_through: str, today: date) -> list[date]:
    """Whole days, before today, with numbers on disk and no report sent yet."""
    last = None
    try:
        last = date.fromisoformat(reported_through) if reported_through else None
    except ValueError:
        pass
    days = []
    for path in glob(os.path.join(kids_dir(base), "????-??-??.json")):
        try:
            day = date.fromisoformat(os.path.basename(path)[:10])
        except ValueError:
            continue
        if day < today and (last is None or day > last):
            days.append(day)
    return sorted(days)[-REPORT_DAYS:]


def all_runs(base: str, days: list[date]) -> list[dict]:
    """Runs from these days and the day before the first, for the gaps across midnight."""
    if not days:
        return []
    runs = []
    for offset in range((days[-1] - days[0]).days + 2):
        runs += load_day(base, days[0] - timedelta(days=1) + timedelta(days=offset))["runs"]
    return runs


def build_report(base: str, days: list[date], limits: dict[str, int], sites_for=day_sites) -> tuple[str, str]:
    """(subject, html body) for these days."""
    runs_gaps = gaps(all_runs(base, days))
    e = html.escape
    parts = ["<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222'>"]
    flagged = 0
    for day in days:
        data = load_day(base, day)
        total = sum(data["apps"].values())
        parts.append("<h2 style='margin:18px 0 4px'>%s</h2>" % e(day.strftime("%A %d %B %Y")))
        parts.append("<p style='margin:0 0 8px'>Screen time: <b>%s</b></p>" % _minutes(total))

        day_gaps = [g for g in runs_gaps if g[0].date() == day]
        if day_gaps:
            flagged += len(day_gaps)
            parts.append("<p style='color:#b00020;margin:8px 0 2px'><b>The clock was not running "
                         "while the PC was on</b> &mdash; nothing was counted or limited then:</p><ul>")
            for start, end, why in day_gaps:
                parts.append("<li style='color:#b00020'>%s&ndash;%s (%s)%s</li>" % (
                    start.strftime("%H:%M"), end.strftime("%H:%M"), _minutes((end - start).total_seconds()),
                    " &mdash; Windows started without the clock" if why == "late"
                    else " &mdash; the clock was closed"))
            parts.append("</ul>")

        for game, minutes in sorted(limits.items()):
            used = data["games"].get(game, 0)
            parts.append("<p style='margin:2px 0'>%s: %s of %d min allowed%s</p>" % (
                e(friendly(game)), _minutes(used), minutes,
                " &mdash; <b>closed when time ran out</b>" if used >= minutes * 60 and data.get("blocked") else ""))

        apps = sorted(data["apps"].items(), key=lambda kv: -kv[1])[:TOP_APPS]
        if apps:
            parts.append("<h3 style='margin:12px 0 4px'>Programs</h3><table cellpadding='3'>")
            for exe, seconds in apps:
                if seconds >= 60:
                    parts.append("<tr><td>%s</td><td align='right'>%s</td></tr>"
                                 % (e(friendly(exe)), _minutes(seconds)))
            parts.append("</table>")

        sites, trouble = sites_for(day)
        parts.append("<h3 style='margin:12px 0 4px'>Websites</h3>")
        if sites:
            parts.append("<table cellpadding='3'><tr><th align='left'>Site</th><th>Visits</th>"
                         "<th>Times</th><th align='left'>A page</th></tr>")
            for site, count, title, first, last in sites[:TOP_SITES]:
                parts.append("<tr><td>%s</td><td align='right'>%d</td><td>%s&ndash;%s</td><td>%s</td></tr>" % (
                    e(site), count, first.strftime("%H:%M"), last.strftime("%H:%M"), e(title[:70])))
            parts.append("</table>")
            if len(sites) > TOP_SITES:
                parts.append("<p>&hellip;and %d more sites.</p>" % (len(sites) - TOP_SITES))
        else:
            parts.append("<p>None in the browsers' history.</p>")
        if trouble:
            parts.append("<p style='color:#666'>Could not read: %s</p>" % e(", ".join(trouble)))

    parts.append("<p style='color:#666;font-size:12px;margin-top:20px'>Counted by Floating Clock on "
                 "this PC, only while it runs. Private and InPrivate windows keep no history, and "
                 "deleted history cannot be read.</p></div>")
    span = days[0].strftime("%a %d %b") if len(days) == 1 else "%s to %s" % (
        days[0].strftime("%a %d %b"), days[-1].strftime("%a %d %b"))
    subject = "Screen time report: %s%s" % (span, " (clock was closed)" if flagged else "")
    return subject, "".join(parts)


def save_report(base: str, days: list[date], body: str) -> str:
    folder = os.path.join(kids_dir(base), "reports")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "report-%s.html" % days[-1].isoformat())
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("<!doctype html><meta charset='utf-8'>" + body)
    return path


# The sending Gmail address and its app password, in Windows Credential Manager.
MAIL_KEY = "kids-mail"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465


def send_gmail(sender: str, password: str, to: str, subject: str, body: str,
               host: str = SMTP_HOST, port: int = SMTP_PORT) -> None:
    """Send through Gmail with an app password: nothing to install or sign in to."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content("This report needs a mail app that shows HTML.")
    msg.add_alternative("<!doctype html><meta charset='utf-8'>" + body, subtype="html")
    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        # Google shows the app password in groups of four; the spaces are not part of it.
        server.login(sender, password.replace(" ", ""))
        server.send_message(msg)


def send_report(to: str, subject: str, body: str, credentials) -> str:
    """Gmail when a sender is stored, else Outlook. Says which one sent it."""
    if credentials and credentials[0] and credentials[1]:
        send_gmail(credentials[0], credentials[1], to, subject, body)
        return "Gmail"
    send_outlook(to, subject, body)
    return "Outlook"


def send_outlook(to: str, subject: str, body: str) -> None:
    """Send through classic Outlook on this PC: no sign-in to set up. The new
    Outlook has no COM interface, and this raises there."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        mail = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
        mail.To = to
        mail.Subject = subject
        mail.HTMLBody = body
        mail.Send()
    finally:
        pythoncom.CoUninitialize()


# --- Windows -------------------------------------------------------------------
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _psapi.EnumProcesses.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD))

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def _exe_of(pid: int, access: int = 0x1000) -> tuple[str, int]:
        """(lower-case file name, open handle or 0). The caller closes the handle."""
        handle = _kernel32.OpenProcess(access, False, pid)
        if not handle:
            return "", 0
        size = wintypes.DWORD(520)
        buf = ctypes.create_unicode_buffer(520)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower(), handle
        return "", handle

    def foreground_exe() -> str:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(pid))
        if not pid.value:
            return ""
        name, handle = _exe_of(pid.value)
        if handle:
            _kernel32.CloseHandle(handle)
        return name

    def idle_seconds() -> float:
        info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

    def kill(exe: str) -> int:
        """End every process with this file name; how many were ended."""
        pids = (wintypes.DWORD * 4096)()
        got = wintypes.DWORD()
        if not _psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(got)):
            return 0
        ended = 0
        for pid in pids[: got.value // ctypes.sizeof(wintypes.DWORD)]:
            # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
            name, handle = _exe_of(pid, 0x1000 | 0x0001)
            if handle:
                if name == exe and _kernel32.TerminateProcess(handle, 1):
                    ended += 1
                _kernel32.CloseHandle(handle)
        return ended

    def boot_time() -> datetime:
        return datetime.now() - timedelta(milliseconds=_kernel32.GetTickCount64())

    def last_shutdown() -> datetime | None:
        """When Windows last shut down, as it records it; None if unreadable."""
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Windows") as key:
                raw, _kind = winreg.QueryValueEx(key, "ShutdownTime")
            filetime = int.from_bytes(raw[:8], "little")
            return datetime.fromtimestamp((filetime - 116_444_736_000_000_000) / 10_000_000)
        except (OSError, ValueError, TypeError):
            return None
