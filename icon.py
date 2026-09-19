"""Generates the application icon.

Run as `python -m floating_clock.icon <out.ico>`; the build script calls it so
the icon is a build artefact rather than a binary checked into the repo.
"""

from __future__ import annotations

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SUPERSAMPLE = 4


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]):
    strip = Image.new("RGB", (1, size))
    pixels = strip.load()
    for y in range(size):
        t = y / max(1, size - 1)
        pixels[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return strip.resize((size, size), Image.BILINEAR)


def render_icon(size: int = 256) -> Image.Image:
    """A rounded-square clock face, drawn oversized then downsampled."""
    s = size * SUPERSAMPLE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Rounded-square body with the app's signature purple gradient.
    body = _gradient(s, (124, 58, 237), (56, 26, 140)).convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    body.putalpha(mask)
    image.alpha_composite(body)

    # Top-left sheen for a little depth.
    sheen = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).ellipse(
        (-s * 0.35, -s * 0.75, s * 0.95, s * 0.45), fill=(255, 255, 255, 40)
    )
    sheen.putalpha(Image.composite(sheen.getchannel("A"), Image.new("L", (s, s), 0), mask))
    image.alpha_composite(sheen.filter(ImageFilter.GaussianBlur(s * 0.03)))

    draw = ImageDraw.Draw(image)
    centre = s / 2
    radius = s * 0.31
    ring = max(1, int(s * 0.035))

    draw.ellipse(
        (centre - radius, centre - radius, centre + radius, centre + radius),
        outline=(255, 255, 255, 236),
        width=ring,
    )

    # Hands at 10:09 -- the angle watchmakers use because it frames the dial.
    def hand(angle_deg: float, length: float, width: int) -> None:
        angle = math.radians(angle_deg - 90)
        draw.line(
            (
                centre,
                centre,
                centre + math.cos(angle) * length,
                centre + math.sin(angle) * length,
            ),
            fill=(255, 255, 255, 245),
            width=width,
        )

    hand(300, radius * 0.52, max(1, int(s * 0.045)))  # hour
    hand(54, radius * 0.76, max(1, int(s * 0.033)))   # minute
    dot = s * 0.022
    draw.ellipse((centre - dot, centre - dot, centre + dot, centre + dot), fill=(255, 255, 255, 255))

    return image.resize((size, size), Image.LANCZOS)


def write_ico(path: str) -> str:
    master = render_icon(256)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    master.save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES])
    return path


def main(argv: list[str]) -> int:
    target = argv[0] if argv else "FloatingClock.ico"
    print("wrote", write_ico(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
