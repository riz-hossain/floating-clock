"""Offline checks for routines.py: firing a routine trigger, a speaker or this computer's own
audio, whichever prayer and whichever combination is switched on, at the right moment and not
twice.

No network, no real casting and no real local playback -- cast.play and localaudio.play are
swapped out, and every worker still runs on its own thread, so this cannot flake and cannot make a
sound.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-routines.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import cast as cast_mod  # noqa: E402
from floating_clock import localaudio  # noqa: E402
from floating_clock import prayer as prayer_mod  # noqa: E402
from floating_clock import routines  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


def wait_for(predicate, seconds: float = 2.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def due_now(name: str = "Fajr", lead_minutes: float = 10.0) -> prayer_mod.Prayer:
    """A prayer whose iqama is `lead_minutes` from now -- exactly due, with room in the grace window."""
    return prayer_mod.Prayer(name, datetime.now() + timedelta(minutes=lead_minutes))


# --- media_table: three independent tables, one settings dict ------------------------------------
print("media_table keeps routine, cast and local apart")
s = {
    "prayer_cast_media_default": "cast-default.mp3", "prayer_cast_media": {"Fajr": "cast-fajr.mp3"},
    "prayer_local_media_default": "local-default.mp3", "prayer_local_media": {"Isha": "local-isha.mp3"},
}
cast_table = routines.media_table(s, "prayer_cast")
local_table = routines.media_table(s, "prayer_local")
check("the cast table is built from the cast keys", cast_table["Fajr"] == "cast-fajr.mp3"
      and cast_table["Isha"] == "cast-default.mp3", str(cast_table))
check("the local table, from the local keys, is a different table entirely",
      local_table["Isha"] == "local-isha.mp3" and local_table["Fajr"] == "local-default.mp3", str(local_table))
check("with no default at all, a prayer with nothing of its own is left out, not given the other kind's",
      "Dhuhr" not in routines.media_table({"prayer_local_media": {"Fajr": "x"}}, "prayer_local"))

# --- Runner.poll(): firing what is due, kind by kind -----------------------------------------------
print("poll(): each of the three fires on its own switch")


def settings(**over):
    base = {
        "prayer_routines_enabled": False, "prayer_routines_lead_minutes": 10, "prayer_routines_hooks": {},
        "prayer_cast_enabled": False, "prayer_cast_lead_minutes": 10, "prayer_cast_media_default": "",
        "prayer_cast_media": {}, "prayer_cast_device": "", "prayer_cast_volume": 0.6,
        "prayer_local_enabled": False, "prayer_local_lead_minutes": 10, "prayer_local_media_default": "",
        "prayer_local_media": {}, "prayer_local_volume": 0.6,
    }
    base.update(over)
    return base


def calling(runner_kwargs=None):
    """A Runner whose three fire_* methods are swapped for ones that just record the call."""
    calls: list = []
    runner = routines.Runner(**(runner_kwargs or {"settings": settings()}))
    runner.fire_hook = lambda name, value: calls.append(("hook", name, value))
    runner.fire_cast = lambda name, value: calls.append(("cast", name, value))
    runner.fire_local = lambda name, value: calls.append(("local", name, value))
    return runner, calls


runner, calls = calling({"settings": settings(prayer_local_enabled=True, prayer_local_media_default="a.mp3")})
runner.poll([due_now()], datetime.now())
check("local alone fires when it alone is switched on", calls == [("local", "Fajr", "a.mp3")], str(calls))

runner, calls = calling({"settings": settings(prayer_local_enabled=True, prayer_local_media_default="a.mp3",
                                              prayer_cast_enabled=True, prayer_cast_media_default="b.mp3",
                                              prayer_routines_enabled=True,
                                              prayer_routines_hooks={"Fajr": "https://x/"})})
runner.poll([due_now()], datetime.now())
check("all three fire for the same prayer when all three are switched on",
      sorted(calls) == sorted([("hook", "Fajr", "https://x/"), ("cast", "Fajr", "b.mp3"), ("local", "Fajr", "a.mp3")]),
      str(calls))

runner, calls = calling({"settings": settings(prayer_local_enabled=True)})       # no media chosen
runner.poll([due_now()], datetime.now())
check("local with nothing chosen to play does not fire at all", calls == [], str(calls))

runner, calls = calling({"settings": settings(prayer_local_enabled=True, prayer_local_media_default="a.mp3")})
prayer, now = due_now(lead_minutes=10), datetime.now()
runner.poll([prayer], now)
runner.poll([prayer], now)
check("the same prayer, polled twice at the same moment, fires local only once", calls == [("local", "Fajr", "a.mp3")], str(calls))

runner, calls = calling({"settings": settings(prayer_local_enabled=True, prayer_cast_enabled=True,
                                              prayer_local_media_default="a.mp3", prayer_cast_media_default="a.mp3")})
runner.poll([due_now()], datetime.now())
check("a mark left by one kind does not suppress the other kind's turn for the same prayer",
      sorted(calls) == sorted([("cast", "Fajr", "a.mp3"), ("local", "Fajr", "a.mp3")]), str(calls))

# --- Runner.next_due(): the soonest of all three -----------------------------------------------------
print("next_due(): whichever of the three is soonest")
soon = datetime.now()
prayers = [prayer_mod.Prayer("Fajr", soon + timedelta(hours=5)), prayer_mod.Prayer("Dhuhr", soon + timedelta(hours=8))]
runner = routines.Runner(settings(prayer_local_enabled=True, prayer_local_lead_minutes=15,
                                  prayer_local_media_default="a.mp3"))
found = runner.next_due(prayers, soon)
check("local alone is found when it alone is armed", found is not None and found[2] == routines.LOCAL, str(found))
check("15 minutes ahead of the 5-hour prayer, as asked", abs((found[1] - (soon + timedelta(hours=5, minutes=-15))
                                                              ).total_seconds()) < 1, str(found))

runner = routines.Runner(settings(prayer_cast_enabled=True, prayer_cast_lead_minutes=60, prayer_cast_media_default="a.mp3",
                                  prayer_local_enabled=True, prayer_local_lead_minutes=5, prayer_local_media_default="a.mp3"))
found = runner.next_due(prayers, soon)
check("of two armed kinds for the same prayer, the one with more lead fires first, and next_due says so",
      found is not None and found[2] == routines.CAST, str(found))

runner = routines.Runner(settings(prayer_routines_enabled=True, prayer_routines_hooks={"Dhuhr": "https://x/"},
                                  prayer_local_enabled=True, prayer_local_media_default="a.mp3"))
found = runner.next_due(prayers, soon)
check("local (on Fajr, 5h away) beats a routine on Dhuhr (8h away)",
      found is not None and found[0].name == "Fajr" and found[2] == routines.LOCAL, str(found))

check("nothing armed at all finds nothing", routines.Runner(settings()).next_due(prayers, soon) is None)

# --- fire_local(): the worker, real cast.play/localaudio.play swapped out ------------------------------
print("fire_local(): on a worker, and never lets an exception through")
real_play = localaudio.play
try:
    said: list = []
    runner = routines.Runner(settings(prayer_local_volume=0.42), on_status=lambda k, t: said.append((k, t)))
    seen: list = []
    localaudio.play = lambda media, volume: seen.append((media, volume)) or ""
    runner.fire_local("Fajr", "a.mp3")
    check("it runs, and reaches localaudio.play with the media and the configured volume",
          wait_for(lambda: seen == [("a.mp3", 0.42)]), str(seen))
    check("success is recorded on the LOCAL status, and told to on_status",
          wait_for(lambda: "playing here" in runner.status[routines.LOCAL])
          and any(k == routines.LOCAL and "playing here" in t for k, t in said), str(runner.status))

    localaudio.play = lambda media, volume: "no MCI service on this computer"
    runner2 = routines.Runner(settings())
    runner2.fire_local("Isha", "b.mp3")
    check("a problem coming back is recorded as a failure, not treated as success",
          wait_for(lambda: "no MCI service" in runner2.status[routines.LOCAL]), str(runner2.status))

    def blows_up(media, volume):
        raise RuntimeError("the driver vanished")
    localaudio.play = blows_up
    runner3 = routines.Runner(settings())
    runner3.fire_local("Asr", "c.mp3")
    check("an exception from localaudio.play is caught here, not left to kill the worker",
          wait_for(lambda: "the driver vanished" in runner3.status[routines.LOCAL]), str(runner3.status))

    gate, entered = threading.Event(), threading.Event()

    def slow(media, volume):
        entered.set()
        gate.wait(5)
        return ""
    localaudio.play = slow
    runner4 = routines.Runner(settings())
    started = time.time()
    runner4.fire_local("Maghrib", "d.mp3")
    took = time.time() - started
    check("fire_local itself returns at once -- the play happens on a worker, not on the caller",
          took < 0.5 and entered.wait(2.0), "%.2fs" % took)
    gate.set()
finally:
    localaudio.play = real_play

# --- and the existing cast path is undisturbed by generalising media_table -------------------------
print("fire_cast is unaffected")
real_cast_play = cast_mod.play
try:
    seen = []
    cast_mod.play = lambda device, media, volume: seen.append((device, media, volume)) or ""
    runner = routines.Runner(settings(prayer_cast_device="Kitchen", prayer_cast_volume=0.7))
    runner.fire_cast("Fajr", "a.mp3")
    check("fire_cast still reaches cast.play the same way it always did",
          wait_for(lambda: seen == [("Kitchen", "a.mp3", 0.7)]), str(seen))
finally:
    cast_mod.play = real_cast_play

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("routines fires the right kind, for the right prayer, once, and never crashes a worker")
