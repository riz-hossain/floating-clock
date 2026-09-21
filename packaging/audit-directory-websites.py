"""Does each website in the bundled directory belong to the masjid it is filed under?

The directory was compiled from Google Maps and a lot of searching, and a
website that was one search result away from the right one is easy to file:
a masjid in Chicago with the same name, a news article on the right domain, a
page that lists twenty centres. The clock reads times off whatever website an
entry carries, so a wrong one is worse than none -- it can show another
masjid's times as this one's.

This fetches every website (politely: four at a time, a second apart per host,
one request each -- two where a certificate or a "www." spelling needs a second
look -- and saying who is asking) and asks two questions of the page it lands on:

    is this the masjid?   distinctive words of its name in the title, a heading,
                          the site name or the host name; or in the text
    is it the place?      its city, street address, postal code, telephone --
                          and nothing that says it is somewhere else: the town
                          in another state or province, another province's
                          postal codes
                          number or e-mail domain on the page

A page that answers both is fine, unless it is not quite the page that was
asked for: sent to a different page, a page that names half the cities in the
directory (an umbrella body's front page or a "choose your centre" list), a
school. Anything else is listed with what the page actually said, for a person
to look at -- this is triage, not a verdict: a masjid whose site is drawn by
script shows a bare title, and a site that turns bots away shows nothing at
all. UNREACHABLE is a site that did not answer (no such domain, a timeout);
REFUSED is one that answered with an error, often only because it does not
care for programs.

    python packaging/audit-directory-websites.py [--source A:/git/LiveAzan/data/mosques]
                                                 [--directory file.json] [--out report.json] [--only word]

Needs the network, so it is not one of the checks a build runs; the offline
half is check-directory.py. Reads data/masjids.json, the file the clock ships;
--source adds the street address, telephone and e-mail the research files
hold, which the shipped file leaves out. It changes nothing: what a person
decides -- the right website, none, or drop the entry -- goes in
directory-overrides.json, which make-directory.py applies.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import pathlib
import re
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HERE = pathlib.Path(__file__).resolve().parent.parent
USER_AGENT = "FloatingClock/1.0 (+https://github.com/riz-hossain/floating-clock; directory-audit)"
TIMEOUT = 20.0
MAX_BYTES = 600_000
WORKERS = 4
PER_HOST_GAP = 1.0

# Words that say "masjid" and nothing about which one, and the ones that say
# "in this town" -- matched separately, as the place.
GENERIC = {"masjid", "mosque", "mosquee", "islamic", "islam", "centre", "center", "muslim", "muslims",
           "association", "society", "community", "cultural", "the", "and", "canada", "canadian",
           "inc", "musalla", "musallah", "prayer", "room", "hall", "jame", "jamia", "jami", "trust",
           "foundation", "education", "educational", "organization", "for", "ontario", "learning"}
STREET_TYPES = {"rd", "road", "st", "street", "ave", "avenue", "dr", "drive", "blvd", "boulevard",
                "cres", "crescent", "ct", "court", "ln", "lane", "pkwy", "parkway", "way", "cir",
                "circle", "pl", "place", "trl", "trail", "hwy", "highway", "sq", "square", "line",
                "terrace", "ter", "gate", "gt", "run", "path", "heights", "hts", "sideroad"}
FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.ca", "hotmail.com", "outlook.com", "live.com", "icloud.com",
             "rogers.com", "bell.net", "sympatico.ca", "protonmail.com", "aol.com", "me.com"}
SOCIAL = {"facebook.com", "instagram.com", "twitter.com", "x.com", "linktr.ee", "youtube.com", "tiktok.com"}
PARKED = ("domain is for sale", "this domain", "buy this domain", "parked", "coming soon",
          "account suspended", "default web page", "under construction", "sedo")
# A page that is a way in to several centres rather than one of them.
SELECTOR = re.compile(r"\b(select|choose|pick) (your|a|the) (centre|center|masjid|mosque|location|branch|chapter)"
                      r"|\bour (centres|centers|masjids|mosques|locations)\b|\bfind (a|your) (centre|center|masjid|mosque)",
                      re.I)
# A Canadian postal code begins with a letter that belongs to one province. A page that prints
# another province's is somewhere else -- and so is one that puts the masjid's own town in another
# state, which matching the town's name will never notice: Richmond Hill, Ontario and Richmond Hill,
# New York have the same name and the same sort of mosque.
POSTAL_PROVINCE = {"A": "Newfoundland and Labrador", "B": "Nova Scotia", "C": "Prince Edward Island",
                   "E": "New Brunswick", "G": "Quebec", "H": "Quebec", "J": "Quebec", "K": "Ontario",
                   "L": "Ontario", "M": "Ontario", "N": "Ontario", "P": "Ontario", "R": "Manitoba",
                   "S": "Saskatchewan", "T": "Alberta", "V": "British Columbia",
                   "X": "Northwest Territories", "Y": "Yukon"}
PROVINCE_CODE = {"Newfoundland and Labrador": "NL", "Nova Scotia": "NS", "Prince Edward Island": "PE",
                 "New Brunswick": "NB", "Quebec": "QC", "Ontario": "ON", "Manitoba": "MB",
                 "Saskatchewan": "SK", "Alberta": "AB", "British Columbia": "BC",
                 "Northwest Territories": "NT", "Yukon": "YT", "Nunavut": "NU"}
POSTAL = re.compile(r"\b([ABCEGHJKLMNPRSTVXY])(\d[A-Za-z])[ -]?(\d[A-Za-z]\d)\b", re.I)
# A name or a page title that says school, not masjid.
SCHOOLISH = re.compile(r"\b(seminary|boarding school|school|academy|darul[- ]?uloom|madr[ae]sa\w*|college|"
                       r"universit\w*|institute)\b", re.I)


def fold(text) -> str:
    plain = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", plain.casefold())).strip()


def host_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


# --- reading a page -----------------------------------------------------------
class Page(HTMLParser):
    """The title, headings, site-name meta tags and visible text of one page."""

    SKIP = {"script", "style", "noscript", "template", "svg"}
    META = {"og:site_name", "og:title", "og:description", "description", "twitter:title",
            "application-name"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.heads, self.meta, self.text = "", [], [], []
        self._skip, self._in, self._buf = 0, None, []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "meta":
            if (a.get("property") or a.get("name") or "").lower() in self.META:
                self.meta.append(a.get("content", ""))
        elif tag in ("title", "h1", "h2") and self._in is None:
            self._in, self._buf = tag, []
        elif tag == "img" and (a.get("alt") or a.get("title")):
            self.text.append(a.get("alt") or a.get("title"))    # a logo is often the name

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == self._in:
            text = " ".join(" ".join(self._buf).split())
            if tag == "title":
                self.title = self.title or text
            elif text:
                self.heads.append(text)
            self._in = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in:
            self._buf.append(data)
        self.text.append(data)


def decode(raw: bytes, header: str) -> str:
    charset = re.search(r"charset=([\w-]+)", header or "", re.I) or re.search(
        rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
    name = charset.group(1) if charset else "utf-8"
    name = name.decode("ascii", "ignore") if isinstance(name, bytes) else name
    try:
        return raw.decode(name, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


# --- fetching, politely ---------------------------------------------------------
_last: dict = {}
_lock = threading.Lock()


def _wait_turn(host: str) -> None:
    with _lock:
        due = max(_last.get(host, 0.0) + PER_HOST_GAP, time.time())
        _last[host] = due
    time.sleep(max(0.0, due - time.time()))


def other_www(url: str) -> str:
    """The same address with "www." added or taken off."""
    parts = urllib.parse.urlparse(url)
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else "www." + parts.netloc
    return urllib.parse.urlunparse(parts._replace(netloc=host))


def fetch(url: str) -> dict:
    """{"status", "final", "html", "type", "error", "insecure", "alt"} for one address.

    A host that does not resolve is tried once more with "www." toggled: the
    clock would not do that, so the entry is still at fault, but it says what
    to change it to.
    """
    out = _get(url)
    if not out["html"] and "getaddrinfo" in out["error"]:
        other = _get(other_www(url))
        if other["html"]:
            out["alt"] = other
    return out


def _get(url: str) -> dict:
    out = {"status": 0, "final": url, "html": "", "type": "", "error": "", "insecure": False, "alt": None}
    for verify in (True, False):
        _wait_turn(host_of(url))
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
            "Accept-Language": "en"})
        context = None if verify else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as reply:
                raw = reply.read(MAX_BYTES)
                out.update(status=reply.status, final=reply.geturl(), insecure=not verify,
                           type=reply.headers.get("Content-Type", ""), error="",
                           html=decode(raw, reply.headers.get("Content-Type", "")))
                return out
        except urllib.error.HTTPError as exc:
            out.update(status=exc.code, final=exc.geturl() or url, error="HTTP %d" % exc.code)
            return out
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            out["error"] = str(reason)[:120] or exc.__class__.__name__
            if verify and isinstance(reason, ssl.SSLError):
                continue                                    # a lapsed certificate: look anyway
            return out
    return out


# --- what the entry knows about itself -------------------------------------------
def load_shipped(path: pathlib.Path | None = None) -> list:
    return json.load(io.open(path or HERE / "data" / "masjids.json", encoding="utf-8"))["masjids"]


def load_research(source: pathlib.Path) -> dict:
    """{(name, lat, lon): raw research record} -- the key make-directory.py dedupes on."""
    found = {}
    for path in sorted(source.rglob("*.json")):
        if "_meta" in path.parts:
            continue
        for item in json.load(io.open(path, encoding="utf-8")).get("mosques") or []:
            try:
                key = (str(item.get("name") or "").strip().lower(),
                       round(float(item["latitude"]), 4), round(float(item["longitude"]), 4))
            except (KeyError, TypeError, ValueError):
                continue
            found.setdefault(key, item)
    return found


def street_of(address: str) -> str:
    """"300 King George Rd, Brantford" -> "300 king george"."""
    for part in str(address or "").split(","):
        words = fold(part).split()
        if len(words) >= 2 and re.match(r"\d+[a-z]?$", words[0]):
            while len(words) > 2 and words[-1] in STREET_TYPES:
                words.pop()
            return " ".join(words)
    return ""


def postal_of(address: str) -> str:
    match = re.search(r"\b([A-Z]\d[A-Z])\s?(\d[A-Z]\d)\b", str(address or "").upper())
    return (match.group(1) + match.group(2)) if match else ""


def phones_in(text: str) -> set:
    return {re.sub(r"\D", "", m)[-10:] for m in re.findall(r"\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]*\d{4}", text)}


def distinctive(entry: dict) -> list:
    city = set(fold(entry.get("city")).split())
    return [w for w in dict.fromkeys(fold(entry.get("name")).split())
            if w not in GENERIC and w not in city and len(w) > 2]


def word_in(word: str, folded: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", folded))


# --- judging one entry ---------------------------------------------------------------
def moved_in_place(asked: str, landed: str) -> bool:
    """A redirect on one site that still ends at the same page: /mosques/x -> /mosque/x/."""
    a = [p for p in urllib.parse.urlparse(asked).path.split("/") if p]
    b = [p for p in urllib.parse.urlparse(landed).path.split("/") if p]
    return (a[-1:] == b[-1:]) or not a


def elsewhere(entry: dict, text: str) -> list:
    """Reasons to think a page is about another place than the masjid's own town and province.

    Not for mentioning other places -- an umbrella body lists all of them, and a masjid may name
    a sister masjid -- but for the two signs that a page is another masjid's: the masjid's own
    town written with another state or province, and another province's postal codes. `text` is
    the page's visible text as written, since "NY" is not "ny".
    """
    province = str(entry.get("province") or "")
    code = PROVINCE_CODE.get(province)
    city = str(entry.get("city") or "").strip()
    out = []
    if city and code:
        for found in re.finditer(re.escape(city) + r"\s*,\s*([A-Z]{2})\b", text):
            if found.group(1) != code:
                out.append("prints '%s, %s' but this masjid is in %s" % (city, found.group(1), province))
                break
    if province:
        foreign: dict = {}
        for found in POSTAL.finditer(text):
            where = POSTAL_PROVINCE.get(found.group(1).upper())
            if where and where != province:
                foreign.setdefault(where, "%s%s %s" % (found.group(1).upper(), found.group(2).upper(),
                                                       found.group(3).upper()))
        for where, sample in sorted(foreign.items()):
            out.append("prints a postal code of %s (%s) but this masjid is in %s" % (where, sample, province))
    return out


def judge(entry: dict, research: dict, got: dict, cities: set) -> dict:
    url = entry["website"]
    report = {"name": entry["name"], "city": entry.get("city", ""), "province": entry.get("province", ""),
              "website": url, "final": got["final"], "status": got["status"], "verdict": "", "why": [],
              "title": "", "headings": [], "snippet": ""}
    flags = report["why"]
    if got["insecure"]:
        flags.append("certificate does not verify")
    if not got["html"]:
        # A site that answers "no" is there; one that does not answer is gone, or down.
        report["verdict"] = "REFUSED" if got["status"] else "UNREACHABLE"
        flags.append(got["error"] or "no page (%s)" % got["type"])
        if got["alt"]:
            report["final"] = got["alt"]["final"]
            flags.append("but %s answers" % got["alt"]["final"])
        return report

    page = Page()
    try:
        page.feed(got["html"])
    except Exception:                                       # broken markup must not stop an audit
        pass
    text = " ".join(page.text)
    visible = fold(text)
    head = fold(" ".join([page.title] + page.heads + page.meta))
    host = re.sub(r"[^a-z0-9]", "", host_of(got["final"]))
    report["title"] = page.title
    report["headings"] = page.heads[:4]
    report["snippet"] = " ".join(text.split())[:240]

    city = fold(entry.get("city"))
    words = distinctive(entry)
    in_head = [w for w in words if word_in(w, head) or w in host]
    in_body = [w for w in words if word_in(w, visible)]
    if not words and city and (word_in(city, head) or city.replace(" ", "") in host):
        in_head = [city]                                    # "Islamic Society of Belleville": the town is the name
    whole = fold(re.sub(r"\(.*?\)", " ", entry["name"]))
    if whole and not in_head and word_in(whole, head):
        in_head = [whole]                                   # "Muslim Association of Woodstock (MAOW)": all of it
    place = ["city"] if city and word_in(city, visible) else []
    strong, contact = [], []
    raw = research.get((entry["name"].strip().lower(), round(entry["latitude"], 4), round(entry["longitude"], 4))) or {}
    street = street_of(raw.get("address"))
    if street and street in visible:
        strong.append("street address")
    postal = postal_of(raw.get("address"))
    if postal and postal in re.sub(r"[^A-Z0-9]", "", text.upper()):
        strong.append("postal code")
    phone = re.sub(r"\D", "", str(raw.get("phone") or ""))[-10:]
    if len(phone) == 10 and phone in phones_in(text):
        contact.append("telephone")
    domain = str(raw.get("email") or "").rpartition("@")[2].lower()
    if domain and domain not in FREE_MAIL and (domain in got["final"].lower() or domain in got["html"].lower()):
        contact.append("e-mail domain")

    # Things that make a page that fits the name still not this masjid's own page.
    doubts = []
    if host_of(url) != host_of(got["final"]):
        doubts.append("redirected off-site to " + got["final"])
    elif not moved_in_place(url, got["final"]):
        doubts.append("redirected to another page: " + got["final"])
    final_host = host_of(got["final"])
    if final_host in SOCIAL or any(final_host.endswith("." + s) for s in SOCIAL):
        doubts.append("a social-media page")
    lead = " ".join([page.title] + page.heads[:1]).lower()
    if any(mark in lead for mark in PARKED):
        doubts.append("looks parked or unfinished: " + page.title[:60])
    if got["status"] >= 400:
        doubts.append("HTTP %d" % got["status"])
    others = sorted(c for c in cities if c != city and word_in(c, visible))
    if len(others) >= 5:
        doubts.append("names %d other cities (%s): an umbrella or selector page?" % (len(others), ", ".join(others[:5])))
    elif SELECTOR.search(visible):
        doubts.append("asks the visitor to pick a centre ('%s'): an umbrella page?" % SELECTOR.search(visible).group(0))
    school = SCHOOLISH.search(entry["name"]) or SCHOOLISH.search(page.title)
    if school:
        doubts.append("a school or institute by its %s ('%s'), not a masjid?" % (
            "name" if SCHOOLISH.search(entry["name"]) else "title", school.group(0)))

    if strong or contact:
        flags.append("matches on " + ", ".join(strong + contact))
    named = bool(in_head or in_body)
    # The masjid's own address on the page vouches for it; a telephone number or an e-mail
    # domain does not on its own, because an umbrella body prints every centre's.
    if (in_head and (place or strong or contact)) or (strong and named):
        verdict = "OK"
        if doubts:
            verdict = "CHECK"
    elif contact or (named and place):
        verdict = "CHECK"
        doubts.append("the name is not in the title or a heading")
    elif named:
        verdict = "WRONG PLACE?"
        flags.append("has the name (%s) but nothing of %s" % (", ".join(in_head or in_body), entry.get("city")))
    elif place:
        verdict = "WRONG MASJID?"
        flags.append("mentions %s but none of the name's words (%s)" % (entry.get("city"), ", ".join(words) or "-"))
    else:
        verdict = "NOT THIS MASJID"
        flags.append("neither the name (%s) nor %s appears" % (", ".join(words) or "no distinctive word", entry.get("city")))
    away = elsewhere(entry, text)
    if away:
        if strong:
            # its own address is on the page, so this is a sister masjid or a list of centres
            doubts.extend(away)
            if verdict == "OK":
                verdict = "CHECK"
        else:
            if verdict in ("OK", "CHECK"):
                verdict = "WRONG PLACE?"
            flags.extend(away)
    flags.extend(doubts)
    report["verdict"] = verdict
    return report


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", type=pathlib.Path, help="the LiveAzan research folder (data/mosques)")
    ap.add_argument("--directory", type=pathlib.Path, help="audit this file instead of data/masjids.json")
    ap.add_argument("--out", type=pathlib.Path, help="write the whole report here as JSON")
    ap.add_argument("--only", default="", help="only entries whose name or website contains this")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args(argv)

    everyone = load_shipped(args.directory)
    cities = {fold(e.get("city")) for e in everyone if e.get("city")}
    entries = [e for e in everyone if e.get("website")]
    if args.only:
        entries = [e for e in entries if args.only.lower() in (e["name"] + " " + e["website"]).lower()]
    research = load_research(args.source) if args.source else {}
    print("%d websites to look at%s" % (len(entries), "" if research else " (no --source: no street/phone/e-mail)"))

    reports = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch, e["website"]): e for e in entries}
        for future in concurrent.futures.as_completed(futures):
            entry = futures[future]
            report = judge(entry, research, future.result(), cities)
            reports.append(report)
            print("  %-16s %s  (%s)" % (report["verdict"], entry["name"], entry["website"]), flush=True)

    reports.sort(key=lambda r: (r["verdict"] == "OK", r["verdict"], r["province"], r["city"], r["name"]))
    counts: dict = {}
    for r in reports:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\n" + ", ".join("%s %d" % kv for kv in sorted(counts.items())))
    if args.out:
        io.open(args.out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(reports, ensure_ascii=False, indent=1))
        print("report -> %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
