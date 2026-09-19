"""A small widget kit for the settings dialog.

Plain Tk cannot antialias, so every rounded shape here -- switch tracks,
slider thumbs, pill buttons, cards, theme swatches, icons -- is drawn by Pillow
at 4x and downsampled, then shown through a PhotoImage. Text stays Tk's, so it
keeps ClearType. Each widget takes a `Ui` context that carries the palette,
the DPI scale, the fonts and an image cache; nothing here reads settings.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageTk

from . import palette as pal

SS = 4  # supersampling factor for the Pillow-drawn chrome


# --- context ---------------------------------------------------------------
class Ui:
    """Palette + scale + fonts + image cache, shared by every widget in a dialog."""

    def __init__(self, root: tk.Misc, palette: pal.Palette, scale: float = 1.0) -> None:
        self.root = root
        self.p = palette
        self.scale = max(0.5, float(scale))
        self._fonts: dict = {}
        self._images: dict = {}
        families = set(tkfont.families(root))
        self.family = "Segoe UI" if "Segoe UI" in families else "TkDefaultFont"
        self.family_semibold = (
            "Segoe UI Semibold" if "Segoe UI Semibold" in families else self.family
        )
        self.mono = "Cascadia Code" if "Cascadia Code" in families else "Consolas"

    # geometry
    def px(self, n: float) -> int:
        return int(round(n * self.scale)) if n else 0

    # fonts
    def font(self, size: int = 10, weight: str = "normal", mono: bool = False) -> tkfont.Font:
        key = (size, weight, mono)
        if key not in self._fonts:
            if mono:
                family, tk_weight = self.mono, "normal"
            elif weight == "semibold":
                family, tk_weight = self.family_semibold, "normal"
                if self.family_semibold == self.family:
                    tk_weight = "bold"
            elif weight == "bold":
                family, tk_weight = self.family, "bold"
            else:
                family, tk_weight = self.family, "normal"
            self._fonts[key] = tkfont.Font(
                root=self.root, family=family, size=size, weight=tk_weight
            )
        return self._fonts[key]

    def text_width(self, text: str, font: tkfont.Font) -> int:
        return font.measure(text)

    # images
    def _cached(self, key: tuple, build) -> ImageTk.PhotoImage:
        image = self._images.get(key)
        if image is None:
            rendered = build()
            image = ImageTk.PhotoImage(rendered, master=self.root)
            self._images[key] = image
        return image

    def rounded(self, w: int, h: int, r: int, fill: str, bg: str,
                outline: str | None = None, width: int = 1) -> ImageTk.PhotoImage:
        """A rounded rectangle, w*h *device* pixels, flattened onto `bg`."""
        w, h = max(1, int(w)), max(1, int(h))
        r = max(0, min(int(r), w // 2, h // 2))
        key = ("rr", w, h, r, fill, bg, outline, width)

        def build() -> Image.Image:
            big = Image.new("RGB", (w * SS, h * SS), bg)
            draw = ImageDraw.Draw(big)
            if outline and width > 0:
                draw.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), r * SS, fill=outline)
                inset = width * SS
                draw.rounded_rectangle(
                    (inset, inset, w * SS - 1 - inset, h * SS - 1 - inset),
                    max(0, r * SS - inset), fill=fill,
                )
            else:
                draw.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), r * SS, fill=fill)
            return big.resize((w, h), Image.LANCZOS)

        return self._cached(key, build)

    def pill(self, w: int, h: int, fill: str, bg: str, outline: str | None = None,
             width: int = 1) -> ImageTk.PhotoImage:
        return self.rounded(w, h, h // 2, fill, bg, outline, width)

    def dot(self, d: int, fill: str, bg: str, outline: str | None = None,
            width: int = 1, core: str | None = None, core_d: int = 0) -> ImageTk.PhotoImage:
        """A circle, optionally with a smaller `core` circle in the middle."""
        d = max(2, int(d))
        key = ("dot", d, fill, bg, outline, width, core, core_d)

        def build() -> Image.Image:
            big = Image.new("RGB", (d * SS, d * SS), bg)
            draw = ImageDraw.Draw(big)
            draw.ellipse((0, 0, d * SS - 1, d * SS - 1), fill=outline or fill)
            if outline:
                inset = width * SS
                draw.ellipse((inset, inset, d * SS - 1 - inset, d * SS - 1 - inset), fill=fill)
            if core and core_d > 0:
                offset = (d - core_d) * SS / 2
                draw.ellipse(
                    (offset, offset, d * SS - 1 - offset, d * SS - 1 - offset), fill=core
                )
            return big.resize((d, d), Image.LANCZOS)

        return self._cached(key, build)

    def switch(self, w: int, h: int, t: float, bg: str, enabled: bool = True
               ) -> ImageTk.PhotoImage:
        """A toggle at knob position t (0 = off, 1 = on)."""
        p = self.p
        t = max(0.0, min(1.0, t))
        step = round(t * 12) / 12  # quantise so frames cache
        key = ("switch", w, h, step, bg, enabled)

        def build() -> Image.Image:
            big = Image.new("RGB", (w * SS, h * SS), bg)
            draw = ImageDraw.Draw(big)
            if not enabled:
                track, border, knob = p.control, p.control_border, p.disabled
            else:
                track = pal.mix(p.control, p.accent, step)
                border = pal.mix(p.control_border, p.accent, step)
                knob = pal.mix(p.knob, p.on_accent, step)
            draw.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), h * SS // 2, fill=border)
            inset = SS
            draw.rounded_rectangle(
                (inset, inset, w * SS - 1 - inset, h * SS - 1 - inset),
                (h * SS - 2 * inset) // 2, fill=track,
            )
            pad = 4 + 1 * (1 - step)   # the knob swells slightly as it switches on
            kd = (h - 2 * pad) * SS
            travel = (w - h) * SS
            x = pad * SS + travel * step
            y = pad * SS
            draw.ellipse((x, y, x + kd, y + kd), fill=knob)
            return big.resize((w, h), Image.LANCZOS)

        return self._cached(key, build)

    def swatch(self, w: int, h: int, top: str, bottom: str, fg: str, muted: str,
               accent: str, bg: str, ring: str | None) -> ImageTk.PhotoImage:
        """A miniature clock card: gradient, two text bars, a seconds bar."""
        key = ("swatch", w, h, top, bottom, fg, muted, accent, bg, ring)

        def build() -> Image.Image:
            W, H = w * SS, h * SS
            big = Image.new("RGB", (W, H), bg)
            draw = ImageDraw.Draw(big)
            gap = 3 * SS
            if ring:
                draw.rounded_rectangle((0, 0, W - 1, H - 1), 9 * SS, fill=ring)
                draw.rounded_rectangle(
                    (2 * SS, 2 * SS, W - 1 - 2 * SS, H - 1 - 2 * SS), 8 * SS, fill=bg
                )
            x0, y0, x1, y1 = gap, gap, W - 1 - gap, H - 1 - gap
            card_h = y1 - y0
            gradient = Image.new("RGB", (1, card_h))
            t_rgb, b_rgb = pal.to_rgb(top), pal.to_rgb(bottom)
            for y in range(card_h):
                f = y / max(1, card_h - 1)
                gradient.putpixel((0, y), tuple(
                    int(t_rgb[i] * (1 - f) + b_rgb[i] * f) for i in range(3)
                ))
            gradient = gradient.resize((x1 - x0, card_h))
            mask = Image.new("L", (x1 - x0, card_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, x1 - x0 - 1, card_h - 1), 7 * SS, fill=255
            )
            big.paste(gradient, (x0, y0), mask)
            # stand-ins for the time and the date
            inner = 9 * SS
            bar_h = 4 * SS
            draw.rounded_rectangle(
                (x0 + inner, y0 + 11 * SS, x0 + inner + 30 * SS, y0 + 11 * SS + bar_h + SS),
                bar_h, fill=fg,
            )
            draw.rounded_rectangle(
                (x0 + inner, y0 + 20 * SS, x0 + inner + 18 * SS, y0 + 20 * SS + 3 * SS),
                2 * SS, fill=muted,
            )
            draw.rounded_rectangle(
                (x0 + inner, y1 - 8 * SS, x0 + inner + 22 * SS, y1 - 6 * SS), SS, fill=accent
            )
            return big.resize((w, h), Image.LANCZOS)

        return self._cached(key, build)

    def icon(self, name: str, size: int, colour: str, bg: str) -> ImageTk.PhotoImage:
        key = ("icon", name, size, colour, bg)

        def build() -> Image.Image:
            S = size * SS
            big = Image.new("RGB", (S, S), bg)
            draw = ImageDraw.Draw(big)
            _draw_icon(draw, name, S, colour, bg)
            return big.resize((size, size), Image.LANCZOS)

        return self._cached(key, build)


def _draw_icon(draw: ImageDraw.ImageDraw, name: str, S: int, colour: str,
               bg_fill: str = "#000000") -> None:
    """Line icons on an S*S canvas (S already supersampled)."""
    stroke = max(1, round(S / 11))
    pad = S * 0.08
    box = (pad, pad, S - pad, S - pad)
    cx = cy = S / 2
    if name == "clock":
        draw.ellipse(box, outline=colour, width=stroke)
        draw.line((cx, cy, cx, S * 0.26), fill=colour, width=stroke)
        draw.line((cx, cy, S * 0.70, cy + S * 0.10), fill=colour, width=stroke)
    elif name == "sliders":
        for frac, knob in ((0.25, 0.62), (0.5, 0.36), (0.75, 0.70)):
            y = S * frac
            draw.line((pad, y, S - pad, y), fill=colour, width=stroke)
            r = S * 0.09
            draw.ellipse((S * knob - r, y - r, S * knob + r, y + r), fill=colour)
    elif name == "calendar":
        top = S * 0.18
        draw.rounded_rectangle((pad, top, S - pad, S - pad), S * 0.12, outline=colour, width=stroke)
        draw.line((pad, S * 0.40, S - pad, S * 0.40), fill=colour, width=stroke)
        for x in (0.32, 0.50, 0.68):
            r = S * 0.05
            draw.ellipse((S * x - r, S * 0.62 - r, S * x + r, S * 0.62 + r), fill=colour)
        for x in (0.32, 0.68):
            draw.line((S * x, S * 0.08, S * x, S * 0.26), fill=colour, width=stroke)
    elif name == "bell":
        draw.pieslice((S * 0.22, S * 0.14, S * 0.78, S * 0.78), 180, 360, outline=colour, width=stroke)
        draw.line((S * 0.22, S * 0.46, S * 0.22, S * 0.66), fill=colour, width=stroke)
        draw.line((S * 0.78, S * 0.46, S * 0.78, S * 0.66), fill=colour, width=stroke)
        draw.line((S * 0.14, S * 0.68, S * 0.86, S * 0.68), fill=colour, width=stroke)
        draw.arc((S * 0.38, S * 0.66, S * 0.62, S * 0.90), 0, 180, fill=colour, width=stroke)
    elif name == "timer":
        draw.ellipse((pad, S * 0.22, S - pad, S - pad), outline=colour, width=stroke)
        draw.line((S * 0.40, S * 0.08, S * 0.60, S * 0.08), fill=colour, width=stroke)
        draw.line((cx, S * 0.08, cx, S * 0.22), fill=colour, width=stroke)
        draw.line((cx, S * 0.61, cx, S * 0.38), fill=colour, width=stroke)
    elif name == "orgs":
        r = S * 0.12
        draw.rounded_rectangle((S * 0.30, S * 0.30, S * 0.92, S * 0.92), r, outline=colour, width=stroke)
        draw.rounded_rectangle((S * 0.08, S * 0.08, S * 0.70, S * 0.70), r, fill=bg_fill, outline=colour, width=stroke)
    elif name == "moon":
        # A crescent, cut from one disc by another: the prayer page's mark.
        draw.ellipse(box, fill=colour)
        cut = S * 0.30
        draw.ellipse((pad + cut, pad - cut * 0.35, S - pad + cut * 1.1, S - pad + cut * 0.35),
                     fill=bg_fill)
    elif name == "chevron":
        draw.line((S * 0.25, S * 0.40, cx, S * 0.65), fill=colour, width=stroke)
        draw.line((cx, S * 0.65, S * 0.75, S * 0.40), fill=colour, width=stroke)
    elif name == "check":
        draw.line((S * 0.22, S * 0.52, S * 0.42, S * 0.72), fill=colour, width=stroke)
        draw.line((S * 0.42, S * 0.72, S * 0.80, S * 0.30), fill=colour, width=stroke)
    elif name == "logo":
        draw.ellipse(box, fill=colour)
        inner = S * 0.24
        draw.ellipse((inner, inner, S - inner, S - inner), fill="#ffffff")
        draw.line((cx, cy, cx, S * 0.34), fill=colour, width=stroke)
        draw.line((cx, cy, S * 0.64, cy + S * 0.06), fill=colour, width=stroke)


# --- static pieces ---------------------------------------------------------
def label(parent, ui: Ui, text: str, size: int = 10, weight: str = "normal",
          colour: str | None = None, bg: str | None = None, **kw) -> tk.Label:
    return tk.Label(
        parent, text=text, font=ui.font(size, weight), fg=colour or ui.p.fg,
        bg=bg or parent.cget("bg"), bd=0, **kw,
    )


def divider(parent, ui: Ui, colour: str | None = None) -> tk.Frame:
    return tk.Frame(parent, bg=colour or ui.p.divider, height=max(1, ui.px(1)), bd=0)


class Card(tk.Frame):
    """A rounded, bordered panel. Put content in `.body`; use `.row()` for
    settings rows, which get hairline dividers between them."""

    def __init__(self, parent, ui: Ui, title: str = "", subtitle: str = "",
                 page: str | None = None, pad: tuple[int, int] = (18, 14)) -> None:
        self.ui = ui
        self._page = page or parent.cget("bg")
        super().__init__(parent, bg=self._page, bd=0, highlightthickness=0)
        self._bg = tk.Label(self, bg=self._page, bd=0, padx=0, pady=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=ui.p.card, bd=0)
        self.body.pack(fill="both", expand=True, padx=ui.px(pad[0]), pady=ui.px(pad[1]))
        self._rows = 0
        if title:
            head = tk.Frame(self.body, bg=ui.p.card)
            head.pack(fill="x", pady=(0, ui.px(4)))
            label(head, ui, title, 11, "semibold").pack(side="left")
            self.aside = tk.Frame(head, bg=ui.p.card)
            self.aside.pack(side="right")
            if subtitle:
                label(
                    self.body, ui, subtitle, 9, colour=ui.p.muted, justify="left",
                    wraplength=ui.px(430), anchor="w",
                ).pack(fill="x", pady=(0, ui.px(4)))
        self._size = (0, 0)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if (w, h) == self._size or w < 4 or h < 4:
            return
        self._size = (w, h)
        image = self.ui.rounded(
            w, h, self.ui.px(12), self.ui.p.card, self._page, self.ui.p.card_border, 1
        )
        self._bg.configure(image=image)

    def row(self, pady: int = 9) -> tk.Frame:
        if self._rows:
            divider(self.body, self.ui).pack(fill="x")
        self._rows += 1
        frame = tk.Frame(self.body, bg=self.ui.p.card, bd=0)
        frame.pack(fill="x", pady=(self.ui.px(pady), self.ui.px(pady)))
        return frame


# --- controls --------------------------------------------------------------
class Switch(tk.Label):
    """An animated toggle bound to a BooleanVar. Follows the variable, so a
    menu checkbutton sharing it stays in step."""

    W, H = 42, 24

    def __init__(self, parent, ui: Ui, variable: tk.BooleanVar, command=None,
                 bg: str | None = None) -> None:
        self.ui = ui
        self.variable = variable
        self.command = command
        self._bg = bg or parent.cget("bg")
        self.enabled = True
        super().__init__(parent, bg=self._bg, bd=0, padx=0, pady=0, cursor="hand2")
        self._t = 1.0 if variable.get() else 0.0
        self._target = self._t
        self._job = None
        self._trace = variable.trace_add("write", lambda *_: self._follow())
        self.bind("<Button-1>", self.toggle)
        self.bind("<Destroy>", self._on_destroy)
        self._render()

    def _on_destroy(self, event) -> None:
        if str(event.widget) != str(self):
            return
        try:
            self.variable.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass
        if self._job:
            self.after_cancel(self._job)
            self._job = None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._render()

    def toggle(self, _event=None) -> None:
        if not self.enabled:
            return
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def _follow(self) -> None:
        if not self.winfo_exists():
            return
        self._target = 1.0 if self.variable.get() else 0.0
        if self._job is None:
            self._step()

    def _step(self) -> None:
        delta = self._target - self._t
        if abs(delta) < 0.02:
            self._t = self._target
            self._job = None
        else:
            self._t += delta * 0.38
            self._job = self.after(14, self._step)
        self._render()

    def _render(self) -> None:
        image = self.ui.switch(
            self.ui.px(self.W), self.ui.px(self.H), self._t, self._bg, self.enabled
        )
        self.configure(image=image)


class Slider(tk.Canvas):
    """A horizontal slider. `command` gets the new value as a number."""

    H = 28
    THUMB = 20
    TRACK = 4

    def __init__(self, parent, ui: Ui, from_, to, variable, command=None,
                 integer: bool = True, bg: str | None = None, step=None) -> None:
        self.ui = ui
        self.lo, self.hi = float(from_), float(to)
        self.variable = variable
        self.command = command
        self.integer = integer
        self.step = step if step is not None else (1 if integer else 0.1)
        self._bg = bg or parent.cget("bg")
        self._hot = False
        self._dragging = False
        super().__init__(
            parent, height=ui.px(self.H), bg=self._bg, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        p = ui.p
        self._trough = self.create_rectangle(0, 0, 0, 0, fill=p.trough, outline="")
        self._fill = self.create_rectangle(0, 0, 0, 0, fill=p.accent, outline="")
        self._thumb = self.create_image(0, 0, anchor="center")
        self.bind("<Configure>", lambda _e: self._layout())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda _e: self._set_hot(True))
        self.bind("<Leave>", lambda _e: self._set_hot(False))
        self.bind("<MouseWheel>", self._wheel)
        self._trace = variable.trace_add("write", lambda *_: self._layout())
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, event) -> None:
        if str(event.widget) == str(self):
            try:
                self.variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass

    # geometry
    def _inset(self) -> int:
        return self.ui.px(self.THUMB) // 2 + 1

    def _fraction(self) -> float:
        try:
            value = float(self.variable.get())
        except (tk.TclError, ValueError):
            value = self.lo
        span = self.hi - self.lo or 1.0
        return max(0.0, min(1.0, (value - self.lo) / span))

    def _layout(self) -> None:
        if not self.winfo_exists():
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8:
            return
        inset = self._inset()
        track = self.ui.px(self.TRACK)
        cy = h // 2
        x = inset + (w - 2 * inset) * self._fraction()
        self.coords(self._trough, inset, cy - track // 2, w - inset, cy + track - track // 2)
        self.coords(self._fill, inset, cy - track // 2, x, cy + track - track // 2)
        p = self.ui.p
        d = self.ui.px(self.THUMB)
        core = self.ui.px(11 if (self._hot or self._dragging) else 8)
        image = self.ui.dot(d, p.card, self._bg, p.control_border, 1, p.accent, core)
        self.itemconfigure(self._thumb, image=image)
        self.coords(self._thumb, x, cy)

    def _set_hot(self, hot: bool) -> None:
        self._hot = hot
        self._layout()

    # input
    def _value_at(self, x: int):
        w = self.winfo_width()
        inset = self._inset()
        fraction = max(0.0, min(1.0, (x - inset) / max(1, w - 2 * inset)))
        value = self.lo + fraction * (self.hi - self.lo)
        return self._snap(value)

    def _snap(self, value: float):
        value = max(self.lo, min(self.hi, value))
        if self.integer:
            return int(round(value))
        return round(round(value / self.step) * self.step, 2)

    def _apply(self, value) -> None:
        if value == self.variable.get():
            return
        self.variable.set(value)
        if self.command:
            self.command(value)

    def _press(self, event) -> None:
        self._dragging = True
        self._apply(self._value_at(event.x))

    def _drag(self, event) -> None:
        self._apply(self._value_at(event.x))

    def _release(self, _event) -> None:
        self._dragging = False
        self._layout()

    def _wheel(self, event) -> str:
        direction = 1 if event.delta > 0 else -1
        self._apply(self._snap(float(self.variable.get()) + direction * self.step))
        return "break"


class Button(tk.Canvas):
    """A pill button. kind: primary | default | quiet | danger."""

    H = 32

    def __init__(self, parent, ui: Ui, text: str, command=None, kind: str = "default",
                 bg: str | None = None, padx: int = 16, min_width: int = 0,
                 font: tkfont.Font | None = None, height: int | None = None) -> None:
        self.ui = ui
        self.text = text
        self.command = command
        self.kind = kind
        self._bg = bg or parent.cget("bg")
        self._font = font or ui.font(10, "semibold" if kind == "primary" else "normal")
        self._state = "normal"
        self.enabled = True
        h = ui.px(height or self.H)
        w = max(ui.px(min_width), ui.text_width(text, self._font) + 2 * ui.px(padx))
        super().__init__(
            parent, width=w, height=h, bg=self._bg, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        self._width, self._height = w, h
        self._image = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(w // 2, h // 2, text=text, font=self._font)
        self.bind("<Enter>", lambda _e: self._set_state("hover"))
        self.bind("<Leave>", lambda _e: self._set_state("normal"))
        self.bind("<ButtonPress-1>", lambda _e: self._set_state("pressed"))
        self.bind("<ButtonRelease-1>", self._release)
        self._render()

    def _colours(self) -> tuple[str, str | None, str]:
        p, s = self.ui.p, self._state
        if not self.enabled:
            return p.control, None, p.disabled
        if self.kind == "primary":
            fill = {"normal": p.accent, "hover": p.accent_hover,
                    "pressed": pal.mix(p.accent, "#000000", 0.15)}[s]
            return fill, None, p.on_accent
        if self.kind == "danger":
            wash = pal.mix(self._bg, p.danger, 0.14)
            fill = {"normal": self._bg, "hover": wash, "pressed": pal.mix(wash, p.danger, 0.2)}[s]
            return fill, None if s == "normal" else None, p.danger
        if self.kind == "quiet":
            fill = {"normal": self._bg, "hover": p.hover, "pressed": p.control}[s]
            return fill, None, p.fg_soft
        fill = {"normal": p.control, "hover": p.control_hover,
                "pressed": pal.mix(p.control_hover, p.fg, 0.08)}[s]
        return fill, p.control_border, p.fg

    def _render(self) -> None:
        fill, outline, text = self._colours()
        image = self.ui.pill(self._width, self._height, fill, self._bg, outline, 1)
        self.itemconfigure(self._image, image=image)
        self.itemconfigure(self._label, fill=text)

    def _set_state(self, state: str) -> None:
        if not self.enabled:
            return
        self._state = state
        self._render()

    def _release(self, event) -> None:
        inside = 0 <= event.x < self._width and 0 <= event.y < self._height
        self._set_state("hover" if inside else "normal")
        if inside and self.enabled and self.command:
            self.command()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._state = "normal"
        self._render()

    def set_text(self, text: str) -> None:
        self.text = text
        self.itemconfigure(self._label, text=text)


class Chip(tk.Canvas):
    """A small toggle pill (day-of-week picker) bound to a BooleanVar."""

    H = 26

    def __init__(self, parent, ui: Ui, text: str, variable: tk.BooleanVar,
                 command=None, bg: str | None = None, padx: int = 10) -> None:
        self.ui = ui
        self.variable = variable
        self.command = command
        self._bg = bg or parent.cget("bg")
        self._font = ui.font(9)
        self._hot = False
        h = ui.px(self.H)
        w = ui.text_width(text, self._font) + 2 * ui.px(padx)
        super().__init__(
            parent, width=w, height=h, bg=self._bg, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        self._width, self._height = w, h
        self._image = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(w // 2, h // 2, text=text, font=self._font)
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda _e: self._set_hot(True))
        self.bind("<Leave>", lambda _e: self._set_hot(False))
        self._render()

    def _set_hot(self, hot: bool) -> None:
        self._hot = hot
        self._render()

    def _toggle(self, _event=None) -> None:
        self.variable.set(not self.variable.get())
        self._render()
        if self.command:
            self.command()

    def _render(self) -> None:
        p = self.ui.p
        if self.variable.get():
            fill = p.accent_hover if self._hot else p.accent
            outline, text = None, p.on_accent
        else:
            fill = p.control_hover if self._hot else p.control
            outline, text = p.control_border, p.fg_soft
        self.itemconfigure(
            self._image, image=self.ui.pill(self._width, self._height, fill, self._bg, outline, 1)
        )
        self.itemconfigure(self._label, fill=text)


class Segmented(tk.Canvas):
    """Mutually exclusive options in one pill, bound to a StringVar."""

    H = 32

    def __init__(self, parent, ui: Ui, options: list[tuple[str, str]], variable: tk.StringVar,
                 command=None, bg: str | None = None, padx: int = 12) -> None:
        self.ui = ui
        self.options = options
        self.variable = variable
        self.command = command
        self._bg = bg or parent.cget("bg")
        self._font = ui.font(9, "semibold")
        self._hover = None
        h = ui.px(self.H)
        seg = max(ui.text_width(text, self._font) for _v, text in options) + 2 * ui.px(padx)
        self._pad = ui.px(3)
        self._seg = seg
        w = seg * len(options) + 2 * self._pad
        super().__init__(
            parent, width=w, height=h, bg=self._bg, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        self._width, self._height = w, h
        self._frame = self.create_image(0, 0, anchor="nw")
        self._thumb = self.create_image(0, 0, anchor="nw")
        self._labels = [
            self.create_text(
                self._pad + seg * i + seg // 2, h // 2, text=text, font=self._font
            )
            for i, (_v, text) in enumerate(options)
        ]
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self._trace = variable.trace_add("write", lambda *_: self._render())
        self.bind("<Destroy>", self._on_destroy)
        self._render()

    def _on_destroy(self, event) -> None:
        if str(event.widget) == str(self):
            try:
                self.variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass

    def _index_at(self, x: int) -> int | None:
        i = (x - self._pad) // self._seg
        return int(i) if 0 <= i < len(self.options) else None

    def _click(self, event) -> None:
        index = self._index_at(event.x)
        if index is None:
            return
        value = self.options[index][0]
        if value != self.variable.get():
            self.variable.set(value)
            if self.command:
                self.command(value)

    def _motion(self, event) -> None:
        self._set_hover(self._index_at(event.x))

    def _set_hover(self, index) -> None:
        if index != self._hover:
            self._hover = index
            self._render()

    def _render(self) -> None:
        if not self.winfo_exists():
            return
        p = self.ui.p
        selected = p.card if not p.is_dark else pal.lighten(p.control, 0.16)
        self.itemconfigure(
            self._frame,
            image=self.ui.pill(self._width, self._height, p.control, self._bg, p.control_border, 1),
        )
        current = self.variable.get()
        values = [v for v, _t in self.options]
        index = values.index(current) if current in values else 0
        x = self._pad + index * self._seg
        inner_h = self._height - 2 * self._pad
        self.itemconfigure(
            self._thumb, image=self.ui.pill(self._seg, inner_h, selected, p.control)
        )
        self.coords(self._thumb, x, self._pad)
        for i, item in enumerate(self._labels):
            if i == index:
                colour = p.fg
            elif i == self._hover:
                colour = p.fg_soft
            else:
                colour = p.muted
            self.itemconfigure(item, fill=colour)


class Dropdown(tk.Canvas):
    """A pill showing the current choice; click posts a menu of the values."""

    H = 32

    def __init__(self, parent, ui: Ui, values: list[str], variable: tk.StringVar,
                 command=None, bg: str | None = None, min_width: int = 160) -> None:
        self.ui = ui
        self.values = list(values)
        self.variable = variable
        self.command = command
        self._bg = bg or parent.cget("bg")
        self._font = ui.font(10)
        self._hot = False
        h = ui.px(self.H)
        widest = max(ui.text_width(v, self._font) for v in self.values) if self.values else 0
        w = max(ui.px(min_width), widest + ui.px(46))
        super().__init__(
            parent, width=w, height=h, bg=self._bg, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        self._width, self._height = w, h
        self._image = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(ui.px(14), h // 2, anchor="w", font=self._font)
        self._chevron = self.create_image(w - ui.px(22), h // 2, anchor="center")
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", lambda _e: self._set_hot(True))
        self.bind("<Leave>", lambda _e: self._set_hot(False))
        self._trace = variable.trace_add("write", lambda *_: self._render())
        self.bind("<Destroy>", self._on_destroy)
        self._render()

    def _on_destroy(self, event) -> None:
        if str(event.widget) == str(self):
            try:
                self.variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass

    def _set_hot(self, hot: bool) -> None:
        self._hot = hot
        self._render()

    def _render(self) -> None:
        if not self.winfo_exists():
            return
        p = self.ui.p
        fill = p.control_hover if self._hot else p.control
        self.itemconfigure(
            self._image, image=self.ui.pill(self._width, self._height, fill, self._bg, p.control_border, 1)
        )
        self.itemconfigure(self._label, text=self.variable.get(), fill=p.fg)
        self.itemconfigure(
            self._chevron, image=self.ui.icon("chevron", self.ui.px(14), p.muted, fill)
        )

    def _open(self, _event=None) -> None:
        menu = tk.Menu(self, **menu_style(self.ui))
        for value in self.values:
            menu.add_radiobutton(
                label=value, variable=self.variable, value=value,
                command=lambda v=value: self._choose(v),
            )
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self._height + self.ui.px(4)
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _choose(self, value: str) -> None:
        if self.command:
            self.command(value)


def menu_style(ui: Ui) -> dict:
    """Keyword arguments that make a tk.Menu match the palette."""
    p = ui.p
    return dict(
        tearoff=0, bg=p.card, fg=p.fg, activebackground=p.accent,
        activeforeground=p.on_accent, activeborderwidth=0, borderwidth=0,
        relief="flat", selectcolor=p.accent_text, font=ui.font(10),
        disabledforeground=p.disabled,
    )


class ThemePicker(tk.Frame):
    """One miniature card per clock theme; click to choose."""

    SW, SH = 78, 52

    def __init__(self, parent, ui: Ui, themes: dict, variable: tk.StringVar,
                 command=None, bg: str | None = None) -> None:
        self._bg = bg or parent.cget("bg")
        super().__init__(parent, bg=self._bg, bd=0)
        self.ui = ui
        self.themes = themes
        self.variable = variable
        self.command = command
        self._chips: dict[str, tuple[tk.Canvas, int, int]] = {}
        self._hover: str | None = None
        sw, sh = ui.px(self.SW), ui.px(self.SH)
        font = ui.font(9)
        for column, (name, theme) in enumerate(themes.items()):
            chip = tk.Canvas(
                self, width=sw, height=sh + ui.px(20), bg=self._bg, bd=0,
                highlightthickness=0, cursor="hand2",
            )
            chip.grid(row=0, column=column, padx=(0 if column == 0 else ui.px(8), 0))
            image = chip.create_image(sw // 2, sh // 2, anchor="center")
            text = chip.create_text(sw // 2, sh + ui.px(11), text=name, font=font)
            chip.bind("<Button-1>", lambda _e, n=name: self._choose(n))
            chip.bind("<Enter>", lambda _e, n=name: self._set_hover(n))
            chip.bind("<Leave>", lambda _e: self._set_hover(None))
            self._chips[name] = (chip, image, text)
        self._trace = variable.trace_add("write", lambda *_: self._render())
        self.bind("<Destroy>", self._on_destroy)
        self._render()

    def _on_destroy(self, event) -> None:
        if str(event.widget) == str(self):
            try:
                self.variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass

    def _set_hover(self, name) -> None:
        self._hover = name
        self._render()

    def _choose(self, name: str) -> None:
        if name != self.variable.get():
            self.variable.set(name)
            if self.command:
                self.command(name)

    def _render(self) -> None:
        if not self.winfo_exists():
            return
        p = self.ui.p
        current = self.variable.get()
        sw, sh = self.ui.px(self.SW), self.ui.px(self.SH)
        for name, (chip, image, text) in self._chips.items():
            theme = self.themes[name]
            if name == current:
                ring = p.accent
            elif name == self._hover:
                ring = p.control_border
            else:
                ring = None
            chip.itemconfigure(image, image=self.ui.swatch(
                sw, sh, pal.to_hex(theme.grad_top), pal.to_hex(theme.grad_bottom),
                pal.to_hex(theme.fg), pal.to_hex(theme.fg_muted), pal.to_hex(theme.accent),
                self._bg, ring,
            ))
            chip.itemconfigure(text, fill=p.fg if name == current else p.muted)


class Field(tk.Frame):
    """A rounded text entry with a focus ring. `.entry` is the tk.Entry."""

    def __init__(self, parent, ui: Ui, width: int = 12, initial: str = "",
                 placeholder: str = "", bg: str | None = None, mono: bool = False) -> None:
        self.ui = ui
        self._bg = bg or parent.cget("bg")
        self.placeholder = placeholder
        self._showing_placeholder = False
        super().__init__(parent, bg=self._bg, bd=0, highlightthickness=0)
        self._image = tk.Label(self, bg=self._bg, bd=0, padx=0, pady=0)
        self._image.place(x=0, y=0, relwidth=1, relheight=1)
        p = ui.p
        self.entry = tk.Entry(
            self, width=width, bd=0, highlightthickness=0, relief="flat",
            bg=p.field, fg=p.fg, insertbackground=p.fg, font=ui.font(10, mono=mono),
            selectbackground=p.accent, selectforeground=p.on_accent,
            disabledbackground=p.field,
        )
        self.entry.pack(padx=ui.px(11), pady=ui.px(7))
        self._focused = False
        self._size = (0, 0)
        self.bind("<Configure>", self._redraw)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        if initial:
            self.entry.insert(0, initial)
        else:
            self._show_placeholder()

    def _redraw(self, _event=None) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        p = self.ui.p
        outline = p.accent if self._focused else p.field_border
        image = self.ui.rounded(
            w, h, self.ui.px(8), p.field, self._bg, outline, 2 if self._focused else 1
        )
        self._image.configure(image=image)

    def _focus_in(self, _event=None) -> None:
        self._focused = True
        if self._showing_placeholder:
            self.entry.delete(0, tk.END)
            self.entry.configure(fg=self.ui.p.fg)
            self._showing_placeholder = False
        self._redraw()

    def _focus_out(self, _event=None) -> None:
        self._focused = False
        if not self.entry.get():
            self._show_placeholder()
        self._redraw()

    def _show_placeholder(self) -> None:
        if self.placeholder and not self._focused:
            self.entry.delete(0, tk.END)
            self.entry.insert(0, self.placeholder)
            self.entry.configure(fg=self.ui.p.muted)
            self._showing_placeholder = True

    def get(self) -> str:
        return "" if self._showing_placeholder else self.entry.get()

    def set(self, text: str) -> None:
        self.entry.delete(0, tk.END)
        self._showing_placeholder = False
        self.entry.configure(fg=self.ui.p.fg)
        if text:
            self.entry.insert(0, text)
        elif not self._focused:
            self._show_placeholder()

    def clear(self) -> None:
        self.set("")


# --- lists -----------------------------------------------------------------
@dataclass
class ListItem:
    key: str
    primary: str
    secondary: str = ""
    meta: str = ""          # left column, monospaced: a time or a countdown
    status: str = ""        # right-hand pill
    kind: str = "muted"     # muted | accent | success | danger
    dim: bool = False       # e.g. a switched-off alarm


class _Pill(tk.Canvas):
    """The little status tag at the end of a list row."""

    H = 22

    def __init__(self, parent, ui: Ui, bg: str) -> None:
        self.ui = ui
        self._bg = bg
        self._font = ui.font(8, "semibold")
        super().__init__(parent, width=1, height=ui.px(self.H), bg=bg, bd=0, highlightthickness=0)
        self._image = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(0, 0, anchor="center", font=self._font)
        self._current = None

    def update_to(self, text: str, kind: str, bg: str) -> None:
        state = (text, kind, bg)
        if state == self._current:
            return
        self._current = state
        p = self.ui.p
        self._bg = bg
        self.configure(bg=bg)
        if not text:
            self.configure(width=1)
            self.itemconfigure(self._image, image="")
            self.itemconfigure(self._label, text="")
            return
        tone = {"accent": p.accent_text, "success": p.success, "danger": p.danger}.get(kind, p.muted)
        fill = pal.mix(bg, tone, 0.16 if kind != "muted" else 0.10)
        w = self.ui.text_width(text.upper(), self._font) + self.ui.px(18)
        h = self.ui.px(self.H)
        self.configure(width=w)
        self.itemconfigure(self._image, image=self.ui.pill(w, h, fill, bg))
        self.coords(self._label, w // 2, h // 2)
        self.itemconfigure(self._label, text=text.upper(), fill=tone)


class _Row(tk.Frame):
    def __init__(self, owner: "RowList", item: ListItem) -> None:
        ui = owner.ui
        super().__init__(owner, bg=ui.p.card, bd=0, cursor="hand2")
        self.owner = owner
        self.key = item.key
        p = ui.p
        self.columnconfigure(1, weight=1)
        self.meta = tk.Label(
            self, font=ui.font(10, mono=True), bg=p.card, fg=p.accent_text, anchor="w",
            width=owner.meta_width, bd=0,
        )
        self.meta.grid(row=0, column=0, rowspan=2, sticky="w", padx=(ui.px(14), ui.px(10)))
        self.primary = tk.Label(self, font=ui.font(10), bg=p.card, fg=p.fg, anchor="w", bd=0)
        self.primary.grid(row=0, column=1, sticky="w", pady=(ui.px(7), 0))
        self.secondary = tk.Label(
            self, font=ui.font(9), bg=p.card, fg=p.muted, anchor="w", bd=0
        )
        self.secondary.grid(row=1, column=1, sticky="w", pady=(0, ui.px(7)))
        self.pill = _Pill(self, ui, p.card)
        self.pill.grid(row=0, column=2, rowspan=2, sticky="e", padx=(ui.px(8), ui.px(12)))
        for widget in (self, self.meta, self.primary, self.secondary, self.pill):
            widget.bind("<Button-1>", lambda _e, k=item.key: owner.select(k))
            widget.bind("<Double-Button-1>", lambda _e, k=item.key: owner._activate(k))
            widget.bind("<Enter>", lambda _e: owner._hover(self.key))
            widget.bind("<Leave>", lambda _e: owner._hover(None))
        self.update_to(item)

    def update_to(self, item: ListItem) -> None:
        self.item = item
        self.meta.configure(text=item.meta)
        self.primary.configure(text=item.primary)
        self.secondary.configure(text=item.secondary)
        if item.secondary:
            self.secondary.grid()
            self.primary.grid_configure(pady=(self.owner.ui.px(7), 0))
        else:
            self.secondary.grid_remove()
            self.primary.grid_configure(pady=(self.owner.ui.px(11), self.owner.ui.px(11)))
        self.paint()

    def paint(self) -> None:
        owner, p = self.owner, self.owner.ui.p
        if self.key == owner.selected_key:
            bg = p.accent_soft
        elif self.key == owner._hovered:
            bg = p.hover
        else:
            bg = p.card
        fg = p.disabled if self.item.dim else p.fg
        meta = p.disabled if self.item.dim else p.accent_text
        for widget in (self, self.meta, self.primary, self.secondary):
            widget.configure(bg=bg)
        self.primary.configure(fg=fg)
        self.meta.configure(fg=meta)
        self.pill.update_to(self.item.status, self.item.kind, bg)


class RowList(tk.Frame):
    """Selectable rows with a time column, a title, a caption and a status pill.
    Re-rendering with the same keys updates rows in place, so a once-a-second
    refresh does not flicker or lose the selection."""

    def __init__(self, parent, ui: Ui, on_activate=None, on_select=None,
                 empty: str = "Nothing here yet", meta_width: int = 8,
                 min_height: int = 3) -> None:
        self.ui = ui
        super().__init__(parent, bg=ui.p.card, bd=0)
        self.on_activate = on_activate
        self.on_select = on_select
        self.empty_text = empty
        self.meta_width = meta_width
        self.min_height = min_height
        self.selected_key: str | None = None
        self._hovered: str | None = None
        self._rows: list[_Row] = []
        self._dividers: list[tk.Frame] = []
        self._empty: tk.Label | None = None
        self._keys: list[str] = []
        self.set_items([])

    def set_items(self, items: list[ListItem]) -> None:
        keys = [item.key for item in items]
        if keys == self._keys and self._rows:
            for row, item in zip(self._rows, items):
                row.update_to(item)
            return
        for child in self.winfo_children():
            child.destroy()
        self._rows, self._dividers, self._empty = [], [], None
        self._keys = keys
        if self.selected_key not in keys:
            self.selected_key = None
        if not items:
            self._empty = tk.Label(
                self, text=self.empty_text, font=self.ui.font(10), fg=self.ui.p.muted,
                bg=self.ui.p.card, height=self.min_height, bd=0,
            )
            self._empty.pack(fill="x", pady=self.ui.px(10))
            return
        for index, item in enumerate(items):
            if index:
                line = divider(self, self.ui)
                line.pack(fill="x", padx=self.ui.px(12))
                self._dividers.append(line)
            row = _Row(self, item)
            row.pack(fill="x")
            self._rows.append(row)

    def _hover(self, key) -> None:
        self._hovered = key
        for row in self._rows:
            row.paint()

    def select(self, key: str | None) -> None:
        self.selected_key = key
        for row in self._rows:
            row.paint()
        if self.on_select:
            self.on_select(key)

    def _activate(self, key: str) -> None:
        self.select(key)
        if self.on_activate:
            self.on_activate(key)


# --- navigation and scrolling ------------------------------------------------
class SideNav(tk.Frame):
    """The page list down the left: icon + label, accent wash on the current one."""

    H = 38

    def __init__(self, parent, ui: Ui, items: list[tuple[str, str, str]], on_select,
                 bg: str | None = None, width: int = 168) -> None:
        self._bg = bg or parent.cget("bg")
        super().__init__(parent, bg=self._bg, bd=0)
        self.ui = ui
        self.on_select = on_select
        self.current: str | None = None
        self._hover: str | None = None
        self._items: dict[str, tuple[tk.Canvas, int, int, int, str]] = {}
        h = ui.px(self.H)
        w = ui.px(width)
        font = ui.font(10)
        for key, text, icon in items:
            canvas = tk.Canvas(
                self, width=w, height=h, bg=self._bg, bd=0, highlightthickness=0,
                cursor="hand2",
            )
            canvas.pack(fill="x", pady=(0, ui.px(2)))
            wash = canvas.create_image(0, 0, anchor="nw")
            glyph = canvas.create_image(ui.px(14), h // 2, anchor="w")
            label_id = canvas.create_text(ui.px(42), h // 2, anchor="w", text=text, font=font)
            canvas.bind("<Button-1>", lambda _e, k=key: self.select(k, notify=True))
            canvas.bind("<Enter>", lambda _e, k=key: self._set_hover(k))
            canvas.bind("<Leave>", lambda _e: self._set_hover(None))
            canvas.bind("<Configure>", lambda _e: self._render())
            self._items[key] = (canvas, wash, glyph, label_id, icon)
        self._render()

    def _set_hover(self, key) -> None:
        self._hover = key
        self._render()

    def select(self, key: str, notify: bool = False) -> None:
        if key not in self._items:
            return
        self.current = key
        self._render()
        if notify and self.on_select:
            self.on_select(key)

    def _render(self) -> None:
        p = self.ui.p
        h = self.ui.px(self.H)
        for key, (canvas, wash, glyph, label_id, icon) in self._items.items():
            w = max(canvas.winfo_width(), 8)
            if key == self.current:
                fill, text, glyph_colour = p.accent_soft, p.fg, p.accent_text
            elif key == self._hover:
                fill, text, glyph_colour = p.hover if p.is_dark else p.control, p.fg, p.fg_soft
            else:
                fill, text, glyph_colour = self._bg, p.fg_soft, p.muted
            canvas.itemconfigure(
                wash, image=self.ui.rounded(w, h, self.ui.px(9), fill, self._bg)
            )
            canvas.itemconfigure(
                glyph, image=self.ui.icon(icon, self.ui.px(18), glyph_colour, fill)
            )
            canvas.itemconfigure(label_id, fill=text)


class ScrollFrame(tk.Frame):
    """A vertically scrolling page. Build into `.body`."""

    def __init__(self, parent, ui: Ui, bg: str | None = None) -> None:
        self._bg = bg or parent.cget("bg")
        super().__init__(parent, bg=self._bg, bd=0)
        self.ui = ui
        self.canvas = tk.Canvas(self, bg=self._bg, bd=0, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar = tk.Canvas(
            self, width=ui.px(6), bg=self._bg, bd=0, highlightthickness=0
        )
        self.bar.pack(side="right", fill="y", padx=(0, ui.px(3)))
        self._thumb = self.bar.create_rectangle(0, 0, 0, 0, fill=ui.p.control_border, outline="")
        self.body = tk.Frame(self.canvas, bg=self._bg, bd=0)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.bar.bind("<Button-1>", self._bar_press)
        self.bar.bind("<B1-Motion>", self._bar_drag)
        self._drag_from = None

    def _on_body(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_scroll(self, first: str, last: str) -> None:
        lo, hi = float(first), float(last)
        h = self.bar.winfo_height()
        if hi - lo >= 0.999:
            self.bar.coords(self._thumb, 0, 0, 0, 0)
            return
        self.bar.coords(self._thumb, 0, lo * h, self.ui.px(6), hi * h)

    def _bar_press(self, event) -> None:
        self._drag_from = (event.y, self.canvas.yview()[0])

    def _bar_drag(self, event) -> None:
        if self._drag_from is None:
            return
        y0, view0 = self._drag_from
        h = max(1, self.bar.winfo_height())
        self.canvas.yview_moveto(view0 + (event.y - y0) / h)

    def wheel(self, event) -> str | None:
        """Route a wheel event here; returns "break" if it was consumed."""
        lo, hi = self.canvas.yview()
        if hi - lo >= 0.999:
            return None
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def scroll_top(self) -> None:
        self.canvas.yview_moveto(0)
