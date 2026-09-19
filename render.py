"""Pillow rendering of the clock card.

The card is drawn into an RGBA image with real per-pixel alpha -- rounded
corners, a blurred drop shadow, a vertical gradient, a glass sheen and an
optional neon bloom -- then handed to UpdateLayeredWindow. None of that is
possible with a Tk canvas, which has no antialiasing and only colour-keyed
transparency.
"""

from __future__ import annotations

import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import icons as icon_art, themes

FONT_FILES = {
    "Segoe UI Light": "segoeuil.ttf",
    "Segoe UI": "segoeui.ttf",
    "Segoe UI Semibold": "seguisb.ttf",
    "Segoe UI Bold": "segoeuib.ttf",
    "Consolas": "consola.ttf",
    "Cascadia Code": "CascadiaCode.ttf",
}
DEFAULT_FONT = "Segoe UI Light"

# Layout constants, in unscaled logical pixels.
SHADOW_MARGIN = 30
CORNER_RADIUS = 22
PAD_X = 32
PAD_Y = 26
DATE_GAP = 16
BAR_GAP = 16
FOOTER_GAP = 12
FOOTER_LINE_GAP = 7
BAR_HEIGHT = 5
MIN_CONTENT = 140
# The compact card keeps the digits at their set size but pulls the padding
# in: with no date, no bar and a single footer line, full padding reads as a
# small clock lost in a big box.
COMPACT_PAD = 0.6

# Reference strings: measuring these instead of the live text keeps the card a
# constant height, so it never twitches as the digits change.
DIGIT_REF = "0123456789"
DATE_REF = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,"


# Off Windows the Segoe files are not there; these stand in, best first, so
# the card keeps a light face, a regular one and a semibold one.
FALLBACK_FONTS = {
    "Segoe UI Light": ("HelveticaNeue-Light", "HelveticaNeueLight", "SFNS", "DejaVuSans-ExtraLight",
                       "Roboto-Light", "NotoSans-Light", "DejaVuSans"),
    "Segoe UI": ("HelveticaNeue", "SFNS", "Roboto-Regular", "NotoSans-Regular", "DejaVuSans"),
    "Segoe UI Semibold": ("HelveticaNeue-Medium", "HelveticaNeueMedium", "SFNS", "Roboto-Medium",
                          "NotoSans-SemiBold", "DejaVuSans-Bold"),
    "Segoe UI Bold": ("HelveticaNeue-Bold", "SFNS", "Roboto-Bold", "NotoSans-Bold", "DejaVuSans-Bold"),
    "Consolas": ("Menlo", "DejaVuSansMono", "LiberationMono-Regular"),
    "Cascadia Code": ("Menlo", "DejaVuSansMono", "LiberationMono-Regular"),
}
FONT_DIRS = (
    "/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
)
_font_index: dict[str, str] | None = None


def _index_fonts() -> dict[str, str]:
    """Stem (lower-cased, no extension) -> path, for every font on the system."""
    global _font_index
    if _font_index is None:
        found: dict[str, str] = {}
        for base in FONT_DIRS:
            if not os.path.isdir(base):
                continue
            for root, _dirs, files in os.walk(base):
                for name in files:
                    stem, ext = os.path.splitext(name)
                    if ext.lower() in (".ttf", ".otf", ".ttc"):
                        found.setdefault(stem.lower(), os.path.join(root, name))
        _font_index = found
    return _font_index


def _font_path(family: str) -> str | None:
    filename = FONT_FILES.get(family)
    if not filename:
        return None
    if os.name == "nt":
        candidate = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", filename)
        return candidate if os.path.exists(candidate) else None
    index = _index_fonts()
    for stem in FALLBACK_FONTS.get(family, ()):
        path = index.get(stem.lower())
        if path:
            return path
    return None


class _FontCache:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, family: str, size: int) -> ImageFont.FreeTypeFont:
        size = max(6, int(size))
        key = (family, size)
        if key not in self._cache:
            path = _font_path(family) or _font_path(DEFAULT_FONT)
            try:
                font = ImageFont.truetype(path, size) if path else None
            except OSError:
                font = None
            self._cache[key] = font or ImageFont.load_default(size)
        return self._cache[key]


_fonts = _FontCache()


def _vertical_gradient(width: int, height: int, top, bottom) -> Image.Image:
    strip = Image.new("RGB", (1, max(1, height)))
    pixels = strip.load()
    span = max(1, height - 1)
    for y in range(height):
        t = y / span
        pixels[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    return strip.resize((width, height), Image.BILINEAR)


def _digit_advance(font: ImageFont.FreeTypeFont) -> float:
    return max(font.getlength(str(d)) for d in range(10))


def text_width(
    font: ImageFont.FreeTypeFont, text: str, tabular: bool = False, tracking: float = 0
) -> float:
    """Width of `text`, laying digits out in equal cells when `tabular`.

    Proportional digits make the clock resize on almost every tick; equal cells
    keep the card perfectly still.
    """
    if not text:
        return 0.0
    if tabular:
        advance = _digit_advance(font)
        width = sum(advance if ch.isdigit() else font.getlength(ch) for ch in text)
    else:
        width = font.getlength(text)
    return width + tracking * (len(text) - 1)


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill,
    tabular: bool = False,
    tracking: float = 0,
) -> None:
    """Draw from a left-baseline origin, with optional tabular cells/tracking."""
    x, baseline = xy
    if not tabular and not tracking:
        draw.text((x, baseline), text, font=font, fill=fill, anchor="ls")
        return
    advance = _digit_advance(font) if tabular else 0
    for ch in text:
        if tabular and ch.isdigit():
            width = advance
            offset = (advance - font.getlength(ch)) / 2
        else:
            width = font.getlength(ch)
            offset = 0
        draw.text((x + offset, baseline), ch, font=font, fill=fill, anchor="ls")
        x += width + tracking


def _ink_box(font: ImageFont.FreeTypeFont, reference: str) -> tuple[float, float]:
    """(top, height) of the reference ink, measured from the baseline.

    PIL reports bboxes from an ascender-top origin, so subtracting the ascent
    re-bases them on the baseline -- which is what aligns two different sizes.
    """
    ascent, _ = font.getmetrics()
    box = font.getbbox(reference)
    return box[1] - ascent, box[3] - box[1]


# --- status icons ----------------------------------------------------------
# Line icons from icons.py: antialiased, one stroke weight, the same family as
# the rest of the card. They sit in a rail off the bottom-right corner, clear
# of the meeting text, which is where a row of controls belongs.

ICON_GAP = 0.65        # of icon size, between icons in the rail
ICON_TEXT_GAP = 1.6    # of icon size, between the footer text and the rail
ICON_IDLE_ALPHA = 170  # muted lines at rest; hovering the card lifts them
ICON_HALO = 1.55       # of icon size: the accent disc behind a hovered icon
ICON_HALO_ALPHA = 48
# The ink is a dozen pixels; a click needs more room than that. Sideways the
# pad stops short of the neighbouring icon (ICON_GAP is wider than two pads),
# the halo fits inside it, and upward it stays out of the text line above.
HIT_PAD_X = 0.3        # of icon size
HIT_PAD_Y = 0.55


def icons_width(icons, size: float) -> float:
    if not icons:
        return 0.0
    return len(icons) * size + (len(icons) - 1) * size * ICON_GAP


def icon_boxes(x: float, cy: float, icons, size: float) -> list[tuple[str, tuple]]:
    """Where each icon in the rail lands, laid out left-to-right from `x`."""
    boxes = []
    for index, kind in enumerate(icons):
        boxes.append((kind, (x, cy - size / 2, x + size, cy + size / 2)))
        x += size + (size * ICON_GAP if index < len(icons) - 1 else 0)
    return boxes


def draw_icons(
    image: Image.Image, boxes, idle, accent, hover_icon=None, card_hover=False,
    active=(),
) -> None:
    """Composite the rail onto `image`.

    Told apart by colour alone: muted lines at rest, full strength while the
    pointer is anywhere on the card, the accent for an icon with something
    running behind it (an armed alarm, a ticking timer), and the accent with a
    soft halo under the one icon the pointer is actually over. The row is a
    permanent set of buttons, so an icon with nothing running is still drawn.
    """
    for kind, (x0, y0, x1, y1) in boxes:
        if kind not in icon_art.SHAPES:
            continue
        size = x1 - x0
        if kind == hover_icon:
            icon_art.paste_disc(
                image, ((x0 + x1) / 2, (y0 + y1) / 2), size * ICON_HALO,
                accent, ICON_HALO_ALPHA,
            )
            icon_art.paste(image, kind, (x0, y0, x1, y1), accent)
        elif kind in active:
            icon_art.paste(image, kind, (x0, y0, x1, y1), accent)
        else:
            icon_art.paste(
                image, kind, (x0, y0, x1, y1), idle,
                255 if card_hover else ICON_IDLE_ALPHA,
            )


def footer_origin(geom: dict) -> tuple[float, float]:
    """(left edge, first-line baseline) of the footer text, in image coordinates."""
    centre_x = geom["margin"] + geom["card_w"] / 2
    top = geom["margin"] + geom["pad_y"]
    return (
        centre_x - geom["footer_w"] / 2,
        top + geom["footer_offset"] + geom["footer_lift"] - geom["footer_top"],
    )


def footer_icon_boxes(geom: dict, icons) -> list[tuple[str, tuple]]:
    """Where the rail's icons land, so a caller can hit-test what was drawn.

    The rail hangs off the card's bottom-right corner: its right edge is the
    content edge the seconds bar ends on, and it is centred on the last
    footer line. The card is a bitmap rather than widgets, so anything
    clickable has to be found by rectangle -- sharing this arithmetic with
    _with_text is the point, since the two would drift apart the moment the
    layout changed.
    """
    if not icons:
        return []
    _x, baseline = footer_origin(geom)
    last = baseline + geom["footer_line_step"] * max(0, geom["footer_lines_n"] - 1)
    cy = last - geom["footer_text_h"] / 2
    right = geom["margin"] + geom["card_w"] - geom["pad_x"]
    size = geom["icon_size"]
    return icon_boxes(right - icons_width(icons, size), cy, icons, size)


def calendar_hit_box(geom: dict):
    """The whole lower block -- progress bar and meeting lines.

    The glyph alone is a few pixels square, which is a hard target on a card
    you are also meant to be able to drag. Everything below the date opens the
    panel instead; the top half stays the grab handle.
    """
    if not (geom.get("has_bar") or geom.get("has_footer")):
        return None
    margin, pad_y = geom["margin"], geom["pad_y"]
    offsets = [geom[key] for key, flag in
               (("bar_offset", "has_bar"), ("footer_offset", "has_footer"))
               if geom.get(flag)]
    top = margin + pad_y + min(offsets) - geom["bar_h"] * 2
    return (
        margin + geom["pad_x"] * 0.25,
        top,
        margin + geom["card_w"] - geom["pad_x"] * 0.25,
        margin + geom["card_h"] - pad_y * 0.25,
    )


def footer_hit_box(geom: dict, icons, kind: str):
    """The clickable rectangle for one rail icon, or None if it is absent."""
    size = geom["icon_size"]
    for found, (x0, y0, x1, y1) in footer_icon_boxes(geom, icons):
        if found == kind:
            return (
                x0 - size * HIT_PAD_X, y0 - size * HIT_PAD_Y,
                x1 + size * HIT_PAD_X, y1 + size * HIT_PAD_Y,
            )
    return None


def footer_icon_at(geom: dict, icons, x: float, y: float) -> str | None:
    """Which rail icon a point (in image coordinates) lands on, if any."""
    for kind in icons:
        box = footer_hit_box(geom, icons, kind)
        if box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            return kind
    return None


def to_premultiplied_bgra(image: Image.Image) -> Image.Image:
    """UpdateLayeredWindow wants premultiplied alpha; PIL keeps it straight."""
    red, green, blue, alpha = image.split()
    return Image.merge(
        "RGBA",
        (
            ImageChops.multiply(red, alpha),
            ImageChops.multiply(green, alpha),
            ImageChops.multiply(blue, alpha),
            alpha,
        ),
    )


# The bar is a handful of pixels tall, so every edge on it is visible: drawn
# straight onto the card its pills and rings come out stair-stepped. Like the
# icons, it is drawn oversized on its own layer and downsampled once.
BAR_SUPERSAMPLE = 4


def _hex_rgb(value: str):
    """'#7f28ff' -> (127, 40, 255), or None for anything else."""
    value = (value or "").strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _draw_bar_marks(layer, theme, marks, left, right, top, bottom, ss, scale) -> None:
    """Draw each meeting as a block along the day bar, in overlay coordinates.

    A block, not a dot, because length is information: a two-hour meeting
    should look like four times the half-hour one beside it. Every block gets a
    ring of the card's own dark tone first -- without it a finished meeting
    sitting on the bright elapsed section is invisible, which is exactly where
    most of them are. A meeting that belongs to a known organisation takes
    that organisation's colour, so the shape of the day also says whose day
    it is.
    """
    draw = ImageDraw.Draw(layer)
    height = bottom - top
    block_h = height * 2.0
    ring = max(1.0, 1.4 * scale) * ss
    outline_w = max(1, int(round(1.1 * scale * ss)))
    centre_y = (top + bottom) / 2
    y0, y1 = centre_y - block_h / 2, centre_y + block_h / 2
    span = right - left

    def block(x0, x1, inset=0.0, **kwargs):
        draw.rounded_rectangle(
            (x0 - inset, y0 - inset, x1 + inset, y1 + inset),
            radius=(block_h + inset * 2) / 2, **kwargs,
        )

    for mark in sorted(marks, key=lambda m: getattr(m, "position", 0.0)):
        state = mark.state
        x0 = left + span * _clamp(mark.position)
        x1 = left + span * _clamp(mark.end_position)
        # A fifteen-minute meeting is barely two pixels wide on a 24-hour bar,
        # so there is a floor -- but it has to stay well under an hour's width,
        # or every meeting up to an hour long would come out the same size and
        # the length would stop meaning anything.
        minimum = max(2.0 * scale * ss, block_h * 0.34)
        if x1 - x0 < minimum:
            centre = (x0 + x1) / 2
            x0, x1 = centre - minimum / 2, centre + minimum / 2
        tint = _hex_rgb(getattr(mark, "colour", "")) or theme.accent

        block(x0, x1, inset=ring, fill=theme.grad_bottom + (255,))
        if state == "done":
            block(x0, x1, outline=theme.fg_muted + (200,), width=outline_w)
        elif state == "live":
            block(x0, x1, fill=(255, 255, 255, 255))
        elif state == "next":
            block(x0, x1, fill=tint + (255,))
            block(x0, x1, outline=(255, 255, 255, 215), width=outline_w)
        else:
            block(x0, x1, fill=tint + (205,))


def bar_geometry(geom: dict) -> tuple[float, float, float, float]:
    """(left, right, top, bottom) of the bar in image coordinates."""
    margin, bar_h = geom["margin"], geom["bar_h"]
    left = margin + geom["pad_x"]
    right = margin + geom["card_w"] - geom["pad_x"]
    top = margin + geom["pad_y"] + geom["bar_offset"]
    return left, right, top, top + bar_h


def bar_mark_boxes(geom: dict, marks, scale: float) -> list[tuple]:
    """Where each block lands, padded a little, so the pointer can find it.

    The same arithmetic as _draw_bar_marks, in card pixels rather than the
    supersampled overlay: a block is twice the bar's height and never
    narrower than the floor the drawing applies.
    """
    if not geom.get("has_bar") or not marks:
        return []
    left, right, top, bottom = bar_geometry(geom)
    span, bar_h = right - left, bottom - top
    block_h = bar_h * 2.0
    centre_y = (top + bottom) / 2
    pad = max(2.0, 3.0 * scale)
    boxes = []
    for mark in marks:
        x0 = left + span * _clamp(mark.position)
        x1 = left + span * _clamp(mark.end_position)
        minimum = max(2.0 * scale, block_h * 0.34)
        if x1 - x0 < minimum:
            centre = (x0 + x1) / 2
            x0, x1 = centre - minimum / 2, centre + minimum / 2
        boxes.append((mark, (
            x0 - pad, centre_y - block_h / 2 - pad, x1 + pad, centre_y + block_h / 2 + pad,
        )))
    return boxes


def bar_mark_at(geom: dict, marks, scale: float, x: float, y: float):
    """(mark, box) under a point in image coordinates, or None."""
    for mark, box in bar_mark_boxes(geom, marks, scale):
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            return mark, box
    return None


def draw_badge(image, theme, geom: dict, centre_y: float, right: float) -> None:
    """The running timer: hourglass and countdown in a pill, ending at `right`."""
    width, height = geom["badge_w"], geom["badge_h"]
    x0, y0 = right - width, centre_y - height / 2
    ss = 4
    pill = Image.new("RGBA", (int(width * ss) + 1, int(height * ss) + 1), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle(
        (0, 0, pill.width - 1, pill.height - 1), radius=pill.height / 2,
        fill=theme.timer + (46,), outline=theme.timer + (120,), width=ss,
    )
    image.alpha_composite(
        pill.resize((int(width) + 1, int(height) + 1), Image.LANCZOS), (int(x0), int(y0))
    )
    icon = geom["badge_icon"]
    ix = x0 + geom["badge_pad"]
    icon_art.paste(image, "timer", (ix, centre_y - icon / 2, ix + icon, centre_y + icon / 2), theme.timer)
    draw = ImageDraw.Draw(image)
    draw.text(
        (ix + icon + geom["badge_gap"], centre_y), geom["badge"], font=geom["badge_font"],
        fill=theme.timer + (255,), anchor="lm",
    )


def draw_bar(image, theme, scale, left, right, top, bottom, fraction, work_end=1.0, marks=(),
             needle=False):
    """Paint the progress bar and everything riding on it onto `image`.

    Every translucent piece goes through its own layer and alpha_composite:
    ImageDraw *replaces* RGBA pixels rather than blending, so a 52-alpha
    track drawn straight on would punch a see-through slot in the card. The
    whole thing is assembled at BAR_SUPERSAMPLE times its size and reduced
    once, which is what makes the pills and rings smooth.
    """
    ss = BAR_SUPERSAMPLE
    bar_h = bottom - top
    # Room around the bar for the marks (twice its height) and the glow.
    pad = int(bar_h * 2 + 8 * scale) + 4
    origin = (int(left) - pad, int(top) - pad)
    width = int(right - left) + pad * 2 + 1
    height = int(bar_h) + pad * 2 + 1
    size = (width * ss, height * ss)

    def X(x):
        return (x - origin[0]) * ss

    def Y(y):
        return (y - origin[1]) * ss

    L, R, T, B = X(left), X(right), Y(top), Y(bottom)
    radius = (B - T) / 2

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        (L, T, R, B), radius=radius, fill=theme.accent + (52,)
    )

    filled = L + (R - L) * _clamp(fraction)
    if filled - L >= (B - T):
        fill = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(fill).rounded_rectangle(
            (L, T, filled, B), radius=radius, fill=theme.accent + (255,)
        )
        overlay.alpha_composite(
            fill.filter(ImageFilter.GaussianBlur(max(1, int(3 * scale)) * ss))
        )
        overlay.alpha_composite(fill)

    if 0.0 < work_end < 1.0:
        # The bar always covers 24 hours so nothing can fall off it; the hours
        # past the end of the working day are dimmed rather than cut, which is
        # what makes an evening meeting visible at all. Laid over the fill, so
        # the boundary still reads late at night.
        divide = L + (R - L) * work_end
        shade = Image.new("RGBA", size, (0, 0, 0, 0))
        shade_draw = ImageDraw.Draw(shade)
        shade_draw.rounded_rectangle(
            (divide, T, R, B), radius=radius, fill=theme.grad_bottom + (175,)
        )
        shade_draw.rectangle(
            (divide, T, divide + max(1.0, scale) * ss, B), fill=theme.fg_muted + (170,)
        )
        overlay.alpha_composite(shade)

    if marks:
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        _draw_bar_marks(layer, theme, marks, L, R, T, B, ss, scale)
        # A soft drop shadow lifts the blocks off the bar. Cut from the
        # blocks' own silhouette, so it fits whatever shape they take.
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        shadow.putalpha(layer.getchannel("A").point(lambda v: v * 130 // 255))
        shadow = shadow.filter(ImageFilter.GaussianBlur(1.6 * scale * ss))
        overlay.alpha_composite(shadow, (0, int(round(1.2 * scale * ss))))
        overlay.alpha_composite(layer)

    if needle:
        # NOW: a needle standing proud of the bar at the fill's leading edge.
        # The fill alone was too easy to lose among the meeting blocks, and
        # the block for the meeting under way is white, so the needle is the
        # theme's warm timer colour -- the one hue nothing else on the bar
        # uses -- with a dark rim to separate it from whatever it crosses.
        # Taller than the blocks, and laid on last, over them.
        x = L + (R - L) * _clamp(fraction)
        cy = (T + B) / 2
        half_h = (B - T) * 1.9           # blocks reach 1.0 -- this clears them
        half_w = max(0.9, 1.0 * scale) * ss
        rim = max(0.8, 0.9 * scale) * ss
        pin = Image.new("RGBA", size, (0, 0, 0, 0))
        pin_draw = ImageDraw.Draw(pin)
        pin_draw.rounded_rectangle(
            (x - half_w - rim, cy - half_h - rim, x + half_w + rim, cy + half_h + rim),
            radius=half_w + rim, fill=theme.grad_bottom + (215,),
        )
        pin_draw.rounded_rectangle(
            (x - half_w, cy - half_h, x + half_w, cy + half_h),
            radius=half_w, fill=theme.timer + (255,),
        )
        pin_shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        pin_shadow.putalpha(pin.getchannel("A").point(lambda v: v * 150 // 255))
        pin_shadow = pin_shadow.filter(ImageFilter.GaussianBlur(1.5 * scale * ss))
        overlay.alpha_composite(pin_shadow, (0, int(round(1.4 * scale * ss))))
        overlay.alpha_composite(pin)

    image.alpha_composite(overlay.resize((width, height), Image.LANCZOS), origin)


class Renderer:
    """Draws the card, caching the parts that do not change every tick."""

    def __init__(self) -> None:
        self._bg_key = None
        self._bg: Image.Image | None = None
        self._base_key = None
        self._base: Image.Image | None = None

    # --- geometry ----------------------------------------------------------
    def measure(
        self, settings: dict, scale: float, clock: str, suffix: str, date: str,
        footer_lines: tuple = (), footer_icons: tuple = (), badge: str = "",
    ) -> dict:
        size = max(1, int(settings["font_size"] * scale))
        family = settings["font_family"]
        time_font = _fonts.get(family, size)
        suffix_font = _fonts.get(family, max(10, int(size * 0.34)))
        date_font = _fonts.get(family, max(10, int(size * 0.24)))
        footer_font = _fonts.get("Segoe UI Semibold", max(10, int(size * 0.20)))
        tabular = bool(settings.get("tabular_digits", True))
        tracking = max(1.0, size * 0.035)

        clock_w = text_width(time_font, clock, tabular)
        suffix_gap = size * 0.14 if suffix else 0
        suffix_w = text_width(suffix_font, suffix, tracking=tracking) if suffix else 0
        time_group_w = clock_w + suffix_gap + suffix_w
        date_w = text_width(date_font, date, tracking=tracking) if date else 0
        footer_track = max(0.8, size * 0.022)
        footer_lines = tuple(footer_lines)
        footer_text_w = max(
            [text_width(footer_font, line, tracking=footer_track)
             for line in footer_lines] or [0]
        )
        # Whole pixels: the icon is rasterised at exactly this size, and a
        # fractional box would smear its strokes across two pixel rows.
        icon_size = float(max(13, int(round(size * 0.27))))
        icon_w = icons_width(footer_icons, icon_size)
        # The rail is reserved to the right of the text whichever of the two
        # ends up setting the card's width.
        footer_w = footer_text_w + (
            icon_w + icon_size * ICON_TEXT_GAP if icon_w else 0
        )

        # The timer badge sits on the date row, right-aligned: a pill with the
        # hourglass and the countdown in the theme's timer colour, a size up
        # from the footer so it is the first thing after the time itself.
        badge = badge if date else ""
        badge_font = _fonts.get("Segoe UI Semibold", max(11, int(size * 0.27)))
        badge_icon = float(max(12, int(round(size * 0.27))))
        badge_pad = badge_icon * 0.55
        badge_gap = badge_icon * 0.4
        badge_text_w = text_width(badge_font, badge, tracking=max(0.8, size * 0.02)) if badge else 0
        badge_w = badge_pad * 2 + badge_icon + badge_gap + badge_text_w if badge else 0
        badge_h = badge_icon * 1.7 if badge else 0

        time_top, time_h = _ink_box(time_font, DIGIT_REF)
        date_top, date_h = _ink_box(date_font, DATE_REF) if date else (0, 0)
        has_footer = bool(footer_lines or footer_icons)
        footer_top, footer_text_h = (
            _ink_box(footer_font, DATE_REF) if has_footer else (0, 0)
        )
        # An icon taller than the caps pushes the text down by half the
        # difference, so the rail stays inside the block it is centred on.
        footer_lift = (
            max(0.0, (icon_size - footer_text_h) / 2) if footer_icons else 0.0
        )
        footer_h = footer_text_h + footer_lift * 2
        footer_line_step = footer_text_h + FOOTER_LINE_GAP * scale
        footer_block_h = (
            footer_h + footer_line_step * max(0, len(footer_lines) - 1)
            if has_footer else 0
        )

        compact = bool(settings.get("compact", False))
        pad_scale = COMPACT_PAD if compact else 1.0
        pad_x = int(PAD_X * scale * pad_scale)
        pad_y = int(PAD_Y * scale * pad_scale)
        bar_h = max(2, int(BAR_HEIGHT * scale))

        # Compact means time and the next thing: the bar goes with the date.
        has_bar = bool(settings["seconds_bar"]) and not compact
        content_w = max(time_group_w, date_w, footer_w)
        if has_bar:
            content_w = max(content_w, MIN_CONTENT * scale)
        if badge:
            # Room for the badge on the right without pushing the date off
            # centre: the same space is left free on the left.
            content_w = max(content_w, date_w + 2 * (badge_w + size * 0.3))

        # Stack the rows once, here, and hand every drawing site the same
        # offsets -- the bar is painted in card() and the text in _with_text,
        # and they would drift apart if each did its own arithmetic.
        offset = time_h
        date_offset = bar_offset = footer_offset = 0.0
        if date:
            offset += DATE_GAP * scale
            date_offset = offset
            offset += date_h
        if has_bar:
            offset += BAR_GAP * scale
            bar_offset = offset
            offset += bar_h
        if has_footer:
            offset += FOOTER_GAP * scale
            footer_offset = offset
            offset += footer_block_h
        content_h = offset

        return {
            "time_font": time_font,
            "suffix_font": suffix_font,
            "date_font": date_font,
            "footer_font": footer_font,
            "footer_w": footer_w,
            "footer_top": footer_top,
            "footer_h": footer_h,
            "footer_text_h": footer_text_h,
            "footer_line_step": footer_line_step,
            "footer_block_h": footer_block_h,
            "footer_lines_n": len(footer_lines),
            "footer_track": footer_track,
            "footer_text_w": footer_text_w,
            "icon_size": icon_size,
            "footer_lift": footer_lift,
            "badge": badge,
            "badge_font": badge_font,
            "badge_icon": badge_icon,
            "badge_pad": badge_pad,
            "badge_gap": badge_gap,
            "badge_w": badge_w,
            "badge_h": badge_h,
            "icon_w": icon_w,
            "has_footer": has_footer,
            "has_bar": has_bar,
            "date_offset": date_offset,
            "bar_offset": bar_offset,
            "footer_offset": footer_offset,
            "tabular": tabular,
            "tracking": tracking,
            "clock_w": clock_w,
            "suffix_w": suffix_w,
            "suffix_gap": suffix_gap,
            "time_group_w": time_group_w,
            "date_w": date_w,
            "time_top": time_top,
            "time_h": time_h,
            "date_top": date_top,
            "date_h": date_h,
            "pad_x": pad_x,
            "pad_y": pad_y,
            "bar_h": bar_h,
            "content_w": int(round(content_w)),
            "card_w": int(round(content_w)) + pad_x * 2,
            "card_h": int(round(content_h)) + pad_y * 2,
            "margin": int(SHADOW_MARGIN * scale),
            "radius": int(CORNER_RADIUS * scale),
        }

    # --- layers ------------------------------------------------------------
    def _background(self, theme: themes.Theme, geom: dict, scale: float,
                    solid: bool = False) -> Image.Image:
        key = (theme.name, geom["card_w"], geom["card_h"], geom["radius"], round(scale, 2),
               solid)
        if key == self._bg_key and self._bg is not None:
            return self._bg

        margin, card_w, card_h = geom["margin"], geom["card_w"], geom["card_h"]
        radius = geom["radius"]
        image = Image.new("RGBA", (card_w + margin * 2, card_h + margin * 2), (0, 0, 0, 0))

        # Drop shadow: a blurred copy of the card silhouette, nudged downwards.
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        drop = int(9 * scale)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (margin, margin + drop, margin + card_w, margin + card_h + drop),
            radius=radius,
            fill=(0, 0, 0, theme.shadow_alpha),
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(int(11 * scale))))

        # Card body: vertical gradient clipped to a rounded rectangle.
        gradient = _vertical_gradient(card_w, card_h, theme.grad_top, theme.grad_bottom)
        mask = Image.new("L", (card_w, card_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, card_w - 1, card_h - 1), radius=radius,
            fill=255 if solid else theme.card_alpha,
        )
        card = gradient.convert("RGBA")
        card.putalpha(mask)

        # Glass sheen: white fading out down the top half of the card.
        if theme.sheen:
            sheen_h = max(1, int(card_h * 0.5))
            sheen = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 0))
            sheen_alpha = Image.new("L", (card_w, card_h), 0)
            strip = _vertical_gradient(1, sheen_h, (theme.sheen,) * 3, (0, 0, 0)).convert("L")
            sheen_alpha.paste(strip.resize((card_w, sheen_h), Image.BILINEAR), (0, 0))
            sheen.putalpha(
                ImageChops.multiply(sheen_alpha, mask.point(lambda v: 255 if v else 0))
            )
            card.alpha_composite(sheen)

        image.alpha_composite(card, (margin, margin))

        # Hairline border last, so it stays crisp on top of everything.
        # Skipped entirely when the theme has none: Pillow's default ink is
        # white, so asking for "no outline" would paint a white rim.
        if theme.border[3]:
            ImageDraw.Draw(image).rounded_rectangle(
                (margin, margin, margin + card_w - 1, margin + card_h - 1),
                radius=radius, outline=theme.border, width=max(1, int(round(scale))),
            )

        self._bg_key, self._bg = key, image
        return image

    def _with_text(
        self,
        theme: themes.Theme,
        geom: dict,
        scale: float,
        clock: str,
        suffix: str,
        date: str,
        footer_lines: tuple = (),
        footer_icons: tuple = (),
        hover_icon: str | None = None,
        card_hover: bool = False,
        active_icons: tuple = (),
        solid: bool = False,
    ) -> Image.Image:
        key = (
            theme.name, geom["card_w"], geom["card_h"], clock, suffix, date,
            footer_lines, footer_icons, hover_icon, card_hover, tuple(active_icons),
            round(scale, 2), geom["tabular"], geom["badge"], solid,
        )
        if key == self._base_key and self._base is not None:
            return self._base

        image = self._background(theme, geom, scale, solid).copy()
        margin = geom["margin"]
        centre_x = margin + geom["card_w"] / 2
        top = margin + geom["pad_y"]

        # Baselines derived from ink boxes, so the block is optically centred
        # rather than padded out by ascender space the digits never reach.
        time_baseline = top - geom["time_top"]
        group_x = centre_x - geom["time_group_w"] / 2

        if theme.glow > 0:
            glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw_text(
                ImageDraw.Draw(glow), (group_x, time_baseline), clock, geom["time_font"],
                theme.accent + (int(210 * theme.glow),), geom["tabular"],
            )
            image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(2, int(10 * scale)))))

        draw = ImageDraw.Draw(image)
        draw_text(
            draw, (group_x, time_baseline), clock, geom["time_font"],
            theme.fg + (255,), geom["tabular"],
        )
        if suffix:
            draw_text(
                draw,
                (group_x + geom["clock_w"] + geom["suffix_gap"], time_baseline),
                suffix.upper(),
                geom["suffix_font"],
                theme.fg_muted + (255,),
                tracking=geom["tracking"],
            )

        if date:
            date_baseline = top + geom["date_offset"] - geom["date_top"]
            draw_text(
                draw,
                (centre_x - geom["date_w"] / 2, date_baseline),
                date,
                geom["date_font"],
                theme.fg_muted + (255,),
                tracking=geom["tracking"],
            )
            if geom["badge"]:
                draw_badge(image, theme, geom, date_baseline - geom["date_h"] / 2,
                           margin + geom["card_w"] - geom["pad_x"])
                draw = ImageDraw.Draw(image)

        if geom["has_footer"]:
            cursor, baseline = footer_origin(geom)
            if footer_icons:
                draw_icons(
                    image, footer_icon_boxes(geom, footer_icons),
                    theme.fg_muted, theme.accent, hover_icon, card_hover,
                    active=tuple(active_icons),
                )
                # Compositing may have swapped the image's pixel buffer.
                draw = ImageDraw.Draw(image)
            # The next meeting leads in accent and the ones after it step
            # back, so a glance finds what is imminent without reading all
            # three lines.
            for index, line in enumerate(footer_lines):
                draw_text(
                    draw, (cursor, baseline + index * geom["footer_line_step"]),
                    line, geom["footer_font"],
                    (theme.accent if index == 0 else theme.fg_muted) + (255,),
                    tracking=geom["footer_track"],
                )

        self._base_key, self._base = key, image
        return image

    # --- public ------------------------------------------------------------
    def card(
        self,
        settings: dict,
        scale: float,
        clock: str,
        suffix: str,
        date: str,
        bar_fraction: float,
        footer_lines: tuple = (),
        footer_icons: tuple = (),
        hover_icon: str | None = None,
        card_hover: bool = False,
        active_icons: tuple = (),
        bar_marks: tuple = (),
        work_end: float = 1.0,
        premultiply: bool = True,
        badge: str = "",
        solid: bool = False,
        now_needle: bool = False,
    ) -> Image.Image:
        """Render the card. `premultiply=False` yields a normal RGBA image,
        which is what previews, tests and the icon generator want.

        `hover_icon` names the rail icon under the pointer, if any, and
        `card_hover` says whether the pointer is over the card at all.
        `solid` drops the theme's translucency: the card is being used, and
        whatever is behind it must not show through the digits. `now_needle`
        stands a marker at the bar's leading edge, for the mode where the bar
        is the day and the fill is how far through it you are."""
        theme = themes.get(settings["theme"])
        geom = self.measure(
            settings, scale, clock, suffix, date, footer_lines, footer_icons, badge
        )
        base = self._with_text(
            theme, geom, scale, clock, suffix, date, footer_lines, footer_icons,
            hover_icon, card_hover, active_icons, solid,
        )

        if not geom["has_bar"]:
            return to_premultiplied_bgra(base) if premultiply else base

        image = base.copy()
        left, right, top, bottom = bar_geometry(geom)
        draw_bar(
            image, theme, scale, left, right, top, bottom,
            bar_fraction, work_end, bar_marks, needle=now_needle,
        )
        return to_premultiplied_bgra(image) if premultiply else image

    def invalidate(self) -> None:
        self._bg_key = self._base_key = None
        self._bg = self._base = None
