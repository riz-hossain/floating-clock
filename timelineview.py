"""Where everything goes when a timeline is drawn.

The Windows window (Tk) and the Mac and Linux window (Qt) draw the same picture, and
the arithmetic of it -- which words sit where, what wraps, how far a step is pushed
in, how tall it all comes to -- is the same arithmetic. It is done once, here, in
plain numbers, so that the two cannot drift apart and none of it needs a window to
be tested. A window supplies `measure(text, bold, small) -> width in pixels` and gets
back a list of things to draw; drawing them is all it has left to do.

Each item is a dict, and the window draws them in order:

    {"op": "rail", "x", "y1", "y2"}                          the line a timeline hangs on
    {"op": "icon", "x", "y", "d", "state", "phase"}          a state, in a d-by-d box
    {"op": "text", "x", "y", "h", "text", "role", "bold", "small", "anchor"}
                                                              y..y+h is the line it sits in;
                                                              anchor "e" ends at x, else starts
    {"op": "bar", "x", "y", "w", "h", "fraction", "role"}    how far along; role FG while it
                                                              works, GOOD / BAD / MUTED once it is over

`role` is one of FG, SOFT, MUTED, DIM, GOOD, BAD, which the window turns into a colour.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import timeline

FG, SOFT, MUTED, DIM, GOOD, BAD = "fg", "soft", "muted", "dim", "good", "bad"
ELLIPSIS = "…"


@dataclass(frozen=True)
class Metrics:
    """Sizes in pixels, already scaled for the screen. Only `width` has no default."""

    width: int
    pad: int = 14
    icon: int = 18              # a source's icon
    icon_small: int = 14        # a step's
    indent: int = 30            # how far a step is pushed in from its source
    gap: int = 10               # between an icon and its words
    line: int = 20              # one line of text
    small_line: int = 17        # one line of small text
    row_gap: int = 6
    bar: int = 6


def headline(snap: dict) -> tuple[str, str]:
    """(what to say above the bar, and the role to say it in).

    The words come with the timeline (Timeline.plan): a check of one masjid says "Checking
    <name>" and "Times found", finding the masjids near you says its own.
    """
    words = snap.get("words") or timeline.CHECK_WORDS
    name = snap.get("title") or ""
    outcome = snap.get("outcome") or ""
    if not outcome:
        busy = words.get("busy") or timeline.CHECK_WORDS["busy"]
        if "%s" in busy:
            return (busy % name if name else "Getting ready", FG)
        return (busy, FG)
    if outcome == "found":
        return (words.get("found") or timeline.CHECK_WORDS["found"], GOOD)
    if outcome == "stopped":
        return (words.get("stopped") or timeline.CHECK_WORDS["stopped"], MUTED)
    return (words.get("none") or timeline.CHECK_WORDS["none"], BAD)


def fit(text: str, room: int, measure, bold: bool = False, small: bool = False) -> str:
    """`text`, cut with an ellipsis if it is wider than `room`: for a word that cannot wrap."""
    if measure(text, bold, small) <= room:
        return text
    while len(text) > 1 and measure(text + ELLIPSIS, bold, small) > room:
        text = text[:-1]
    return text + ELLIPSIS


def wrap(text: str, room: int, measure, bold: bool = False, small: bool = False) -> list[str]:
    """`text` broken into lines no wider than `room`, at spaces. A word wider than that is cut."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        word = fit(word, room, measure, bold, small)
        trial = word if not current else current + " " + word
        if current and measure(trial, bold, small) > room:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


_LABEL_ROLE = {timeline.PENDING: DIM, timeline.RUNNING: FG, timeline.DONE: FG,
               timeline.SKIPPED: MUTED, timeline.FAILED: FG}


def layout(snap: dict, m: Metrics, measure, phase: int = 0) -> dict:
    """{"height": int, "items": [...], "running": (top, bottom) or None} for one snapshot.

    `phase` is which frame of the spinner to show, counted by the window as it redraws.
    """
    items: list[dict] = []
    right = m.width - m.pad
    y = m.pad

    words, role = headline(snap)
    items.append({"op": "text", "x": m.pad, "y": y, "h": m.line, "bold": True, "small": False,
                  "anchor": "w", "role": role, "text": fit(words, right - m.pad - 60, measure, True)})
    items.append({"op": "text", "x": right, "y": y, "h": m.line, "bold": False, "small": False,
                  "anchor": "e", "role": MUTED, "text": timeline.clock_text(snap.get("elapsed") or 0.0)})
    y += m.line + 6
    items.append({"op": "bar", "x": m.pad, "y": y, "w": max(1, m.width - 2 * m.pad), "h": m.bar,
                  "fraction": max(0.0, min(1.0, float(snap.get("fraction") or 0.0))), "role": role})
    y += m.bar + 14

    rows = snap.get("rows") or []
    if not rows:
        items.append({"op": "text", "x": m.pad, "y": y, "h": m.line, "bold": False, "small": True,
                      "anchor": "w", "role": MUTED,
                      "text": "Getting ready..." if snap.get("busy", True) else "Nothing was tried."})
        y += m.line

    times_w = measure("00:00", False, True) + m.gap        # the room the time column keeps
    sources: list[tuple] = []                              # (centre x, centre y) of each source's icon
    steps: dict = {}                                       # source key -> the same, for its steps
    running = None
    for row in rows:
        level = row["level"]
        d = m.icon_small if level else m.icon
        icon_x = m.pad + (m.indent if level else 0)
        text_x = icon_x + d + m.gap
        room = right - times_w - text_x
        bold = level == 0
        top = y

        label = fit(row["label"], room, measure, bold)
        line_end = text_x + measure(label, bold)
        items.append({"op": "text", "x": text_x, "y": top, "h": m.line, "bold": bold, "small": False,
                      "anchor": "w", "role": _LABEL_ROLE.get(row["state"], FG), "text": label})
        if row["sub"]:
            sub_x = line_end + m.gap
            if sub_x + measure(row["sub"], False, True) <= right - times_w:
                items.append({"op": "text", "x": sub_x, "y": top, "h": m.line, "bold": False, "small": True,
                              "anchor": "w", "role": MUTED, "text": row["sub"]})
                line_end = sub_x + measure(row["sub"], False, True)

        detail = row["detail"]
        height = m.line
        if detail:
            detail_role = BAD if row["state"] == timeline.FAILED else MUTED
            if line_end + m.gap + measure(detail, False, True) <= right - times_w:
                items.append({"op": "text", "x": line_end + m.gap, "y": top, "h": m.line, "bold": False,
                              "small": True, "anchor": "w", "role": detail_role, "text": detail})
            else:
                for extra in wrap(detail, room, measure, small=True):
                    items.append({"op": "text", "x": text_x, "y": top + height, "h": m.small_line,
                                  "bold": False, "small": True, "anchor": "w", "role": detail_role, "text": extra})
                    height += m.small_line

        span = timeline.span_text(row["seconds"])
        if span:
            items.append({"op": "text", "x": right, "y": top, "h": m.line, "bold": False, "small": True,
                          "anchor": "e", "role": MUTED, "text": span})
        centre = (icon_x + d / 2.0, top + m.line / 2.0)
        items.append({"op": "icon", "x": icon_x, "y": top + (m.line - d) // 2, "d": d,
                      "state": row["state"], "phase": phase})
        if level:
            steps.setdefault(row["key"].split(".")[0], []).append(centre)
        else:
            sources.append(centre)
        if row["state"] == timeline.RUNNING:
            running = (top, top + height) if running is None else (min(running[0], top), max(running[1], top + height))
        y += height + m.row_gap

    rails = []
    for group in [sources] + list(steps.values()):
        if len(group) > 1:
            rails.append({"op": "rail", "x": group[0][0], "y1": group[0][1], "y2": group[-1][1]})
    # rails go first, so that each icon is drawn over the line it sits on
    return {"height": int(y - m.row_gap + m.pad) if rows else int(y + m.pad),
            "items": rails + items, "running": running}
