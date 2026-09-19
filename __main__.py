"""Entry point: python -m floating_clock"""

from __future__ import annotations

import os
import sys

from . import __version__, settings as cfg


def _configure_logging() -> None:
    """A rolling log next to the settings file.

    The app runs windowed, so stderr goes nowhere: without this a crash in a
    menu handler is invisible. It used to give up silently when the folder
    would not open, which is how a clock ran two days with no log at all;
    logsetup retries, falls back, and re-attaches later instead.
    """
    from . import logsetup

    logsetup.configure(__version__)


WAIT_ARG = "--wait-for-pid"


def _pid_to_wait_for(argv) -> int:
    """The pid in --wait-for-pid=N, or 0."""
    for arg in argv:
        if arg.startswith(WAIT_ARG + "="):
            try:
                return max(0, int(arg.split("=", 1)[1]))
            except ValueError:
                return 0
    return 0


def _wait_for_exit(pid: int, timeout_s: float = 30.0) -> None:
    """Block until a process has gone, or the timeout passes.

    A clock restarting itself starts its replacement first and quits after;
    the replacement waits here, because until the old one has exited it
    still holds the one-instance lock and the new one would just bow out.
    """
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
        if not handle:
            return                                                   # already gone
        try:
            kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return


def _check() -> int:
    """Import everything a real start needs, then say so and exit.

    What a packaged build is smoke-tested with: a bundle missing one lazily
    imported module looks fine until someone opens the settings window.
    """
    try:
        from . import (                                    # noqa: F401
            alerts, app, caldav, cast, daybar, dpt, google_oauth, hovercard,
            ics, icon, meetings, orgs, outlook, palette, peek, popupmenu,
            prayer, providers, render, routines, settings_ui, sounds, themes,
            timetext, toast, tray, vault, widgets, win32util,
        )
    except Exception as exc:
        print("Floating Clock %s is incomplete: %s: %s"
              % (__version__, exc.__class__.__name__, exc))
        return 1
    print("Floating Clock %s: every module loaded." % __version__)
    return 0


def main(argv: list[str]) -> int:
    if "--reset" in argv:
        print(cfg.reset())
        if "--run" not in argv:
            return 0
    if "--check" in argv:
        return _check()
    if sys.platform != "win32":
        print("Floating Clock targets Windows: it needs the Win32 layered-window API.")
        return 2

    _configure_logging()
    from . import win32util as w32

    waiting = _pid_to_wait_for(argv)
    if waiting:
        _wait_for_exit(waiting)

    if not w32.single_instance("Local\\FloatingClock.Instance"):
        # Relaunching is the natural thing to try when the clock stops
        # responding to the mouse, so make it the rescue: nudge the running
        # copy back to a usable state instead of starting a second one.
        cfg.request_rescue()
        print("Floating Clock is already running -- restored it to a usable state.")
        return 0

    from .app import FloatingClock

    app = FloatingClock()
    if "--settings" in argv:
        # Diagnostics: open the dialog straight away, so the log shows what
        # happens to it without anyone having to click.
        app.root.after(800, app.open_settings)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
