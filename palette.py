"""Colours for the settings dialog and menus: a dark and a light palette.

The clock card has its own themes (see themes.py); this is the chrome around
it. Each palette is a fixed set of neutrals, and the one colour that moves is
the accent, borrowed from the current clock theme and then nudged until it
actually reads against the page -- the clock accents are picked for a glowing
card, not for 10pt text, so a couple of them start out far too dark for a dark
page or far too bright for a light one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

UI_MODES = ("auto", "dark", "light")

# Minimum contrast ratios, per WCAG: 3:1 for a control's silhouette, 4.5:1
# for the text it carries.
CONTROL_CONTRAST = 3.0
TEXT_CONTRAST = 4.5


# --- colour maths ----------------------------------------------------------
def to_rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb[:3])


def mix(colour: str, other: str, amount: float) -> str:
    """Blend `colour` towards `other`; 0 leaves it alone, 1 is all `other`."""
    amount = max(0.0, min(1.0, amount))
    a, b = to_rgb(colour), to_rgb(other)
    return to_hex(tuple(a[i] * (1 - amount) + b[i] * amount for i in range(3)))


def lighten(colour: str, amount: float) -> str:
    return mix(colour, "#ffffff", amount)


def darken(colour: str, amount: float) -> str:
    return mix(colour, "#000000", amount)


def luminance(colour: str) -> float:
    """Relative luminance, per WCAG."""
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c / 255) for c in to_rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one: str, two: str) -> float:
    a, b = sorted((luminance(one), luminance(two)))
    return (b + 0.05) / (a + 0.05)


def ensure_contrast(colour: str, against: str, minimum: float, towards: str) -> str:
    """Walk `colour` towards `towards` until it clears `minimum` against `against`.

    Small steps keep the hue: the accent should still look like the clock's
    accent, just readable.
    """
    for _ in range(60):
        if contrast(colour, against) >= minimum:
            return colour
        colour = mix(colour, towards, 0.05)
    return towards


def text_on(fill: str) -> str:
    """White or near-black, whichever reads better on `fill`."""
    return "#ffffff" if contrast(fill, "#ffffff") >= contrast(fill, "#15161c") else "#15161c"


# --- the palettes ----------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    mode: str
    window: str          # sidebar and title bar
    page: str            # content background
    card: str            # raised panel
    card_border: str
    field: str           # entry background
    field_border: str
    control: str         # switch track (off), default button
    control_hover: str
    control_border: str
    hover: str           # wash behind a hovered row
    trough: str          # slider groove
    knob: str            # switch knob when off
    divider: str
    fg: str
    fg_soft: str         # secondary text
    muted: str           # captions, hints
    disabled: str
    danger: str
    success: str
    accent: str = "#7f28ff"        # fills: primary button, switch on, slider
    accent_hover: str = "#9a55ff"
    accent_text: str = "#b388ff"   # the accent used *as text* on page/card
    accent_soft: str = "#2a2340"   # tinted wash: selected nav, selected row
    on_accent: str = "#ffffff"     # text sitting on an accent fill

    @property
    def is_dark(self) -> bool:
        return self.mode == "dark"


DARK = Palette(
    mode="dark",
    window="#111118",
    page="#181820",
    card="#20202b",
    card_border="#2b2b39",
    field="#15151d",
    field_border="#33334a",
    control="#2e2e3e",
    control_hover="#3a3a4d",
    control_border="#3b3b4f",
    hover="#262633",
    trough="#3a3a4d",
    knob="#c4c4d3",
    divider="#2a2a37",
    fg="#f1f1f6",
    fg_soft="#c3c3d1",
    muted="#8e8ea4",
    disabled="#5a5a70",
    danger="#ff6b6b",
    success="#4fd47f",
)

LIGHT = Palette(
    mode="light",
    window="#eceef4",
    page="#f6f7fb",
    card="#ffffff",
    card_border="#e1e3ec",
    field="#f6f7fb",
    field_border="#d6d9e4",
    control="#e6e8f0",
    control_hover="#d9dce7",
    control_border="#c9cdda",
    hover="#f1f2f7",
    trough="#d4d7e2",
    knob="#ffffff",
    divider="#eceef3",
    fg="#15161c",
    fg_soft="#3f4150",
    muted="#5f6377",
    disabled="#b0b3c2",
    danger="#d6453f",
    success="#1f9d55",
)


def resolve(mode: str, accent_rgb) -> Palette:
    """The palette for `mode` ("dark"/"light"), coloured with the clock's accent."""
    base = LIGHT if mode == "light" else DARK
    raw = to_hex(accent_rgb)
    towards = "#000000" if base.mode == "light" else "#ffffff"
    # Controls sit on cards, so that is the surface the accent must clear.
    accent = ensure_contrast(raw, base.card, CONTROL_CONTRAST, towards)
    accent_text = ensure_contrast(accent, base.card, TEXT_CONTRAST, towards)
    if base.mode == "light":
        hover = darken(accent, 0.12)
        soft = mix(base.card, accent, 0.10)
    else:
        hover = lighten(accent, 0.12)
        soft = mix(base.card, accent, 0.20)
    return replace(
        base, accent=accent, accent_hover=hover, accent_text=accent_text,
        accent_soft=soft, on_accent=text_on(accent),
    )


# --- which mode ------------------------------------------------------------
def system_prefers_light() -> bool:
    """Windows' "Choose your default app mode"; dark when it cannot be read."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except (ImportError, OSError):
        return False


def effective_mode(ui_mode: str) -> str:
    if ui_mode in ("dark", "light"):
        return ui_mode
    return "light" if system_prefers_light() else "dark"
