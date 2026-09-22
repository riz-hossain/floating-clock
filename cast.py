"""Playing the adhan on a Google or Nest speaker, without a routine.

Alexa routines can be started by a device, so a trigger skill turns a plain
web address into one and prayer.py just opens that address. Google Home has
no such starter: its automations begin from a time, from the sun, or from a
device's state, and none of those is a thing a clock on a desk can move. So
for Google the clock skips the routine and talks to the speaker itself, over
the local network.

That turns out to be the better half of the bargain. There is no account to
link, no skill to enable and nothing in the path that can be switched off by
someone else. The volume is set on the same connection that starts the audio,
so the "set volume, wait ten seconds, then play" race that catches every
Alexa routine cannot happen here at all.

The audio is the user's own file or URL. Nothing is shipped or licensed --
the rule sounds.py already follows. A file on disk is handed over by serving
it for as long as the speaker takes to play it: a Cast device fetches the
media itself, so there has to be something at this machine's LAN address for
it to fetch. That server answers one file, for a bounded few minutes, and
nothing else.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

# Long enough for a slow speaker to answer; short enough that a prayer whose
# speaker is switched off does not hold the worker past its grace window.
DISCOVER_TIMEOUT = 12.0
CONNECT_TIMEOUT = 12.0

# How long a local file stays reachable after it is handed over. An adhan runs
# two or three minutes; a speaker that re-fetches partway through must still
# find it, and a listener left open for ever would be a quiet surprise.
SERVE_SECONDS = 600.0

NO_LIBRARY = ("casting needs the pychromecast package, which this build does "
              "not have")


def _pychromecast():
    """The library, or None. Optional: the rest of the clock works without it."""
    try:
        import pychromecast
    except ImportError:
        return None
    return pychromecast


def available() -> str:
    """Empty when casting can be used here, else why it cannot."""
    return "" if _pychromecast() is not None else NO_LIBRARY


# --- what the user typed ----------------------------------------------------
def is_url(media: str) -> bool:
    return (media or "").strip().lower().startswith(("http://", "https://"))


def check_media(media: str) -> str:
    """Empty when the audio could be played, else what is wrong with it.

    The mistake worth naming is a path that has since been moved or renamed:
    it is the one that turns a working adhan into a silent prayer months
    later, with nothing on screen to say why.
    """
    media = (media or "").strip()
    if not media:
        return "no audio chosen"
    if is_url(media):
        return ""
    if not os.path.isfile(media):
        return "no file at %s" % media
    return ""


def content_type(media: str) -> str:
    """The speaker is told what it is fetching; it will not guess."""
    guess, _ = mimetypes.guess_type(media.split("?", 1)[0])
    return guess if (guess or "").startswith("audio/") else "audio/mpeg"


# --- serving a local file to the speaker ------------------------------------
def lan_address(peer: str = "8.8.8.8") -> str:
    """This machine's address on the network the speaker is on.

    Asked of the routing table rather than of the hostname: a laptop with a
    VPN up, or with WSL's virtual adapter installed, resolves its own name to
    an address the speaker cannot reach.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((peer, 53))      # no packet leaves; it only picks a route
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class _OneFileHandler(BaseHTTPRequestHandler):
    """Serves the single file its server was built around, and nothing else.

    Range matters: Cast devices commonly ask for a byte range rather than the
    whole file, and a server that answers 200 to a Range request gets audio
    that stalls partway or refuses to start at all.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:     # no stderr from a windowed app
        log.debug("cast http: " + fmt, *args)

    def _span(self, size: int):
        """(start, end) inclusive from a Range header, or None."""
        header = self.headers.get("Range", "")
        if not header.startswith("bytes="):
            return None
        first, _, last = header[len("bytes="):].partition("-")
        try:
            if not first:                          # bytes=-500: the final 500
                return max(0, size - int(last)), size - 1
            start = int(first)
            end = int(last) if last else size - 1
        except ValueError:
            return None
        if start >= size or start > end:
            return None
        return start, min(end, size - 1)

    def _send(self, body: bool) -> None:
        path = self.server.file_path               # type: ignore[attr-defined]
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error(404)
            return
        span = self._span(size)
        start, end = span if span else (0, size - 1)
        self.send_response(206 if span else 200)
        self.send_header("Content-Type", self.server.content_type)  # type: ignore[attr-defined]
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if span:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if not body:
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(64 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (OSError, ConnectionError):
            pass                                   # the speaker hung up; nothing to do

    def do_GET(self) -> None:
        self._send(body=True)

    def do_HEAD(self) -> None:
        self._send(body=False)


def serve_file(path: str, seconds: float = SERVE_SECONDS) -> str:
    """Put one file on this machine's LAN address and return its URL.

    The server retires itself after `seconds`, so a clock left running for
    weeks is not also a file server for weeks.
    """
    address = lan_address()
    server = ThreadingHTTPServer((address, 0), _OneFileHandler)
    server.file_path = path                        # type: ignore[attr-defined]
    server.content_type = content_type(path)       # type: ignore[attr-defined]
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="floating-clock-cast-http",
                     daemon=True).start()

    def retire() -> None:
        time.sleep(seconds)
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            log.debug("could not retire the cast file server", exc_info=True)

    threading.Thread(target=retire, name="floating-clock-cast-retire",
                     daemon=True).start()
    port = server.server_address[1]
    name = os.path.basename(path).replace(" ", "%20")
    return "http://%s:%d/%s" % (address, port, name)


# --- the speakers themselves ------------------------------------------------
def discover(timeout: float = DISCOVER_TIMEOUT) -> tuple[list[str], str]:
    """(speaker names, problem). Groups appear alongside single speakers."""
    pcc = _pychromecast()
    if pcc is None:
        return [], NO_LIBRARY
    try:
        casts, browser = pcc.get_chromecasts(timeout=timeout)
    except Exception as exc:
        return [], _reason(exc)
    try:
        return sorted({str(c.name) for c in casts if c.name}), ""
    finally:
        _stop(pcc, browser)


def play(device: str, media: str, volume: float | None = None,
         timeout: float = DISCOVER_TIMEOUT) -> str:
    """Play `media` on `device`. Empty when it started, else what went wrong.

    Blocking, and meant for a worker thread: discovery alone can take ten
    seconds, and the moment a prayer is due is exactly when the clock must
    not stall.
    """
    pcc = _pychromecast()
    if pcc is None:
        return NO_LIBRARY
    device = (device or "").strip()
    if not device:
        return "no speaker chosen"
    problem = check_media(media)
    if problem:
        return problem

    try:
        casts, browser = pcc.get_listed_chromecasts(
            friendly_names=[device], discovery_timeout=timeout)
    except Exception as exc:
        return _reason(exc)
    try:
        if not casts:
            return 'no speaker called "%s" answered on this network' % device
        speaker = casts[0]
        try:
            speaker.wait(timeout=CONNECT_TIMEOUT)
            if volume is not None:
                # On the same connection as the audio, so the volume is
                # already in place when the first note plays.
                speaker.set_volume(max(0.0, min(1.0, float(volume))))
            url = media.strip() if is_url(media) else serve_file(media.strip())
            controller = speaker.media_controller
            controller.play_media(url, content_type(media), stream_type="BUFFERED")
            controller.block_until_active(timeout=CONNECT_TIMEOUT)
        except Exception as exc:
            return _reason(exc)
        finally:
            try:
                speaker.disconnect(blocking=False)
            except Exception:
                log.debug("could not close the speaker connection", exc_info=True)
        return ""
    finally:
        _stop(pcc, browser)


def stop(device: str, timeout: float = DISCOVER_TIMEOUT) -> str:
    """Stop whatever is playing on `device` now. Empty when it was told to, else what went wrong.

    play() disconnects the moment the adhan has started, so there is no open connection left to
    reuse here -- stopping means finding the speaker again, exactly as starting did, and telling it
    to stop. Safe to call when nothing is playing there: a speaker that is already idle just says so.
    """
    pcc = _pychromecast()
    if pcc is None:
        return NO_LIBRARY
    device = (device or "").strip()
    if not device:
        return "no speaker chosen"
    try:
        casts, browser = pcc.get_listed_chromecasts(
            friendly_names=[device], discovery_timeout=timeout)
    except Exception as exc:
        return _reason(exc)
    try:
        if not casts:
            return 'no speaker called "%s" answered on this network' % device
        speaker = casts[0]
        try:
            speaker.wait(timeout=CONNECT_TIMEOUT)
            speaker.media_controller.stop()
        except Exception as exc:
            return _reason(exc)
        finally:
            try:
                speaker.disconnect(blocking=False)
            except Exception:
                log.debug("could not close the speaker connection", exc_info=True)
        return ""
    finally:
        _stop(pcc, browser)


def _stop(pcc, browser) -> None:
    try:
        pcc.discovery.stop_discovery(browser)
    except Exception:
        log.debug("could not stop cast discovery", exc_info=True)


def _reason(exc: Exception) -> str:
    """Something short enough for the settings page to show on one line."""
    return str(exc).strip()[:90] or exc.__class__.__name__
