"""Per-organisation identity: a mark and a colour for each calendar.

Belonging to several companies means the next meeting is ambiguous on its own
-- "Standup" tells you nothing about which company's standup. Each configured
calendar therefore carries an organisation: a domain, a small icon and a
colour, so a row can be recognised without reading it.

Icons default to the organisation's own favicon, fetched once from its domain
and cached on disk. When the site offers nothing usable -- no icon, or only a
16-pixel one that would blur -- the logo is looked up through the public icon
services in LOGO_SERVICES. Either way the only thing sent anywhere is the
domain: never the calendar, the meeting or an address. Anything the user picks
by hand wins over the fetched one, and a monogram tile stands in when there is
no network or no usable icon.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace

log = logging.getLogger(__name__)

# Re-exported so the UI needs one import to edit an Org.
__all__ = ["Org", "replace"]

USER_AGENT = "FloatingClock/1.0 (+calendar-sync)"
FETCH_TIMEOUT = 10.0
# A favicon is kilobytes; anything larger is a redirect to something unwanted.
MAX_ICON_BYTES = 2 * 1024 * 1024
MAX_HTML_BYTES = 512 * 1024
ICON_SIZE = 64
# Anything smaller than this is a blur once blown up to ICON_SIZE, so a site
# that only offers a tiny favicon is passed over for an online lookup.
MIN_GOOD_ICON = 32
# Public icon lookups, tried in order after the company's own site. Each is
# given the bare domain and nothing else.
LOGO_SERVICES = (
    "https://icons.duckduckgo.com/ip3/{domain}.ico",
    "https://www.google.com/s2/favicons?domain={domain}&sz=128",
)

# Deliberately spread around the wheel and legible on the clock's dark card.
PALETTE = (
    "#7f28ff", "#2f81f7", "#1f9d55", "#e0533d", "#d29922",
    "#c026d3", "#0f9b9b", "#e0629b", "#6b7cff", "#8a6a3d",
)


@dataclass(frozen=True)
class Org:
    """One organisation, and the calendar feed that belongs to it."""

    id: str
    name: str
    domain: str = ""
    ics_url: str = ""
    colour: str = ""
    icon: str = ""          # a cached PNG path, or "" for the monogram
    enabled: bool = True
    auto: bool = False      # discovered from a meeting rather than configured
    # How the calendar is read: "ics" from ics_url; "caldav" from caldav_url
    # with the app password in the vault under this org's id; "google" with
    # the calendar id in caldav_url and the sign-in tokens in the vault; or
    # "" for an organisation with no calendar of its own.
    kind: str = ""
    account: str = ""       # the sign-in name for a caldav calendar
    caldav_url: str = ""    # the calendar collection, after discovery

    @property
    def reads_a_calendar(self) -> bool:
        return bool(self.ics_url or (self.kind in ("caldav", "google") and self.caldav_url))

    @property
    def initials(self) -> str:
        return monogram(self.name or self.domain or self.id)

    def resolved_colour(self) -> str:
        return self.colour or colour_for(self.domain or self.name or self.id)


def monogram(name: str) -> str:
    """One or two letters to stand in for a missing icon."""
    words = [w for w in re.split(r"[^0-9A-Za-z]+", name) if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def colour_for(seed: str) -> str:
    """A stable colour per organisation.

    Stable across restarts and across machines, so the company that is green
    today is green tomorrow -- hashing rather than allocating in config order,
    which would reshuffle every time a calendar is added or removed.
    """
    digest = hashlib.sha256(seed.strip().lower().encode("utf-8")).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def normalise_domain(value: str) -> str:
    """Accept a URL, an email address or a bare domain; return the host."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "@" in value and "://" not in value:
        value = value.rsplit("@", 1)[1]
    if "://" not in value:
        value = "https://" + value
    host = urllib.parse.urlsplit(value).netloc
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


# --- fetching the mark --------------------------------------------------- #

def _open(url: str, limit: int, timeout: float) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # read(limit + 1) so an oversized body is detected rather than trimmed
        # into a corrupt image.
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response larger than %d bytes" % limit)
        return body, response.headers.get_content_type()


ICON_REL = re.compile(r"\b(?:apple-touch-icon(?:-precomposed)?|(?:shortcut )?icon)\b", re.I)
LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
ATTR = re.compile(r"""(\w[\w-]*)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")


def _link_icons(html: str, base: str) -> list[tuple[int, str]]:
    """Every <link rel=icon> in the page, as (size, absolute url)."""
    found: list[tuple[int, str]] = []
    for tag in LINK_TAG.findall(html):
        attrs = {
            name.lower(): value.strip("\"'")
            for name, value in ATTR.findall(tag)
        }
        if not ICON_REL.search(attrs.get("rel", "")):
            continue
        href = attrs.get("href", "").strip()
        if not href or href.startswith("data:"):
            continue
        sizes = attrs.get("sizes", "")
        match = re.search(r"(\d+)\s*[xX]\s*\d+", sizes)
        size = int(match.group(1)) if match else 0
        # An apple-touch-icon is a real logo at a usable size; a bare favicon
        # is often a 16px glyph, so prefer the former when neither says.
        if not size and "apple-touch-icon" in attrs.get("rel", "").lower():
            size = 180
        found.append((size, urllib.parse.urljoin(base, href)))
    return found


def candidate_icon_urls(domain: str, opener=_open) -> list[str]:
    """Icon URLs for a domain, best first."""
    domain = normalise_domain(domain)
    if not domain:
        return []
    base = "https://%s/" % domain
    candidates: list[tuple[int, str]] = []
    try:
        body, _ = opener(base, MAX_HTML_BYTES, FETCH_TIMEOUT)
        html = body.decode("utf-8", "replace")
        candidates = _link_icons(html, base)
    except Exception as exc:
        log.debug("no HTML icons for %s: %s", domain, exc)
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    urls = [url for _, url in candidates]
    fallback = base + "favicon.ico"
    if fallback not in urls:
        urls.append(fallback)
    return urls


def _to_png(raw: bytes, size: int = ICON_SIZE) -> bytes:
    """Normalise whatever came back into a square RGBA PNG."""
    return _decode(raw, size)[0]


def _decode(raw: bytes, size: int = ICON_SIZE) -> tuple[bytes, int]:
    """(square RGBA PNG, the icon's native size before scaling)."""
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        # A .ico holds several resolutions; pick the largest before scaling so
        # a 16px frame is not what gets blown up to 64.
        if getattr(image, "n_frames", 1) > 1 and image.format == "ICO":
            best, area = image, 0
            for frame in range(image.n_frames):
                image.seek(frame)
                if image.width * image.height > area:
                    area = image.width * image.height
                    best = image.copy()
            image = best
        native = max(image.width, image.height)
        image = image.convert("RGBA")
        image.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(
            image, ((size - image.width) // 2, (size - image.height) // 2), image
        )
        out = io.BytesIO()
        canvas.save(out, "PNG")
        return out.getvalue(), native


def icon_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "icons")


# Bumped when the way icons are chosen improves, so every cached mark is
# fetched again once rather than staying at whatever quality it was found at.
ICON_CACHE_VERSION = 2


def icon_path(base_dir: str, domain: str) -> str:
    safe = re.sub(r"[^a-z0-9.-]", "_", normalise_domain(domain) or "unknown")
    return os.path.join(icon_dir(base_dir), "%s-v%d.png" % (safe, ICON_CACHE_VERSION))


def fetch_icon(domain: str, base_dir: str, opener=_open, force: bool = False) -> str:
    """Fetch and cache an organisation's mark. Returns the path, or "".

    Never raises: a missing icon is a cosmetic problem, and the monogram is a
    perfectly good stand-in.
    """
    domain = normalise_domain(domain)
    if not domain:
        return ""
    target = icon_path(base_dir, domain)
    if os.path.exists(target) and not force:
        return target
    png = best_icon(domain, opener)
    if not png:
        return ""
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(png)
        return target
    except OSError as exc:
        log.warning("could not cache the icon for %s: %s", domain, exc)
        return ""


def best_icon(domain: str, opener=_open) -> bytes:
    """The best PNG for a domain, or b"".

    The company's own site first, largest icon first; the first one at least
    MIN_GOOD_ICON pixels wins outright. Only when the site has nothing that
    good are the online lookups asked, and only if they do no better either
    does the largest thing found stand in.
    """
    domain = normalise_domain(domain)
    if not domain:
        return b""
    urls = candidate_icon_urls(domain, opener) + [
        service.format(domain=domain) for service in LOGO_SERVICES
    ]
    largest: tuple[int, bytes] = (0, b"")
    for url in urls:
        try:
            raw, _ = opener(url, MAX_ICON_BYTES, FETCH_TIMEOUT)
            png, native = _decode(raw)
        except Exception as exc:
            log.debug("icon %s failed: %s", url, exc)
            continue
        if native >= MIN_GOOD_ICON:
            return png
        if native > largest[0]:
            largest = (native, png)
    return largest[1]


def dominant_colour(png_path: str) -> str:
    """The strongest colour in a cached icon, for tinting the row.

    Near-white, near-black and near-grey pixels are ignored: almost every logo
    sits on white, and averaging that in returns grey for everything.
    """
    from PIL import Image

    try:
        with Image.open(png_path) as image:
            image = image.convert("RGBA")
            image.thumbnail((32, 32))
            # getcolors already tallies the image; getdata is deprecated in
            # Pillow 14 and counting by hand only reimplements this.
            tally = image.getcolors(maxcolors=32 * 32) or []
            best, score = None, 0
            counts: dict[tuple[int, int, int], int] = {}
            for count, pixel in tally:
                r, g, b, a = pixel
                if a < 128:
                    continue
                spread = max(r, g, b) - min(r, g, b)
                if spread < 32 or max(r, g, b) < 40 or min(r, g, b) > 225:
                    continue
                key = (r // 24, g // 24, b // 24)
                counts[key] = counts.get(key, 0) + count
            for key, count in counts.items():
                if count > score:
                    best, score = key, count
    except Exception as exc:
        log.debug("no dominant colour for %s: %s", png_path, exc)
        return ""
    if best is None:
        return ""
    return "#%02x%02x%02x" % tuple(min(255, channel * 24 + 12) for channel in best)


# --- the configured set -------------------------------------------------- #

def make_id(name: str, taken: set[str]) -> str:
    """A short, stable, unique key for a new organisation."""
    stem = re.sub(r"[^a-z0-9]+", "-", (name or "org").strip().lower()).strip("-") or "org"
    candidate, suffix = stem, 2
    while candidate in taken:
        candidate = "%s-%d" % (stem, suffix)
        suffix += 1
    return candidate


def from_config(raw: list) -> list[Org]:
    """Build Orgs from the saved list, dropping anything malformed."""
    out: list[Org] = []
    seen: set[str] = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("ics_url") or "").strip()
        caldav_url = str(entry.get("caldav_url") or "").strip()
        if not name and not url and not caldav_url:
            continue
        key = str(entry.get("id") or "").strip() or make_id(name, seen)
        if key in seen:
            key = make_id(key, seen)
        seen.add(key)
        out.append(Org(
            id=key,
            name=name or key,
            domain=normalise_domain(str(entry.get("domain") or "")),
            ics_url=url,
            colour=str(entry.get("colour") or ""),
            icon=str(entry.get("icon") or ""),
            enabled=bool(entry.get("enabled", True)),
            auto=bool(entry.get("auto", False)),
            kind=str(entry.get("kind") or ("ics" if url else "")),
            account=str(entry.get("account") or ""),
            caldav_url=caldav_url,
        ))
    return out


def to_config(orgs: list[Org]) -> list[dict]:
    return [
        {
            "id": org.id, "name": org.name, "domain": org.domain,
            "ics_url": org.ics_url, "colour": org.colour, "icon": org.icon,
            "enabled": org.enabled, "auto": org.auto, "kind": org.kind,
            "account": org.account, "caldav_url": org.caldav_url,
        }
        for org in orgs
    ]


def org_for_domain(orgs, domain: str):
    """The organisation whose domain covers `domain`, or None.

    A subdomain counts: mail.zeuz.ai belongs to zeuz.ai.
    """
    domain = normalise_domain(domain)
    if not domain:
        return None
    for org in orgs:
        if org.domain and (domain == org.domain or domain.endswith("." + org.domain)):
            return org
    return None


# Second-level registries: the organisation is the label before these.
_REGISTRY_LABELS = frozenset({"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc"})


def label_for_domain(domain: str) -> str:
    """A display name from a domain: 'mail.acme-corp.co.uk' -> 'Acme Corp'."""
    parts = [p for p in normalise_domain(domain).split(".") if p]
    if not parts:
        return ""
    if len(parts) >= 3 and parts[-2] in _REGISTRY_LABELS and len(parts[-1]) <= 3:
        stem = parts[-3]
    elif len(parts) >= 2:
        stem = parts[-2]
    else:
        stem = parts[0]
    words = [w for w in re.split(r"[-_]+", stem) if w]
    return " ".join(w.capitalize() for w in words) or stem


def discover(orgs, domains) -> list[Org]:
    """Automatic organisations for the domains no configured one covers.

    One per domain, in the order seen, so the first meeting with a company
    decides its entry. They are marked `auto` so the settings page can tell
    them from the calendars the user typed in.
    """
    found: list[Org] = []
    taken = {org.id for org in orgs}
    known = list(orgs)
    for domain in domains:
        domain = normalise_domain(domain)
        if not domain or org_for_domain(known, domain) is not None:
            continue
        label = label_for_domain(domain) or domain
        org = Org(id=make_id(label, taken), name=label, domain=domain, auto=True)
        taken.add(org.id)
        known.append(org)
        found.append(org)
    return found


def _is_old_cache(icon: str, base_dir: str, domain: str) -> bool:
    """A mark this module fetched under an earlier cache version -- not one
    the user picked by hand, which lives wherever they put it."""
    if not icon:
        return False
    current = icon_path(base_dir, domain)
    if os.path.normcase(icon) == os.path.normcase(current):
        return False
    if os.path.normcase(os.path.dirname(icon)) != os.path.normcase(icon_dir(base_dir)):
        return False
    safe = re.sub(r"[^a-z0-9.-]", "_", normalise_domain(domain) or "unknown")
    name = os.path.basename(icon)
    return name == safe + ".png" or bool(re.fullmatch(re.escape(safe) + r"-v\d+\.png", name))


def merge_marks(current: list, snapshot: list, filled: list) -> list:
    """Fold fetched marks into the list as it is *now*.

    A fetch runs on a worker for as long as the slowest website takes, and
    the list can change under it: a calendar added, another removed, an
    icon picked by hand. Replacing the list with the worker's copy would
    undo all of that -- which is exactly how a freshly added calendar once
    vanished. So: every entry in `current` survives; those the fetch knew
    about take its icon and colour, unless the user changed them meanwhile.
    """
    before = {org.id: org for org in snapshot}
    after = {org.id: org for org in filled}
    out: list = []
    for org in current:
        was, got = before.get(org.id), after.get(org.id)
        if was is None or got is None:
            out.append(org)
            continue
        icon = got.icon if org.icon == was.icon else org.icon
        colour = got.colour if org.colour == was.colour else org.colour
        out.append(replace(org, icon=icon, colour=colour))
    return out


def ensure_icons(orgs: list[Org], base_dir: str, opener=_open) -> list[Org]:
    """Fill in any missing mark and colour. Safe to call on a worker thread."""
    out: list[Org] = []
    for org in orgs:
        icon = org.icon
        stale = bool(org.domain) and _is_old_cache(icon, base_dir, org.domain)
        if not icon or not os.path.exists(icon) or stale:
            icon = fetch_icon(org.domain, base_dir, opener) if org.domain else ""
            if not icon and org.icon and os.path.exists(org.icon):
                icon = org.icon   # the old mark beats no mark
        colour = org.colour or (dominant_colour(icon) if icon else "")
        out.append(replace(org, icon=icon, colour=colour or org.colour))
    return out
