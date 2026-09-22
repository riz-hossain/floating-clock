"""Offline checks for playing the adhan through this computer's own speakers, no smart speaker or
assistant needed (localaudio.py).

Every external call -- the DLL, the subprocess, which players exist, the network -- is a parameter,
so this drives the actual decisions (which platform, what command line, how volume is mapped, when a
downloaded file is cleaned up, that a bad file or a missing player is reported rather than crashing)
without a real DLL, a real player, or a real network. No audio plays and nothing here can flake.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-localaudio.py
"""

from __future__ import annotations

import os
import sys
import threading

from floating_clock import localaudio as la

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


def safe(fn, *a, **kw):
    """Call fn, or say what it raised -- for the calls that are meant to turn a real failure into
    a plain string, so a regression that lets the exception straight through fails a check here
    instead of crashing the whole run before it can be reported."""
    try:
        return fn(*a, **kw)
    except Exception as exc:                        # noqa: BLE001
        return "!! raised %s instead of reporting it: %s" % (exc.__class__.__name__, exc)


HERE = os.path.abspath(__file__)      # a real file on disk, so check_media("") style checks pass


# --- available() --------------------------------------------------------------------------------
print("whether this computer can play a file itself")
check("Windows always can (MCI ships with it)", la.available(platform="win32") == "")
check("so does macOS (afplay ships with it)", la.available(platform="darwin") == "")
found = la.available(platform="linux", which=lambda name: "/usr/bin/" + name if name == "ffplay" else None)
check("Linux can, if any of the known players is installed", found == "")
missing = la.available(platform="linux", which=lambda name: None)
check("and says so, by name, when none of them is", missing == (
    "no audio player (paplay, ffplay, mpg123, cvlc) was found on this computer"), missing)

# --- the download step ---------------------------------------------------------------------------
print("getting the audio onto local disk")
check("a local path is used as it is, and is not this module's to delete",
      safe(la._local_copy, HERE) == (HERE, None))


class Reply:
    """A stand-in for what urlopen() gives back: a context manager with .read(n)."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1):
        if size is None or size < 0:
            size = len(self.data) - self.at
        chunk = self.data[self.at:self.at + size]
        self.at += len(chunk)
        return chunk


def opens(data: bytes):
    def opener(request, timeout=None):
        return Reply(data)
    return opener


path, cleanup = la._local_copy("https://example.com/azan.mp3", opener=opens(b"pretend-audio-bytes"))
try:
    check("a URL is downloaded to a real temporary file", os.path.isfile(path) and path != "https://example.com/azan.mp3")
    check("holding exactly what was served", open(path, "rb").read() == b"pretend-audio-bytes")
    check("named after the URL's own extension", path.endswith(".mp3"), path)
    check("and cleanup is given, since this module fetched it and it is this module's to delete", cleanup is not None)
finally:
    cleanup()
check("cleanup actually removes it", not os.path.isfile(path))

path2, _ = la._local_copy("https://example.com/no-extension-here", opener=opens(b"x"))
try:
    check("a URL with no extension of its own still gets a usable temporary file", os.path.isfile(path2))
finally:
    os.remove(path2)


def fails_to_open(request, timeout=None):
    raise OSError("could not connect")


try:
    la._fetch("https://example.com/x.mp3", opener=fails_to_open)
    raised = False
except OSError:
    raised = True
check("a download that cannot connect raises, rather than being swallowed here", raised)

# --- play(): the dispatch, before any platform-specific mechanics ---------------------------------
print("play(): choosing a platform, fetching first if it must")
calls: list = []


def fake_starter(problem: str = ""):
    def starter(path, volume, cleanup):
        calls.append((path, volume, cleanup))
        return problem
    return starter


calls.clear()
result = la.play(HERE, volume=0.5, platform="win32", windows=fake_starter())
check("on Windows the windows starter is used", calls and calls[0][0] == HERE and calls[0][1] == 0.5, str(calls))
check("and play() returns whatever it says", result == "")

calls.clear()
la.play(HERE, platform="darwin", macos=fake_starter())
check("on macOS the macos starter is used", calls and calls[0][0] == HERE, str(calls))

calls.clear()
la.play(HERE, platform="linux", linux=fake_starter())
check("elsewhere the linux starter is used", calls and calls[0][0] == HERE, str(calls))

calls.clear()
problem = la.play("", platform="win32", windows=fake_starter())
check("nothing chosen is refused before any platform is even asked", problem == "no audio chosen" and not calls, str(calls))

calls.clear()
problem = la.play("/does/not/exist.mp3", platform="win32", windows=fake_starter())
check("a file that is not there is refused the same way, before being asked to play",
      problem == "no file at /does/not/exist.mp3" and not calls, problem)

calls.clear()
result = la.play("https://example.com/azan.mp3", platform="win32", windows=fake_starter(),
                 opener=opens(b"y"))
check("a URL is downloaded before the starter ever sees it",
      calls and calls[0][0] != "https://example.com/azan.mp3" and os.path.isfile(calls[0][0]), str(calls))
downloaded = calls[0][0]
calls[0][2]()          # run the cleanup play() handed the starter, the way a real one would once done
check("and the starter's own cleanup, once run, removes the download", not os.path.isfile(downloaded))

calls.clear()
problem = safe(la.play, "https://example.com/azan.mp3", platform="win32", windows=fake_starter(),
              opener=fails_to_open)
check("a download that fails is reported, and no starter is ever asked to play nothing",
      problem.startswith("could not fetch the adhan:") and not calls, problem)

leftover: list = []


def starter_that_raises(path, volume, cleanup):
    leftover.append(cleanup)
    raise RuntimeError("the driver vanished")


problem = safe(la.play, "https://example.com/azan.mp3", platform="win32", windows=starter_that_raises,
              opener=opens(b"z"))
check("a starter that raises is turned into a message, not a crash", problem == "the driver vanished", problem)
check("a starter is at least given the chance to clean up on its way out", bool(leftover))
downloaded_path = None


def starter_records_path(path, volume, cleanup):
    global downloaded_path
    downloaded_path = path
    raise RuntimeError("boom")


safe(la.play, "https://example.com/azan.mp3", platform="win32", windows=starter_records_path, opener=opens(b"z"))
check("concretely: the file play() downloaded is gone after a starter that raised",
      downloaded_path is not None and not os.path.isfile(downloaded_path), downloaded_path)

problem = la.play(HERE, platform="win32", windows=fake_starter("no such device"))
check("a starter that answers with a problem is passed straight through", problem == "no such device")

path_when_refused = None


def starter_reports_a_problem(path, volume, cleanup):
    global path_when_refused
    path_when_refused = path
    return "the device refused it"        # a problem returned, not raised -- a different code path in play()


la.play("https://example.com/azan.mp3", platform="win32", windows=starter_reports_a_problem, opener=opens(b"q"))
check("a starter that returns a problem, rather than raising one, still gets its download cleaned up",
      path_when_refused is not None and not os.path.isfile(path_when_refused), path_when_refused)

# --- Windows: MCI --------------------------------------------------------------------------------
print("Windows: MCI")


class FakeMci:
    """A stand-in for winmm: records every command, and can be told how to answer one."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.refuse: dict[str, str] = {}       # a command word -> the error text to give it
        self.status_sequence = ["playing", "playing", "stopped"]
        self.closed = threading.Event()

    def mciSendStringW(self, command, reply, buflen, hwnd):
        self.commands.append(command)
        word = command.split(" ", 1)[0]
        if word in self.refuse:
            return 999
        if command.startswith("status") and command.endswith("mode"):
            state = self.status_sequence.pop(0) if len(self.status_sequence) > 1 else self.status_sequence[0]
            reply.value = state
        elif word == "close":
            self.closed.set()
        return 0

    def mciGetErrorStringW(self, err, reply, buflen):
        reply.value = self.refuse.get(self._last_word(), "some MCI error")
        return 1

    def _last_word(self):
        return self.commands[-1].split(" ", 1)[0] if self.commands else ""


dll = FakeMci()
dll.status_sequence = ["playing", "playing", "stopped"]
result = la._play_windows(HERE, 0.5, cleanup=None, dll=dll, sleep=lambda s: None)
check("it starts cleanly", result == "")
check("opened as the Windows Media wrapper, which plays MP3 as well as WAV and takes a volume",
      any(c.startswith('open "') and "type mpegvideo" in c for c in dll.commands), str(dll.commands))
check("with a unique alias -- a second azan while one still plays must not collide",
      len({c.split("alias ")[-1] for c in dll.commands if "alias " in c}) == 1)
check("the volume asked for is on the 0..1000 scale MCI uses", any("volume to 500" in c for c in dll.commands), str(dll.commands))
check("play is sent", any(c.startswith("play ") for c in dll.commands))
check("it waits for the alias to stop, then closes it", dll.closed.wait(timeout=2.0) and
      any(c.startswith("close ") for c in dll.commands))

dll_extreme = FakeMci()
la._play_windows(HERE, 3.0, cleanup=None, dll=dll_extreme, sleep=lambda s: None)
check("a volume above the top is clamped to MCI's own maximum, not sent as-is",
      any("volume to 1000" in c for c in dll_extreme.commands), str(dll_extreme.commands))
dll_extreme2 = FakeMci()
la._play_windows(HERE, -1.0, cleanup=None, dll=dll_extreme2, sleep=lambda s: None)
check("and a negative one is clamped to zero, not sent negative",
      any("volume to 0" in c for c in dll_extreme2.commands), str(dll_extreme2.commands))

dll_a, dll_b = FakeMci(), FakeMci()
la._play_windows(HERE, None, cleanup=None, dll=dll_a, sleep=lambda s: None)
la._play_windows(HERE, None, cleanup=None, dll=dll_b, sleep=lambda s: None)
alias_a = next(c.split("alias ")[-1] for c in dll_a.commands if "alias " in c)
alias_b = next(c.split("alias ")[-1] for c in dll_b.commands if "alias " in c)
check("two separate plays get two different aliases -- a fixed one would collide if the first is still open",
      alias_a != alias_b, "%s vs %s" % (alias_a, alias_b))

dll2 = FakeMci()
dll2.refuse["setaudio"] = "The driver cannot recognize the specified command."
result = la._play_windows(HERE, 0.4, cleanup=None, dll=dll2, sleep=lambda s: None)
check("a device that will not take a volume (the plain waveaudio driver, for one) still plays",
      result == "" and dll2.closed.wait(timeout=2.0), result)
check("play was still sent even though setaudio was refused", any(c.startswith("play ") for c in dll2.commands))

dll3 = FakeMci()
dll3.refuse["open"] = "The specified device is not open or is not recognized by MCI."
result = la._play_windows(HERE, None, cleanup=None, dll=dll3, sleep=lambda s: None)
check("a file MCI cannot open at all is reported", result == dll3.refuse["open"], result)
check("and nothing is played or closed, since nothing was ever opened",
      not any(c.startswith("play ") or c.startswith("close ") for c in dll3.commands), str(dll3.commands))

dll4 = FakeMci()
dll4.refuse["play"] = "A problem occurred in initializing MCI."
result = la._play_windows(HERE, None, cleanup=None, dll=dll4, sleep=lambda s: None)
check("a file that opens but will not play is reported", result == dll4.refuse["play"], result)
check("and is still closed, since it was opened", any(c.startswith("close ") for c in dll4.commands), str(dll4.commands))

cleaned = threading.Event()
dll5 = FakeMci()
la._play_windows(HERE, None, cleanup=cleaned.set, dll=dll5, sleep=lambda s: None)
check("the cleanup play() was given runs once playback has actually finished", cleaned.wait(timeout=2.0))

# Off Windows there is truly no winmm; on Windows there is, so this is forced the same way on either.
real_winmm, la._winmm = la._winmm, None
try:
    result = la._play_windows(HERE, None, cleanup=None, dll=None, sleep=lambda s: None)
finally:
    la._winmm = real_winmm
check("with no MCI service at all, that is reported plainly rather than raising", "MCI" in result, result)

# --- macOS: afplay ---------------------------------------------------------------------------------
print("macOS: afplay")


class FakeProcess:
    def __init__(self) -> None:
        self.waited = threading.Event()

    def wait(self):
        self.waited.set()


def recording_popen(log):
    def popen(args, **kw):
        log.append(args)
        return FakeProcess()
    return popen


log: list = []
result = la._play_macos(HERE, 0.5, cleanup=None, popen=recording_popen(log))
check("it starts cleanly", result == "")
check("afplay is asked for, with the file last", log and log[0][0] == "afplay" and log[0][-1] == HERE, str(log))
check("and the volume, 0..1, formatted for the command line", "-v" in log[0] and "0.50" in log[0], str(log))

log = []
la._play_macos(HERE, None, cleanup=None, popen=recording_popen(log))
check("with no volume given, afplay is left at whatever it already plays at", "-v" not in log[0], str(log))

cleaned2 = threading.Event()
proc_log: list = []


def popen_tracking(args, **kw):
    proc = FakeProcess()
    proc_log.append(proc)
    return proc


la._play_macos(HERE, None, cleanup=cleaned2.set, popen=popen_tracking)
check("its cleanup runs once afplay itself has exited", cleaned2.wait(timeout=2.0) and proc_log[0].waited.is_set())


def popen_missing(args, **kw):
    raise OSError("[Errno 2] No such file or directory: 'afplay'")


result = la._play_macos(HERE, None, cleanup=None, popen=popen_missing)
check("if afplay itself somehow is not there, that is reported rather than crashing", "No such file" in result, result)

# --- Linux: whichever player is installed -----------------------------------------------------------
print("Linux: whichever player is installed")


def which_only(*names):
    def which(name):
        return ("/usr/bin/" + name) if name in names else None
    return which


log = []
la._play_linux(HERE, 0.5, cleanup=None, which=which_only("paplay", "ffplay"), popen=recording_popen(log))
check("paplay is preferred when it is there", log and log[0][0] == "paplay", str(log))
check("its volume is PulseAudio's 0..65536 scale", "--volume" in log[0] and "32768" in log[0], str(log))

log = []
la._play_linux(HERE, 0.5, cleanup=None, which=which_only("ffplay", "mpg123"), popen=recording_popen(log))
check("ffplay next, when paplay is not there", log and log[0][0] == "ffplay", str(log))
check("its volume is 0..100", "-volume" in log[0] and "50" in log[0], str(log))

log = []
la._play_linux(HERE, None, cleanup=None, which=which_only("mpg123", "cvlc"), popen=recording_popen(log))
check("mpg123 next", log and log[0][0] == "mpg123" and HERE in log[0], str(log))

log = []
la._play_linux(HERE, None, cleanup=None, which=which_only("cvlc"), popen=recording_popen(log))
check("cvlc last", log and log[0][0] == "cvlc" and HERE in log[0], str(log))

result = la._play_linux(HERE, None, cleanup=None, which=which_only(), popen=recording_popen([]))
check("with none of the four installed, that is said plainly, and nothing is launched",
      result.startswith("no audio player"), result)

result = safe(la._play_linux, HERE, None, cleanup=None, which=which_only("paplay"),
             popen=lambda args, **kw: (_ for _ in ()).throw(OSError("permission denied")))
check("a player that is found but will not start is reported, not crashed on",
      result == "permission denied", result)     # exactly that, from _play_linux itself -- not safe()'s own fallback text

print()
if failures:
    print("%d check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("the adhan can be played through this computer, and every failure is reported plainly")
