"""Offline checks for "masjids near me": where this computer is, what lies around it, and what the picker says.

    * Windows' location service is asked the way a desktop program asks it, waits for a position rather than
      reading it before it has come, and says plainly when it is switched off or not allowed
    * the internet address is the fallback, tried service by service, each answer looked at before it is believed
    * the two are used in order, each try is reported to the timeline, and a position that is only approximate
      says so, and why Windows would not give a better one
    * the masjids around that point are searched for, nearest first, and Stop is heard between the steps
    * what the picker offers at each moment (pickerflow), and that its window always fits the screen

No network, no PowerShell and no window, so it cannot flake.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-near-me.py
"""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import tempfile

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import masjids, pickerflow as flow, timeline, whereami  # noqa: E402
from floating_clock.timeline import DONE, FAILED, SKIPPED, Cancelled, Section, Timeline  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


class Ran:
    """What subprocess.run gives back."""

    def __init__(self, out: str) -> None:
        self.stdout, self.stderr, self.returncode = out, "", 0


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def asks(out: str):
    """A stand-in for subprocess.run that records how it was called and answers with `out`."""
    calls: list = []

    def run(command, **kw):
        calls.append((command, kw))
        return Ran(out)
    run.calls = calls
    return run


def fails_with(error: BaseException):
    def run(command, **kw):
        raise error
    return run


def unavailable(work) -> str:
    try:
        work()
    except whereami.Unavailable as exc:
        return str(exc)
    except Exception as exc:                        # noqa: BLE001 -- a different failure is a failed check, not a crash
        return "!! raised %s instead: %s" % (exc.__class__.__name__, exc)
    return ""


def unseen(table: dict, blank):
    """A table that says a row is not shown, rather than raising, when a check asks for one that is not."""
    return collections.defaultdict(lambda: blank, table)


KITCHENER = {"latitude": 43.45, "longitude": -80.49, "city": "Kitchener", "region": "Ontario"}

# --- asking Windows ------------------------------------------------------------------------------
print("asking Windows")
run = asks("state Ready Granted\nwhere 43.4643 -80.5204 35\n")
got = whereami.from_windows(run)
check("a position from Windows is used as it is", (got.latitude, got.longitude, got.source) == (43.4643, -80.5204, "windows"), str(got))
check("with how close it says it is, in km", got.accuracy_km == 0.035, str(got.accuracy_km))
command, kw = run.calls[0]
script = kw.get("input", "")
check("it is asked through PowerShell without a profile or a prompt in the way, and the script comes in on standard input",
      command == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"] and script, str(command))
check("and in nothing that security software takes for malware: no encoded command, no bypassed execution policy",
      "-EncodedCommand" not in command and "-enc" not in command and "Bypass" not in command
      and "-ExecutionPolicy" not in command, str(command))
check("in a way that cannot flash a console window at the person", kw.get("creationflags") == 0x08000000, str(kw))
check("and with a limit on how long it may take", 5 < kw.get("timeout", 0) <= 30, str(kw.get("timeout")))
check("what is asked is the location service that desktop programs use", "GeoCoordinateWatcher" in script, script[:200])
check("and it waits for a position to arrive instead of reading it the moment it starts",
      "IsUnknown" in script and "while" in script and "Start-Sleep" in script, script)
check("numbers come back with dots whatever the language of the computer", "InvariantCulture" in script, script)

got = whereami.from_windows(asks("state Ready Granted\r\nwhere 51.05 -114.07 NaN\r\n"))
check("a position with no known accuracy is still a position", got.latitude == 51.05 and got.accuracy_km is None, str(got))

why = unavailable(lambda: whereami.from_windows(asks("state Ready Denied\n")))
check("location refused to desktop programs says so, and where to change it",
      "desktop apps" in why and "Settings > Privacy & security > Location" in why, why)
why = unavailable(lambda: whereami.from_windows(asks("state Disabled Unknown\n")))
check("location switched off says so, and where to switch it on",
      "switched off" in why and "Settings > Privacy & security > Location" in why, why)
why = unavailable(lambda: whereami.from_windows(asks("state NoData Granted\n")))
check("no data at all says that Windows could not tell", why == "Windows could not tell where this computer is", why)
why = unavailable(lambda: whereami.from_windows(asks("")))
check("and so does an answer that is empty", why == "Windows could not tell where this computer is", why)
why = unavailable(lambda: whereami.from_windows(fails_with(subprocess.TimeoutExpired("powershell.exe", 15))))
check("a Windows that does not answer in time is reported as that", why == "Windows did not answer in time", why)
why = unavailable(lambda: whereami.from_windows(fails_with(FileNotFoundError("powershell.exe"))))
check("a computer without PowerShell is reported as that", why.startswith("the clock could not ask Windows"), why)
for absurd in ("where 0 0 10", "where 91.5 20 10", "where 40 -181 10"):
    why = unavailable(lambda absurd=absurd: whereami.from_windows(asks("state Ready Granted\n" + absurd + "\n")))
    check("a position off the map is not believed: %s" % absurd, why == "Windows gave a position that makes no sense", why)
why = unavailable(lambda: whereami.from_windows(asks("state Ready Granted\nwhere 43,4643 -80,5204 35\n")))
check("a comma for a decimal point is not misread as some other number", why != "" and "43" not in why, why)

# --- asking the internet address ------------------------------------------------------------------
print("asking the internet address")
A, B, C = "https://a.example/", "https://b.example/", "https://c.example/"


def answers(table: dict, asked: list):
    def get(url):
        asked.append(url)
        reply = table[url]
        if isinstance(reply, BaseException):
            raise reply
        return reply
    return get


asked: list = []
got = whereami.from_internet(answers({A: KITCHENER, B: {"latitude": 1, "longitude": 1}}, asked), (A, B))
check("the address is placed by a public service, to the town", (got.latitude, got.longitude, got.place, got.source)
      == (43.45, -80.49, "Kitchener, Ontario", "internet"), str(got))
check("and is called approximate, about twenty-five kilometres out", got.accuracy_km == 25.0, str(got.accuracy_km))
check("the first service that answers is the last one asked", asked == [A], str(asked))

asked = []
table = {A: OSError("down"), B: {"success": False, "message": "quota"}, C: {"latitude": "43.0", "longitude": "-80.0", "region_name": "Ontario"}}
try:
    got = whereami.from_internet(answers(table, asked), (A, B, C))
except whereami.Unavailable as exc:
    got = whereami.Where(0.0, 0.0, "nowhere", "raised: %s" % exc)
check("a service that is down, or that says it failed, is gone past to the next", asked == [A, B, C] and got.latitude == 43.0, str(asked) + str(got))
check("a region on its own is a fine name for a place, and numbers written as text are read", got.place == "Ontario", got.place)

for name, junk in (("zero, zero", {"latitude": 0, "longitude": 0}), ("words for numbers", {"latitude": "north", "longitude": "west"}),
                   ("nothing at all", {}), ("a list, not an object", [43.4, -80.4]), ("a number missing", {"latitude": 43.4}),
                   ("off the map", {"latitude": 143.4, "longitude": -80.4})):
    asked = []
    got = whereami.from_internet(answers({A: junk, B: KITCHENER}, asked), (A, B))
    check("an answer that is %s is not believed" % name, asked == [A, B] and got.place == "Kitchener, Ontario", str(asked))

why = unavailable(lambda: whereami.from_internet(answers({A: OSError("x"), B: {}}, []), (A, B)))
check("if none of them can say, that is what is reported", "no location service answered" in why and "internet connection" in why, why)

line = Timeline(Clock())
asked = []


def stop_after_the_first(url):
    asked.append(url)
    line.cancel()
    raise OSError("down")


try:
    with timeline.using(line):
        whereami.from_internet(stop_after_the_first, (A, B, C))
    stopped = False
except Cancelled:
    stopped = True
except whereami.Unavailable:                            # every service was asked: the Stop was not heard
    stopped = False
check("Stop is heard between one service and the next, and no more are asked", stopped and asked == [A], str(asked))

# --- both, in order ------------------------------------------------------------------------------
print("windows first, then the internet address")
WHERE_STEPS = Section("where", "Where you are", "", ("locate", "internet"))


def located(windows, internet, platform="win32"):
    """(what locate() gave or raised, and the timeline rows) with Where you are left open."""
    line = Timeline(Clock())
    line.plan("masjids near you", [WHERE_STEPS])
    got = error = None
    with timeline.using(line):
        try:
            with line.section("where") as source:
                got = whereami.locate(windows, internet, platform)
                source.ok("found")
        except whereami.Unavailable as exc:
            error = exc
    line.finish("found" if error is None else "none", "", open="where")
    return got, error, unseen({r["key"]: (r["state"], r["detail"]) for r in line.snapshot()["rows"]}, ("(not shown)", ""))


def windows_says(where):
    def ask(run=None):
        if isinstance(where, BaseException):
            raise where
        return where
    return ask


def internet_says(where):
    calls = []

    def ask(get=None, services=None):
        calls.append(1)
        if isinstance(where, BaseException):
            raise where
        return where
    ask.calls = calls
    return ask


EXACT = whereami.Where(43.4643, -80.5204, "windows", "", 0.035)
APPROX = whereami.Where(43.45, -80.49, "internet", "Kitchener, Ontario", 25.0)
REFUSED = whereami.Unavailable("location is switched off in Windows; turn it on in Settings > Privacy & security > Location")
OFFLINE = whereami.Unavailable("no location service answered; check the internet connection")

net = internet_says(APPROX)
got, error, r = located(windows_says(EXACT), net)
check("Windows' answer is used when it gives one", got is EXACT and error is None, str(got))
check("and the internet address is then not asked at all", net.calls == [] and r["where.internet"] == (SKIPPED, "not needed"), str(r))
check("the timeline says it was found, and to within how many metres", r["where.locate"] == (DONE, "found it to within about 35 m"), str(r))

got, error, r = located(windows_says(REFUSED), internet_says(APPROX))
check("when Windows will not say, the internet address is used", got is not None and got.source == "internet" and got.place == "Kitchener, Ontario", str(got))
check("and the position says why Windows would not, so that it can be told to the person", got.why_not == str(REFUSED), got.why_not)
check("the timeline says that Windows was tried and why it did not do", r["where.locate"] == (FAILED, str(REFUSED)), str(r))
check("and what the internet address made of it", r["where.internet"] == (DONE, "about Kitchener, Ontario"), str(r))

got, error, r = located(windows_says(REFUSED), internet_says(OFFLINE))
message = str(error)
check("when neither can say, that is reported", got is None and error is not None)
check("with what each of them said, Windows first", 0 <= message.find("switched off") < message.find("no location service answered"), message)
check("and it reads as one sentence after the other", message.startswith("Location is switched off in Windows") and "Nor could it be worked out from your internet address" in message, message)
check("the timeline has both tries, and both failed", r["where.locate"][0] == FAILED and r["where.internet"] == (FAILED, str(OFFLINE)), str(r))

for system in ("darwin", "linux"):
    calls = []
    got, error, r = located(lambda run=None: calls.append(1) or EXACT, internet_says(APPROX), platform=system)
    check("on %s Windows is not asked, and the timeline says why" % system, calls == [] and r["where.locate"][0] == SKIPPED
          and "no location service" in r["where.locate"][1], str(r))
    check("the internet address answers, with nothing to say about Windows", got is not None and got.source == "internet" and got.why_not == "", str(got))

got, error, r = located(windows_says(whereami.Unavailable("x")), internet_says(OFFLINE), platform="linux")
check("off Windows, the internet address failing is reported as it is, with no Windows in it", str(error) == str(OFFLINE), str(error))

# --- what lies around it ---------------------------------------------------------------------------
print("the masjids near it")
ROWS = [{"name": "Masjid Al-Salaam", "km": 0.8, "slug": "al-salaam"}, {"name": "Islamic Centre", "km": 2.1}]
asks_search: list = []


def searching(rows=ROWS, trouble=""):
    def search(text="", lat=None, lon=None, radius_km=50.0, limit=60, online=True):
        asks_search.append({"text": text, "lat": lat, "lon": lon, "radius_km": radius_km, "limit": limit})
        return list(rows), trouble
    return search


def near(locate=None, search=None, before=None):
    line = Timeline(Clock())
    if before:
        before(line)
    found = error = None
    try:
        found = masjids.near_me(trail=line, locate=locate or (lambda: EXACT), search_for=search or searching())
    except whereami.Unavailable as exc:
        error = exc
    except Cancelled:
        error = "Stop escaped near_me instead of being answered"
    return line, found, error


asks_search.clear()
line, found, error = near()
rows_, trouble, where = found
snap = line.snapshot()
by = unseen({r["key"]: r for r in snap["rows"]}, {"state": "(not shown)", "detail": ""})
check("what comes back is the masjids, what went wrong, and where it looked", (rows_, trouble, where) == (ROWS, "", EXACT), str(found))
check("it looked around the point, within 20 km, for at most 40, of any name",
      asks_search == [{"text": "", "lat": 43.4643, "lon": -80.5204, "radius_km": 20.0, "limit": 40}], str(asks_search))
check("the timeline is about masjids near you, and says so in its own words",
      snap["title"] == "masjids near you" and snap["words"]["busy"] == "Finding the masjids near you"
      and snap["words"]["found"] == "Found the masjids near you", str(snap["title"]) + str(snap["words"]))
check("it has where you are, and the masjids around it, and both are done",
      by["where"]["state"] == DONE and by["near"]["state"] == DONE and snap["outcome"] == "found", str(by) + snap["outcome"])
check("and the first says how the position was worked out", by["where"]["detail"] == "this computer's location, from Windows", by["where"]["detail"])
check("and the second how many there were", by["near"]["detail"] == "2 masjids", by["near"]["detail"])
line, found, error = near(search=searching(ROWS[:1]))
check("one is a masjid, not one masjids", {r["key"]: r for r in line.snapshot()["rows"]}["near"]["detail"] == "1 masjid")

line, found, error = near(search=searching([], "map: timed out"))
snap = line.snapshot()
check("nothing near is a finished look that found nothing, not an error", error is None and found[0] == [] and found[1] == "map: timed out", str(found))
check("the timeline ends 'nothing found', with the radius", snap["outcome"] == "none" and "20 km" in snap["summary"]
      and snap["words"]["none"] == "Nothing found near you", str(snap["outcome"]) + snap["summary"])

def search_breaks(*a, **k):
    raise RuntimeError("the map is on fire")


line, found, error = near(search=search_breaks)
snap = line.snapshot()
check("a search that breaks is a search that found nothing, with what went wrong, not a crash",
      error is None and found == ([], "the map is on fire", EXACT) and snap["outcome"] == "none", str(found) + str(error))

asks_search.clear()


def locate_fails():
    with timeline.current().step("locate") as step:
        step.fail(str(REFUSED))
    with timeline.current().step("internet") as step:
        step.fail(str(OFFLINE))
    raise whereami.Unavailable("%s. Nor: %s" % (REFUSED, OFFLINE))


line, found, error = near(locate=locate_fails)
snap = line.snapshot()
by = unseen({r["key"]: r for r in snap["rows"]}, {"state": "(not shown)", "detail": ""})
check("not knowing where you are is raised, with why", error is not None and "Nor" in str(error), str(error))
check("and the search is never made without a point to search around", asks_search == [], str(asks_search))
check("the timeline says it could not find where you are, in words of its own", snap["outcome"] == "none"
      and snap["words"]["none"] == "Could not find where you are", str(snap["words"]))
check("Where you are failed, and the masjids around it were put away", by["where"]["state"] == FAILED and by["near"]["state"] == SKIPPED, str(by))
check("and its own line is short, since the tries beneath it say what went wrong", by["where"]["detail"] == "could not be worked out", by["where"]["detail"])
check("and what each try said is left in view", by["where.locate"]["detail"] == str(REFUSED) and by["where.internet"]["detail"] == str(OFFLINE), str(by))

asks_search.clear()


def stop_now(line):
    line.cancel()


line, found, error = near(before=stop_now)
check("Stop before it starts gives nothing, says so, and searches for nothing", found == ([], "Stopped.", None) and asks_search == [], str(found))
check("and the timeline says it was stopped", line.snapshot()["outcome"] == "stopped")


def stop_during_search(rows=ROWS):
    def search(text="", lat=None, lon=None, radius_km=50.0, limit=60, online=True):
        holder[0].cancel()
        return list(rows), ""
    return search


holder: list = []
line = Timeline(Clock())
holder.append(line)
found = masjids.near_me(trail=line, locate=lambda: EXACT, search_for=stop_during_search())
check("Stop pressed while it searches is honoured when the search comes back, not ignored", found == ([], "Stopped.", None), str(found))

asks_search.clear()
real_search, real_locate = masjids.search, whereami.locate
masjids.search = searching()
whereami.locate = lambda *a, **k: EXACT
try:
    found = masjids.near_me()
finally:
    masjids.search, whereami.locate = real_search, real_locate
check("with nothing handed to it, it uses the real search, and the real way of finding where you are",
      found[0] == ROWS and found[2] is EXACT and len(asks_search) == 1, str(found))

# --- what the picker offers ---------------------------------------------------------------------------
print("what the picker offers at each moment")
c = flow.controls(flow.LIST, picked=False)
check("with nothing picked, the main button says what it will do and cannot yet be pressed",
      (c.main, c.enabled, c.action) == ("Use this masjid", False, flow.CHECK), str(c))
c = flow.controls(flow.LIST, picked=True)
check("with a masjid picked it can", (c.main, c.enabled, c.action) == ("Use this masjid", True, flow.CHECK), str(c))
check("and Stop and Back are out of the way, Search and Near me are there",
      not c.stop and not c.back and c.look and not c.timeline, str(c))
c = flow.controls(flow.SEARCHING, picked=True)
check("while it searches the list stays, and nothing can be started on top of it",
      not c.enabled and not c.look and not c.timeline and not c.stop, str(c))
c = flow.controls(flow.WORKING, picked=True)
check("while it checks or finds the masjids near you the timeline is up, with Stop, and nothing else can be started",
      c.timeline and c.stop and not c.enabled and not c.look and not c.back, str(c))
c = flow.controls(flow.FOUND, picked=False)
check("when times are found the main button says Done, and pressing it keeps them", (c.main, c.enabled, c.action) == ("Done", True, flow.KEEP), str(c))
check("with Back to results beside it, no Stop, and the timeline still up to read", c.back and not c.stop and c.timeline, str(c))
c = flow.controls(flow.FAILED, picked=False)
check("when nothing was found it offers Try again, not Done", (c.main, c.enabled, c.action) == ("Try again", True, flow.AGAIN), str(c))
check("with Back to results, and the timeline up to say why", c.back and not c.stop and c.timeline, str(c))
check("only one state says Done", [m for m in (flow.LIST, flow.SEARCHING, flow.WORKING, flow.FOUND, flow.FAILED)
                                   if flow.controls(m, True).main == "Done"] == [flow.FOUND])
check("and it is the only one in which the main button keeps a masjid",
      [m for m in (flow.LIST, flow.SEARCHING, flow.WORKING, flow.FOUND, flow.FAILED)
       if flow.controls(m, True).action == flow.KEEP] == [flow.FOUND])

print("what it says")
check("a search that found some", flow.found(12) == "Found 12. Pick one, then press Use this masjid.", flow.found(12))
check("a search that found some, with something wrong", flow.found(3, "map: busy") == "Found 3. (map: busy)", flow.found(3, "map: busy"))
check("a search that found none", flow.found(0).startswith("Nothing matched"), flow.found(0))
says = flow.around(14, EXACT, 20.0)
check("masjids near a position Windows gave say how it was known, and how many, and how near",
      says.startswith("Found 14 masjids within 20 km of this computer's location, from Windows."), says)
check("and say what to do, and do not say the place might be wrong", "press Use this masjid" in says and "Not the right place" not in says, says)
check("one masjid is a masjid", flow.around(1, EXACT, 20.0).startswith("Found 1 masjid within"), flow.around(1, EXACT, 20.0))
guess = whereami.Where(43.45, -80.49, "internet", "Kitchener, Ontario", 25.0, why_not=str(REFUSED))
says = flow.around(9, guess, 20.0, "map: busy")
check("masjids near an internet address say so, and that it may be out, and offer a way to put it right",
      "Kitchener, Ontario, worked out from your internet address, so it may be a few kilometres out" in says
      and "Not the right place? Type a town or a postal code and press Search." in says, says)
check("and say why Windows would not give a better one, and what else went wrong",
      "Windows would not say where this computer is: %s" % REFUSED in says and says.endswith("(map: busy)"), says)
says = flow.around(0, guess, 20.0)
check("none near says none, and where, and what to try", says.startswith("No masjids within 20 km of Kitchener, Ontario") and "Try a town" in says, says)
check("not finding where you are says why, and that a town can be typed instead",
      flow.unlocated("Windows is off. Nor the net.") == "Could not find where you are: Windows is off. Nor the net. Type a town or a postal code above and press Search instead.",
      flow.unlocated("Windows is off. Nor the net."))
check("Stop is Stopped, not a failure", flow.unlocated("Stopped.") == "Stopped." and flow.unlocated("stopped") == "Stopped.")
check("and a reason that is missing is not called Stopped", flow.unlocated("").startswith("Could not find where you are."), flow.unlocated(""))
sample = {"kind": "exact", "name": "Masjid Al-Salaam", "source": "mawaqit", "address": "https://mawaqit.net/en/al-salaam", "asked": "",
          "times": [("Fajr", "05:30"), ("Dhuhr", "13:15"), ("Asr", "17:00"), ("Maghrib", "19:20"), ("Isha", "20:45")], "status": "", "how": ""}
for kind in ("exact", "read", "proxy"):
    text = masjids.confirmation(dict(sample, kind=kind, asked="Masjid X" if kind == "proxy" else ""), False)
    check("a %s answer is confirmed with Done, not with 'press Use this masjid again'" % kind,
          "Press Done to use them." in text and "Use this masjid" not in text and "again to keep" not in text, text)

print("how big the window is, and where")
for width, height, scale, want in ((1920, 1040, 1.0, (600, 640)), (1366, 728, 1.0, (600, 628)), (7680, 2160, 1.5, (900, 960)),
                                   (1280, 680, 1.25, (750, 555)), (800, 500, 1.0, (600, 400))):
    got = flow.size(width, height, scale)
    check("on a %dx%d screen at %d%% it is %dx%d" % (width, height, scale * 100, want[0], want[1]), got == want, str(got))
worst = []
for width in (320, 640, 800, 1024, 1366, 1920):
    for height in (300, 480, 600, 768, 1080):
        for scale in (1.0, 1.25, 1.5, 2.0):
            w_, h_ = flow.size(width, height, scale)
            if w_ > max(1, width - round(40 * scale)) or h_ > max(1, height - round(100 * scale)) or w_ < 1 or h_ < 1:
                worst.append((width, height, scale, w_, h_))
check("never bigger than the room the screen has, and never nothing", not worst, str(worst[:3]))
check("it is never asked to be dragged smaller than it starts, and never below a usable size",
      flow.smallest(600, 640) == (440, 420) and flow.smallest(300, 300) == (300, 300) and flow.smallest(900, 960, 1.5) == (660, 630),
      "%s %s %s" % (flow.smallest(600, 640), flow.smallest(300, 300), flow.smallest(900, 960, 1.5)))

DESKTOP = (0, 0, 1920, 1040)
check("it opens over the middle of the window that opened it", flow.place((100, 100, 900, 700), (600, 640), DESKTOP) == (250, 160))
check("but not below the bottom of the screen", flow.place((100, 700, 900, 700), (600, 640), DESKTOP) == (250, 360))
check("nor off the right, with its side borders kept on the screen too", flow.place((1700, 100, 900, 700), (600, 640), DESKTOP)[0] == 1300)
check("nor off the left", flow.place((-500, 100, 900, 700), (600, 640), DESKTOP)[0] == 0)
LEFT = (-1920, 0, 0, 1040)
check("on a monitor to the left of the main one it stays on that monitor", flow.place((-1500, 100, 900, 700), (600, 640), LEFT) == (-1350, 160))
check("and is kept on it from either side", flow.place((-3000, 100, 900, 700), (600, 640), LEFT)[0] == -1920
      and flow.place((-100, 100, 900, 700), (600, 640), LEFT)[0] == -620)
off = []
for area in ((0, 0, 1366, 728), (0, 0, 1920, 1040), (-1920, 0, 0, 1040), (0, -1080, 1920, 0), (1920, 0, 5760, 2160)):
    for scale in (1.0, 1.5):
        width, height = flow.size(area[2] - area[0], area[3] - area[1], scale)
        for dx in (-4000, -1000, 0, 500, 3000, 7000):
            for dy in (-3000, -500, 0, 400, 900, 2500):
                x, y = flow.place((dx, dy, 900, 700), (width, height), area, scale)
                if x < area[0] or x + width + round(20 * scale) > area[2] or y < area[1] or y + height + round(40 * scale) > area[3]:
                    off.append((area, scale, (dx, dy), (x, y), (width, height)))
check("wherever the settings window is, the picker with its title bar and borders lies inside the monitor's usable area", not off, str(off[:2]))
check("a short window drops the explanation to give the list the room, and a tall one keeps it",
      flow.short(578, 1.5) and flow.short(555, 1.25) and flow.short(410, 1.0) and flow.short(519, 1.0)
      and not flow.short(520, 1.0) and not flow.short(628, 1.0) and not flow.short(960, 1.5) and not flow.short(890, 1.5),
      "%s %s %s %s %s" % (flow.short(578, 1.5), flow.short(555, 1.25), flow.short(628, 1.0), flow.short(960, 1.5), flow.short(890, 1.5)))

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all near-me checks passed")
