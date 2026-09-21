"""Distil a masjid directory into the small file the clock ships.

The source is the research directory in the LiveAzan project -- 53 city files
of masjids with addresses, coordinates and websites. Ninety percent of that
is descriptions, opening hours and lists of facilities, none of which the
clock has any use for: it only needs enough to show a name in a list, tell
two masjids in the same city apart, and know where to look for times.

    python packaging/make-directory.py A:/git/LiveAzan/data/mosques

Writes data/masjids.json. Run it again when the source is refreshed; the
result is committed, so a build needs neither the other project nor a
network.

The research has errors the clock cannot live with -- a website that is
another masjid's, a school filed as a mosque -- and they are corrected in
directory-overrides.json, next to this file, which is applied on every run so
that refreshing the source does not bring them back. audit-directory-websites.py
finds them; check-directory.py holds the result to the file.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys

# Keys worth carrying, and nothing else.
FIELDS = ("name", "city", "province", "country", "latitude", "longitude",
          "website", "type")

OVERRIDES = pathlib.Path(__file__).resolve().with_name("directory-overrides.json")
ACTIONS = ("website", "clear", "remove")


def load_overrides(path: pathlib.Path = OVERRIDES) -> list:
    """The corrections to make, each one:

        {"name", "city", "action": "website" | "clear" | "remove",
         "was": the website the research gives (null for none), "website": the right one,
         "why": what is wrong with it}

    "website" puts the right address in, "clear" leaves the masjid on the map
    without one, "remove" drops something that is not a masjid. A rule applies
    to the entry of that name and city -- and, when it says `was`, only while
    the research still holds that website, so a source that has since been
    fixed is not overruled by a correction meant for the old mistake.
    """
    try:
        rules = json.load(io.open(path, encoding="utf-8")).get("overrides") or []
    except FileNotFoundError:
        return []
    for rule in rules:
        where = "%s (%s)" % (rule.get("name"), rule.get("city"))
        if not rule.get("name") or not rule.get("city") or rule.get("action") not in ACTIONS:
            raise ValueError("%s: an override needs a name, a city and an action of %s" % (where, "/".join(ACTIONS)))
        if rule["action"] == "website" and not str(rule.get("website") or "").lower().startswith(("http://", "https://")):
            raise ValueError("%s: a 'website' override needs an http(s) address" % where)
        if not str(rule.get("why") or "").strip():
            raise ValueError("%s: say why" % where)
    return rules


def _correct(item: dict, rules: list, used: set) -> dict | None:
    """The research record with its corrections made, or None if it is to be dropped."""
    name, city = str(item.get("name") or "").strip(), str(item.get("city") or "").strip()
    for index, rule in enumerate(rules):
        if rule["name"] != name or rule["city"] != city:
            continue
        if "was" in rule and (rule["was"] or None) != (item.get("website") or None):
            continue
        used.add(index)
        if rule["action"] == "remove":
            return None
        item = dict(item, website=rule["website"] if rule["action"] == "website" else None)
    return item


def distil(source: pathlib.Path, overrides: list = ()) -> dict:
    rules = list(overrides)
    entries, seen, used = [], set(), set()
    for path in sorted(source.rglob("*.json")):
        if "_meta" in path.parts:
            continue
        try:
            data = json.load(io.open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("  skipped %s (%s)" % (path.name, exc))
            continue
        for item in data.get("mosques") or []:
            item = _correct(item, rules, used)
            if item is None:
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            lat, lon = item.get("latitude"), item.get("longitude")
            if lat is None or lon is None:
                continue
            # The same masjid can appear in two neighbouring city files.
            key = (name.lower(), round(float(lat), 4), round(float(lon), 4))
            if key in seen:
                continue
            seen.add(key)
            row = {}
            for field in FIELDS:
                value = item.get(field)
                if value in (None, "", []):
                    continue
                if field in ("latitude", "longitude"):
                    row[field] = round(float(value), 5)
                else:
                    row[field] = str(value).strip()
            entries.append(row)
    entries.sort(key=lambda row: (row.get("province", ""), row.get("city", ""),
                                  row["name"]))
    for index, rule in enumerate(rules):
        if index not in used:
            print("  override not used, the research no longer has %s (%s) as filed: %s"
                  % (rule["name"], rule["city"], rule.get("was") or rule["action"]))
    return {"source": "LiveAzan research directory", "masjids": entries}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    source = pathlib.Path(argv[0])
    if not source.is_dir():
        print("no such directory: %s" % source)
        return 1
    here = pathlib.Path(__file__).resolve().parent.parent
    out = here / "data" / "masjids.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    rules = load_overrides()
    result = distil(source, rules)
    print("%d corrections in %s" % (len(rules), OVERRIDES.name))
    # Compact separators: this is data the clock reads, not a file anyone edits.
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    io.open(out, "w", encoding="utf-8", newline="\n").write(text)
    print("%d masjids -> %s (%.0f KB)"
          % (len(result["masjids"]), out, os.path.getsize(out) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
