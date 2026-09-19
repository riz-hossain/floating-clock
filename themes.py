"""Colour themes for the clock card.

Every colour is RGB; alphas are separate 0-255 ints so a theme can be tuned
for translucency without touching the drawing code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    grad_top: tuple[int, int, int]
    grad_bottom: tuple[int, int, int]
    card_alpha: int          # base translucency of the card itself
    fg: tuple[int, int, int]        # time text
    fg_muted: tuple[int, int, int]  # date text
    accent: tuple[int, int, int]    # seconds bar, glow
    border: tuple[int, int, int, int]   # alpha 0: no hairline, the shadow separates
    sheen: int               # strength of the glassy highlight along the top
    glow: float              # 0 = flat text, 1 = full neon bloom
    shadow_alpha: int
    # A running timer's badge: deliberately not the accent, so it reads as
    # "something is counting down" before the eye has parsed a digit.
    timer: tuple[int, int, int] = (255, 181, 61)


THEMES: dict[str, Theme] = {
    "Midnight": Theme(
        name="Midnight",
        grad_top=(32, 33, 46),
        grad_bottom=(16, 16, 24),
        card_alpha=236,
        fg=(245, 246, 250),
        fg_muted=(150, 153, 170),
        accent=(127, 40, 255),
        border=(255, 255, 255, 0),
        sheen=20,
        glow=0.0,
        shadow_alpha=150,
    ),
    "Aurora": Theme(
        name="Aurora",
        grad_top=(58, 33, 110),
        grad_bottom=(18, 11, 40),
        card_alpha=238,
        fg=(255, 255, 255),
        fg_muted=(188, 168, 240),
        accent=(179, 136, 255),
        border=(190, 160, 255, 0),
        sheen=26,
        glow=0.55,
        shadow_alpha=165,
    ),
    "Nocturne": Theme(
        name="Nocturne",
        grad_top=(14, 14, 17),
        grad_bottom=(0, 0, 0),
        card_alpha=225,
        fg=(232, 232, 236),
        fg_muted=(120, 120, 130),
        accent=(90, 92, 105),
        border=(255, 255, 255, 0),
        sheen=10,
        glow=0.0,
        shadow_alpha=170,
    ),
    "Frost": Theme(
        name="Frost",
        grad_top=(255, 255, 255),
        grad_bottom=(232, 237, 246),
        card_alpha=234,
        fg=(16, 19, 26),
        fg_muted=(108, 118, 138),
        accent=(37, 99, 235),
        border=(20, 30, 60, 0),
        sheen=0,
        glow=0.0,
        shadow_alpha=90,
        timer=(196, 108, 0),
    ),
    "Ember": Theme(
        name="Ember",
        grad_top=(48, 22, 17),
        grad_bottom=(20, 9, 10),
        card_alpha=236,
        fg=(255, 233, 221),
        fg_muted=(190, 137, 116),
        accent=(255, 107, 53),
        border=(255, 140, 90, 0),
        sheen=18,
        glow=0.4,
        shadow_alpha=160,
        timer=(96, 224, 255),
    ),
    "Neon": Theme(
        name="Neon",
        grad_top=(6, 10, 20),
        grad_bottom=(0, 3, 9),
        card_alpha=228,
        fg=(180, 250, 255),
        fg_muted=(60, 150, 165),
        accent=(0, 240, 255),
        border=(0, 240, 255, 0),
        sheen=8,
        glow=1.0,
        shadow_alpha=175,
    ),
}

DEFAULT_THEME = "Midnight"


def get(name: str) -> Theme:
    return THEMES.get(name, THEMES[DEFAULT_THEME])
