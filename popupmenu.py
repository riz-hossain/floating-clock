"""The right-click menu, drawn like the rest of the clock.

Tk's native menu is a Windows 95 control with a colour scheme: square,
flat, with tick marks from another era. This one is a layered window
rendered with Pillow -- rounded, shadowed, in the card's own palette -- with
a hover highlight, line-icon ticks and dots, right-aligned shortcuts, and
submenus that open on hover beside their row.

A layered tool window never takes focus, so it cannot be told about clicks
elsewhere. Instead the open menu watches the mouse: a press anywhere outside
the menu chain closes it, as does Escape.
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFilter

from . import icons as icon_art, render, themes, win32util as w32

ROW_H = 30
SEP_H = 11
PAD_Y = 6
PAD_X = 6
TEXT_X = 34        # where the label starts, after the tick column
RIGHT_PAD = 16
SHORTCUT_GAP = 28
MIN_WIDTH = 180
MAX_WIDTH = 320
RADIUS = 12
MARGIN = 18        # transparent border for the shadow
SUBMENU_DELAY = 140
SUBMENU_OVERLAP = 6
WATCH_INTERVAL = 40
VK_LBUTTON, VK_RBUTTON, VK_ESCAPE = 0x01, 0x02, 0x1B


@dataclass
class Item:
    """One row. `kind` is command, check, radio, submenu or separator."""

    kind: str
    label: str = ""
    command: object = None
    shortcut: str = ""
    checked: object = None  # callable -> bool, for check/radio rows
    items: list = field(default_factory=list)   # for submenu rows
    enabled: bool = True


def separator() -> Item:
    return Item("separator")


def command(label, command, shortcut="", enabled=True) -> Item:
    return Item("command", label, command, shortcut, enabled=enabled)


def check(label, checked, command, enabled=True) -> Item:
    return Item("check", label, command, checked=checked, enabled=enabled)


def radio(label, checked, command) -> Item:
    return Item("radio", label, command, checked=checked)


def submenu(label, items) -> Item:
    return Item("submenu", label, items=list(items))


# --- layout, kept pure so it can be tested without a window ----------------
@dataclass
class Row:
    item: Item
    top: int
    bottom: int

    def contains(self, y: float) -> bool:
        return self.top <= y < self.bottom


def layout(items, scale: float, label_font, shortcut_font) -> tuple[list[Row], int, int]:
    """(rows, width, height) of the card body, in card pixels."""
    rows: list[Row] = []
    y = int(PAD_Y * scale)
    widest = 0
    for item in items:
        if item.kind == "separator":
            height = int(SEP_H * scale)
        else:
            height = int(ROW_H * scale)
            width = label_font.getlength(item.label)
            if item.shortcut:
                width += SHORTCUT_GAP * scale + shortcut_font.getlength(item.shortcut)
            if item.kind == "submenu":
                width += 24 * scale
            widest = max(widest, width)
        rows.append(Row(item, y, y + height))
        y += height
    width = int(min(MAX_WIDTH * scale, max(MIN_WIDTH * scale, widest + (TEXT_X + RIGHT_PAD) * scale)))
    return rows, width, y + int(PAD_Y * scale)


def row_at(rows, y: float):
    for row in rows:
        if row.contains(y) and row.item.kind != "separator":
            return row
    return None


# --- the window ---------------------------------------------------------------
class PopupMenu:
    def __init__(self, parent: tk.Tk, theme_name: str, scale: float, items,
                 on_close=None, parent_menu=None) -> None:
        self.parent = parent
        self.theme = themes.get(theme_name)
        self.scale = scale
        self.items = list(items)
        self.on_close = on_close
        self.parent_menu = parent_menu
        self.child: PopupMenu | None = None
        self._child_timer = None
        self._watch_timer = None
        self._hover: Row | None = None
        self._closed = False
        self._pressed = False

        self.label_font = render._fonts.get("Segoe UI", max(10, int(12.5 * scale)))
        self.shortcut_font = render._fonts.get("Segoe UI", max(9, int(11 * scale)))
        self.rows, self.width, self.height = layout(
            self.items, scale, self.label_font, self.shortcut_font
        )
        self.margin = int(MARGIN * scale)

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.withdraw()
        self.win.update_idletasks()
        self.hwnd = self._hwnd()
        w32.update_ex_style(self.hwnd, add=w32.WS_EX_LAYERED | w32.WS_EX_TOOLWINDOW)
        self.win.bind("<Motion>", self._on_motion)
        self.win.bind("<Leave>", self._on_leave)
        self.win.bind("<ButtonPress-1>", lambda _e: setattr(self, "_pressed", True))
        self.win.bind("<ButtonRelease-1>", self._on_release)
        self._image = None

    def _hwnd(self) -> int:
        window_id = self.win.winfo_id()
        try:
            parent = w32.user32.GetParent(window_id)
        except OSError:
            parent = 0
        return parent or window_id

    # --- drawing -----------------------------------------------------------
    def render(self, hover: Row | None = None) -> Image.Image:
        theme, scale = self.theme, self.scale
        margin = self.margin
        card_w, card_h = self.width, self.height
        image = Image.new("RGBA", (card_w + margin * 2, card_h + margin * 2), (0, 0, 0, 0))
        radius = int(RADIUS * scale)

        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (margin, margin + int(5 * scale), margin + card_w, margin + card_h + int(5 * scale)),
            radius=radius, fill=(0, 0, 0, 160),
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(int(9 * scale))))

        body = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        ImageDraw.Draw(body).rounded_rectangle(
            (0, 0, card_w - 1, card_h - 1), radius=radius, fill=theme.grad_top + (250,),
            outline=(theme.border if theme.border[3] else None), width=max(1, int(round(scale))),
        )

        draw = ImageDraw.Draw(body)
        pad_x = int(PAD_X * scale)
        text_x = int(TEXT_X * scale)
        tick = int(15 * scale)
        light = theme.fg_muted
        for row in self.rows:
            item = row.item
            if item.kind == "separator":
                y = (row.top + row.bottom) // 2
                line = Image.new("RGBA", (card_w - pad_x * 2 - text_x + pad_x, 1), theme.fg_muted + (70,))
                body.alpha_composite(line, (text_x - int(4 * scale), y))
                continue
            enabled = item.enabled
            if row is hover and enabled:
                hl = Image.new("RGBA", (card_w - pad_x * 2, row.bottom - row.top), (0, 0, 0, 0))
                ImageDraw.Draw(hl).rounded_rectangle(
                    (0, int(1 * scale), hl.width - 1, hl.height - 1 - int(1 * scale)),
                    radius=int(7 * scale), fill=theme.fg + (26,),
                )
                body.alpha_composite(hl, (pad_x, row.top))
                draw = ImageDraw.Draw(body)
            colour = theme.fg if enabled else light
            cy = (row.top + row.bottom) / 2
            checked = bool(item.checked()) if item.checked else False
            if item.kind == "check" and checked:
                icon_art.paste(body, "check", (text_x // 2 - tick // 2, cy - tick / 2,
                                               text_x // 2 + tick // 2, cy + tick / 2), theme.accent)
            elif item.kind == "radio" and checked:
                icon_art.paste(body, "dot", (text_x // 2 - tick // 2, cy - tick / 2,
                                             text_x // 2 + tick // 2, cy + tick / 2), theme.accent)
            draw = ImageDraw.Draw(body)
            draw.text((text_x, cy), item.label, font=self.label_font,
                      fill=colour + (255 if enabled else 150,), anchor="lm")
            if item.shortcut:
                draw.text((card_w - int(RIGHT_PAD * scale), cy), item.shortcut,
                          font=self.shortcut_font, fill=light + (255,), anchor="rm")
            if item.kind == "submenu":
                chev = int(14 * scale)
                icon_art.paste(body, "chevron", (card_w - int(RIGHT_PAD * scale) - chev, cy - chev / 2,
                                                 card_w - int(RIGHT_PAD * scale), cy + chev / 2), light)
        image.alpha_composite(body, (margin, margin))
        return image

    def _push(self) -> None:
        self._image = self.render(self._hover)
        w32.push_layered_bitmap(self.hwnd, render.to_premultiplied_bgra(self._image), 1.0)

    # --- showing -----------------------------------------------------------
    def show(self, x: int, y: int, anchor: str = "cursor") -> None:
        """Open with the card's top-left at (x, y) in screen pixels, pulled
        back onto the screen when it would run off an edge."""
        width, height = self.width + self.margin * 2, self.height + self.margin * 2
        left, top, right, bottom = w32.virtual_screen()
        px, py = x - self.margin, y - self.margin
        if px + width - self.margin > right:
            px = (x - self.width - self.margin) if anchor == "submenu" else right - width + self.margin
        if py + height - self.margin > bottom:
            py = bottom - height + self.margin
        px = max(left - self.margin, px)
        py = max(top - self.margin, py)
        self.win.geometry("%dx%d+%d+%d" % (width, height, int(px), int(py)))
        self.win.update_idletasks()
        w32.move_window(self.hwnd, int(px), int(py))
        self.win.deiconify()
        self._push()
        if self.parent_menu is None:
            self._watch()

    # --- interaction -------------------------------------------------------
    def _row_under(self, x: int, y: int):
        """The row under a window-relative point, or None."""
        cx, cy = x - self.margin, y - self.margin
        if not (0 <= cx < self.width):
            return None
        return row_at(self.rows, cy)

    def _on_motion(self, event) -> None:
        row = self._row_under(event.x, event.y)
        self._set_hover(row)

    def _on_leave(self, _event) -> None:
        # Moving onto a child keeps its parent row lit; anything else clears.
        if self.child is not None and not self.child._closed:
            return
        self._set_hover(None)

    def _set_hover(self, row) -> None:
        if row is self._hover:
            return
        self._hover = row
        self._push()
        self._cancel_child_timer()
        if row is not None and row.item.kind == "submenu" and row.item.enabled:
            self._child_timer = self.win.after(SUBMENU_DELAY, self._open_child, row)
        elif row is not None:
            self._close_child()

    def _cancel_child_timer(self) -> None:
        if self._child_timer is not None:
            try:
                self.win.after_cancel(self._child_timer)
            except tk.TclError:
                pass
            self._child_timer = None

    def _open_child(self, row) -> None:
        self._child_timer = None
        if self.child is not None and self.child.rows and self.child.items is row.item.items:
            return
        self._close_child()
        left, top, _r, _b = w32.window_rect(self.hwnd)
        child = PopupMenu(self.parent, self.theme.name, self.scale, row.item.items,
                          on_close=None, parent_menu=self)
        self.child = child
        child.show(
            left + self.margin + self.width - int(SUBMENU_OVERLAP * self.scale),
            top + self.margin + row.top - int(PAD_Y * self.scale),
            anchor="submenu",
        )

    def _close_child(self) -> None:
        if self.child is not None:
            self.child.close(chain=False)
            self.child = None

    def _on_release(self, event) -> None:
        if not self._pressed:
            return
        self._pressed = False
        row = self._row_under(event.x, event.y)
        if row is None or not row.item.enabled:
            return
        item = row.item
        if item.kind == "submenu":
            self._open_child(row)
            return
        self.root_menu().close()
        if item.command is not None:
            item.command()

    def root_menu(self) -> "PopupMenu":
        menu = self
        while menu.parent_menu is not None:
            menu = menu.parent_menu
        return menu

    def _chain_contains(self, x: int, y: int) -> bool:
        menu = self
        while menu is not None:
            try:
                left, top, right, bottom = w32.window_rect(menu.hwnd)
            except Exception:
                return False
            m = menu.margin
            if left + m <= x < right - m and top + m <= y < bottom - m:
                return True
            menu = menu.child
        return False

    def _watch(self) -> None:
        """Close on a press outside the chain, or on Escape. A layered tool
        window gets no focus and no events from elsewhere, so this polls."""
        if self._closed:
            return
        key = ctypes.windll.user32.GetAsyncKeyState
        pressed = bool(key(VK_LBUTTON) & 0x8000) or bool(key(VK_RBUTTON) & 0x8000)
        if pressed:
            x, y = w32.cursor_pos()
            if not self._chain_contains(x, y):
                self.close()
                return
        if key(VK_ESCAPE) & 0x8000:
            self.close()
            return
        self._watch_timer = self.win.after(WATCH_INTERVAL, self._watch)

    def close(self, chain: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_child_timer()
        if self._watch_timer is not None:
            try:
                self.win.after_cancel(self._watch_timer)
            except tk.TclError:
                pass
        self._close_child()
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        if chain and self.parent_menu is not None:
            self.parent_menu.close()
        if self.on_close is not None:
            self.on_close()
