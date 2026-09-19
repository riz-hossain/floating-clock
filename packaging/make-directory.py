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


def distil(source: pathlib.Path) -> dict:
    entries, seen = [], set()
    for path in sorted(source.rglob("*.json")):
        if "_meta" in path.parts:
            continue
        try:
            data = json.load(io.open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("  skipped %s (%s)" % (path.name, exc))
            continue
        for item in data.get("mosques") or []:
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

    result = distil(source)
    # Compact separators: this is data the clock reads, not a file anyone edits.
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    io.open(out, "w", encoding="utf-8", newline="\n").write(text)
    print("%d masjids -> %s (%.0f KB)"
          % (len(result["masjids"]), out, os.path.getsize(out) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
