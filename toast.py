"""The alert popup: meeting reminders, alarms and finished timers.

Drawn the same way as the clock card -- Pillow into a layered window -- so the
two look like one product. Because the window's pixels come from a bitmap
rather than Tk widgets, the buttons are rectangles we hit-test ourselves.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import render, themes, win32util as w32

WIDTH = 380
PAD = 20
MARGIN = 26
RADIUS = 18
BUTTON_H = 32
BUTTON_GAP = 8
GAP = 60           # distance from the screen corner
STACK_GAP = 12
SLIDE_STEPS = 12
SLIDE_INTERVAL = 12

KIND_LABEL = {"meeting": "MEETING", "alarm": "ALARM", "timer": "TIMER",
              "prayer": "PRAYER", "adhan": "ADHAN"}


@dataclass
class Button:
    key: str
    label: str
    rect: tuple[int, int, int, int]  # in window coordinates
    primary: bool = False


def play_sound(kind: str) -> None:
    """System sounds only -- no bundled audio, nothing to ship or license."""
    try:
        import winsound

        if kind in ("meeting", "prayer"):
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        else:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def _wrap(draw, text: str, font, max_width: float, max_lines: int = 2) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if font.getlength(trial) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return [""]
    # Ellipsise the last line if anything was dropped.
    consumed = sum(len(line.split()) for line in lines)
    if consumed < len(words):
        last = lines[-1]
        while last and font.getlength(last + "...") > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines


class Toast:
    """One popup. The owner keeps them in a list and re-stacks on close."""

    def __init__(self, parent: tk.Tk, fired, theme_name: str, scale: float, on_close, on_snooze, on_stop):
        self.fired = fired
        self.theme = themes.get(theme_name)
        self.scale = scale
        self.on_close = on_close
        self.on_snooze = on_snooze
        self.on_stop = on_stop
        self.buttons: list[Button] = []
        self._closed = False
        self._offset = 0

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.withdraw()
        self.win.update_idletasks()

        self.hwnd = self._hwnd()
        w32.update_ex_style(self.hwnd, add=w32.WS_EX_LAYERED | w32.WS_EX_TOOLWINDOW)

        self.image = self._render()
        self.win.geometry("%dx%d" % self.image.size)
        self.win.update_idletasks()
        self.win.deiconify()

        self.win.bind("<Button-1>", self._on_click)
        self.win.bind("<Button-3>", lambda _e: self.close())

    def _hwnd(self) -> int:
        window_id = self.win.winfo_id()
        try:
            parent = w32.user32.GetParent(window_id)
        except OSError:
            parent = 0
        return parent or window_id

    # --- drawing -----------------------------------------------------------
    def _render(self) -> Image.Image:
        scale = self.scale
        theme = self.theme
        width = int(WIDTH * scale)
        margin = int(MARGIN * scale)
        pad = int(PAD * scale)
        radius = int(RADIUS * scale)
        inner = width - pad * 2

        kind_font = render._fonts.get("Segoe UI Semibold", max(9, int(10 * scale)))
        title_font = render._fonts.get("Segoe UI Semibold", max(13, int(16 * scale)))
        detail_font = render._fonts.get("Segoe UI", max(11, int(12 * scale)))
        button_font = render._fonts.get("Segoe UI Semibold", max(11, int(12 * scale)))

        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        title_lines = _wrap(probe, self.fired.title, title_font, inner, 2)
        detail_lines = _wrap(probe, self.fired.detail, detail_font, inner, 2)

        kind_h = kind_font.getmetrics()[0] + kind_font.getmetrics()[1]
        title_h = sum(title_font.getmetrics()) * len(title_lines)
        detail_h = sum(detail_font.getmetrics()) * len(detail_lines) if self.fired.detail else 0

        self.buttons = self._button_specs()
        buttons_h = int(BUTTON_H * scale) + int(14 * scale) if self.buttons else 0

        card_h = int(pad * 2 + kind_h + 8 * scale + title_h + (6 * scale + detail_h if detail_h else 0) + buttons_h)
        image = Image.new("RGBA", (width + margin * 2, card_h + margin * 2), (0, 0, 0, 0))

        # Shadow, card, border -- same recipe as the clock card.
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (margin, margin + int(8 * scale), margin + width, margin + card_h + int(8 * scale)),
            radius=radius, fill=(0, 0, 0, min(220, theme.shadow_alpha + 40)),
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(int(12 * scale))))

        gradient = render._vertical_gradient(width, card_h, theme.grad_top, theme.grad_bottom)
        mask = Image.new("L", (width, card_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width - 1, card_h - 1), radius=radius, fill=248
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
        # Accent rail down the left edge, clipped by the card mask so it picks
        # up the rounded corners instead of squaring them off.
        rail = Image.new("RGBA", (width, card_h), (0, 0, 0, 0))
        ImageDraw.Draw(rail).rectangle(
            (0, 0, max(2, int(4 * scale)), card_h), fill=theme.accent + (255,)
        )
        rail.putalpha(
            ImageChops.multiply(rail.getchannel("A"), mask.point(lambda v: 255 if v else 0))
        )
        image.alpha_composite(rail, (margin, margin))

        x = margin + pad
        y = margin + pad

        render.draw_text(
            draw, (x, y + kind_font.getmetrics()[0]),
            KIND_LABEL.get(self.fired.kind, "ALERT"), kind_font,
            theme.accent + (255,), tracking=max(1.0, 1.4 * scale),
        )
        y += kind_h + int(8 * scale)

        for line in title_lines:
            ascent, descent = title_font.getmetrics()
            draw.text((x, y + ascent), line, font=title_font, fill=theme.fg + (255,), anchor="ls")
            y += ascent + descent

        if detail_lines and self.fired.detail:
            y += int(6 * scale)
            for line in detail_lines:
                ascent, descent = detail_font.getmetrics()
                draw.text(
                    (x, y + ascent), line, font=detail_font,
                    fill=theme.fg_muted + (255,), anchor="ls",
                )
                y += ascent + descent

        if self.buttons:
            y += int(14 * scale)
            height = int(BUTTON_H * scale)
            for button in self.buttons:
                bx = x + button.rect[0]
                rect = (bx, y, bx + button.rect[2], y + height)
                button.rect = rect
                if button.primary:
                    draw.rounded_rectangle(rect, radius=height // 2, fill=theme.accent + (255,))
                else:
                    # Composited, not drawn: a drawn alpha-28 fill would cut a
                    # near-transparent button shape out of the card.
                    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
                    ImageDraw.Draw(layer).rounded_rectangle(
                        rect, radius=height // 2, fill=(255, 255, 255, 28))
                    image.alpha_composite(layer)
                text_w = button_font.getlength(button.label)
                ascent, descent = button_font.getmetrics()
                draw.text(
                    ((rect[0] + rect[2]) / 2 - text_w / 2,
                     y + (height + ascent - descent) / 2),
                    button.label, font=button_font,
                    fill=(255, 255, 255, 255) if button.primary else theme.fg + (235,),
                    anchor="ls",
                )
        return render.to_premultiplied_bgra(image)

    def _button_specs(self) -> list[Button]:
        scale = self.scale
        font = render._fonts.get("Segoe UI Semibold", max(11, int(12 * scale)))
        labels = []
        if self.fired.kind == "meeting":
            if self.fired.join_url:
                labels.append(("join", "Join", True))
            labels.append(("snooze", "Snooze 2m", False))
            labels.append(("dismiss", "Dismiss", False))
        elif self.fired.kind == "adhan":
            labels.append(("stop", "Stop", True))
        else:
            labels.append(("dismiss", "Dismiss", True))
            labels.append(("snooze", "Snooze 5m", False))

        buttons: list[Button] = []
        offset = 0
        for key, label, primary in labels:
            width = int(font.getlength(label) + 28 * scale)
            buttons.append(Button(key, label, (offset, 0, width, 0), primary))
            offset += width + int(BUTTON_GAP * scale)
        return buttons

    # --- behaviour ---------------------------------------------------------
    def place(self, index: int) -> None:
        """Stack upward from the bottom-right of the work area."""
        left, top, right, bottom = w32.work_area(self.hwnd)
        width, height = self.image.size
        margin = int(MARGIN * self.scale)
        x = right - width + margin - int(GAP * self.scale) + margin
        y = bottom - height + margin - int(GAP * self.scale) - index * (
            height - margin * 2 + int(STACK_GAP * self.scale)
        )
        self._home = (x, y)
        w32.move_window(self.hwnd, x, y)

    def show(self, index: int = 0, sound: bool = True) -> None:
        self.place(index)
        if sound:
            play_sound(self.fired.kind)
        self._slide(1)

    def _slide(self, step: int) -> None:
        if self._closed:
            return
        progress = min(1.0, step / SLIDE_STEPS)
        eased = 1 - (1 - progress) ** 3
        x, y = self._home
        w32.move_window(self.hwnd, x, int(y + (1 - eased) * 36 * self.scale))
        w32.push_layered_bitmap(self.hwnd, self.image, eased)
        if step < SLIDE_STEPS:
            self.win.after(SLIDE_INTERVAL, self._slide, step + 1)

    def _on_click(self, event) -> None:
        left, top, _r, _b = w32.window_rect(self.hwnd)
        cx, cy = w32.cursor_pos()
        x, y = cx - left, cy - top
        for button in self.buttons:
            bx0, by0, bx1, by1 = button.rect
            if bx0 <= x <= bx1 and by0 <= y <= by1:
                self._activate(button.key)
                return

    def _activate(self, key: str) -> None:
        if key == "join" and self.fired.join_url:
            try:
                webbrowser.open(self.fired.join_url)
            except Exception:
                pass
        elif key == "snooze":
            minutes = 2 if self.fired.kind == "meeting" else 5
            self.on_snooze(self.fired, minutes)
        elif key == "stop":
            self.on_stop(self.fired)
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_close(self)
