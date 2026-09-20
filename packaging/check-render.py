"""Drive a real headless browser at a page that writes its times in with a script.

The plain page a server sends holds no times at all -- a script builds them a
moment after it loads -- so this is the one check that proves the browser
driver, the WebSocket client under it, and the reader on top of it work
together. It also proves nothing is left running afterwards, which is the
failure that would make a clock leak a browser every night.

It uses a page served from this machine, so no network; and it is skipped,
successfully, where there is no Chrome, Chromium or Edge to drive.

    PYTHONPATH=<folder holding floating_clock> python packaging/check-render.py
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date

os.environ["FLOATING_CLOCK_HOME"] = tempfile.mkdtemp(prefix="floating-clock-check-")

from floating_clock import scrape, webrender  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          "" if condition else "  -- " + detail))
    if not condition:
        failures.append(name)


if not webrender.available():
    print("no Chrome, Chromium or Edge on this machine: nothing to drive, skipped")
    sys.exit(0)

# The times are made at run time from numbers, so that "6:15" is nowhere in what
# the server sends -- only in what the page turns into.
PAGE = b"""<html><head><title>Script Masjid</title></head><body><h1>Prayer times</h1><div id="t">Loading...</div>
<script>
setTimeout(function () {
  var rows = [["Fajr", 5, 49, 6, 15], ["Dhuhr", 1, 16, 1, 45], ["Asr", 5, 34, 5, 45],
              ["Maghrib", 7, 24, 7, 28], ["Isha", 8, 43, 9, 0]];
  function two(n) { return (n < 10 ? "0" : "") + n; }
  document.getElementById("t").innerHTML = rows.map(function (r) {
    var pm = r[0] === "Fajr" ? " AM" : " PM";
    return "<p>" + r[0] + " Athan " + r[1] + ":" + two(r[2]) + pm + " Iqama: " + r[3] + ":" + two(r[4]) + pm + "</p>";
  }).join("");
}, 400);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % server.server_address[1]

print("a page whose times a script writes in")
raw = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
check("what the server sends has no times in it", "6:15" not in raw and scrape.extract(raw, date.today()) is None)

started = time.time()
final, html = webrender.html_of(url)
check("a browser is driven and hands back the page as its script left it", "6:15" in html and "Iqama" in html, html[:200])
check("at the address it was given", final.startswith("http://127.0.0.1"), final)
check("in a reasonable time", time.time() - started < 30, "%.1fs" % (time.time() - started))

found = scrape.extract(html, date.today())
check("and that is read as the five iqamas",
      found is not None and [found["iqamah"][p] for p in scrape.DAILY] == ["06:15", "13:45", "17:45", "19:28", "21:00"],
      str(found))

got = json.loads(scrape.fetch(url, date.today(), render=webrender.html_of))
check("through fetch, a page that reads only once drawn is read", got["iqamah"]["Isha"] == "21:00", str(got))
check("named for its title", got["name"] == "Script Masjid", got.get("name", ""))

print("nothing is left running")
time.sleep(1.0)
check("no browser process from these renders is still alive", webrender._kill_by_profile(webrender.PROFILE_PREFIX) == 0)
left = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith(webrender.PROFILE_PREFIX)]
check("and no profile folder", not left, str(left))

# a render cut short leaves a folder behind, and the next start clears it
fake = os.path.join(tempfile.gettempdir(), webrender.PROFILE_PREFIX + "left-behind")
os.makedirs(fake, exist_ok=True)
old = time.time() - 3600
os.utime(fake, (old, old))
fresh = os.path.join(tempfile.gettempdir(), webrender.PROFILE_PREFIX + "in-flight")
os.makedirs(fresh, exist_ok=True)
webrender.sweep_stale()
check("an hour-old leftover is swept away at the next start", not os.path.exists(fake))
check("but a folder from a render that may still be running is not", os.path.exists(fresh))
os.rmdir(fresh)

try:
    scrape.fetch("http://127.0.0.1:1/", date.today(), render=webrender.html_of)
    check("a site that is down is an error even when a browser is tried", False, "no error raised")
except scrape.ScrapeError as exc:
    check("a site that is down is an error even when a browser is tried", "could not open" in str(exc), str(exc))
time.sleep(0.5)
check("and that leaves nothing running either", webrender._kill_by_profile(webrender.PROFILE_PREFIX) == 0)

server.shutdown()
print()
if failures:
    print("%d render check(s) FAILED: %s" % (len(failures), "; ".join(failures)))
    sys.exit(1)
print("all render checks passed")
