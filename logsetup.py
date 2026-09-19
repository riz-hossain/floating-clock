"""Where the clock writes its log, and making sure it keeps writing.

The log is the only witness a windowed app has. At the first sign-in after a
Windows update the settings folder can refuse to open for a moment, and the
clock that started into that moment ran for two days without a single line
of log -- so nobody could see that its prayer triggers had stopped. So: retry
the real folder, fall back to another one rather than go silent, and look
again later so the log finds its way home.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler

from . import settings as cfg

LOG_NAME = "FloatingClock.log"
MAX_BYTES = 512_000
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# The post-update lock clears in seconds; this waits out about eight.
ATTEMPTS = 5
PAUSE_S = 2.0

_state: dict = {"folder": "", "handler": None}


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def folders() -> list[str]:
    """The settings folder first, then places that are nearly always writable."""
    found = [cfg.config_dir()]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(os.path.join(local, cfg.APP_NAME))
    found.append(os.path.join(tempfile.gettempdir(), cfg.APP_NAME))
    unique: list[str] = []
    for folder in found:
        if not any(_same(folder, seen) for seen in unique):
            unique.append(folder)
    return unique


def open_handler(folder: str) -> logging.Handler:
    os.makedirs(folder, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(folder, LOG_NAME), maxBytes=MAX_BYTES, backupCount=1, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(FORMAT))
    return handler


def attach(candidates, attempts: int = ATTEMPTS, pause: float = PAUSE_S,
           sleep=time.sleep, opener=open_handler):
    """(handler, folder): the first candidate that opens.

    The first one -- the real folder -- is tried several times before
    settling for a fallback, because the failure worth planning for is the
    brief one. (None, "") only when nothing at all will open.
    """
    candidates = list(candidates)
    if not candidates:
        return None, ""
    main, fallbacks = candidates[0], candidates[1:]
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            return opener(main), main
        except OSError:
            if attempt + 1 < attempts:
                sleep(pause)
    for folder in fallbacks:
        try:
            return opener(folder), folder
        except OSError:
            continue
    return None, ""


def _install(handler: logging.Handler, folder: str) -> None:
    root = logging.getLogger()
    old = _state.get("handler")
    if old is not None:
        root.removeHandler(old)
        try:
            old.close()
        except Exception:
            pass
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    _state["handler"], _state["folder"] = handler, folder


def folder() -> str:
    """Where the log is going right now; "" when it is going nowhere."""
    return _state.get("folder") or ""


def configure(version: str, sleep=time.sleep) -> str:
    """Open the log at startup. Returns the folder it landed in."""
    _hook_exceptions()
    handler, where = attach(folders(), sleep=sleep)
    if handler is None:
        return ""
    _install(handler, where)
    log = logging.getLogger("floating_clock")
    log.info("Floating Clock %s starting; settings in %s", version, cfg.config_dir())
    if not _same(where, cfg.config_dir()):
        log.warning("The settings folder would not take the log; writing to %s until it does",
                    where)
    return where


def ensure() -> str:
    """Called now and then by the running clock: open a log that never
    opened, and move it back to the settings folder once that will take it."""
    main = cfg.config_dir()
    current = folder()
    if current and _same(current, main):
        return current
    no_wait = lambda _seconds: None  # noqa: E731
    handler, where = attach([main], attempts=1, sleep=no_wait)
    if handler is None and not current:
        handler, where = attach(folders()[1:], attempts=1, sleep=no_wait)
    if handler is None:
        return current
    _install(handler, where)
    logging.getLogger("floating_clock").info("Log re-attached in %s", where)
    return where


def _hook_exceptions() -> None:
    """Unhandled errors go to the log, from the main thread and from workers.

    Set whether or not a log opened: a handler attached later still gets
    them, which is the whole point of ensure()."""
    log = logging.getLogger("floating_clock")
    sys.excepthook = lambda *exc: log.critical("Unhandled exception", exc_info=exc)

    def thread_hook(args) -> None:
        log.error(
            "Unhandled exception in thread %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = thread_hook
