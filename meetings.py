"""The meetings panel: what is still ahead, what has just finished, and --
when the masjid's times are being followed -- today's iqamas beside them.

Opened by clicking the calendar glyph on the clock card. Drawn the same way as
the card and the alert toast -- Pillow into a layered window -- so the three
read as one product, which also means every clickable row is a rectangle we
hit-test ourselves rather than a widget.

Everything above MeetingsPanel is plain layout the Qt host reuses, so Tk is
imported inside the panel rather than up here: a Mac build ships Qt alone.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PIL import Image, ImageChops, ImageDraw, ImageFilter

if TYPE_CHECKING:
    import tkinter as tk

from . import outlook, prayer as prayer_mod, render, themes, win32util as w32
from .timetext import clock_text

WIDTH = 420
# The prayer column, added to the panel's width only when there are times to
# put in it: a day with none should not leave a stripe of empty panel.
PRAYER_COLUMN = 150
PRAYER_GAP = 18       # between the meetings and that column
PAD = 18
MARGIN = 26
RADIUS = 16
GAP = 12            # from the clock card's visible edge
ROW_PAD = 5
SECTION_GAP = 13
REFRESH_MS = 30_000

# Kept deliberately short: this is a glance, not an agenda. Anything longer
# belongs in Outlook, which is one click away on any row that can be joined.
# What is ahead is the point of the panel, so it gets most of the room; the
# history is there to answer "what was that one just now?" and no more.
AHEAD_LIMIT = 10
PAST_WINDOW_MINUTES = 120.0
MIN_PAST = 2   # after a quiet morning, the last two still beat an empty list
PAST_CAP = 5   # a busy two hours should still not push the panel off-screen

EMPTY_TEXT = {
    "ALL DAY": "",
    "REMAINING": "Nothing left ahead",
    "EARLIER": "No earlier meetings",
}
ALL_DAY_TEXT = "All day"


def split_meetings(events, now: datetime, ahead_limit: int = AHEAD_LIMIT,
                   past_minutes: float = PAST_WINDOW_MINUTES,
                   min_past: int = MIN_PAST):
    """(remaining, earlier) for the panel.

    `remaining` keeps meetings already under way -- those are the ones being
    looked for most often. `earlier` shows everything from the last couple of
    hours, but never fewer than `min_past`: on a quiet morning the useful
    answer is still "yesterday's last two", not an empty section. Both run
    so the meeting nearest to now reads first.
    """
    # All-day entries have their own section; here they would sit at the top
    # as "12:00 am, now" and stay highlighted all day.
    events = [e for e in events if not getattr(e, "all_day", False)]
    cutoff = now - timedelta(minutes=past_minutes)
    remaining = sorted((e for e in events if e.end > now), key=lambda e: e.start)
    finished = sorted(
        (e for e in events if e.end <= now), key=lambda e: e.end, reverse=True
    )
    earlier = [e for e in finished if e.end > cutoff]
    if len(earlier) < min_past:
        earlier = finished[:min_past]
    return remaining[:ahead_limit], earlier[:PAST_CAP]


def all_day_events(events, now: datetime) -> list:
    """Today's all-day entries, for the panel's own section: the ones whose
    span covers this moment, so yesterday's holiday is not still listed and
    tomorrow's does not show up early."""
    return sorted(
        (e for e in events if getattr(e, "all_day", False) and e.start <= now < e.end),
        key=lambda e: (e.start, e.subject or ""),
    )


def row_stamp(event, use_24h: bool, today=None) -> str:
    """The time column for a row: the start time, or the words for an
    all-day entry, which has no time worth printing."""
    if getattr(event, "all_day", False):
        return ALL_DAY_TEXT
    return clock_text(event.start, use_24h, today)


def row_status(event, now: datetime, past: bool) -> str:
    """The right-hand column: how long ago it ran, or how long until it does."""
    if past:
        return outlook.describe_ago((now - event.end).total_seconds() / 60.0)
    if event.is_live(now):
        return "now"
    return outlook.describe_gap(event.minutes_until(now))


def _ellipsise(font, text: str, max_width: float) -> str:
    text = text or ""
    if max_width <= 0:
        return ""
    if font.getlength(text) <= max_width:
        return text
    while text and font.getlength(text + "...") > max_width:
        text = text[:-1]
    return text.rstrip() + "..."


def mark_ink(image) -> tuple[float, float]:
    """(bright-end luminance, mean chroma) of a mark's opaque pixels, 0-255.

    The bright end rather than the mean, because a logo is mostly its own
    background: averaging a white glyph on a transparent field with its few
    dark outlines says "dark" about something plainly legible.
    """
    tally = [
        (count, pixel) for count, pixel in
        (image.getcolors(maxcolors=image.width * image.height) or [])
        if pixel[3] >= 128
    ]
    if not tally:
        return 0.0, 0.0
    weight = sum(count for count, _ in tally)
    chroma = sum(
        (max(p[:3]) - min(p[:3])) * count for count, p in tally
    ) / weight

    scored = sorted(
        (0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2], count) for count, p in tally
    )
    # The luminance at the 90th percentile of opaque pixels.
    cutoff, seen = weight * 0.9, 0
    bright = scored[-1][0]
    for lum, count in scored:
        seen += count
        if seen >= cutoff:
            bright = lum
            break
    return bright, chroma


# A mark needs a light tile behind it only when it is both dark and colourless
# -- GitHub's black octocat, and every wordmark drawn for a white page. A
# saturated mark reads fine on the dark card even at low luminance (ZeuZ's
# purple sits at 88), so chroma is what keeps those from being boxed in.
DARK_MARK_LUMINANCE = 70.0
FLAT_MARK_CHROMA = 40.0


def _rgb(colour: str) -> tuple[int, int, int]:
    """#rrggbb -> (r, g, b), falling back to a neutral grey."""
    try:
        return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError, TypeError):
        return (120, 120, 140)


def _wash(canvas, draw, box, radius: int, fill) -> None:
    """A translucent rounded fill laid *over* the picture.

    ImageDraw writes pixels; on an RGBA image a fill with alpha 40 does not
    tint the card, it replaces the card's alpha with 40 and the desktop
    shows through the hole. Composite a layer instead, and fall back to
    the plain draw when there is nothing to composite onto."""
    if canvas is None or len(fill) < 4 or fill[3] >= 255:
        draw.rounded_rectangle(box, radius=radius, fill=fill)
        return
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=fill)
    canvas.alpha_composite(layer)


def _fade(image, amount: float):
    faded = image.copy()
    faded.putalpha(image.getchannel("A").point(lambda a: int(a * amount)))
    return faded


PLATFORMS = (
    ("zoom.us", "Zoom"), ("teams.microsoft.com", "Teams"), ("teams.live.com", "Teams"),
    ("meet.google.com", "Google Meet"), ("webex.com", "Webex"), ("whereby.com", "Whereby"),
    ("gotomeeting.com", "GoToMeeting"), ("chime.aws", "Chime"),
)


def platform_of(join_url: str) -> str:
    """'https://us02web.zoom.us/j/8156…' -> 'Zoom'; the host when unknown."""
    if not join_url:
        return ""
    host = join_url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].lower()
    for suffix, name in PLATFORMS:
        if host == suffix or host.endswith("." + suffix):
            return name
    return host[4:] if host.startswith("www.") else host


def _person(organizer: str) -> str:
    """A display name from whatever Outlook put in Organizer."""
    name = (organizer or "").strip()
    if "<" in name:
        name = name.split("<", 1)[0].strip().strip('"')
    if "@" in name:
        name = name.split("@", 1)[0].replace(".", " ").title()
    return name


def detail_line(event, org: str = "", calendar: str = "") -> str:
    """The muted second line of a row: who, whose, where, from which calendar.

    Everything here is optional, so a bare internal meeting stays a single
    line and a client call carries the company, the organiser and the
    platform. The calendar name only appears when there is more than one
    calendar to tell apart -- the caller passes "" otherwise.
    """
    parts = [org, _person(getattr(event, "organizer", ""))]
    location = (getattr(event, "location", "") or "").strip()
    platform = platform_of(getattr(event, "join_url", ""))
    if platform:
        parts.append(platform)
    elif location and not location.lower().startswith("http"):
        parts.append(location)
    parts.append(calendar)
    seen: list[str] = []
    for part in parts:
        part = " ".join((part or "").split())
        if part and part.lower() not in [s.lower() for s in seen]:
            seen.append(part)
    return "  ·  ".join(seen)


def _inside(rect, x: float, y: float) -> bool:
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


class MeetingsPanel:
    """One panel. The owner keeps at most one, and closing it is the toggle.

    Content is pulled from `provider` rather than passed in, so the panel can
    redraw itself while it sits open: the gaps it prints ("in 20m") would
    otherwise freeze at whatever they were when it was opened.
    """

    def __init__(self, parent: "tk.Tk", provider, settings: dict, scale: float,
                 on_close, anchor, org_lookup=None, label_lookup=None,
                 calendar_lookup=None, prayers_provider=None) -> None:
        import tkinter as tk

        self.provider = provider        # () -> (list[outlook.Event], error str)
        self.prayers_provider = prayers_provider   # () -> list[prayer.Prayer]
        # source id -> (mark image or None, colour, initials); None when the
        # clock is reading Outlook alone and every row is from one place.
        self.org_lookup = org_lookup
        self.label_lookup = label_lookup          # event -> organisation name
        self.calendar_lookup = calendar_lookup    # event -> calendar name, or ""
        self.s = settings
        self.theme = themes.get(settings["theme"])
        self.scale = scale
        self.on_close = on_close
        self.anchor = anchor            # the clock window's rect
        self.rows: list[tuple[tuple[float, float, float, float], str]] = []
        self.close_rect = None
        self.image: Image.Image | None = None
        self._closed = False
        self._timer = None

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.withdraw()
        self.win.update_idletasks()

        self.hwnd = self._hwnd()
        w32.update_ex_style(self.hwnd, add=w32.WS_EX_LAYERED | w32.WS_EX_TOOLWINDOW)

        self.win.bind("<Button-1>", self._on_click)
        self.win.bind("<Button-3>", lambda _e: self.close())

        self.refresh()
        self.win.deiconify()

    def _hwnd(self) -> int:
        window_id = self.win.winfo_id()
        try:
            parent = w32.user32.GetParent(window_id)
        except OSError:
            parent = 0
        return parent or window_id

    # --- drawing -----------------------------------------------------------
    def refresh(self) -> None:
        """Redraw from the current events and re-arm the next redraw."""
        import tkinter as tk

        if self._closed:
            return
        if self._timer is not None:
            try:
                self.win.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None
        # Settings are live, so a theme picked while the panel is open
        # applies on the next redraw rather than being frozen at open.
        self.theme = themes.get(self.s["theme"])
        self.image = self._render()
        self.win.geometry("%dx%d" % self.image.size)
        self.win.update_idletasks()
        self.place()
        w32.push_layered_bitmap(self.hwnd, self.image, 1.0)
        self._timer = self.win.after(REFRESH_MS, self.refresh)

    def _render(self) -> Image.Image:
        scale, theme = self.scale, self.theme
        margin = int(MARGIN * scale)
        pad = int(PAD * scale)
        radius = int(RADIUS * scale)
        gap = int(PRAYER_GAP * scale)
        prayers = self._todays_prayers()
        # The meetings keep the width they always had; the column is added
        # beside them rather than taken out of them.
        inner = int(WIDTH * scale) - pad * 2
        column_w = int(PRAYER_COLUMN * scale) + gap if prayers else 0
        width = int(WIDTH * scale) + column_w
        full = width - pad * 2          # the panel's own inner width

        head_font = render._fonts.get("Segoe UI Semibold", max(9, int(10 * scale)))
        time_font = render._fonts.get("Segoe UI Semibold", max(10, int(12 * scale)))
        subject_font = render._fonts.get("Segoe UI", max(11, int(13 * scale)))
        status_font = render._fonts.get("Segoe UI", max(9, int(11 * scale)))
        detail_font = render._fonts.get("Segoe UI", max(9, int(10.5 * scale)))
        detail_h = int(sum(detail_font.getmetrics()) + 1 * scale)

        now = datetime.now()
        events, error = self.provider()
        remaining, earlier = split_meetings(events, now)
        whole_day = all_day_events(events, now)
        sections = (("REMAINING", remaining, False), ("EARLIER", earlier, True))
        if whole_day:
            # Only when there is one: an empty ALL DAY heading would be noise
            # on every ordinary day.
            sections = (("ALL DAY", whole_day, False),) + sections
        use_24h = bool(self.s["use_24h"])
        today = now.date()
        prayer_row_h = int(sum(subject_font.getmetrics()) + 9 * scale)
        # One shared time column across both sections: a weekday prefix on
        # some rows and not others would stagger the subjects otherwise.
        stamps = [row_stamp(e, use_24h, today)
                  for _label, items, _past in sections for e in items]
        column_offset = max(
            [time_font.getlength(s) for s in stamps] + [52 * scale]
        ) + int(11 * scale)

        head_h = sum(head_font.getmetrics())
        row_h = int(
            max(sum(subject_font.getmetrics()), sum(time_font.getmetrics()))
            + ROW_PAD * scale
        )
        title_gap = int(11 * scale)
        label_gap = int(5 * scale)
        section_gap = int(SECTION_GAP * scale)

        details = {id(e): self._details(e) for _l, items, _p in sections for e in items}

        card_h = pad * 2 + head_h + title_gap
        note = error if (error and (remaining or earlier)) else ""
        if error and not note:
            card_h += row_h * 2
        else:
            if note:
                card_h += detail_h + label_gap
            for _label, items, _past in sections:
                # An empty section still prints one line, so the panel keeps a
                # steady shape instead of collapsing to a sliver.
                card_h += head_h + label_gap + section_gap
                card_h += (row_h + detail_h) * len(items) if items else row_h
            card_h -= section_gap
        if prayers:
            # Whichever column is taller sets the height: a quiet day of
            # meetings must not cut the prayer list off.
            card_h = max(card_h, pad * 2 + head_h + title_gap
                         + prayer_row_h * len(prayers))
        card_h = int(card_h)

        image = Image.new("RGBA", (width + margin * 2, card_h + margin * 2), (0, 0, 0, 0))

        # Shadow, card, border, accent rail -- the toast's recipe exactly.
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (margin, margin + int(8 * scale), margin + width, margin + card_h + int(8 * scale)),
            radius=radius, fill=(0, 0, 0, min(220, theme.shadow_alpha + 40)),
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(int(12 * scale))))

        gradient = render._vertical_gradient(width, card_h, theme.grad_top, theme.grad_bottom)
        mask = Image.new("L", (width, card_h), 0)
        # Fully opaque: the panel exists to be read, and a desktop showing
        # through a list of meetings just makes both harder to see.
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width - 1, card_h - 1), radius=radius, fill=255
        )
        card = gradient.convert("RGBA")
        card.putalpha(mask)
        image.alpha_composite(card, (margin, margin))

        draw = ImageDraw.Draw(image)
        if theme.border[3]:
            draw.rounded_rectangle(
                (margin, margin, margin + width - 1, margin + card_h - 1),
                radius=radius, outline=theme.border, width=max(1, int(scale)),
            )
        rail = Image.new("RGBA", (width, card_h), (0, 0, 0, 0))
        ImageDraw.Draw(rail).rectangle(
            (0, 0, max(2, int(4 * scale)), card_h), fill=theme.accent + (255,)
        )
        rail.putalpha(
            ImageChops.multiply(rail.getchannel("A"), mask.point(lambda v: 255 if v else 0))
        )
        image.alpha_composite(rail, (margin, margin))

        self.rows = []
        x = margin + pad
        y = margin + pad

        render.draw_text(
            draw, (x, y + head_font.getmetrics()[0]), "MEETINGS", head_font,
            theme.accent + (255,), tracking=max(1.0, 1.4 * scale),
        )
        # A dismiss target in the corner: the panel takes no focus, so there is
        # no Escape key to lean on.
        cross = int(4.5 * scale)
        ccx, ccy = x + full - cross, y + head_font.getmetrics()[0] * 0.55
        stroke = max(1, int(round(1.4 * scale)))
        for dx in (-1, 1):
            draw.line(
                (ccx - cross, ccy - cross * dx, ccx + cross, ccy + cross * dx),
                fill=theme.fg_muted + (220,), width=stroke,
            )
        pick = int(9 * scale)
        self.close_rect = (ccx - cross - pick, ccy - cross - pick,
                           ccx + cross + pick, ccy + cross + pick)
        if prayers:
            self._draw_prayer_column(
                image, draw, prayers, now, x + inner + gap, y,
                int(PRAYER_COLUMN * scale), card_h - pad * 2, gap, scale, theme,
                head_font, time_font, subject_font, prayer_row_h, title_gap, use_24h,
            )
        y += head_h + title_gap

        if error and not note:
            for line in (_ellipsise(subject_font, error, inner), "Right-click to dismiss"):
                draw.text(
                    (x, y + subject_font.getmetrics()[0]), line, font=subject_font,
                    fill=theme.fg_muted + (220,), anchor="ls",
                )
                y += row_h
            return render.to_premultiplied_bgra(image)

        for label, items, past in sections:
            render.draw_text(
                draw, (x, y + head_font.getmetrics()[0]), label, head_font,
                theme.fg_muted + (200,), tracking=max(1.0, 1.2 * scale),
            )
            y += head_h + label_gap
            if not items:
                draw.text(
                    (x, y + subject_font.getmetrics()[0]), EMPTY_TEXT[label],
                    font=subject_font, fill=theme.fg_muted + (150,), anchor="ls",
                )
                y += row_h
            for event in items:
                y = self._draw_row(
                    draw, event, now, today, past, x, y, inner, row_h, scale,
                    theme, use_24h, column_offset, time_font, subject_font,
                    status_font, image,
                    detail=(details[id(event)], detail_font, detail_h),
                )
            y += section_gap

        if note:
            # A calendar that could not be read, under the ones that could.
            draw.text(
                (x, y - section_gap + label_gap + detail_font.getmetrics()[0]),
                _ellipsise(detail_font, "Not read: " + note, inner),
                font=detail_font, anchor="ls", fill=theme.fg_muted + (170,),
            )

        return render.to_premultiplied_bgra(image)

    def _org_mark(self, canvas, draw, event, column, baseline, scale, subject_font,
                  past: bool) -> float:
        """Badge the row with the organisation it belongs to.

        Returns how far the subject has to move right. Belonging to several
        companies makes a bare "Standup" ambiguous, so the mark carries the
        answer without spending a word of the row on it.
        """
        if self.org_lookup is None or not self.s.get("show_org_marks", True):
            return 0.0
        found = self.org_lookup(event)
        if not found:
            return 0.0
        mark, colour, initials = found
        # Sized to the capitals, not the font's ascent: Segoe UI's ascent
        # runs well above its cap height, so a mark that tall stood proud of
        # the text. Centred on the middle of the capitals instead, so it
        # reads as part of the line rather than a hat on it.
        em = float(getattr(subject_font, "size", 0) or subject_font.getmetrics()[0])
        size = int(max(10, round(em * 0.86)))
        top = int(round(baseline - em * 0.36 - size / 2))
        box = (int(column), top, int(column) + size, top + size)

        if mark is not None:
            thumb = mark.resize((size, size), Image.LANCZOS)
            if self._needs_chip(mark):
                # Inset the logo so the chip reads as a tile behind it rather
                # than a border stuck to its edges.
                pad = max(1, int(size * 0.12))
                _wash(canvas, draw, box, int(3 * scale), (236, 236, 242, 120 if past else 255))
                inner_size = size - pad * 2
                thumb = mark.resize((inner_size, inner_size), Image.LANCZOS)
                if past:
                    thumb = _fade(thumb, 0.45)
                canvas.paste(thumb, (box[0] + pad, box[1] + pad), thumb)
            else:
                if past:
                    # Finished rows are muted; a full-strength logo would
                    # out-shout the text that is still live.
                    thumb = _fade(thumb, 0.45)
                canvas.paste(thumb, (box[0], box[1]), thumb)
        else:
            _wash(canvas, draw, box, int(3 * scale), _rgb(colour) + (140 if past else 235,))
            draw.text(
                ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), initials,
                font=self._badge_font(size), anchor="mm", fill=(255, 255, 255, 240),
            )
        return size + int(6 * scale)

    def _todays_prayers(self) -> list:
        """Today's iqamas, or nothing when they are switched off.

        Never lets a missing or broken feed take the panel with it: the
        meetings are what the panel is for.
        """
        # getattr, like the other lookups: the panel is also built piecemeal
        # in tests, and a missing provider simply means no column.
        provider = getattr(self, "prayers_provider", None)
        if provider is None or not self.s.get("prayer_enabled", True):
            return []
        try:
            return prayer_mod.on_day(provider() or (), datetime.now())
        except Exception:
            return []

    def _draw_prayer_column(self, canvas, draw, prayers, now, x, y, width, height,
                            gap, scale, theme, head_font, time_font, body_font,
                            row_h, head_gap, use_24h) -> None:
        """Today's iqamas beside the meetings: the ones gone dimmed, the next
        one lit, the rest plain.

        Time on the left edge and name on the right gives the narrow column
        two clean margins, which is what stops it reading as a ragged
        afterthought next to the meeting rows.
        """
        # A hairline between the two columns, composited rather than drawn:
        # ImageDraw would replace the card's alpha and cut a slot in it.
        _wash(canvas, draw, (x - gap // 2, y, x - gap // 2 + max(1, int(scale)), y + height),
              0, theme.fg_muted + (48,))

        render.draw_text(
            draw, (x, y + head_font.getmetrics()[0]), "PRAYER", head_font,
            theme.accent + (255,), tracking=max(1.0, 1.4 * scale),
        )
        y += sum(head_font.getmetrics()) + head_gap

        following = prayer_mod.next_prayer(prayers, now)
        for item in prayers:
            past = item.iqama <= now
            is_next = following is not None and item.key == following.key
            baseline = y + body_font.getmetrics()[0] + int(4 * scale)
            if is_next:
                # Reaching past the text on both sides, the way the live
                # meeting row does, so the name is not flush to the pill.
                _wash(canvas, draw,
                      (x - int(7 * scale), y, x + width + int(7 * scale),
                       y + row_h - int(2 * scale)),
                      int(6 * scale), theme.accent + (52,))
            alpha = 255 if is_next else (120 if past else 225)
            draw.text(
                (x, baseline), item.time_text(use_24h), font=time_font, anchor="ls",
                fill=(theme.accent if is_next else theme.fg_muted) + (alpha,),
            )
            draw.text(
                (x + width, baseline), item.name, font=body_font, anchor="rs",
                fill=(theme.fg_muted if past else theme.fg) + (alpha,),
            )
            y += row_h

    def _needs_chip(self, mark) -> bool:
        cache = getattr(self, "_chip_cache", None)
        if cache is None:
            cache = self._chip_cache = {}
        key = id(mark)
        if key not in cache:
            bright, chroma = mark_ink(mark)
            cache[key] = bright < DARK_MARK_LUMINANCE and chroma < FLAT_MARK_CHROMA
        return cache[key]

    def _badge_font(self, size: int):
        return render._fonts.get("Segoe UI Semibold", max(7, int(size * 0.52)))

    def _details(self, event) -> str:
        """The second line for a row, from the owner's lookups when it has them."""
        org = ""
        lookup = getattr(self, "label_lookup", None)
        if lookup is not None and self.s.get("show_org_marks", True):
            org = lookup(event) or ""
        calendar = ""
        which = getattr(self, "calendar_lookup", None)
        if which is not None:
            calendar = which(event) or ""
        return detail_line(event, org, calendar)

    def _draw_row(self, draw, event, now, today, past, x, y, inner, row_h, scale,
                  theme, use_24h, column_offset, time_font, subject_font,
                  status_font, canvas=None, detail=None) -> float:
        detail_text, detail_font, detail_h = detail or ("", status_font, 0)
        rect = (x - int(7 * scale), y, x + inner + int(7 * scale), y + row_h + detail_h)
        all_day = bool(getattr(event, "all_day", False))
        if not past and not all_day and event.is_live(now):
            # The meeting you are in gets a wash of accent, so the eye lands on
            # it before it reads a single word. Not for an all-day entry: it
            # is "live" from midnight to midnight and the wash would mean
            # nothing.
            _wash(canvas, draw, rect, int(6 * scale), theme.accent + (40,))

        time_text = row_stamp(event, use_24h, today)
        status = "" if all_day else row_status(event, now, past)
        column = x + column_offset
        baseline = y + subject_font.getmetrics()[0] + int(1 * scale)
        shift = 0.0
        if canvas is not None:
            shift = self._org_mark(
                canvas, draw, event, column, baseline, scale, subject_font, past
            )
        subject = _ellipsise(
            subject_font, event.subject or "(no subject)",
            x + inner - column - shift - int(4 * scale),
        )

        draw.text(
            (x, baseline), time_text, font=time_font, anchor="ls",
            fill=(theme.fg_muted if past else theme.accent) + (255,),
        )
        draw.text(
            (column + shift, baseline), subject, font=subject_font, anchor="ls",
            fill=(theme.fg_muted if past else theme.fg) + (255,),
        )
        if event.join_url:
            # A dot in the gutter marks the rows that go somewhere when clicked;
            # the platform on the second line says where.
            dot = max(1.5, 2.0 * scale)
            cy = baseline - subject_font.getmetrics()[0] * 0.32
            draw.ellipse(
                (x - int(11 * scale) - dot, cy - dot, x - int(11 * scale) + dot, cy + dot),
                fill=theme.accent + (255,),
            )
            self.rows.append((rect, event.join_url))
        # The second line: how far off, then who it is with and where, in a
        # quieter voice than the row itself.
        second = "  ·  ".join(part for part in (status, detail_text) if part)
        detail_baseline = baseline + detail_font.getmetrics()[0] + int(2 * scale)
        draw.text(
            (column + shift, detail_baseline),
            _ellipsise(detail_font, second, x + inner - column - shift),
            font=detail_font, anchor="ls",
            fill=theme.fg_muted + (150 if past else 215,),
        )
        return y + row_h + detail_h

    # --- behaviour ---------------------------------------------------------
    def place(self) -> None:
        """Sit under the clock card, flipping above it when there is no room."""
        a_left, a_top, a_right, a_bottom = self.anchor
        # The clock's monitor, not the panel's: a fresh window sits on the
        # primary monitor until it is moved, which is the wrong screen to
        # clamp to when the clock lives on another.
        left, top, right, bottom = w32.work_area_at(
            (a_left + a_right) // 2, (a_top + a_bottom) // 2)
        card_margin = int(render.SHADOW_MARGIN * self.scale)
        margin = int(MARGIN * self.scale)
        width, height = self.image.size
        gap = int(GAP * self.scale)

        # Line the visible panel up with the visible card. Both bitmaps carry a
        # transparent shadow margin, so aligning the windows themselves would
        # leave the panel looking offset by the difference between the two.
        x = a_left + card_margin - margin
        y = a_bottom - card_margin + gap - margin
        if y + height - margin > bottom:
            y = a_top + card_margin - gap - height + margin
        x = max(left - margin, min(x, right - width + margin))
        y = max(top - margin, min(y, bottom - height + margin))
        w32.move_window(self.hwnd, int(x), int(y))

    def _on_click(self, _event) -> None:
        win_left, win_top, _r, _b = w32.window_rect(self.hwnd)
        cx, cy = w32.cursor_pos()
        x, y = cx - win_left, cy - win_top
        if self.close_rect and _inside(self.close_rect, x, y):
            self.close()
            return
        for rect, url in self.rows:
            if _inside(rect, x, y):
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
                self.close()
                return

    def close(self) -> None:
        import tkinter as tk

        if self._closed:
            return
        self._closed = True
        if self._timer is not None:
            try:
                self.win.after_cancel(self._timer)
            except tk.TclError:
                pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_close(self)
