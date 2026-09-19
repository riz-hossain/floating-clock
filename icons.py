"""Line icons for the clock card.

The card is a Pillow bitmap, and ImageDraw has no antialiasing: a shape drawn
straight onto it at 13 px comes out jagged. Every icon here is drawn on an
8-bit mask several times larger than it will be shown, then downsampled with
a Lanczos filter, which is the same coverage antialiasing a browser applies
to an SVG. Round caps and joins are drawn explicitly, since ImageDraw only
mitres.

Icons are authored on a 24-unit grid with a ~2-unit stroke -- the Lucide and
Feather conventions -- so any of those sets can be transcribed here directly.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

GRID = 24.0
SUPERSAMPLE = 6
STROKE = 2.0   # grid units

# Primitives, all on the 24-unit grid:
#   ("path", [(x, y), ...])                 open polyline, round caps and joins
#   ("circle", cx, cy, r)
#   ("arc", cx, cy, r, start, end)           degrees, clockwise from 3 o'clock
#   ("rrect", x0, y0, x1, y1, radius)
SHAPES: dict[str, tuple] = {
    "meeting": (
        ("rrect", 3, 4, 21, 22, 2.5),
        ("path", [(3, 10), (21, 10)]),
        ("path", [(8, 2), (8, 6)]),
        ("path", [(16, 2), (16, 6)]),
    ),
    "alarm": (
        ("arc", 12, 8, 6, 180, 360),
        ("path", [(6, 8), (6, 12.2), (4.7, 15.3), (3, 17), (21, 17),
                  (19.3, 15.3), (18, 12.2), (18, 8)]),
        ("arc", 12, 20.4, 1.9, 20, 160),
    ),
    "timer": (
        ("path", [(5, 2), (19, 2)]),
        ("path", [(5, 22), (19, 22)]),
        ("path", [(7, 2), (7, 6.2), (12, 12), (17, 6.2), (17, 2)]),
        ("path", [(7, 22), (7, 17.8), (12, 12), (17, 17.8), (17, 22)]),
    ),
    "stopwatch": (
        ("path", [(10, 2), (14, 2)]),
        ("path", [(12, 2), (12, 6)]),
        ("circle", 12, 14, 8),
        ("path", [(12, 14), (15.2, 10.8)]),
    ),
    # Menu furniture.
    "check": (("path", [(5, 12.5), (10, 17.5), (19, 7)]),),
    "chevron": (("path", [(9.5, 6), (15.5, 12), (9.5, 18)]),),
    "dot": (("disc", 12, 12, 4.5),),
}
# The rail's icons; the menu furniture after them is not part of the rail.
KINDS = ("meeting", "alarm", "timer", "stopwatch")

_masks: dict[tuple[str, int], Image.Image] = {}
_tinted: dict[tuple, Image.Image] = {}


def _cap(draw: ImageDraw.ImageDraw, x: float, y: float, width: float) -> None:
    r = width / 2
    draw.ellipse((x - r, y - r, x + r, y + r), fill=255)


def _stroke_path(draw: ImageDraw.ImageDraw, points, width: float) -> None:
    if len(points) == 1:
        _cap(draw, points[0][0], points[0][1], width)
        return
    draw.line(points, fill=255, width=max(1, int(round(width))), joint="curve")
    for x, y in (points[0], points[-1]):
        _cap(draw, x, y, width)


def _stroke_arc(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, start: float, end: float,
    width: float,
) -> None:
    # PIL strokes inward from the bounding box, so push the box out by half
    # the stroke to centre it on the radius.
    outer = r + width / 2
    draw.arc(
        (cx - outer, cy - outer, cx + outer, cy + outer), start, end,
        fill=255, width=max(1, int(round(width))),
    )
    for angle in (start, end):
        theta = math.radians(angle)
        _cap(draw, cx + math.cos(theta) * r, cy + math.sin(theta) * r, width)


def _stroke_circle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, width: float) -> None:
    outer = r + width / 2
    draw.ellipse(
        (cx - outer, cy - outer, cx + outer, cy + outer),
        outline=255, width=max(1, int(round(width))),
    )


def _stroke_rrect(draw, x0, y0, x1, y1, radius, width) -> None:
    half = width / 2
    draw.rounded_rectangle(
        (x0 - half, y0 - half, x1 + half, y1 + half),
        radius=radius + half, outline=255, width=max(1, int(round(width))),
    )


def mask(kind: str, size: int) -> Image.Image:
    """An antialiased 8-bit coverage mask of `kind`, `size` pixels square."""
    size = max(4, int(size))
    key = (kind, size)
    cached = _masks.get(key)
    if cached is not None:
        return cached

    shapes = SHAPES[kind]   # KeyError for an unknown icon is the right error
    big = size * SUPERSAMPLE
    unit = big / GRID
    canvas = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(canvas)
    width = STROKE * unit

    for shape in shapes:
        tag = shape[0]
        if tag == "path":
            _stroke_path(draw, [(x * unit, y * unit) for x, y in shape[1]], width)
        elif tag == "circle":
            _, cx, cy, r = shape
            _stroke_circle(draw, cx * unit, cy * unit, r * unit, width)
        elif tag == "arc":
            _, cx, cy, r, start, end = shape
            _stroke_arc(draw, cx * unit, cy * unit, r * unit, start, end, width)
        elif tag == "rrect":
            _, x0, y0, x1, y1, radius = shape
            _stroke_rrect(
                draw, x0 * unit, y0 * unit, x1 * unit, y1 * unit, radius * unit, width
            )
        elif tag == "disc":
            _, cx, cy, r = shape
            draw.ellipse(
                ((cx - r) * unit, (cy - r) * unit, (cx + r) * unit, (cy + r) * unit),
                fill=255,
            )
        else:
            raise ValueError("unknown icon primitive %r" % (tag,))

    result = canvas.resize((size, size), Image.LANCZOS)
    _masks[key] = result
    return result


def tinted(kind: str, size: int, colour, alpha: int = 255) -> Image.Image:
    """The icon as an RGBA image in one flat colour, ready to composite."""
    colour = tuple(int(c) for c in colour[:3])
    alpha = max(0, min(255, int(alpha)))
    key = (kind, int(size), colour, alpha)
    cached = _tinted.get(key)
    if cached is not None:
        return cached
    coverage = mask(kind, size)
    image = Image.new("RGBA", coverage.size, colour + (0,))
    image.putalpha(coverage.point(lambda v: v * alpha // 255) if alpha < 255 else coverage)
    _tinted[key] = image
    return image


def disc(diameter: int, colour, alpha: int) -> Image.Image:
    """A soft-edged filled circle -- the halo behind a hovered icon."""
    diameter = max(2, int(diameter))
    key = ("disc", diameter, tuple(int(c) for c in colour[:3]), int(alpha))
    cached = _tinted.get(key)
    if cached is not None:
        return cached
    big = diameter * SUPERSAMPLE
    coverage = Image.new("L", (big, big), 0)
    ImageDraw.Draw(coverage).ellipse((0, 0, big - 1, big - 1), fill=255)
    coverage = coverage.resize((diameter, diameter), Image.LANCZOS)
    image = Image.new("RGBA", coverage.size, key[2] + (0,))
    image.putalpha(coverage.point(lambda v: v * int(alpha) // 255))
    _tinted[key] = image
    return image


def paste(image: Image.Image, kind: str, box, colour, alpha: int = 255) -> None:
    """Composite `kind` centred in `box` (x0, y0, x1, y1) onto `image`."""
    x0, y0, x1, y1 = box
    size = int(round(min(x1 - x0, y1 - y0)))
    icon = tinted(kind, size, colour, alpha)
    left = int(round((x0 + x1) / 2 - size / 2))
    top = int(round((y0 + y1) / 2 - size / 2))
    image.alpha_composite(icon, (left, top))


def paste_disc(image: Image.Image, centre, diameter: float, colour, alpha: int) -> None:
    halo = disc(int(round(diameter)), colour, alpha)
    left = int(round(centre[0] - halo.width / 2))
    top = int(round(centre[1] - halo.height / 2))
    image.alpha_composite(halo, (left, top))
