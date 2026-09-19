"""Entry point for the Qt host: `python -m floating_clock.qt.main`."""

from __future__ import annotations

import logging
import os
import sys


def _configure_logging() -> None:
    from logging.handlers import RotatingFileHandler

    from .. import __version__, settings as cfg

    try:
        os.makedirs(cfg.config_dir(), exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(cfg.config_dir(), "FloatingClock.log"),
            maxBytes=512_000, backupCount=1, encoding="utf-8",
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    sys.excepthook = lambda *exc: logging.getLogger("floating_clock").critical(
        "Unhandled exception", exc_info=exc
    )
    logging.getLogger("floating_clock").info(
        "Floating Clock %s (Qt host) starting on %s", __version__, sys.platform
    )


def _check() -> int:
    """Import everything a real start needs, then say so and exit.

    What a packaged build is smoke-tested with: a bundle missing one lazily
    imported module looks perfectly fine until someone opens the settings
    window, and the build that shipped it is long gone by then. --reset is
    no substitute, since it returns before Qt is ever touched.
    """
    from .. import __version__

    try:
        from PySide6 import QtWidgets                      # noqa: F401
        from .. import (                                   # noqa: F401
            alerts, cast, caldav, daybar, dpt, google_oauth, hovercard, ics,
            icon, meetings, orgs, outlook, palette, prayer, providers, render,
            routines, sounds, themes, timetext, vault,
        )
        from . import bitmapwindow, clock, menu, settings  # noqa: F401
    except Exception as exc:
        print("Floating Clock %s is incomplete: %s: %s"
              % (__version__, exc.__class__.__name__, exc))
        return 1
    print("Floating Clock %s (Qt host): every module loaded." % __version__)
    return 0


def main(argv: list[str]) -> int:
    from .. import settings as cfg

    if "--reset" in argv:
        print(cfg.reset())
        if "--run" not in argv:
            return 0
    if "--check" in argv:
        return _check()
    _configure_logging()

    from PySide6 import QtCore, QtWidgets

    from .clock import QtClock

    QtCore.QCoreApplication.setApplicationName("Floating Clock")
    QtCore.QCoreApplication.setOrganizationName("Floating Clock")
    app = QtWidgets.QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    clock = QtClock(app)
    if "--settings" in argv:
        QtCore.QTimer.singleShot(800, clock.open_settings)
    clock.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
