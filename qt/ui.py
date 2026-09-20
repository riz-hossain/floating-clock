"""Getting from a worker thread back onto the UI thread.

Everything slow -- reading a calendar, searching for a masjid, fetching the
prayer times -- runs on a worker, and its answer has to be shown on the UI
thread. The obvious way to hop back, QTimer.singleShot(0, fn), does not work
from there: a timer needs an event loop in the thread that starts it, and a
plain Python thread has none, so the call is accepted and never fires. The
symptom is a dialog stuck on "Searching..." for ever, with nothing in the log.

A signal is the supported way across. Emitted from any thread, it is delivered
to a receiver on the receiver's own thread through a queued connection, which
is the UI thread here because the bridge is built on it.
"""

from __future__ import annotations

import logging

from PySide6 import QtCore

log = logging.getLogger(__name__)


class _Bridge(QtCore.QObject):
    _call = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        # Queued even when emitted from the UI thread itself, so a call made
        # there is deferred to the next turn of the event loop, exactly as
        # singleShot(0) was, rather than running re-entrantly.
        self._call.connect(self._run, QtCore.Qt.ConnectionType.QueuedConnection)

    @QtCore.Slot(object)
    def _run(self, fn) -> None:
        try:
            fn()
        except Exception:
            # One bad callback must not take the bridge, or the app, with it.
            log.exception("A call handed to the UI thread raised")


_bridge: _Bridge | None = None


def init() -> None:
    """Build the bridge. Run once, on the UI thread, after QApplication exists.

    Its thread is the thread that builds it, and that is where every call
    posted to it will run -- so building it from a worker would send them all
    to the wrong place.
    """
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()


def post(fn) -> None:
    """Run fn on the UI thread, soon. Safe to call from any thread."""
    if _bridge is None:
        raise RuntimeError("qt.ui.init() has not been called on the UI thread")
    _bridge._call.emit(fn)
