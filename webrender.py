"""A web page as a browser shows it, for the sites that fill their times in by script.

A masjid's page can be perfectly readable to a person and empty to a program:
the times arrive from a script a moment after the page loads, and the HTML the
server sent holds a "-" or a "12:00 am". The only way to see what the person
sees is to run the page. Almost every computer already has something that can:
Edge is part of Windows, and Chrome, Chromium or Edge are on most Macs and
Linux desktops. This drives whichever is there, headless -- no window, a
throwaway profile, gone when it is done.

It is a last resort and behaves like one. It runs only after the plain page has
failed, one page at a time, on a worker, with a hard time limit and the whole
process tree killed at the end. If there is no browser it says so and the caller
carries on without it.

The conversation is the Chrome DevTools Protocol over a WebSocket. There is no
library for that in the standard library, and none is worth adding for what is
needed here, so the few dozen lines of the protocol that are used are below.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

# How long a whole render may take, and how long a page gets to finish its
# scripts after it has loaded. A page still changing after that is not going to
# settle, and what it has shown so far is what there is.
TOTAL_TIMEOUT = 40.0
SETTLE_TIMEOUT = 8.0
USER_AGENT = "FloatingClock/1.0 (+prayer-times)"

_CANDIDATES = {
    "win32": (
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ),
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ),
}
_ON_PATH = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
            "microsoft-edge", "microsoft-edge-stable", "brave-browser")


class RenderError(Exception):
    """The page could not be rendered."""


def find_browser() -> str:
    """Path to a Chromium-based browser on this machine, or ""."""
    for template in _CANDIDATES.get(sys.platform, ()):
        path = os.path.expandvars(template)
        if os.path.isfile(path):
            return path
    for name in _ON_PATH:
        found = shutil.which(name)
        if found:
            return found
    return ""


def available() -> bool:
    return bool(find_browser())


def _needs_no_sandbox() -> bool:
    """Whether the browser has to be started without its sandbox.

    Chrome will not start as root with it, and some build machines cannot give
    it the kernel feature it wants. Never on an ordinary desktop, where the
    sandbox is what makes it reasonable to open a stranger's web page at all.
    """
    if os.environ.get("FLOATING_CLOCK_NO_SANDBOX") == "1":
        return True
    return hasattr(os, "geteuid") and os.geteuid() == 0


# --- a WebSocket client, just enough for DevTools ------------------------------
class _Socket:
    def __init__(self, url: str, timeout: float) -> None:
        parts = urllib.parse.urlsplit(url)
        self._sock = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        self._sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
                   "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n") % (
            parts.path or "/", parts.hostname, parts.port, key)
        self._sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RenderError("the browser closed the connection")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RenderError("the browser refused the connection")
        self._buffer = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise RenderError("the browser closed the connection")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        message = b""
        while True:
            first, second = self._read(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(size)
            if opcode == 8:
                raise RenderError("the browser closed the connection")
            if opcode == 9:                                   # ping
                self._sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode in (0, 1, 2):
                message += payload
                if first & 0x80:
                    return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class _Page:
    """One tab, driven over DevTools."""

    def __init__(self, ws_url: str, timeout: float) -> None:
        self._ws = _Socket(ws_url, timeout)
        self._next = 0

    def call(self, method: str, params: dict | None = None, wait: float = 15.0):
        self._next += 1
        ident = self._next
        self._ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        deadline = time.time() + wait
        while time.time() < deadline:
            message = json.loads(self._ws.recv())
            if message.get("id") == ident:
                if "error" in message:
                    raise RenderError(str(message["error"].get("message", "browser error")))
                return message.get("result", {})
        raise RenderError("the browser did not answer %s" % method)

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return result.get("result", {}).get("value")

    def close(self) -> None:
        self._ws.close()


PROFILE_PREFIX = "floating-clock-render-"


def _kill_by_profile(marker: str) -> int:
    """Kill every process whose command line carries `marker`. How many were found.

    This, not the process handle, is how the browser is found again: on Windows
    msedge.exe is only a launcher that starts the real browser and exits with
    code 0, so the handle is to something already gone. The profile folder's
    path is on every one of the browser's processes' command lines and is
    unique to this render, which makes it the one reliable handle there is.
    """
    found = 0
    try:
        if sys.platform == "win32":
            script = ("$m = $env:FC_MARKER; "
                      "$ids = @(Get-CimInstance Win32_Process | Where-Object "
                      "{ $_.CommandLine -and $_.CommandLine.Contains($m) } | "
                      "ForEach-Object { $_.ProcessId }); "
                      "foreach ($i in $ids) { Stop-Process -Id $i -Force -ErrorAction SilentlyContinue }; "
                      "$ids.Count")
            env = dict(os.environ, FC_MARKER=marker)
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=30, env=env,
                creationflags=0x08000000)
            found = int((done.stdout or "0").strip().split()[-1] or 0)
        else:
            listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True,
                                     text=True, timeout=15).stdout
            for line in listing.splitlines():
                pid, _, command = line.strip().partition(" ")
                if marker in command and pid.isdigit() and int(pid) != os.getpid():
                    try:
                        os.kill(int(pid), 9)
                        found += 1
                    except OSError:
                        pass
    except Exception:
        log.debug("could not sweep for browser processes", exc_info=True)
    return found


def sweep_stale(older_than: float = 600.0) -> int:
    """Kill the browsers, and remove the profiles, that an earlier run left behind.

    Only those older than `older_than` seconds: a render takes a minute at the
    outside, so anything older was cut short -- by a crash, or the machine going
    to sleep -- and anything younger may be somebody's read still in flight. Cheap
    when there is nothing to do: a look in the temporary folder. Returns how many
    browser processes were found.
    """
    root = tempfile.gettempdir()
    killed = 0
    try:
        names = [n for n in os.listdir(root) if n.startswith(PROFILE_PREFIX)]
    except OSError:
        return 0
    for name in names:
        path = os.path.join(root, name)
        try:
            if time.time() - os.path.getmtime(path) < older_than:
                continue
        except OSError:
            continue
        killed += _kill_by_profile(path)
        shutil.rmtree(path, ignore_errors=True)
    return killed


def _close(browser_ws: str, process: subprocess.Popen | None, profile: str) -> None:
    """Ask the browser to quit, then make certain it has."""
    if browser_ws:
        try:
            ws = _Socket(browser_ws, 5)
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            ws.close()
        except Exception:
            pass
        time.sleep(0.8)
    if process is not None:
        try:
            process.kill()
        except Exception:
            pass
    _kill_by_profile(profile)


def render(url: str, screenshot: bool = False, total: float = TOTAL_TIMEOUT,
           settle: float = SETTLE_TIMEOUT) -> dict:
    """{"html": str, "text": str, "url": str, "png": bytes|None} for a page.

    Raises RenderError when there is no browser or the page will not render.
    The page has run its scripts; `html` is the document as they left it.
    """
    exe = find_browser()
    if not exe:
        raise RenderError("no browser on this machine to render the page with")
    profile = tempfile.mkdtemp(prefix=PROFILE_PREFIX)
    started = time.time()
    process = None
    page = None
    browser_ws = ""
    try:
        args = [exe, "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--mute-audio",
                "--disable-background-networking", "--disable-sync", "--hide-scrollbars",
                "--window-size=1280,2400", "--remote-debugging-port=0",
                "--user-data-dir=" + profile, "--user-agent=" + USER_AGENT, "about:blank"]
        if _needs_no_sandbox():
            args.insert(1, "--no-sandbox")
        flags = 0x08000000 if sys.platform == "win32" else 0            # CREATE_NO_WINDOW
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=flags)
        # Chrome writes the port it picked into the profile once it is listening.
        port_file = os.path.join(profile, "DevToolsActivePort")
        port = None
        while time.time() - started < 20:
            if os.path.exists(port_file):
                try:
                    port = int(open(port_file, encoding="utf-8").read().split()[0])
                    break
                except (ValueError, IndexError, OSError):
                    pass
            # A launcher that has already exited is not a failure -- on Windows
            # that is simply how Edge starts. The port file is what says so.
            time.sleep(0.15)
        if port is None:
            raise RenderError("the browser did not start")

        version = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/version" % port, timeout=10).read().decode())
        browser_ws = version.get("webSocketDebuggerUrl", "")
        targets = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/list" % port, timeout=10).read().decode())
        target = next((t for t in targets if t.get("type") == "page"), None)
        if target is None:
            raise RenderError("the browser opened no page")
        page = _Page(target["webSocketDebuggerUrl"], timeout=15)
        page.call("Page.enable")
        page.call("Page.navigate", {"url": url})
        # The page's own load event, then however long its scripts need to stop
        # changing what is on screen.
        deadline = min(started + total, time.time() + total)
        last, steady = -1, 0
        time.sleep(1.0)
        while time.time() < deadline:
            state = page.evaluate("document.readyState")
            size = page.evaluate("document.body ? document.body.innerText.length : 0") or 0
            if state == "complete":
                steady = steady + 1 if size == last else 0
                if steady >= 2 and time.time() - started > 3.0:
                    break
                if time.time() - started > settle + 4.0:
                    break
            last = size
            time.sleep(0.6)
        html = page.evaluate("document.documentElement.outerHTML") or ""
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        final = page.evaluate("location.href") or url
        png = None
        if screenshot:
            shot = page.call("Page.captureScreenshot",
                             {"format": "png", "captureBeyondViewport": True}, wait=20)
            png = base64.b64decode(shot.get("data", "")) or None
        return {"html": html, "text": text, "url": final, "png": png}
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(str(exc)[:80] or exc.__class__.__name__) from None
    finally:
        if page is not None:
            page.close()
        _close(browser_ws, process, profile)
        shutil.rmtree(profile, ignore_errors=True)


def html_of(url: str, total: float = 25.0) -> tuple[str, str]:
    """(final url, html as the page's scripts left it): the shape scrape.fetch asks of
    its `render`, kept short because a masjid site that has not settled in this
    long is not going to."""
    page = render(url, total=total)
    if page["url"].startswith(("chrome-error:", "edge-error:")):
        raise RenderError("the page could not be reached")     # the browser's own error page
    return page["url"], page["html"]
