"""Offline checks for the timeline that shows what a masjid check is doing.

    * the plan is laid out up front, and only what is running is unfolded
    * steps say how they went, by their own word or by how they were left
    * the bar: weighted by time, never backwards, never full until it is over
    * a neighbour is one line that shows what it is doing, not a checklist
    * Stop is honoured at the next step and leaves nothing half-done
    * a thread that reports and a thread that reads at once
    * with nobody listening, every call is a no-op
    * where everything goes when it is drawn: wrapping, indenting, and never off the edge

No network, no windows, no clock but a made-up one, so it cannot flake.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-timeline.py
"""

from __future__ import annotations

import sys
import threading

from floating_clock import timeline
from floating_clock.timeline import (
    DONE, FAILED, PENDING, RUNNING, SKIPPED, Cancelled, Section, Timeline,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


class Clock:
    """A clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def states(line: Timeline) -> dict:
    return {row["key"]: row["state"] for row in line.snapshot()["rows"]}


def plan(clock=None, nearby: int = 0) -> tuple:
    clock = clock or Clock()
    line = Timeline(clock)
    sections = [
        Section("mawaqit", "mawaqit.net", "", timeline.MAWAQIT),
        Section("site", "The masjid's own website", "example.org", timeline.WEBSITE),
    ]
    if nearby:
        sections.append(Section("nearby", "Nearby masjids", "only if this one has nothing",
                                attempts=tuple(("n%d" % i, "Masjid %d" % i, "%d km away" % (i + 1))
                                               for i in range(nearby))))
    line.plan("Test Masjid", sections)
    return line, clock


print("the plan")
line, clock = plan(nearby=2)
snap = line.snapshot()
check("every source is on it from the start, before anything has run",
      [r["key"] for r in snap["rows"]] == ["mawaqit", "site", "nearby"], str(snap["rows"]))
check("and all of it is waiting", all(r["state"] == PENDING for r in snap["rows"]))
check("nothing has started, so nothing has taken any time", all(r["seconds"] is None for r in snap["rows"]))
check("it is busy, with an empty bar", snap["busy"] and snap["fraction"] == 0.0 and snap["outcome"] == "", str(snap))
check("and it carries the masjid's name", snap["title"] == "Test Masjid")

print("unfolding")
line, clock = plan()
line.section("site", "comparing")
keys = [r["key"] for r in line.snapshot()["rows"]]
check("a source that is running shows its steps",
      keys == ["mawaqit", "site"] + ["site." + s for s in timeline.WEBSITE], str(keys))
check("and the steps that have not run are waiting",
      all(r["state"] == PENDING for r in line.snapshot()["rows"] if r["level"]))
check("a source that is only planned shows one line", "mawaqit.ask" not in keys)
steps = [r for r in line.snapshot()["rows"] if r["level"]]
check("steps are indented under their source and carry the wording for the window",
      all(r["level"] == 1 for r in steps) and steps[0]["label"] == "Reaching the website", str(steps[:1]))

print("a step says how it went")
line, clock = plan()
with line.section("site") as site:
    with line.step("reach") as s:
        clock.tick(0.9)
        s.ok("reached example.org")
    check("its own word is what is shown", states(line)["site.reach"] == DONE)
    row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.reach")
    check("with what it said and how long it took",
          row["detail"] == "reached example.org" and abs(row["seconds"] - 0.9) < 1e-6, str(row))
    with line.step("feed") as s:
        s.ok("none found")
    with line.step("embed"):
        pass
    check("leaving a step without a word is a success", states(line)["site.embed"] == DONE)
    with line.step("calendar") as s:
        s.skip("no need")
    check("a step can be put aside", states(line)["site.calendar"] == SKIPPED)
    try:
        with line.step("home"):
            raise ValueError("connection reset by the peer")
    except ValueError:
        raised = True
    else:
        raised = False
    check("an exception fails the step, and carries on up", raised and states(line)["site.home"] == FAILED)
    row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.home")
    check("saying what went wrong", "connection reset" in row["detail"], row["detail"])
    with line.step("pages") as s:
        s.ok("a")
        s.fail("b")
        s.ok("c")
    row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.pages")
    check("the first word on how a step went stands", row["state"] == DONE and row["detail"] == "a", str(row))
    site.ok("read")
line.finish("found", "read", open="site")
row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.browser")
check("what was never reached is put away when the source ends, and said to be unneeded",
      row["state"] == SKIPPED and row["detail"] == "not needed", str(row))

print("the same, without the block")
line, clock = plan()
line.section("site")
line.start("reach", "opening")
check("a step can be started in one place", states(line)["site.reach"] == RUNNING)
line.note("reach", "still opening")
row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.reach")
check("told what it is doing while it runs", row["detail"] == "still opening", str(row))
clock.tick(2)
line.done("reach", "reached")
row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.reach")
check("and ended in another, with the time between", row["state"] == DONE and abs(row["seconds"] - 2) < 1e-6, str(row))
line.note("reach", "too late to change")
row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.reach")
check("a finished step's words are not overwritten", row["detail"] == "reached", str(row))
line.fail("home", "never started")
check("ending a step that never started is allowed and takes no time",
      states(line)["site.home"] == FAILED)
line.start("no-such-step")
line.done("no-such-step")
check("a step that is not on the plan is ignored, not an error", True)

print("collapsing")
line, clock = plan()
with line.section("mawaqit") as m:
    with line.step("ask") as s:
        s.fail("it has no listing for this masjid")
    m.fail("no listing")
rows = line.snapshot()["rows"]
check("a source that has finished folds to one line",
      [r["key"] for r in rows] == ["mawaqit", "site"], str([r["key"] for r in rows]))
check("that says how it went", rows[0]["state"] == FAILED and rows[0]["detail"] == "no listing", str(rows[0]))
line, clock = plan()
with line.section("mawaqit") as m:
    m.ok("found")
line.finish("found", "read", open="mawaqit")
keys = [r["key"] for r in line.snapshot()["rows"]]
check("the source that supplied the answer stays open when it is over",
      "mawaqit.ask" in keys and "mawaqit.check" in keys, str(keys))
check("and one that did not stays folded", "site.reach" not in keys)

print("the bar")
line, clock = plan()
seen = [line.snapshot()["fraction"]]
line.section("site")
for key in timeline.WEBSITE[:-1]:
    line.start(key)
    clock.tick(1.0)
    seen.append(line.snapshot()["fraction"])
    line.done(key)
    seen.append(line.snapshot()["fraction"])
check("it never goes backwards", all(b >= a - 1e-12 for a, b in zip(seen, seen[1:])), str(seen))
check("and it is not full until the check is over", max(seen) < 1.0, str(max(seen)))
line, clock = plan()
line.section("site")
line.start("browser")
first = line.snapshot()["fraction"]
clock.tick(10)
second = line.snapshot()["fraction"]
clock.tick(1000)
third = line.snapshot()["fraction"]
check("a long step keeps the bar moving while it runs", second > first, "%s %s" % (first, second))
check("but never to the end of itself: it is an estimate, not a claim", third < 1.0, str(third))
planned = sum(timeline.STEPS[k][1] for k in timeline.WEBSITE + timeline.MAWAQIT)      # both sources are on the plan
own = timeline.STEPS["browser"][1] * 0.9 / planned         # the promise, stated here and not read back from the code
check("a step running for ever counts for nine-tenths of itself and no more", abs(third - own) < 1e-3,
      "%s vs %s" % (third, own))
line, clock = plan()
with line.section("site") as s:
    line.done("reach", "ok")
    s.ok("found")
line.finish("found", "done", open="site")
check("when the check is over the bar is full, whatever was left undone",
      line.snapshot()["fraction"] == 1.0 and not line.snapshot()["busy"])
line, clock = plan()
check("a plan with no steps has a bar of nothing and does not divide by zero",
      Timeline(clock).snapshot()["fraction"] == 0.0)
one, clock = plan()
two, _ = plan(nearby=5)
for line in (one, two):
    line.section("site")
    line.done("reach")
    line.done("feed")
check("the neighbours are on the bar from the start, so what is left is honest",
      one.snapshot()["fraction"] > two.snapshot()["fraction"] > 0)

print("the elapsed time")
line, clock = plan()
clock.tick(37)
check("counts from the start", abs(line.snapshot()["elapsed"] - 37) < 1e-6)
line.finish("none", "nothing")
clock.tick(500)
check("and stops when it is over", abs(line.snapshot()["elapsed"] - 37) < 1e-6, str(line.snapshot()["elapsed"]))

print("neighbours")
line, clock = plan(nearby=3)
with line.section("nearby", "trying up to 3") as near:
    with line.attempt("n0") as a:
        with line.step("reach") as s:
            check("a neighbour shows what it is doing, on its own line",
                  "Reaching the website" in next(r for r in line.snapshot()["rows"]
                                                 if r["key"] == "nearby.n0")["detail"])
        line.start("browser", "page 2 of 3")
        row = next(r for r in line.snapshot()["rows"] if r["key"] == "nearby.n0")
        check("and how far along that is", row["detail"] == "Opening the pages in a browser: page 2 of 3", row["detail"])
        a.fail("its timetable is posted as an image")
    with line.attempt("n1") as a:
        a.ok("times found")
    near.ok("used Masjid 1's")
line.finish("found", "read", open="nearby")
rows = {r["key"]: r for r in line.snapshot()["rows"]}
check("a neighbour that did not read says why", rows["nearby.n0"]["detail"] == "its timetable is posted as an image",
      str(rows["nearby.n0"]))
check("neighbours are listed once they have been tried, when they gave the answer",
      rows["nearby.n0"]["state"] == FAILED and rows["nearby.n1"]["state"] == DONE, str({k: v["state"] for k, v in rows.items()}))
check("and the one that was never needed was put away", rows["nearby.n2"]["state"] == SKIPPED)
line, clock = plan(nearby=1)
with line.section("nearby"):
    with line.attempt("n0"):
        line.step("reach")
        line.step("home")
    check("a neighbour shows one line, not a checklist",
          not any(r["key"].startswith("nearby.reach") for r in line.snapshot()["rows"]))

print("when nothing was found")
line, clock = plan(nearby=2)
with line.section("mawaqit") as m:
    m.fail("no listing")
with line.section("site") as s:
    line.done("reach", "reached")
    line.fail("home", "no times on it")
    s.fail("nothing readable")
with line.section("nearby") as near:
    with line.attempt("n0") as a:
        a.fail("its timetable is posted as an image")
    with line.attempt("n1") as a:
        a.fail("no page prints its times")
    near.fail("none has times")
check("the sources are folded while it works: the reason is the line that matters", "site.reach" not in
      [r["key"] for r in line.snapshot()["rows"]])
line.finish("none", "Nothing was found.")
rows = {r["key"]: r for r in line.snapshot()["rows"]}
check("but when nothing was found, every source that was tried is opened so it can be read",
      "site.reach" in rows and "nearby.n0" in rows and "nearby.n1" in rows, str(list(rows)))
check("with what went wrong at each", rows["nearby.n0"]["detail"] == "its timetable is posted as an image"
      and rows["site.home"]["detail"] == "no times on it", str((rows["nearby.n0"], rows["site.home"])))
check("but a source with only a step or two is not opened: its own line says it all",
      "mawaqit.ask" not in rows, str(list(rows)))
line, clock = plan(nearby=1)
with line.section("site") as s:
    s.fail("nothing readable")
line.finish("stopped", "Stopped.")
check("a check that was stopped leaves nothing unfolded: it was not the sources' failure",
      "site.reach" not in [r["key"] for r in line.snapshot()["rows"]])
line, clock = plan(nearby=1)
with line.section("site") as s:
    s.ok("read")
line.finish("found", "", open="site")
check("and a check that found something opens only what supplied it",
      "site.reach" in [r["key"] for r in line.snapshot()["rows"]] and "mawaqit.ask" not in [r["key"] for r in line.snapshot()["rows"]])

print("a finished step stays finished")
line, clock = plan()
with line.section("site"):
    with line.step("reach") as s:
        s.ok("reached")
    line.start("reach", "again")
    row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.reach")
    check("it cannot be started again, and what it said stands", row["state"] == DONE and row["detail"] == "reached", str(row))
line, clock = plan()
with timeline.using(line):
    with line.section("site"):
        with timeline.hushed():
            timeline.current().start("reach", "from a nested read")
            timeline.current().done("reach", "nested")
        row = next(r for r in line.snapshot()["rows"] if r["key"] == "site.reach")
        check("a hushed read writes nothing to the timeline around it", row["state"] == PENDING and row["detail"] == "", str(row))
        line.cancel()
        try:
            with timeline.hushed():
                timeline.current().check()
        except Cancelled:
            stopped = True
        else:
            stopped = False
        check("but Stop still reaches it", stopped)

print("Stop")
line, clock = plan()
line.section("site")
line.start("reach")
line.cancel()
check("the work is told to stop only when it looks", line.cancelled and states(line)["site.reach"] == RUNNING)
try:
    line.step("feed")
except Cancelled:
    stopped = True
else:
    stopped = False
check("and it does, at the next step", stopped)
line, clock = plan()
try:
    with line.section("site") as s:
        line.cancel()
        with line.step("reach"):
            pass
except Cancelled:
    stopped = True
else:
    stopped = False
row = next(r for r in line.snapshot()["rows"] if r["key"] == "site")
check("a section that is stopped is left failed, and says so, not left running",
      stopped and row["state"] == FAILED and row["detail"] == "stopped", str(row))
line.finish("stopped", "Stopped.")
check("finishing as stopped fails what was running rather than calling it done",
      all(r["state"] != RUNNING for r in line.snapshot()["rows"]))
line, clock = plan()
line.section("site")
line.start("reach")
line.finish("stopped", "Stopped.", open="site")
check("and the step that was running is the one that failed, not one called done", states(line)["site.reach"] == FAILED,
      str(states(line)))
check("the outcome is kept", line.snapshot()["outcome"] == "stopped" and line.snapshot()["summary"] == "Stopped.")
line.finish("found", "too late")
check("an ended check cannot be ended again", line.snapshot()["outcome"] == "stopped")

print("with nobody listening")
idle = timeline.current()
idle.plan("x", [Section("site", "s", "", timeline.WEBSITE)])
idle.section("site")
idle.start("reach")
idle.done("reach", "ok")
idle.cancel()
idle.finish("found", "x")
with idle.section("site") as s:
    with idle.step("reach") as t:
        t.ok("fine")
        t.note("fine")
    s.ok()
check("every call is accepted and nothing is kept", idle.snapshot()["rows"] == [] and not idle.cancelled)
idle.check()
check("and nobody can stop what nobody is watching", True)
with timeline.using(line):
    check("a check can be reported into from the thread doing it", timeline.current() is line)
    with timeline.using(Timeline()):
        pass
    check("and that comes back after a nested one", timeline.current() is line)
check("and stops when the block ends", timeline.current() is not line)
found = []
worker = threading.Thread(target=lambda: found.append(timeline.current() is line))
with timeline.using(line):
    worker.start()
    worker.join()
check("another thread does not report into it unless told to", found == [False], str(found))

print("a worker and a window at once")
line, clock = plan(nearby=4)
stop = threading.Event()
errors: list = []


def reader() -> None:
    try:
        while not stop.is_set():
            snap = line.snapshot()
            assert 0.0 <= snap["fraction"] <= 1.0
            assert all(r["state"] in (PENDING, RUNNING, DONE, SKIPPED, FAILED) for r in snap["rows"])
    except Exception as exc:                     # noqa: BLE001
        errors.append(exc)


watchers = [threading.Thread(target=reader) for _ in range(4)]
for w in watchers:
    w.start()
with timeline.using(line):
    for _ in range(200):
        with line.section("site") as s:
            for key in timeline.WEBSITE:
                with line.step(key) as t:
                    t.note("x")
                    t.ok("y")
            s.ok("z")
        clock.tick(0.01)
stop.set()
for w in watchers:
    w.join()
check("reading it while it is written never sees it half-changed", not errors, str(errors[:1]))

print("drawing it")
from floating_clock import timelineview as view  # noqa: E402


def measure(text, bold=False, small=False):
    """A window's ruler: every character a fixed width, bold wider and small narrower."""
    return len(text) * (8 if bold else 6 if small else 7)


def drawn(line, width=520, phase=0):
    return view.layout(line.snapshot(), view.Metrics(width=width), measure, phase)


def of(out, op):
    return [i for i in out["items"] if i["op"] == op]


line, clock = plan(nearby=2)
line.section("site", "comparing with mawaqit.net")
clock.tick(37)
out = drawn(line)
head = of(out, "text")[:2]
check("the masjid's name heads it, in bold, with the time running beside it",
      head[0]["text"] == "Checking Test Masjid" and head[0]["bold"] and head[1]["text"] == "0:37" and head[1]["anchor"] == "e",
      str(head))
check("the bar is as far along as the timeline says", abs(of(out, "bar")[0]["fraction"] - line.snapshot()["fraction"]) < 1e-9)
check("and in the colour of how it is going: plain while it works", of(out, "bar")[0]["role"] == view.FG)
over, _ = plan()
over.finish("found", "")
check("green when it found something", of(drawn(over), "bar")[0]["role"] == view.GOOD)
over, _ = plan()
over.finish("none", "")
check("red when it found nothing", of(drawn(over), "bar")[0]["role"] == view.BAD)
snap = line.snapshot()
check("every line of the timeline has an icon, in its own state",
      [i["state"] for i in of(out, "icon")] == [r["state"] for r in snap["rows"]])
levels = {r["level"]: i["x"] for r, i in zip(snap["rows"], of(out, "icon"))}
check("and a step is pushed in under its source", levels[1] > levels[0], str(levels))
check("a source's hint is shown beside it", any(i["text"] == "only if this one has nothing" for i in of(out, "text")))
check("a spinner is told which frame to be on", all(i["phase"] == 3 for i in of(drawn(line, phase=3), "icon")))
check("what is running is marked, for the window to scroll to it", out["running"] is not None and out["running"][0] < out["running"][1])
check("and where nothing is running there is nothing to scroll to", drawn(plan()[0])["running"] is None)
rails = of(out, "rail")
check("the sources hang on a line, and the steps of the one that is open on another", len(rails) >= 2, str(rails))
check("which is drawn before what sits on it", [i["op"] for i in out["items"]].index("icon") > max(
    n for n, i in enumerate(out["items"]) if i["op"] == "rail"))

line, clock = plan()
long_reason = "its timetable is posted as an image, which the clock cannot read yet, so nothing at all was taken from it"
with line.section("site"):
    line.start("home")
    line.fail("home", long_reason)
    out = drawn(line, width=360)                  # while the source is open: it folds when it ends
extra = [i for i in of(out, "text") if i["small"] and i["role"] == view.BAD]
check("a reason too long for its line goes under it, in words that were the reason",
      len(extra) > 1 and " ".join(i["text"] for i in extra) == long_reason, str([i["text"] for i in extra]))
check("in the colour of a failure", all(i["role"] == view.BAD for i in extra))
check("every line of it stays inside the room", all(i["x"] + measure(i["text"], False, True) <= 360 - 14 for i in extra))
short = "no times on it"
line, clock = plan()
with line.section("site"):
    line.start("home")
    line.done("home", short)
    out = drawn(line, width=520)
mine =[i for i in of(out, "text") if i["text"] == short][0]
label = [i for i in of(out, "text") if i["text"] == "Reading the home page"][0]
check("a short one goes on the same line as what it belongs to", mine["y"] == label["y"] and mine["x"] > label["x"])

print("drawing it at any width")
line, clock = plan(nearby=3)
with line.section("mawaqit") as m:
    m.fail("its timetable is posted as an image, which the clock cannot read yet")
with line.section("site") as s:
    line.done("reach", "reached example-with-a-quite-long-name.org")
    line.done("feed", "the site has no timetable plug-in")
    line.start("pages", "reading /prayer-times, /salah, /iqama and 3 more")
    clock.tick(12)
    for width in (200, 260, 360, 520, 900):
        out = drawn(line, width=width)
        fits = True
        why = ""
        for i in of(out, "text"):
            w = measure(i["text"], i["bold"], i["small"])
            left = i["x"] - w if i["anchor"] == "e" else i["x"]
            right = i["x"] if i["anchor"] == "e" else i["x"] + w
            if left < 0 or right > width or i["y"] < 0 or i["y"] + i["h"] > out["height"]:
                fits, why = False, "%d: %r at x=%s y=%s (height %s)" % (width, i["text"], i["x"], i["y"], out["height"])
                break
        check("at %d pixels nothing is wider than the window or outside it" % width, fits, why)
    narrow = drawn(line, width=200)
    wide = drawn(line, width=900)
    check("a narrow window is taller, not cut off", narrow["height"] > wide["height"], "%s %s" % (narrow["height"], wide["height"]))
    check("and its height is what it takes", all(i["y"] + i["h"] <= narrow["height"] for i in of(narrow, "text")))
    s.ok("done")

for width in (150, 200):
    long_ones = Timeline(Clock())
    long_ones.plan("A" * 90, [Section("site", "B" * 90, "c" * 60, timeline.WEBSITE)])
    long_ones.section("site")
    out = view.layout(long_ones.snapshot(), view.Metrics(width=width), measure)
    room_ok = all(i["x"] + measure(i["text"], i["bold"], i["small"]) <= width for i in of(out, "text") if i["anchor"] == "w")
    check("one long word with no spaces is cut with an ellipsis and not run off the edge at %d" % width,
          room_ok and any(i["text"].endswith(view.ELLIPSIS) for i in of(out, "text")),
          str([i["text"] for i in of(out, "text")][:3]))

print("what is said above the bar")
line, clock = plan()
check("while it works", view.headline(line.snapshot()) == ("Checking Test Masjid", view.FG))
check("before it has a name", view.headline(Timeline(Clock()).snapshot()) == ("Getting ready", view.FG))
line.finish("found", "")
check("when times were found", view.headline(line.snapshot()) == ("Times found", view.GOOD))
line, clock = plan()
line.finish("none", "nothing")
check("when there were none", view.headline(line.snapshot()) == ("Nothing readable", view.BAD))
line, clock = plan()
line.finish("stopped", "Stopped.")
check("when it was stopped", view.headline(line.snapshot()) == ("Stopped", view.MUTED))
empty = Timeline(Clock())
out = view.layout(empty.snapshot(), view.Metrics(width=400), measure)
check("with nothing planned yet it says it is getting ready", any("Getting ready" in i["text"] for i in of(out, "text")))
empty.finish("none", "x")
out = view.layout(empty.snapshot(), view.Metrics(width=400), measure)
check("and if nothing was ever planned, that is said", any("Nothing was tried" in i["text"] for i in of(out, "text")))
check("a bar can never be drawn outside its trough", all(0.0 <= i["fraction"] <= 1.0 for i in of(
    view.layout(dict(empty.snapshot(), fraction=7.5), view.Metrics(width=400), measure), "bar")))

print("wrapping")
check("words are broken at spaces, none lost", " ".join(view.wrap("one two three four five six", 60, measure)) == "one two three four five six")
check("into lines that fit", all(measure(l) <= 60 for l in view.wrap("one two three four five six", 60, measure)),
      str(view.wrap("one two three four five six", 60, measure)))
check("nothing gives one empty line, not none", view.wrap("", 60, measure) == [""])
check("a word too wide for a line is cut short, not left to overflow",
      all(measure(l) <= 60 for l in view.wrap("x" * 80, 60, measure)) and view.wrap("x" * 80, 60, measure)[0].endswith(view.ELLIPSIS))

print("how it is worded")
check("a stopwatch", [timeline.clock_text(x) for x in (0, 7.9, 59.9, 65, 3600)] == ["0:00", "0:07", "0:59", "1:05", "60:00"],
      str([timeline.clock_text(x) for x in (0, 7.9, 59.9, 65, 3600)]))
check("a step's time", [timeline.span_text(x) for x in (None, 0.04, 9.94, 12.4, 59.6, 65)] ==
      ["", "0.0 s", "9.9 s", "12 s", "60 s", "1:05"], str([timeline.span_text(x) for x in (None, 0.04, 9.94, 12.4, 59.6, 65)]))
check("every step on a plan has words and a duration",
      all(key in timeline.STEPS and timeline.STEPS[key][0] and timeline.STEPS[key][1] > 0
          for key in timeline.WEBSITE + timeline.MAWAQIT))

print()
if failures:
    print("%d timeline check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all timeline checks passed")
