"""Playing the adhan through this computer's own speakers or headphones.

Casting (cast.py) needs a Google or Nest speaker on the network; a routine trigger
needs Alexa, or Home Assistant, or IFTTT already set up. Travelling with just the
laptop, none of that may be there -- so this plays the audio itself, the plain way any
program on the machine would.

Nothing is bundled to do it. Every desktop already carries something:

  * Windows has MCI, a service inside winmm.dll every Windows has shipped with since
    3.1. It plays MP3 as well as WAV, and it needs no window.
  * macOS ships `afplay` on every machine.
  * Linux has no one guaranteed player, so the first of `paplay`, `ffplay`, `mpg123`
    or `cvlc` that is actually installed is used, in that order -- paplay first
    because it is the one whose volume can be set the same way as the others.

A URL is downloaded to a temporary file first (MCI in particular wants a local
path), and the file is deleted again once playback ends. The audio is always the
user's own file or link -- nothing is shipped or licensed, the rule cast.py and
sounds.py already follow.

Toolkit-free. Every external call -- the DLL, the subprocess, the download, which
players exist -- is a parameter with a real default, so the decisions made here are
tested without a real DLL, a real player, or a real network.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

from . import cast as cast_mod

log = logging.getLogger(__name__)

MCI_TIMEOUT_S = 8.0          # an MCI call that has not answered in this long is given up on
DOWNLOAD_TIMEOUT_S = 20.0
POLL_S = 0.3                 # how often "is it still playing" is asked, on Windows
# Tried in order; paplay first because, alone of the four, its volume can be set the
# same way the others' can (a plain 0..1).
LINUX_PLAYERS = ("paplay", "ffplay", "mpg123", "cvlc")

WINDOWS = sys.platform.startswith("win")
_winmm = None
if WINDOWS:
    _winmm = ctypes.windll.winmm
    _winmm.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    _winmm.mciSendStringW.restype = ctypes.c_uint
    _winmm.mciGetErrorStringW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    _winmm.mciGetErrorStringW.restype = ctypes.c_int


def _short(exc: BaseException) -> str:
    return (str(exc).strip()[:90]) or exc.__class__.__name__


def available(platform: str | None = None, which=shutil.which) -> str:
    """Empty when this computer can play a file itself, else why not."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("win") or platform == "darwin":
        return ""            # MCI and afplay ship with the OS; there is nothing to be missing
    found = next((name for name in LINUX_PLAYERS if which(name)), None)
    if found:
        return ""
    return "no audio player (%s) was found on this computer" % ", ".join(LINUX_PLAYERS)


# --- getting the audio onto local disk ---------------------------------------------------------
def _fetch(url: str, opener=urllib.request.urlopen) -> str:
    """Download `url` to a temporary file and return its path. Raises on failure."""
    request = urllib.request.Request(url, headers={"User-Agent": "FloatingClock/1.0 (adhan)"})
    with opener(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
        suffix = os.path.splitext(url.split("?", 1)[0])[1][:8] or ".audio"
        fd, path = tempfile.mkstemp(prefix="floating-clock-adhan-", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                shutil.copyfileobj(response, fh, length=64 * 1024)
        except Exception:
            _forget(path)
            raise
        return path


def _forget(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        log.debug("could not remove a temporary adhan file", exc_info=True)


def _local_copy(media: str, opener=urllib.request.urlopen):
    """(path, cleanup): cleanup is a no-arg callable to run once playback ends, or None
    for a file that was already the user's own and is not this module's to delete."""
    if not cast_mod.is_url(media):
        return media.strip(), None
    path = _fetch(media, opener=opener)
    return path, (lambda: _forget(path))


def _finish_in_background(wait, cleanup) -> None:
    """Run `wait()` (however long playback takes) then `cleanup()`, off the caller's thread."""
    def work() -> None:
        try:
            wait()
        except Exception:
            log.debug("waiting for local playback to finish raised", exc_info=True)
        if cleanup:
            cleanup()
    threading.Thread(target=work, name="floating-clock-local-adhan-wait", daemon=True).start()


# --- Windows: MCI --------------------------------------------------------------------------------
class MciError(Exception):
    """One MCI command failed. The message is what Windows itself says went wrong."""


def _mci(command: str, dll=None) -> str:
    dll = _winmm if dll is None else dll
    if dll is None:
        raise MciError("no MCI service on this computer")
    reply = ctypes.create_unicode_buffer(256)
    err = dll.mciSendStringW(command, reply, 255, None)
    if err:
        why = ctypes.create_unicode_buffer(256)
        if not dll.mciGetErrorStringW(err, why, 255):
            why.value = "MCI error %d" % err
        raise MciError(why.value)
    return reply.value


def _play_windows(path: str, volume, cleanup, dll=None, sleep=time.sleep) -> str:
    alias = "adhan%s" % uuid.uuid4().hex[:12]     # unique: a second azan must not collide with one still playing
    # "type mpegvideo" -- the Windows Media MCI wrapper -- plays WAV as well as MP3 and answers
    # setaudio; the plain auto-detected "waveaudio" driver a bare `open` gives a WAV file does not
    # ("the driver cannot recognize the specified command"), so volume on it would always fail.
    try:
        _mci('open "%s" type mpegvideo alias %s' % (path, alias), dll)
    except MciError as exc:
        return _short(exc)
    try:
        if volume is not None:
            try:
                _mci("setaudio %s volume to %d" % (alias, max(0, min(1000, round(volume * 1000)))), dll)
            except MciError:
                log.debug("this file's MCI driver would not take a volume", exc_info=True)
        _mci("play %s" % alias, dll)
    except MciError as exc:
        _mci_close(alias, dll)
        return _short(exc)

    def wait() -> None:
        try:
            while _mci("status %s mode" % alias, dll).strip().lower() == "playing":
                sleep(POLL_S)
        finally:
            _mci_close(alias, dll)

    _finish_in_background(wait, cleanup)
    return ""


def _mci_close(alias: str, dll=None) -> None:
    try:
        _mci("close %s" % alias, dll)
    except MciError:
        pass          # already gone; nothing more to release


# --- macOS: afplay -------------------------------------------------------------------------------
def _play_macos(path: str, volume, cleanup, popen=subprocess.Popen) -> str:
    args = ["afplay"]
    if volume is not None:
        args += ["-v", "%.2f" % max(0.0, min(1.0, float(volume)))]
    args.append(path)
    try:
        proc = popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return _short(exc)
    _finish_in_background(proc.wait, cleanup)
    return ""


# --- Linux: whichever player is actually installed ------------------------------------------------
def _linux_command(name: str, path: str, volume) -> list:
    if name == "paplay":
        args = ["paplay"]
        if volume is not None:
            args += ["--volume", str(round(max(0.0, min(1.0, float(volume))) * 65536))]
        return args + [path]
    if name == "ffplay":
        args = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
        if volume is not None:
            args += ["-volume", str(round(max(0.0, min(1.0, float(volume))) * 100))]
        return args + [path]
    if name == "mpg123":
        return ["mpg123", "-q", path]              # no simple linear volume flag; plays at system volume
    return ["cvlc", "--play-and-exit", "--quiet", path]        # cvlc


def _play_linux(path: str, volume, cleanup, which=shutil.which, popen=subprocess.Popen) -> str:
    name = next((n for n in LINUX_PLAYERS if which(n)), None)
    if name is None:
        return available(platform="linux", which=which)
    try:
        proc = popen(_linux_command(name, path, volume), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return _short(exc)
    _finish_in_background(proc.wait, cleanup)
    return ""


# --- the one call the rest of the clock makes -----------------------------------------------------
def play(media: str, volume: float | None = None, platform: str | None = None,
         windows=None, macos=None, linux=None, opener=urllib.request.urlopen) -> str:
    """Play `media` (a local file or a URL) through this computer. Empty when it started, else why not.

    Blocking only long enough to know whether it started -- discovering nothing has to happen here,
    unlike casting, but the file may need fetching first. Meant for a worker thread regardless, the
    same as cast.play: playback itself, and its own cleanup, continue after this returns.
    """
    platform = sys.platform if platform is None else platform
    problem = cast_mod.check_media(media)
    if problem:
        return problem
    starter = (windows or _play_windows) if platform.startswith("win") else \
        (macos or _play_macos) if platform == "darwin" else (linux or _play_linux)
    try:
        path, cleanup = _local_copy(media, opener=opener)
    except Exception as exc:
        return "could not fetch the adhan: %s" % _short(exc)
    try:
        problem = starter(path, volume, cleanup)
    except Exception as exc:
        problem = _short(exc)
    if problem and cleanup:
        cleanup()
    return problem
