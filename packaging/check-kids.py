"""Offline checks for kids.py: counting, game limits, the gaps when the clock
was not running, browser history and the report.

No Windows calls -- the foreground program, idle time and killing are swapped
for fakes -- so this runs anywhere and cannot close a real game.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-kids.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import kids  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + str(detail)))
    if not condition:
        failures.append(name)


print("settings")
check("limits parse", kids.parse_limits("RobloxPlayerBeta.exe=60, minecraft = 30; bad, x=y")
      == {"robloxplayerbeta.exe": 60, "minecraft.exe": 30})
stored = kids.hash_pin("1234")
check("right PIN matches", kids.pin_matches("1234", stored))
check("wrong PIN does not", not kids.pin_matches("1235", stored))
check("no PIN set matches nothing", not kids.pin_matches("", ""))

print("counting and limits")
base = tempfile.mkdtemp(prefix="kids-")
state = {"exe": "robloxplayerbeta.exe", "idle": 0.0}
killed: list[str] = []


def fake_kill(exe: str) -> int:
    killed.append((exe, mon.data["games"].get(exe, 0)))
    return 1


t0 = datetime(2026, 11, 16, 15, 0, 0)
boot = t0 - timedelta(minutes=1)
mon = kids.Monitor(base, t0, foreground=lambda: state["exe"], idle_seconds=lambda: state["idle"],
                   kill=fake_kill, boot_time=boot, pid=100)
limits = {"robloxplayerbeta.exe": 15}
said: list[tuple] = []
now = t0
for _ in range(int(16 * 60 / kids.POLL_S)):          # 16 minutes of Roblox in front
    said += mon.poll(now, limits)
    now += timedelta(seconds=kids.POLL_S)
warns = [s for s in said if s[0] == "warn"]
check("two warnings, 10 then 2 min", [w[2] for w in warns] == [10, 2], warns)
check("closed once time is up", killed and killed[0][0] == "robloxplayerbeta.exe", killed)
check("closed at 15 min, not before", killed and 15 * 60 <= killed[0][1] < 15 * 60 + kids.POLL_S, killed[:1])
check("relaunch is closed again", len(killed) >= 2, killed)

used = mon.data["apps"]["robloxplayerbeta.exe"]
state["exe"], state["idle"] = "chrome.exe", 400.0  # away from the keyboard
for _ in range(12):
    mon.poll(now, limits)
    now += timedelta(seconds=kids.POLL_S)
check("idle time is not counted", "chrome.exe" not in mon.data["apps"], mon.data["apps"])
check("game time untouched by other use", mon.data["apps"]["robloxplayerbeta.exe"] == used)

state["idle"] = 0.0
now += timedelta(hours=1)                         # asleep for an hour
mon.poll(now, limits)
check("a sleep is not counted as use", "chrome.exe" not in mon.data["apps"], mon.data["apps"])
check("a sleep starts a new run", len(mon.data["runs"]) == 2, mon.data["runs"])
mon.save()
check("warnings do not repeat after reload",
      kids.load_day(base, t0.date())["warned"] == ["robloxplayerbeta.exe:10", "robloxplayerbeta.exe:2"])

print("midnight")
late = datetime(2026, 11, 16, 23, 59, 58)
mon.last = late - timedelta(seconds=5)
mon.poll(late, limits)
out = mon.poll(late + timedelta(seconds=5), limits)
check("new day is announced", ("new_day", date(2026, 11, 16)) in out, out)
check("the new day starts with a fresh count", mon.data["games"] == {} and mon.day == date(2026, 11, 17))

print("gaps")


def run(start, end, pid, boot_at, shut=None):
    entry = {"start": start.isoformat(), "end": end.isoformat(), "pid": pid, "boot": boot_at.isoformat()}
    if shut:
        entry["prev_shutdown"] = shut.isoformat()
    return entry


b1 = datetime(2026, 11, 16, 8, 0)
day_runs = [
    run(b1 + timedelta(minutes=1), b1 + timedelta(hours=2), 1, b1),
    run(b1 + timedelta(hours=3), b1 + timedelta(hours=4), 1, b1),            # asleep 2-3h
    run(b1 + timedelta(hours=5), b1 + timedelta(hours=6), 2, b1),            # closed 4-5h
]
found = kids.gaps(day_runs)
check("sleep is not a gap; closing is", [(g[0].hour, g[1].hour, g[2]) for g in found] == [(12, 13, "closed")], found)

b2 = datetime(2026, 11, 17, 7, 0)
fast = kids.gaps(day_runs + [run(b2 + timedelta(minutes=1), b2 + timedelta(hours=1), 3, b1,
                                 shut=b1 + timedelta(hours=6, minutes=1))])
check("a shutdown with Fast Startup is not a gap", len(fast) == 1, fast)
evening = kids.gaps(day_runs + [run(b2 + timedelta(minutes=1), b2 + timedelta(hours=1), 3, b2,
                                    shut=b1 + timedelta(hours=9))])
check("closed in the evening, then shut down", [(g[0].hour, g[1].hour) for g in evening][-1] == (14, 17), evening)
started_late = kids.gaps([run(b2 + timedelta(hours=2), b2 + timedelta(hours=3), 4, b2)])
check("Windows ran 2 h before the clock", [g[2] for g in started_late] == ["late"], started_late)

print("browser history")
hist = tempfile.mkdtemp(prefix="kids-hist-")
chrome = os.path.join(hist, "History")
with sqlite3.connect(chrome) as db:
    db.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    db.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    db.execute("INSERT INTO urls VALUES (1, 'https://www.roblox.com/games/1', 'Roblox game')")
    db.execute("INSERT INTO urls VALUES (2, 'https://youtube.com/watch?v=x', 'A video')")
    db.execute("INSERT INTO urls VALUES (3, 'chrome://settings', 'Settings')")
    for url_id, when in ((1, datetime(2026, 11, 16, 16, 0)), (1, datetime(2026, 11, 16, 17, 0)),
                         (2, datetime(2026, 11, 16, 18, 0)), (2, datetime(2026, 11, 15, 18, 0)),
                         (3, datetime(2026, 11, 16, 18, 0))):
        us = int(when.timestamp() * 1_000_000) + 11_644_473_600 * 1_000_000
        db.execute("INSERT INTO visits (url, visit_time) VALUES (?, ?)", (url_id, us))
db.close()
day = date(2026, 11, 16)
visits = kids.read_visits(chrome, "chromium", datetime(2026, 11, 16), datetime(2026, 11, 17))
sites = kids.summarize_sites(visits)
check("visits read for that day only", len(visits) == 4, visits)
check("sites grouped, www. dropped, browser pages left out",
      [(s[0], s[1]) for s in sites] == [("roblox.com", 2), ("youtube.com", 1)], sites)

print("report")
check("only whole, unreported days are due",
      kids.due_days(base, "", date(2026, 11, 17)) == [date(2026, 11, 16)])
check("nothing due once reported", kids.due_days(base, "2026-11-16", date(2026, 11, 17)) == [])
subject, body = kids.build_report(base, [day], limits, sites_for=lambda _d: (sites, ["Edge: OSError"]))
check("subject names the day", "Mon 16 Nov" in subject, subject)
check("Roblox minutes and the close are in it", "Roblox: 15 min of 15 min" in body
      and "closed when time ran out" in body, body[:400])
check("sites are in it", "roblox.com" in body and "youtube.com" in body)
check("an unreadable browser is said", "Edge: OSError" in body)
path = kids.save_report(base, [day], body)
check("a copy is saved", os.path.exists(path), path)

print("sending")
sent: list[tuple] = []


class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        sent.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        sent.append(("login", user, password))

    def send_message(self, msg):
        sent.append(("message", msg["From"], msg["To"], msg["Subject"],
                     msg.get_body(("html",)).get_content()))


outlook_calls: list[tuple] = []
kids.smtplib.SMTP_SSL = FakeSMTP
kids.send_outlook = lambda *args: outlook_calls.append(args)
via = kids.send_report("parent@example.com", subject, body, ("sender@example.com", "abcd efgh ijkl mnop"))
check("Gmail is used when a sender is stored", via == "Gmail" and not outlook_calls, via)
check("connects to Gmail over SSL", sent[0] == ("connect", "smtp.gmail.com", 465), sent[:1])
check("app password sent without its spaces", sent[1] == ("login", "sender@example.com", "abcdefghijklmnop"), sent[1])
check("the report is the HTML body", sent[2][1:4] == ("sender@example.com", "parent@example.com", subject)
      and "roblox.com" in sent[2][4])
via = kids.send_report("parent@example.com", subject, body, None)
check("Outlook without a sender", via == "Outlook" and len(outlook_calls) == 1, via)

print()
if failures:
    print("%d check(s) failed: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("All kids checks passed.")
