"""The floating detail over a bar marker: what the meeting is, when, and whose.

Drawn the way the clock card is -- Pillow into a layered window -- so it looks
like a piece of the same thing. It is click-through and never activated: it
sits just above the marker, not under the pointer, so hovering it cannot make
the clock think the mouse has left.

The drawing is a plain function, render_card, so the Qt host can show the
same card in its own window.
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter

from . import render, themes, win32util as w32

PAD_X = 14
PAD_Y = 10
RADIUS = 10
MARGIN = 14          # transparent border for the shadow
GAP = 10             # between the marker and the card
MAX_WIDTH = 340
LINE_GAP = 4


def render_card(theme, scale: float, title: str, lines, colour=None) -> Image.Image:
    """The card: a title, a few muted lines, and a colour bar on the left
    when the meeting belongs to an organisation."""
    title_font = render._fonts.get("Segoe UI Semibold", max(10, int(12.5 * scale)))
    body_font = render._fonts.get("Segoe UI", max(9, int(11 * scale)))
    pad_x, pad_y = int(PAD_X * scale), int(PAD_Y * scale)
    margin = int(MARGIN * scale)
    max_w = int(MAX_WIDTH * scale)

    title = _ellipsize(title_font, title, max_w - pad_x * 2)
    lines = [_ellipsize(body_font, line, max_w - pad_x * 2) for line in lines if line]
    widths = [title_font.getlength(title)] + [body_font.getlength(line) for line in lines]
    stripe = int(3 * scale) if colour else 0
    card_w = int(max(widths)) + pad_x * 2 + stripe
    title_h = title_font.getmetrics()[0]
    body_h = body_font.getmetrics()[0]
    line_gap = int(LINE_GAP * scale)
    card_h = pad_y * 2 + title_h + sum(body_h + line_gap for _ in lines)

    image = Image.new("RGBA", (card_w + margin * 2, card_h + margin * 2), (0, 0, 0, 0))
    radius = int(RADIUS * scale)

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin + int(4 * scale), margin + card_w, margin + card_h + int(4 * scale)),
        radius=radius, fill=(0, 0, 0, 150),
    )
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(int(7 * scale))))

    body = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    ImageDraw.Draw(body).rounded_rectangle(
        (0, 0, card_w - 1, card_h - 1), radius=radius, fill=theme.grad_top + (246,),
        outline=(theme.border if theme.border[3] else None), width=max(1, int(round(scale))),
    )
    if colour:
        stripe_layer = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        ImageDraw.Draw(stripe_layer).rounded_rectangle(
            (0, 0, stripe * 2 + radius, card_h - 1), radius=radius, fill=colour + (255,)
        )
        mask = Image.new("L", (card_w, card_h), 0)
        ImageDraw.Draw(mask).rectangle((0, 0, stripe - 1, card_h), fill=255)
        body.paste(stripe_layer, (0, 0), mask)
    image.alpha_composite(body, (margin, margin))

    draw = ImageDraw.Draw(image)
    x = margin + stripe + pad_x
    y = margin + pad_y + title_h
    draw.text((x, y), title, font=title_font, fill=theme.fg + (255,), anchor="ls")
    for line in lines:
        y += body_h + line_gap
        draw.text((x, y), line, font=body_font, fill=theme.fg_muted + (255,), anchor="ls")
    return image


class HoverCard:
    """The Windows window that shows render_card's picture."""

    def __init__(self, parent: tk.Tk, theme_name: str, scale: float) -> None:
        self.theme = themes.get(theme_name)
        self.scale = scale
        self._key = None
        self._image: Image.Image | None = None
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.withdraw()
        self.win.update_idletasks()
        self.hwnd = self._hwnd()
        w32.update_ex_style(
            self.hwnd,
            add=w32.WS_EX_LAYERED | w32.WS_EX_TOOLWINDOW | w32.WS_EX_TRANSPARENT,
        )
        self.visible = False

    def _hwnd(self) -> int:
        window_id = self.win.winfo_id()
        try:
            parent = w32.user32.GetParent(window_id)
        except OSError:
            parent = 0
        return parent or window_id

    def render(self, title: str, lines, colour=None) -> Image.Image:
        return render_card(self.theme, self.scale, title, lines, colour)

    def show(self, key, title: str, lines, anchor: tuple[int, int], colour=None) -> None:
        """Show the card centred above `anchor` (screen pixels: the top-centre
        of the marker). Re-rendered only when the content changes."""
        if key != self._key or self._image is None:
            self._image = self.render(title, lines, colour)
            self._key = key
            self.win.geometry("%dx%d" % self._image.size)
            self.win.update_idletasks()
        width, height = self._image.size
        margin = int(MARGIN * self.scale)
        x = anchor[0] - width // 2
        y = anchor[1] - height + margin - int(GAP * self.scale)
        left, top, right, bottom = w32.virtual_screen()
        x = max(left - margin, min(right - width + margin, x))
        if y < top - margin:
            # No room above: drop below the marker instead.
            y = anchor[1] + int(GAP * self.scale) * 3 - margin
        w32.move_window(self.hwnd, int(x), int(y))
        if not self.visible:
            self.win.deiconify()
            self.visible = True
        w32.push_layered_bitmap(self.hwnd, render.to_premultiplied_bgra(self._image), 1.0)

    def hide(self) -> None:
        if self.visible:
            self.visible = False
            try:
                self.win.withdraw()
            except tk.TclError:
                pass

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def _ellipsize(font, text: str, max_width: float) -> str:
    text = " ".join((text or "").split())
    if font.getlength(text) <= max_width:
        return text
    while len(text) > 1 and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return text.rstrip() + "…"
