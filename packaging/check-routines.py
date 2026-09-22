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
        "prayer_warn_seconds": 0,     # off by default in these fixtures; each warning check turns it on
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

# --- warning ahead of a prayer, and stop_now() ------------------------------------------------------
print("a warning ahead of it, and stop_now()")


def fires_at(name: str, lead_minutes: float, seconds_before: float) -> prayer_mod.Prayer:
    """A prayer whose iqama is set so that a kind with this lead fires in `seconds_before`."""
    return prayer_mod.Prayer(name, datetime.now() + timedelta(minutes=lead_minutes, seconds=seconds_before))


warned: list = []
runner, calls = calling({"settings": settings(prayer_cast_enabled=True, prayer_cast_media_default="a.mp3",
                                              prayer_warn_seconds=10)})
runner.on_warn = lambda item, kinds, seconds_left: warned.append((item.name, kinds, seconds_left))
runner.poll([fires_at("Fajr", 10, 8)], datetime.now())
check("with 10s of warning wanted, a prayer 8s from firing is warned about",
      warned == [("Fajr", [routines.CAST], 8)], str(warned))
check("and nothing has actually fired yet", calls == [], str(calls))
check("it is now something stop_now() could act on", runner.stoppable() is not None and runner.stoppable().name == "Fajr")

warned.clear()
runner.poll([fires_at("Fajr", 10, 7)], datetime.now())
check("polled again a moment later, the same prayer is not warned about twice", warned == [], str(warned))

warned.clear()
runner, calls = calling({"settings": settings(prayer_cast_enabled=True, prayer_cast_media_default="a.mp3",
                                              prayer_warn_seconds=10)})
runner.on_warn = lambda item, kinds, seconds_left: warned.append(1)
runner.poll([fires_at("Fajr", 10, 25)], datetime.now())
check("25s out with only 10s of warning wanted, nothing is said yet", warned == [])
check("and nothing is offered to stop yet either", runner.stoppable() is None)

warned.clear()
runner, calls = calling({"settings": settings(prayer_cast_enabled=True, prayer_cast_media_default="a.mp3",
                                              prayer_warn_seconds=0)})
runner.on_warn = lambda item, kinds, seconds_left: warned.append(1)
runner.poll([fires_at("Fajr", 10, 2)], datetime.now())
check("with warning turned off, nothing is ever said, however close it is", warned == [])

warned.clear()
runner, calls = calling({"settings": settings(prayer_cast_enabled=False, prayer_cast_media_default="a.mp3",
                                              prayer_warn_seconds=10)})
runner.on_warn = lambda item, kinds, seconds_left: warned.append(1)
runner.poll([fires_at("Fajr", 10, 5)], datetime.now())
check("an adhan chosen for a kind that is switched off is not warned about -- it is not really due",
      warned == [])

# Skipping it during the warning: it must not go on to fire when its moment actually arrives.
runner, calls = calling({"settings": settings(prayer_cast_enabled=True, prayer_cast_media_default="a.mp3",
                                              prayer_local_enabled=True, prayer_local_media_default="b.mp3",
                                              prayer_routines_enabled=True, prayer_routines_hooks={"Fajr": "https://x/"},
                                              prayer_warn_seconds=10)})
prayer = fires_at("Fajr", 10, 8)
runner.poll([prayer], datetime.now())
check("warned about, with every active kind named", sorted(runner.stoppable() and [prayer.name] or []) == ["Fajr"])
runner.stop_now()
check("stop_now() clears what was pending", runner.stoppable() is None)
# Its moment has now passed (it was 8s off when built), but still well inside the grace window
# hooks_due allows for a prayer that really was due -- so only the suppression marks explain
# what happens next, not simply having missed the window.
runner.poll([prayer], datetime.now() + timedelta(seconds=60))
check("having been told no, none of the three go on to fire when their moment comes",
      calls == [], str(calls))

# What it actually calls to stop something that may really be playing.
real_cast_stop, real_local_stop = cast_mod.stop, localaudio.stop
try:
    stopped: list = []
    cast_mod.stop = lambda device: stopped.append(("cast", device)) or ""
    localaudio.stop = lambda: stopped.append(("local",)) or ""
    runner = routines.Runner(settings(prayer_cast_enabled=True, prayer_cast_media_default="a.mp3",
                                      prayer_cast_device="Kitchen", prayer_local_enabled=True,
                                      prayer_local_media_default="b.mp3", prayer_warn_seconds=10))
    runner.poll([fires_at("Fajr", 10, 5)], datetime.now())
    runner.stop_now()
    check("stop_now() reaches cast.stop, naming the configured speaker",
          wait_for(lambda: ("cast", "Kitchen") in stopped), str(stopped))
    check("and localaudio.stop, unconditionally",
          wait_for(lambda: ("local",) in stopped), str(stopped))

    stopped.clear()
    runner.stop_now()
    check("calling it again with nothing pending reaches neither -- there is nothing to stop",
          wait_for(lambda: not stopped, seconds=0.3) and not stopped, str(stopped))
finally:
    cast_mod.stop, localaudio.stop = real_cast_stop, real_local_stop

# A real fire (no warning at all) still leaves something stop_now() can reach.
runner, calls = calling({"settings": settings(prayer_local_enabled=True, prayer_local_media_default="a.mp3")})
runner.poll([due_now(lead_minutes=10)], datetime.now())
check("a local play that has actually started is itself something to stop, warnings or not",
      calls == [("local", "Fajr", "a.mp3")] and runner.stoppable() is not None, str(calls))

# A routine alone: still worth a warning (it can be prevented before it fires), still nothing
# stop_now() can reach once it actually has been -- calling it is harmless either way.
real_hook_calls: list = []
runner, calls = calling({"settings": settings(prayer_routines_enabled=True,
                                              prayer_routines_hooks={"Fajr": "https://x/"},
                                              prayer_warn_seconds=10)})
warned = []
runner.on_warn = lambda item, kinds, seconds_left: warned.append(kinds)
hook_prayer = fires_at("Fajr", 10, 8)
runner.poll([hook_prayer], datetime.now())
check("a routine alone is still warned about ahead of time", warned == [[routines.HOOK]], str(warned))
runner.stop_now()
runner.poll([hook_prayer], datetime.now() + timedelta(seconds=15))    # past its moment, still in grace
check("suppressed during its warning, the routine itself is never called", calls == [], str(calls))

# A routine that is allowed to fire for real (no warning this time) is out of reach the moment
# it has: this clock cannot call it back once an assistant has already been told.
runner, calls = calling({"settings": settings(prayer_routines_enabled=True,
                                              prayer_routines_hooks={"Fajr": "https://x/"})})   # warn_seconds: 0
runner.poll([due_now(lead_minutes=10)], datetime.now())
check("a routine that actually fired, unwarned, is not remembered as stoppable -- there is nothing left to reach",
      calls == [("hook", "Fajr", "https://x/")] and runner.stoppable() is None, str(calls))

# Pruning: stale pending entries do not linger for ever.
runner, calls = calling({"settings": settings(prayer_local_enabled=True, prayer_local_media_default="a.mp3")})
runner.poll([due_now(lead_minutes=10)], datetime.now())
check("right after it fires, it is still something to stop", runner.stoppable() is not None)
runner.poll([due_now(lead_minutes=999)], datetime.now() + timedelta(seconds=routines.STOP_WINDOW_S + 5))
check("long after, that offer has quietly expired", runner.stoppable() is None)

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("routines fires the right kind, for the right prayer, once, and never crashes a worker")
