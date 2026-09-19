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


def main(argv: list[str]) -> int:
    from .. import settings as cfg

    if "--reset" in argv:
        print(cfg.reset())
        if "--run" not in argv:
            return 0
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
