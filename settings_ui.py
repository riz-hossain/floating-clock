"""The settings window: a sidebar of pages, each a column of cards.

Split out as a mixin so app.py stays about the window and the clock, and the
form-building lives on its own. Every control writes straight through to
`self.s` / `self.scheduler` and repaints, so there is no apply/cancel state to
keep in sync.

The chrome follows a light or dark palette (palette.py) coloured with the
clock theme's accent. Changing either rebuilds the dialog in place, which is
cheap enough that nothing needs to be restyled widget by widget.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
import webbrowser
from datetime import datetime

from . import (
    alerts, outlook, palette as pal, prayer as prayer_mod, render,
    settings as cfg, themes, widgets as w, win32util as w32,
)
from . import __version__
from . import cast as cast_mod, masjids as masjids_mod, routines as routines_mod
from .timetext import clock_text, parse_clock_time
from .calendars_ui import CalendarsPage

log = logging.getLogger(__name__)

PAGES = (
    ("clock", "Clock", "clock", "The look of the clock card."),
    ("behaviour", "Behaviour", "sliders", "How the window behaves on your desktop."),
    ("meetings", "Meetings", "calendar", "Meeting reminders and the progress bar."),
    ("calendars", "Calendars", "orgs", "Outlook on this PC, plus any other calendars you add."),
    ("prayer", "Prayer", "moon", "Iqama times from your masjid, and a reminder before each one."),
    ("alarms", "Alarms", "bell", "Ring at a set time, once or on chosen days."),
    ("timers", "Timers", "timer", "Count down from a length you type."),
)
# Legacy numeric tab indices (menu callers used to pass these); keys are preferred.
PAGE_INDEX = {0: "clock", 1: "behaviour", 2: "meetings", 3: "alarms", 4: "timers", 5: "calendars"}

WINDOW_W, WINDOW_H = 820, 660
SIDEBAR_W = 196
HINT = "Drag to move  ·  wheel = opacity  ·  Ctrl+wheel = size"

BAR_MODE_LABELS = {
    "day": "My day, with meeting dots",
    "meeting": "Countdown to the next meeting",
    "seconds": "The passing minute",
}
BAR_MODE_BY_LABEL = {label: mode for mode, label in BAR_MODE_LABELS.items()}
# Under the name in the sidebar: which build this is, without having to ask.
BRAND_SUBTITLE = "Settings  \u00b7  v%s" % __version__

# What each trigger box needs saying about it. Maghrib is the one real
# exception in the set: its azan follows the sun, and the masjid's iqama is
# a few minutes after sunset, so firing early against it would call the azan
# before the sun had actually gone down.
ROUTINE_ROW_NOTES = {
    "Dhuhr": "Friday's Jumuah uses this one too.",
    "Maghrib": "Best left empty. Maghrib is called at sunset, not at the iqama, so "
               "the assistant's own sunset trigger is the one to use for it.",
}
# The speaker has the same sunset problem and no sunset trigger to fall back
# on, so the honest advice differs: give Maghrib its own lead, or leave it out.
CAST_ROW_NOTES = {
    "Dhuhr": "Friday's Jumuah uses this one too.",
    "Maghrib": "Maghrib is called at sunset, a few minutes before the iqama here, so "
               "playing early against the iqama would call it before sunset.",
}
# The mistake that looks like nothing happening at all: the chime rings, the
# azan does not, because it played at whatever volume the Echo was left on.
# The wait is what actually fixes it -- setting the volume is not instant, and
# without a pause the skill starts talking before the new volume has landed.
ALEXA_ORDER_NOTE = (
    "For an Alexa routine, order matters inside it: Set volume first, then Wait about 10 seconds, "
    "then the azan. Without that wait the azan starts before the new volume has taken "
    "hold and plays at whatever the Echo was left on overnight -- which sounds exactly "
    "like nothing happening, since the doorbell chime rings on its own volume either "
    "way. Add the pause with \"Add another action\" -> Wait."
)


def _hour_label(hour: float) -> str:
    """5.5 -> '5:30 am'. Half hours so a day can end at 11:30 pm."""
    total = int(round(float(hour) * 60)) % (24 * 60)
    hours, minutes = divmod(total, 60)
    display = hours % 12 or 12
    return "%d:%02d %s" % (display, minutes, "am" if hours < 12 else "pm")


HOUR_STEPS = [h / 2 for h in range(48)]
HOUR_CHOICES = [_hour_label(h) for h in HOUR_STEPS]
HOUR_BY_LABEL = {_hour_label(h): h for h in HOUR_STEPS}


def _minutes_label(minutes) -> str:
    """0 -> 'on time'; 90 -> '1 h 30 min'. A slider that reads in hours once
    it is past one saves counting zeroes."""
    minutes = int(minutes)
    if minutes <= 0:
        return "on time"
    if minutes < 60:
        return "%d min" % minutes
    hours, rest = divmod(minutes, 60)
    return "%d h" % hours if not rest else "%d h %d min" % (hours, rest)


class SettingsUI(CalendarsPage):
    """Mixin for FloatingClock: everything the settings window needs."""

    # Tests set this False: the dialog is built in full but never mapped, so a
    # test run cannot pop a topmost window over whatever the user is doing.
    settings_visible = True

    # --- palette -----------------------------------------------------------
    def _palette(self) -> pal.Palette:
        mode = pal.effective_mode(self.s.get("ui_mode", "auto"))
        return pal.resolve(mode, themes.get(self.s["theme"]).accent)

    def _accent_hex(self) -> str:
        return self._palette().accent

    # --- window ------------------------------------------------------------
    def _settings_open(self) -> bool:
        return self._settings_win is not None and self._settings_win.winfo_exists()

    def _toplevel_hwnd(self, window: tk.Toplevel) -> int:
        window_id = window.winfo_id()
        try:
            parent = w32.user32.GetParent(window_id)
        except OSError:
            parent = 0
        return parent or window_id

    def open_settings(self) -> None:
        if self._settings_open():
            if self.settings_visible:
                self._settings_win.lift()
                self._settings_win.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("Floating Clock")
        win.withdraw()
        if self.settings_visible:
            win.attributes("-topmost", True)
        scale = win.winfo_fpixels("1i") / 96.0
        screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
        width = min(int(WINDOW_W * scale), screen_w - int(40 * scale))
        height = min(int(WINDOW_H * scale), screen_h - int(120 * scale))
        win.minsize(int(720 * scale), int(520 * scale))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - int(20 * scale))
        win.geometry("%dx%d+%d+%d" % (width, height, x, y))
        win.bind("<MouseWheel>", self._settings_wheel)
        win.bind("<Escape>", lambda _e: self._close_settings(win))
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_settings(win))
        self._settings_page = getattr(self, "_settings_page", "clock")
        try:
            self._build_settings(win)
        except Exception:
            # A half-built dialog left withdrawn would make every later click
            # a silent no-op, so tear it down and let the next attempt start
            # clean -- and say why in the log.
            log.exception("Could not build the settings dialog")
            self._settings_win = None
            win.destroy()
            raise
        if self.settings_visible:
            win.deiconify()
            win.update_idletasks()
            self._log_window_state(win, "settings dialog shown")
        self._tick_settings()

    def _close_settings(self, win: tk.Toplevel) -> None:
        """Save what is typed, then close.

        Text fields commit on FocusOut, and pressing Done never takes focus
        off the box being typed in -- so without this, the last address typed
        is the one that gets thrown away.
        """
        self._flush_fields()
        win.destroy()

    def _flush_fields(self) -> None:
        for save in list(getattr(self, "_flushers", ())):
            try:
                save()
            except Exception:
                log.warning("Could not save a settings field", exc_info=True)

    def _on_close_save(self, save) -> None:
        """Pages register a saver here for anything typed rather than clicked."""
        self._flushers = list(getattr(self, "_flushers", ())) + [save]

    def _log_window_state(self, win: tk.Toplevel, what: str) -> None:
        """What Windows thinks of the dialog -- for a report of 'nothing shows'."""
        try:
            hwnd = self._toplevel_hwnd(win)
            visible = bool(w32.user32.IsWindowVisible(hwnd))
            rect = w32.window_rect(hwnd)
        except Exception:
            hwnd, visible, rect = 0, None, None
        log.info(
            "%s: state=%s geometry=%s viewable=%s hwnd=%s visible=%s rect=%s screen=%sx%s",
            what, win.state(), win.geometry(), win.winfo_viewable(), hwnd, visible,
            rect, win.winfo_screenwidth(), win.winfo_screenheight(),
        )

    def open_settings_tab(self, page) -> None:
        """Open the dialog straight onto one page (from the right-click menu).

        `page` is a key from PAGES; a legacy tab number is accepted too.
        """
        key = page if page in self._page_keys() else PAGE_INDEX.get(page, "clock")
        if self._settings_open():
            self._show_page(key)
            self._nav.select(key)
            self._settings_win.lift()
            return
        self._settings_page = key
        self.open_settings()

    @staticmethod
    def _page_keys() -> tuple:
        return tuple(key for key, *_rest in PAGES)

    def _restyle_settings(self) -> None:
        """Rebuild the open dialog after the palette changed (mode or accent).

        Deferred a tick: this is usually called from inside a widget that is
        about to be destroyed by the rebuild.
        """
        if not self._settings_open():
            return
        win = self._settings_win
        self.root.after(0, lambda: win.winfo_exists() and self._build_settings(win))

    def _set_ui_mode(self, mode: str) -> None:
        self.s["ui_mode"] = mode
        self._restyle_menus()
        self._restyle_settings()

    def _restyle_menus(self) -> None:
        """Hook for app.py; the stub keeps the mixin usable on its own."""

    def reload_orgs(self) -> None:
        """Hook for app.py (re-reads the configured calendars)."""

    def _settings_wheel(self, event):
        scroll = getattr(self, "_scroll", None)
        if scroll is None or not scroll.winfo_exists():
            return None
        # A slider under the pointer takes the wheel for itself.
        under = event.widget if isinstance(event.widget, tk.Misc) else None
        if isinstance(under, w.Slider):
            return None
        return scroll.wheel(event)

    def _tick_settings(self) -> None:
        """Keep the live lists moving while the window is open."""
        if not self._settings_open():
            return
        self._refresh_timers()
        self._refresh_agenda()
        self._refresh_prayer_page()
        if self.s.get("ui_mode", "auto") == "auto":
            if pal.effective_mode("auto") != self._settings_mode:
                self._restyle_settings()
        self.root.after(1000, self._tick_settings)

    # --- building ----------------------------------------------------------
    def _build_settings(self, win: tk.Toplevel) -> None:
        for child in win.winfo_children():
            child.destroy()
        palette = self._palette()
        self._settings_mode = palette.mode
        ui = w.Ui(win, palette, win.winfo_fpixels("1i") / 96.0)
        self._ui = ui
        px = ui.px
        win.configure(bg=palette.window)
        win.update_idletasks()
        w32.set_titlebar(
            self._toplevel_hwnd(win), dark=palette.is_dark, colour=palette.window,
            text=palette.fg,
        )

        # sidebar
        sidebar = tk.Frame(win, bg=palette.window, width=px(SIDEBAR_W))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=palette.window)
        brand.pack(fill="x", padx=px(18), pady=(px(22), px(18)))
        logo = tk.Label(
            brand, image=ui.icon("logo", px(30), palette.accent, palette.window),
            bg=palette.window, bd=0,
        )
        logo.pack(side="left")
        names = tk.Frame(brand, bg=palette.window)
        names.pack(side="left", padx=(px(10), 0))
        w.label(names, ui, "Floating Clock", 11, "semibold").pack(anchor="w")
        w.label(names, ui, BRAND_SUBTITLE, 9, colour=palette.muted).pack(anchor="w")

        self._nav = w.SideNav(
            sidebar, ui, [(key, title, icon) for key, title, icon, _sub in PAGES],
            on_select=self._show_page, width=SIDEBAR_W - 24,
        )
        self._nav.pack(fill="x", padx=px(12))

        appearance = tk.Frame(sidebar, bg=palette.window)
        appearance.pack(side="bottom", fill="x", padx=px(18), pady=(0, px(18)))
        w.label(
            appearance, ui, "APPEARANCE", 8, "semibold", colour=palette.muted,
        ).pack(anchor="w", pady=(0, px(6)))
        self.ui_mode_var = tk.StringVar(value=self.s.get("ui_mode", "auto"))
        w.Segmented(
            appearance, ui, [("auto", "Auto"), ("dark", "Dark"), ("light", "Light")],
            self.ui_mode_var, command=self._set_ui_mode, padx=9,
        ).pack(anchor="w")
        w.label(
            appearance, ui,
            "Auto follows Windows." if self.s.get("ui_mode") == "auto" else "",
            8, colour=palette.muted,
        ).pack(anchor="w", pady=(px(5), 0))

        # main column
        main = tk.Frame(win, bg=palette.page)
        main.pack(side="left", fill="both", expand=True)

        header = tk.Frame(main, bg=palette.page)
        header.pack(fill="x", padx=(px(28), px(28)), pady=(px(22), px(10)))
        self._page_title = w.label(header, ui, "", 16, "semibold")
        self._page_title.pack(anchor="w")
        self._page_sub = w.label(header, ui, "", 9, colour=palette.muted)
        self._page_sub.pack(anchor="w", pady=(px(1), 0))

        # Rebuilt with the dialog: the old pages' fields are gone with it.
        self._flushers = []

        footer = tk.Frame(main, bg=palette.page)
        footer.pack(side="bottom", fill="x", padx=(px(28), px(24)), pady=(px(10), px(18)))
        w.label(footer, ui, HINT, 9, colour=palette.muted).pack(side="left")
        w.Button(
            footer, ui, "Done", command=lambda: self._close_settings(win),
            kind="primary", min_width=92,
        ).pack(side="right")

        self._scroll = w.ScrollFrame(main, ui, bg=palette.page)
        self._scroll.pack(fill="both", expand=True, padx=(px(28), px(14)))

        self._pages: dict[str, tk.Frame] = {}
        for key, _title, _icon, _sub in PAGES:
            page = tk.Frame(self._scroll.body, bg=palette.page)
            self._pages[key] = page
            getattr(self, "_page_%s" % key)(page)
        self._nav.select(self._settings_page)
        self._show_page(self._settings_page)

    def _show_page(self, key: str) -> None:
        self._settings_page = key
        for page_key, page in self._pages.items():
            if page_key == key:
                page.pack(fill="x", padx=(0, self._ui.px(10)), pady=(0, self._ui.px(16)))
            else:
                page.pack_forget()
        for page_key, title, _icon, sub in PAGES:
            if page_key == key:
                self._page_title.configure(text=title)
                self._page_sub.configure(text=sub)
        self._scroll.scroll_top()

    # --- row helpers -------------------------------------------------------
    def _card(self, page, title: str = "", subtitle: str = "") -> w.Card:
        card = w.Card(page, self._ui, title, subtitle)
        card.pack(fill="x", pady=(0, self._ui.px(14)))
        return card

    def _row_texts(self_, row, text: str, caption: str = "") -> tk.Frame:
        ui = self_._ui
        texts = tk.Frame(row, bg=row.cget("bg"))
        texts.pack(side="left", fill="x", expand=True)
        w.label(texts, ui, text).pack(anchor="w")
        if caption:
            w.label(
                texts, ui, caption, 9, colour=ui.p.muted, wraplength=ui.px(380),
                justify="left",
            ).pack(anchor="w", pady=(ui.px(1), 0))
        return texts

    def _switch_row(self, card: w.Card, text: str, variable: tk.BooleanVar, command=None,
                    caption: str = "", enabled: bool = True) -> w.Switch:
        row = card.row()
        switch = w.Switch(row, self._ui, variable, command)
        switch.pack(side="right", padx=(self._ui.px(16), 0))
        texts = self._row_texts(row, text, caption)
        if enabled:
            for widget in (row, texts, *texts.winfo_children()):
                widget.bind("<Button-1>", switch.toggle)
                widget.configure(cursor="hand2")
        else:
            switch.set_enabled(False)
        return switch

    def _slider_row(self, card: w.Card, text: str, from_, to, variable, fmt: str,
                    command=None, integer: bool = True, step=None, caption: str = "") -> w.Slider:
        ui = self._ui
        row = card.row(pady=8)
        top = tk.Frame(row, bg=ui.p.card)
        top.pack(fill="x")
        show = fmt if callable(fmt) else (lambda value: fmt % value)
        readout = w.label(top, ui, show(variable.get()), 10, "semibold", colour=ui.p.accent_text)
        readout.pack(side="right")
        self._row_texts(top, text, caption)

        def on_change(value) -> None:
            readout.configure(text=show(value))
            if command:
                command(value)

        slider = w.Slider(row, ui, from_, to, variable, on_change, integer=integer, step=step)
        slider.pack(fill="x", pady=(ui.px(6), 0))
        return slider

    def _control_row(self, card: w.Card, text: str, caption: str = "") -> tk.Frame:
        # The control is packed before the text on purpose. _row_texts packs
        # an expanding frame, and Tk hands out space in packing order, so a
        # control packed after a long caption gets none and is silently left
        # unmapped -- present, correct and invisible.
        row = card.row()
        slot = tk.Frame(row, bg=self._ui.p.card)
        slot.pack(side="right", padx=(self._ui.px(16), 0))
        self._row_texts(row, text, caption)
        return slot

    # --- clock page --------------------------------------------------------
    def _page_clock(self, page: tk.Frame) -> None:
        ui = self._ui
        card = self._card(page, "Theme", "Click a card to restyle the clock. The dialog picks up the theme's accent too.")
        row = card.row(pady=6)
        w.ThemePicker(row, ui, themes.THEMES, self.theme_var, command=self.set_theme).pack(anchor="w")
        slot = self._control_row(card, "Font", "The typeface for the time and date.")
        w.Dropdown(
            slot, ui, list(render.FONT_FILES), self.family_var, command=self.set_font_family,
        ).pack()

        card = self._card(page, "Opacity & size")
        self.opacity_var = tk.IntVar(value=int(round(self.s["opacity"] * 100)))
        self._slider_row(
            card, "Opacity", int(cfg.MIN_OPACITY * 100), int(cfg.MAX_OPACITY * 100),
            self.opacity_var, "%d%%", lambda v: self.set_opacity(v / 100),
        )
        self.font_var = tk.IntVar(value=self.s["font_size"])
        self._slider_row(
            card, "Size", cfg.MIN_FONT, cfg.MAX_FONT, self.font_var, "%d px",
            self.set_font_size,
        )

        card = self._card(page, "Display")
        for text, caption, var, key in (
            ("24-hour clock", "14:05 rather than 2:05 pm.", self.var_24h, "use_24h"),
            ("Show seconds", "", self.var_seconds, "show_seconds"),
            ("Show date", "The weekday and date under the time.", self.var_date, "show_date"),
            ("Meeting progress bar",
             "A thin bar along the bottom that fills during the hour before your next meeting.",
             self.var_bar, "seconds_bar"),
            ("Monospaced digits",
             "Every digit takes the same width, so the clock never jitters as it ticks.",
             self.var_tabular, "tabular_digits"),
            ("Compact view",
             "Time and the next thing only: no date, no bar, a single line under the clock.",
             self.var_compact, "compact"),
        ):
            self._switch_row(
                card, text, var, lambda k=key, v=var: self._toggle(k, v, True), caption
            )

    # --- behaviour page ----------------------------------------------------
    def _page_behaviour(self, page: tk.Frame) -> None:
        ui = self._ui
        card = self._card(page, "Window")
        self._switch_row(card, "Always on top", self.var_topmost, self._toggle_topmost)
        escape = getattr(self, "escape_hotkey", "")
        self._switch_row(
            card, "Click-through", self.var_click_through, self._set_click_through,
            ("The clock ignores the mouse entirely. Press %s to get it back." % escape)
            if escape else "Unavailable: no global shortcut is free to switch it off again.",
            enabled=bool(escape),
        )
        self._switch_row(
            card, "Solid while in use", self.var_hover,
            lambda: self._toggle("hover_boost", self.var_hover),
            "A translucent clock turns fully solid while the pointer is over it, "
            "the meetings panel or menu is open, or it is being moved.",
        )
        self._switch_row(
            card, "Lock position", self.var_lock,
            lambda: self._toggle("lock_position", self.var_lock),
            "Dragging does nothing until this is off again.",
        )
        self._switch_row(card, "Start with Windows", self.var_startup, self._toggle_startup)
        self._switch_row(
            card, "Minimize to tray", self.var_minimized,
            lambda: self.set_minimized(self.var_minimized.get()),
            "Hides the clock and leaves the tray icon; click it to bring the clock back."
            if self.tray is not None else "Unavailable: the tray icon could not be created.",
            enabled=self.tray is not None,
        )

        card = self._card(
            page, "Peek",
            "Every so often the clock glides to the middle of the screen, waits a "
            "moment, then slides back to where you parked it. It still peeks while "
            "minimized to the tray -- switch it off here to stop it.",
        )
        w.Button(card.aside, ui, "Peek now", command=self.peek_now).pack()
        self._switch_row(card, "Peek at the centre periodically", self.var_peek, self._toggle_peek)
        self.var_peek_sound = tk.BooleanVar(value=bool(self.s.get("peek_sound", True)))
        self._switch_row(
            card, "Play a soft whoosh as it comes and goes", self.var_peek_sound,
            lambda: self._toggle("peek_sound", self.var_peek_sound, False),
        )

        def bound(key: str, integer: bool = True):
            variable = (tk.IntVar if integer else tk.DoubleVar)(value=self.s[key])
            setattr(self, "%s_var" % key, variable)
            return variable

        def store(key: str, after=None):
            def handler(value) -> None:
                self.s[key] = value
                if after:
                    after()
            return handler

        self._slider_row(
            card, "How often", 1, 120, bound("peek_interval_minutes"), "every %d min",
            store("peek_interval_minutes", self.reschedule_peek),
        )
        self._slider_row(
            card, "Hold in the centre", 0.3, 10.0, bound("peek_hold_seconds", False), "%s s",
            store("peek_hold_seconds"), integer=False, step=0.1,
        )
        self._slider_row(
            card, "Glide time", 120, 2000, bound("peek_travel_ms"), "%d ms",
            store("peek_travel_ms"), step=10,
        )
        self._slider_row(
            card, "Zoom while centred", 1.0, 2.0, bound("peek_zoom", False), "%sx",
            store("peek_zoom"), integer=False, step=0.05,
        )

    def _toggle_peek(self) -> None:
        self.s["peek_enabled"] = bool(self.var_peek.get())
        self.reschedule_peek()

    # --- meetings page -----------------------------------------------------
    def _page_meetings(self, page: tk.Frame) -> None:
        ui = self._ui
        card = self._card(
            page, "Meetings on the clock",
            "Which calendars are read is set on the Calendars page.",
        )
        self._switch_row(
            card, "Show the next meeting on the clock", self.var_next_meeting,
            lambda: self._toggle("show_next_meeting", self.var_next_meeting, True),
        )
        self._switch_row(
            card, "Play a sound with alerts", self.var_sound,
            lambda: self._toggle("alerts_sound", self.var_sound),
        )
        self.lead_var = tk.IntVar(value=int(self.s["meeting_lead_minutes"]))

        def on_lead(minutes: int) -> None:
            self.s["meeting_lead_minutes"] = minutes

        self._slider_row(
            card, "Remind me before each meeting", 1, 30, self.lead_var, "%d min", on_lead,
        )

        card = self._card(
            page, "The nudge",
            "Just before a meeting the clock flies to the middle of the screen and "
            "shakes there, where a toast behind a full-screen window would go unseen. "
            "Click the clock to stop it.",
        )
        w.Button(card.aside, ui, "Try it", command=self._try_nudge).pack(side="left")
        self.var_nudge = tk.BooleanVar(value=bool(self.s.get("nudge_enabled", True)))
        self._switch_row(
            card, "Fly in and shake before a meeting", self.var_nudge,
            lambda: self._toggle("nudge_enabled", self.var_nudge),
        )
        self.nudge_lead_var = tk.IntVar(value=int(self.s.get("nudge_lead_minutes", 1)))
        self._slider_row(
            card, "How early", 0, 15, self.nudge_lead_var, _minutes_label,
            lambda v: self.s.__setitem__("nudge_lead_minutes", int(v)),
        )
        self.nudge_shake_var = tk.DoubleVar(value=float(self.s.get("nudge_shake_seconds", 5.0)))
        self._slider_row(
            card, "Shake for", 1.0, 15.0, self.nudge_shake_var, "%s s",
            lambda v: self.s.__setitem__("nudge_shake_seconds", float(v)),
            integer=False, step=0.5,
        )

        card = self._card(
            page, "Progress bar",
            "The bar covers a full 24 hours from the moment your day starts, so nothing "
            "can fall off it. The hours after your day ends are shaded, and anything "
            "there belongs to tonight. Each meeting is a dot: hollow once over, bright "
            "while still ahead, white for the one you are in.",
        )
        self.bar_mode_var = tk.StringVar(value=BAR_MODE_LABELS[self.s["bar_mode"]])
        slot = self._control_row(card, "Shows")
        w.Dropdown(
            slot, ui, list(BAR_MODE_LABELS.values()), self.bar_mode_var,
            command=lambda _v: self._set_bar_mode(), min_width=150,
        ).pack()
        self.day_start_var = tk.DoubleVar(value=float(self.s["day_start_hour"]))
        self.day_end_var = tk.DoubleVar(value=float(self.s["day_end_hour"]))
        self._slider_row(
            card, "My day starts", 0.0, 23.5, self.day_start_var, _hour_label,
            lambda v: self._set_day_hour("day_start_hour", v), integer=False, step=0.5,
        )
        self._slider_row(
            card, "Work ends", 0.0, 23.5, self.day_end_var, _hour_label,
            lambda v: self._set_day_hour("day_end_hour", v), integer=False, step=0.5,
            caption="The bar is shaded after this hour.",
        )

        card = self._card(page, "Next up")
        w.Button(card.aside, ui, "Refresh", command=self._refresh_calendar_now).pack(side="left")
        self._join_button = w.Button(
            card.aside, ui, "Join", command=self._join_selected_meeting, kind="primary",
        )
        self._join_button.pack(side="left", padx=(ui.px(8), 0))
        self.agenda_box = w.RowList(
            card.body, ui, on_activate=lambda _k: self._join_selected_meeting(),
            on_select=lambda _k: self._sync_join_button(), empty="Nothing scheduled",
            meta_width=8,
        )
        self.agenda_box.pack(fill="x", pady=(ui.px(6), 0))
        self._refresh_agenda()
        self._sync_join_button()

    def _upcoming(self) -> list:
        """What is still to come, today's all-day entries first: they are
        not something to count down to, so they sit above the countdowns
        the way the popup panel gives them their own heading."""
        now = datetime.now()
        ahead = [event for event in self.events if event.end > now]
        whole_day = [e for e in ahead if getattr(e, "all_day", False)]
        timed = [e for e in ahead if not getattr(e, "all_day", False)]
        return sorted(whole_day, key=lambda e: e.start) + sorted(timed, key=lambda e: e.start)

    def _agenda_items(self) -> list[w.ListItem]:
        from .meetings import detail_line

        now = datetime.now()
        use_24h = bool(self.s["use_24h"])
        today = now.date()
        # Whose meeting it is, from the clock's own lookups when this page
        # is part of the running app.
        org_of = getattr(self, "org_label", None)
        calendar_of = getattr(self, "calendar_label", None)
        items = []
        for index, event in enumerate(self._upcoming()[:20]):
            all_day = bool(getattr(event, "all_day", False))
            live = event.is_live(now) and not all_day
            if all_day:
                # "12:00 am, live, ends 12:00 am" is what a holiday looked
                # like here. It is a day, not a meeting under way.
                meta = "All day"
                if event.start.date() <= today:
                    when = "today"
                else:
                    when = event.start.strftime("%a %d %b")
                status, kind = "All day", "muted"
            else:
                meta = clock_text(event.start, use_24h)
                when = "live · ends %s" % clock_text(event.end, use_24h) if live else (
                    outlook.describe_gap(event.minutes_until(now))
                )
                if live:
                    status, kind = "Live", "success"
                elif event.join_url:
                    status, kind = "Online", "accent"
                else:
                    status, kind = "", "muted"
            org = ""
            if org_of is not None and self.s.get("show_org_marks", True):
                org = org_of(event) or ""
            calendar = calendar_of(event) or "" if calendar_of is not None else ""
            detail = detail_line(event, org, calendar)
            if not detail and event.location and not event.join_url:
                detail = event.location[:40]
            items.append(w.ListItem(
                key="%d:%s" % (index, event.entry_id or event.subject),
                meta=meta, primary=event.subject[:60],
                secondary="  ·  ".join(part for part in (when, detail) if part),
                status=status, kind=kind,
            ))
        return items

    def _refresh_agenda(self) -> None:
        box = getattr(self, "agenda_box", None)
        if box is None or not box.winfo_exists():
            return
        if not self.s["calendar_enabled"]:
            box.empty_text = "Outlook calendar is switched off"
            box.set_items([])
        elif self.calendar_error:
            box.empty_text = self.calendar_error[:70]
            box.set_items([])
        else:
            box.empty_text = "Nothing scheduled"
            box.set_items(self._agenda_items())
        self._sync_join_button()

    def _selected_event(self):
        upcoming = self._upcoming()
        box = getattr(self, "agenda_box", None)
        if box is None or not upcoming:
            return None
        key = box.selected_key
        if key is None:
            return next((event for event in upcoming if event.join_url), None)
        index = int(key.split(":", 1)[0])
        return upcoming[index] if index < len(upcoming) else None

    def _sync_join_button(self) -> None:
        button = getattr(self, "_join_button", None)
        if button is None or not button.winfo_exists():
            return
        event = self._selected_event()
        button.set_enabled(bool(event and event.join_url))

    def _refresh_calendar_now(self) -> None:
        self.refresh_calendar()
        self._refresh_agenda()

    def _join_selected_meeting(self, _event=None) -> None:
        event = self._selected_event()
        if event is not None and event.join_url:
            webbrowser.open(event.join_url)

    def _set_bar_mode(self) -> None:
        self.s["bar_mode"] = BAR_MODE_BY_LABEL.get(self.bar_mode_var.get(), "day")
        self._paint(force=True)

    def _set_day_hour(self, key: str, label: str) -> None:
        if label in HOUR_BY_LABEL:
            self.s[key] = HOUR_BY_LABEL[label]
            self._paint(force=True)

    def _set_bar_mode(self) -> None:
        self.s["bar_mode"] = BAR_MODE_BY_LABEL.get(self.bar_mode_var.get(), "day")
        self._paint(force=True)

    def _set_day_hour(self, key: str, value) -> None:
        label = value if isinstance(value, str) else _hour_label(value)
        if label in HOUR_BY_LABEL:
            self.s[key] = HOUR_BY_LABEL[label]
            self._paint(force=True)

    def _try_nudge(self) -> None:
        """Show what the nudge looks like, without waiting for a meeting."""
        peek = getattr(self, "peek", None)
        if peek is not None:
            peek.nudge()

    # --- prayer page -------------------------------------------------------
    def _page_prayer(self, page: tk.Frame) -> None:
        ui = self._ui
        card = self._card(
            page, "Iqama times",
            "Iqama is when the congregation stands, and every masjid sets its own, so "
            "the times come from the masjid's own calendar -- %s unless you point this "
            "somewhere else. They are saved on this PC, so they still show when you "
            "are offline." % prayer_mod.SOURCE_NAME,
        )
        w.Button(
            card.aside, ui, "Find my masjid\u2026", command=self._open_masjid_picker,
            kind="primary",
        ).pack(side="left")
        w.Button(
            card.aside, ui, "Refresh", command=self._refresh_prayers_now,
        ).pack(side="left", padx=(ui.px(8), 0))
        self.var_prayer = tk.BooleanVar(value=bool(self.s.get("prayer_enabled", True)))
        self._switch_row(
            card, "Follow the masjid's prayer times", self.var_prayer, self._toggle_prayer,
        )
        self.prayer_lead_var = tk.IntVar(value=int(self.s.get("prayer_lead_minutes", 5)))
        self._slider_row(
            card, "Remind me before iqama", 0, 30, self.prayer_lead_var, _minutes_label,
            lambda v: self.s.__setitem__("prayer_lead_minutes", int(v)),
            caption="A reminder pops this long before the congregation stands.",
        )
        self.prayer_show_var = tk.IntVar(value=int(self.s.get("prayer_show_minutes", 60)))
        self._slider_row(
            card, "Show on the clock within", 0, 240, self.prayer_show_var, _minutes_label,
            self._set_prayer_window, step=5,
        )
        slot = self._control_row(
            card, "Masjid",
            "Or paste an address. Blank uses %s." % prayer_mod.SOURCE_NAME,
        )
        self.prayer_url_field = w.Field(
            slot, ui, width=30, initial=str(self.s.get("prayer_ics_url", "") or ""),
            placeholder="a masjid's website, or an iCal address",
        )
        self.prayer_url_field.pack(side="left")
        w.Button(
            slot, ui, "Apply", command=self._set_prayer_url,
            kind="quiet", padx=12, height=28,
        ).pack(side="left", padx=(ui.px(6), 0))
        self.prayer_url_field.entry.bind("<FocusOut>", self._set_prayer_url)
        self.prayer_url_field.entry.bind("<Return>", self._set_prayer_url)
        self._on_close_save(self._set_prayer_url)

        card = self._card(page, "Today")
        self.prayer_box = w.RowList(card.body, ui, empty="No times yet", meta_width=9)
        self.prayer_box.pack(fill="x", pady=(ui.px(6), 0))
        self.prayer_status_label = w.label(
            card.body, ui, "", 9, colour=ui.p.muted, wraplength=ui.px(520), justify="left",
        )
        self.prayer_status_label.pack(anchor="w", pady=(ui.px(8), 0))

        card = self._card(
            page, "Routine triggers",
            "No assistant lets you change a routine's time from outside, so the "
            "routine stops using a time at all. It starts from a plain web address "
            "instead, and the clock calls that address at the right moment every "
            "day -- so the times stay right all year.  "
            "Alexa: add a trigger skill (URL Routine Trigger is free), make one "
            "trigger per prayer, and set each routine's WHEN to its trigger. "
            "Google: a Home Assistant webhook or an IFTTT applet ends in the same "
            "kind of address -- or skip routines entirely and use the speaker card "
            "below, which needs no account at all.  "
            "One trigger is usually enough: if the same thing should happen at "
            "every prayer, paste its link below and press Same for all.",
        )
        w.Button(
            card.aside, ui, "Same for all", command=self._routine_same_for_all,
        ).pack(side="left")
        w.Button(
            card.aside, ui, "How to set this up", command=self._routine_help,
        ).pack(side="left", padx=(ui.px(8), 0))
        self.var_routines = tk.BooleanVar(
            value=bool(self.s.get("prayer_routines_enabled", False)))
        self._switch_row(
            card, "Call a trigger address at each prayer", self.var_routines,
            lambda: self._toggle("prayer_routines_enabled", self.var_routines),
        )
        self.routine_lead_var = tk.IntVar(
            value=int(self.s.get("prayer_routines_lead_minutes", 10)))
        self._slider_row(
            card, "Fire this long before iqama", 0, 60, self.routine_lead_var, _minutes_label,
            lambda v: self.s.__setitem__("prayer_routines_lead_minutes", int(v)),
            caption="The azan is called before the congregation stands, so a routine "
                    "usually wants to run a little ahead of the iqama time.",
        )
        hooks = self.s.get("prayer_routines_hooks") or {}
        self.routine_fields = {}
        for name in prayer_mod.DAILY:
            slot = self._control_row(card, name, ROUTINE_ROW_NOTES.get(name, ""))
            field = w.Field(slot, ui, width=26, initial=str(hooks.get(name) or ""),
                            placeholder="https://…")
            field.pack(side="left")
            self.routine_fields[name] = field
            field.entry.bind("<FocusOut>", lambda _e, n=name: self._set_routine_hook(n))
            field.entry.bind("<Return>", lambda _e, n=name: self._set_routine_hook(n))
            self._on_close_save(lambda n=name: self._set_routine_hook(n))
            w.Button(
                slot, ui, "Test", command=lambda n=name: self._test_routine(n),
                kind="quiet", padx=10, height=28,
            ).pack(side="left", padx=(ui.px(6), 0))
        w.label(
            card.body, ui, ALEXA_ORDER_NOTE, 9, colour=ui.p.muted,
            wraplength=ui.px(520), justify="left",
        ).pack(anchor="w", pady=(ui.px(10), 0))
        self.routine_status_label = w.label(
            card.body, ui, "", 9, colour=ui.p.muted, wraplength=ui.px(520), justify="left",
        )
        self.routine_status_label.pack(anchor="w", pady=(ui.px(8), 0))

        card = self._card(
            page, "Google or Nest speaker",
            "Google Home has no trigger address to point a routine at -- its "
            "automations start from a time, from the sun, or from a device -- so for "
            "Google the clock skips the routine and plays the adhan on the speaker "
            "itself, over your network. Nothing to link, no skill to enable, and the "
            "volume is set on the same connection as the audio, so it cannot play at "
            "last night's volume the way an Alexa routine can.  "
            "The audio is your own file or link: nothing is shipped with the clock. "
            "The PC has to be awake and on the same network as the speaker.",
        )
        w.Button(
            card.aside, ui, "Find speakers", command=self._find_speakers,
        ).pack(side="left")
        self.var_cast = tk.BooleanVar(value=bool(self.s.get("prayer_cast_enabled", False)))
        self._switch_row(
            card, "Play the adhan on a speaker at each prayer", self.var_cast,
            lambda: self._toggle("prayer_cast_enabled", self.var_cast),
        )
        slot = self._control_row(
            card, "Speaker",
            "The name as it appears in the Google Home app. A speaker group works too.",
        )
        self.cast_device_field = w.Field(
            slot, ui, width=26, initial=str(self.s.get("prayer_cast_device") or ""),
            placeholder="Kitchen speaker")
        self.cast_device_field.pack(side="left")
        self.cast_device_field.entry.bind("<FocusOut>", lambda _e: self._set_cast_device())
        self.cast_device_field.entry.bind("<Return>", lambda _e: self._set_cast_device())
        self._on_close_save(self._set_cast_device)

        self.cast_lead_var = tk.IntVar(value=int(self.s.get("prayer_cast_lead_minutes", 10)))
        self._slider_row(
            card, "Play this long before iqama", 0, 60, self.cast_lead_var, _minutes_label,
            lambda v: self.s.__setitem__("prayer_cast_lead_minutes", int(v)),
        )
        self.cast_volume_var = tk.IntVar(
            value=int(round(float(self.s.get("prayer_cast_volume", 0.6)) * 100)))
        self._slider_row(
            card, "Speaker volume", 0, 100, self.cast_volume_var, "%d%%",
            lambda v: self.s.__setitem__("prayer_cast_volume", int(v) / 100.0),
            caption="Set on the speaker as the adhan starts, and left there afterwards.",
        )

        slot = self._control_row(
            card, "Adhan", "Used for every prayer unless one below says otherwise.")
        self.cast_media_fields = {}
        field = w.Field(
            slot, ui, width=22,
            initial=str(self.s.get("prayer_cast_media_default") or ""),
            placeholder="a file, or https://…")
        field.pack(side="left")
        self.cast_media_fields[""] = field
        field.entry.bind("<FocusOut>", lambda _e: self._set_cast_media(""))
        field.entry.bind("<Return>", lambda _e: self._set_cast_media(""))
        self._on_close_save(lambda: self._set_cast_media(""))
        w.Button(
            slot, ui, "Choose…", command=lambda: self._choose_cast_file(""),
            kind="quiet", padx=10, height=28,
        ).pack(side="left", padx=(ui.px(6), 0))
        w.Button(
            slot, ui, "Test", command=lambda: self._test_cast(""),
            kind="quiet", padx=10, height=28,
        ).pack(side="left", padx=(ui.px(6), 0))

        media = self.s.get("prayer_cast_media") or {}
        for name in prayer_mod.DAILY:
            slot = self._control_row(card, name, CAST_ROW_NOTES.get(name, ""))
            field = w.Field(slot, ui, width=22, initial=str(media.get(name) or ""),
                            placeholder="same as above")
            field.pack(side="left")
            self.cast_media_fields[name] = field
            field.entry.bind("<FocusOut>", lambda _e, n=name: self._set_cast_media(n))
            field.entry.bind("<Return>", lambda _e, n=name: self._set_cast_media(n))
            self._on_close_save(lambda n=name: self._set_cast_media(n))
            w.Button(
                slot, ui, "Choose…", command=lambda n=name: self._choose_cast_file(n),
                kind="quiet", padx=10, height=28,
            ).pack(side="left", padx=(ui.px(6), 0))
            w.Button(
                slot, ui, "Test", command=lambda n=name: self._test_cast(n),
                kind="quiet", padx=10, height=28,
            ).pack(side="left", padx=(ui.px(6), 0))
        self.cast_status_label = w.label(
            card.body, ui, "", 9, colour=ui.p.muted, wraplength=ui.px(520), justify="left",
        )
        self.cast_status_label.pack(anchor="w", pady=(ui.px(8), 0))
        self._refresh_prayer_page()

    # --- finding a masjid --------------------------------------------------
    def _open_masjid_picker(self) -> None:
        """Search for a masjid by name or town and pick one from the list.

        The address box still works for anyone who has a link, but nobody
        should have to go and find one: this looks the masjid up, and knows
        which of the things it finds can actually supply times.
        """
        ui = self._ui
        p = ui.p
        px = ui.px
        parent = self._settings_win
        win = tk.Toplevel(parent)
        win.title("Find your masjid")
        win.configure(bg=p.window)
        win.transient(parent)
        win.attributes("-topmost", True)
        win.withdraw()
        win.update_idletasks()
        w32.set_titlebar(self._toplevel_hwnd(win), dark=p.is_dark,
                         colour=p.window, text=p.fg)

        frame = tk.Frame(win, bg=p.window)
        frame.pack(fill="both", expand=True, padx=px(24), pady=(px(18), px(20)))

        w.label(frame, ui, "Search by name or town", 10, "semibold").pack(anchor="w")
        w.label(
            frame, ui,
            "Masjids on mawaqit.net come with their congregation times. The "
            "rest are from a directory that ships with the clock, and their "
            "times depend on what their own website publishes.",
            9, colour=p.muted, wraplength=px(520), justify="left",
        ).pack(anchor="w", pady=(px(2), px(8)))

        row = tk.Frame(frame, bg=p.window)
        row.pack(fill="x")
        box = w.Field(row, ui, width=34, placeholder="Waterloo, or your masjid's name")
        box.pack(side="left", fill="x", expand=True)

        state: dict = {"rows": [], "busy": False}

        note = w.label(frame, ui, "", 9, colour=p.muted,
                       wraplength=px(520), justify="left")

        def say(text: str) -> None:
            if note.winfo_exists():
                note.configure(text=text)

        listing = w.RowList(frame, ui, empty="Nothing found yet", meta_width=0,
                            min_height=6)

        def show(found, trouble) -> None:
            state["busy"] = False
            state["rows"] = found
            listing.set_items([
                w.ListItem(
                    key=str(index),
                    primary=str(entry.get("name") or "")[:60],
                    secondary=masjids_mod.describe(entry),
                    status="mawaqit" if entry.get("slug") else "",
                    kind="accent" if entry.get("slug") else "muted",
                    dim=not masjids_mod.address_for(entry),
                )
                for index, entry in enumerate(found)
            ])
            if trouble:
                say("Found %d. (mawaqit.net: %s)" % (len(found), trouble))
            elif found:
                say("Found %d. Pick one, then press Use this masjid." % len(found))
            else:
                say("Nothing matched. Try the town instead of the masjid's name.")

        def look(_event=None) -> None:
            text = box.get().strip()
            if not text or state["busy"]:
                return
            state["busy"] = True
            say("Searching…")

            def work() -> None:
                try:
                    found, trouble = masjids_mod.search(text)
                except Exception as exc:        # a search must never crash
                    found, trouble = [], str(exc)[:90] or exc.__class__.__name__
                try:
                    self.root.after(0, show, found, trouble)
                except Exception:
                    state["busy"] = False

            threading.Thread(target=work, name="floating-clock-masjid-search",
                             daemon=True).start()

        def use() -> None:
            key = listing.selected_key
            if key is None:
                say("Pick one from the list first.")
                return
            entry = state["rows"][int(key)]
            address = masjids_mod.address_for(entry)
            if not address:
                say("%s has no website on record, so the clock has nowhere to "
                    "read its times from." % entry.get("name"))
                return
            field = getattr(self, "prayer_url_field", None)
            if field is not None and field.winfo_exists():
                field.set(address)
            self._set_prayer_url()
            win.destroy()

        box.entry.bind("<Return>", look)
        w.Button(row, ui, "Search", command=look, kind="primary",
                 padx=14).pack(side="left", padx=(px(8), 0))
        listing.pack(fill="both", expand=True, pady=(px(10), 0))
        listing.on_activate = lambda _key: use()
        note.pack(anchor="w", pady=(px(8), 0))

        buttons = tk.Frame(frame, bg=p.window)
        buttons.pack(fill="x", pady=(px(12), 0))
        w.Button(buttons, ui, "Use this masjid", command=use,
                 kind="primary", padx=16).pack(side="left")
        w.Button(buttons, ui, "Cancel", command=win.destroy,
                 kind="quiet", padx=16).pack(side="left", padx=(px(8), 0))

        win.update_idletasks()
        win.geometry("+%d+%d" % (
            parent.winfo_rootx() + max(0, (parent.winfo_width() - win.winfo_width()) // 2),
            parent.winfo_rooty() + px(60),
        ))
        if self.settings_visible:
            win.deiconify()
            box.entry.focus_set()
        say("Type a town or a masjid's name, then press Search.")

    # --- routine triggers --------------------------------------------------
    def _routine_same_for_all(self) -> None:
        """Copy the one address that is filled in to every prayer.

        The common case by far: the same thing should happen at each prayer --
        set the volume, play the azan -- which is one trigger and one routine,
        not five of each. Only a prayer that wants something different needs
        an address of its own.
        """
        for name in prayer_mod.DAILY:
            self._set_routine_hook(name)
        hooks = dict(self.s.get("prayer_routines_hooks") or {})
        url = next((hooks[name] for name in prayer_mod.DAILY if hooks.get(name)), "")
        if not url:
            self._routine_note("Paste a trigger link into one of the boxes first.")
            return
        for name in prayer_mod.DAILY:
            hooks[name] = url
            field = self.routine_fields.get(name)
            if field is not None and field.winfo_exists():
                field.set(url)
        self.s["prayer_routines_hooks"] = hooks
        cfg.save(self.s)
        self._routine_note("Every prayer now uses that one trigger.")

    def _routine_help(self) -> None:
        webbrowser.open("https://www.virtualsmarthome.xyz/url_routine_trigger/")

    def _routine_note(self, text: str) -> None:
        label = getattr(self, "routine_status_label", None)
        if label is not None and label.winfo_exists():
            label.configure(text=text)

    def _set_routine_hook(self, name: str) -> None:
        """Save one prayer's trigger link, and say so.

        Saved to disk the moment it is typed, like a calendar address: an
        upgrade stops the clock with taskkill, and a link retyped from a
        phone screen is not something to lose to that.
        """
        field = self.routine_fields.get(name)
        if field is None or not field.winfo_exists():
            return
        url = field.get().strip()
        problem = prayer_mod.check_hook(url)
        if problem:
            self._routine_note("%s: %s" % (name, problem))
            return
        hooks = dict(self.s.get("prayer_routines_hooks") or {})
        if url == str(hooks.get(name) or ""):
            return
        if url:
            hooks[name] = url
        else:
            hooks.pop(name, None)
        self.s["prayer_routines_hooks"] = hooks
        cfg.save(self.s)
        self._routine_note(
            "%s trigger saved." % name if url else "%s trigger cleared." % name)

    def _test_routine(self, name: str) -> None:
        """Fire one routine now, so the wiring can be proved without waiting
        for a prayer."""
        self._set_routine_hook(name)
        url = prayer_mod.hook_for(name, self.s.get("prayer_routines_hooks") or {})
        if not url:
            self._routine_note("Paste %s's trigger link first." % name)
            return
        fire = getattr(self, "fire_routine", None)
        if fire is None:
            return
        self._routine_note("Calling %s's trigger…" % name)
        fire(name, url)

    # --- the speaker -------------------------------------------------------
    def _cast_note(self, text: str) -> None:
        label = getattr(self, "cast_status_label", None)
        if label is not None and label.winfo_exists():
            label.configure(text=text)

    def _set_cast_device(self, _event=None) -> None:
        field = getattr(self, "cast_device_field", None)
        if field is None or not field.winfo_exists():
            return
        name = field.get().strip()
        if name == str(self.s.get("prayer_cast_device") or ""):
            return
        self.s["prayer_cast_device"] = name
        cfg.save(self.s)
        self._cast_note("Speaker saved." if name else "Speaker cleared.")

    def _find_speakers(self) -> None:
        """List what is on the network, so the name need not be typed blind.

        On a worker: discovery listens for several seconds, and the settings
        window must not freeze while it does.
        """
        problem = cast_mod.available()
        if problem:
            self._cast_note(problem)
            return
        self._cast_note("Looking for speakers…")

        def work() -> None:
            names, trouble = cast_mod.discover()
            def show() -> None:
                if trouble:
                    self._cast_note("Could not look: %s" % trouble)
                elif not names:
                    self._cast_note(
                        "No speakers answered. They have to be on the same network "
                        "as this PC, and switched on.")
                else:
                    field = getattr(self, "cast_device_field", None)
                    if len(names) == 1 and field is not None and field.winfo_exists() \
                            and not field.get().strip():
                        field.set(names[0])
                        self._set_cast_device()
                    self._cast_note("Found: %s." % ", ".join(names))
            try:
                self.root.after(0, show)
            except Exception:
                pass

        threading.Thread(target=work, name="floating-clock-cast-find",
                         daemon=True).start()

    def _cast_field(self, name: str):
        return (getattr(self, "cast_media_fields", None) or {}).get(name)

    def _set_cast_media(self, name: str) -> None:
        """Save one prayer's adhan -- or, for "", the one every prayer uses."""
        field = self._cast_field(name)
        if field is None or not field.winfo_exists():
            return
        media = field.get().strip()
        if media:
            problem = cast_mod.check_media(media)
            if problem:
                self._cast_note("%s: %s" % (name or "Adhan", problem))
                return
        if not name:
            if media == str(self.s.get("prayer_cast_media_default") or ""):
                return
            self.s["prayer_cast_media_default"] = media
        else:
            table = dict(self.s.get("prayer_cast_media") or {})
            if media == str(table.get(name) or ""):
                return
            if media:
                table[name] = media
            else:
                table.pop(name, None)
            self.s["prayer_cast_media"] = table
        cfg.save(self.s)
        self._cast_note(
            "%s adhan saved." % (name or "Default") if media
            else "%s adhan cleared." % (name or "Default"))

    def _choose_cast_file(self, name: str) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(
            parent=self._settings_win,
            title="Choose the adhan to play" if not name else "Choose %s's adhan" % name,
            filetypes=[("Audio", "*.mp3 *.m4a *.aac *.wav *.ogg *.flac"),
                       ("All files", "*.*")],
        )
        if not chosen:
            return
        field = self._cast_field(name)
        if field is not None and field.winfo_exists():
            field.set(chosen)
            self._set_cast_media(name)

    def _test_cast(self, name: str) -> None:
        """Play it now, so the speaker and the file can be proved together."""
        self._set_cast_media(name)
        self._set_cast_device()
        media = (prayer_mod.hook_for(name, routines_mod.media_table(self.s))
                 if name else str(self.s.get("prayer_cast_media_default") or "").strip())
        if not media:
            self._cast_note("Choose the audio first.")
            return
        if not str(self.s.get("prayer_cast_device") or "").strip():
            self._cast_note("Name the speaker first, or press Find speakers.")
            return
        fire = getattr(self, "fire_cast", None)
        if fire is None:
            return
        self._cast_note("Starting on %s…" % self.s["prayer_cast_device"])
        fire(name or "Adhan", media)

    def _toggle_prayer(self) -> None:
        self.s["prayer_enabled"] = bool(self.var_prayer.get())
        self.refresh_prayers(force=False)
        self._refresh_prayer_page()

    def _set_prayer_window(self, minutes) -> None:
        self.s["prayer_show_minutes"] = int(minutes)
        self._paint(force=True)

    def _set_prayer_url(self, _event=None) -> None:
        """Save the masjid address and read it now.

        Written to disk here rather than at close, like the trigger URLs: an
        address typed off a phone screen is not something to lose to an
        upgrade, and pressing Apply should visibly do something.
        """
        field = getattr(self, "prayer_url_field", None)
        if field is None or not field.winfo_exists():
            return
        url = field.get().strip()
        if url == str(self.s.get("prayer_ics_url", "") or ""):
            self._refresh_prayers_now()
            return
        self.s["prayer_ics_url"] = url
        cfg.save(self.s)
        label = getattr(self, "prayer_status_label", None)
        if label is not None and label.winfo_exists():
            label.configure(text="Reading %s…" % (url or prayer_mod.SOURCE_NAME))
        self.refresh_prayers()

    def _refresh_prayers_now(self) -> None:
        label = getattr(self, "prayer_status_label", None)
        if label is not None and label.winfo_exists():
            label.configure(text="Reading the calendar…")
        self.refresh_prayers()

    def _prayer_items(self, now: datetime) -> list:
        """Today's iqamas, with the next one called out."""
        prayers = list(getattr(self, "prayers", ()) or ())
        use_24h = bool(self.s["use_24h"])
        following = prayer_mod.next_prayer(prayers, now)
        items = []
        for item in prayer_mod.on_day(prayers, now):
            is_next = following is not None and item.key == following.key
            minutes = item.minutes_until(now)
            if minutes >= 0:
                caption = outlook.describe_gap(minutes)
            else:
                caption = outlook.describe_ago(-minutes)
            items.append(w.ListItem(
                key=item.key, meta=item.time_text(use_24h),
                primary="%s iqama" % item.name, secondary=caption,
                status="Next" if is_next else "", kind="accent" if is_next else "muted",
                dim=minutes < 0,
            ))
        return items

    def _refresh_prayer_page(self) -> None:
        box = getattr(self, "prayer_box", None)
        if box is None or not box.winfo_exists():
            return
        if not self.s.get("prayer_enabled", True):
            box.empty_text = "Switched off."
            box.set_items([])
        else:
            box.empty_text = "No times for today in that calendar."
            box.set_items(self._prayer_items(datetime.now()))
        label = getattr(self, "prayer_status_label", None)
        if label is not None and label.winfo_exists():
            status = getattr(self, "prayer_status", "")
            if not self.s.get("prayer_enabled", True):
                status = "Switch this on to read %s's iqamah calendar." % prayer_mod.SOURCE_NAME
            label.configure(text=status or "Reading the calendar…")
        status = getattr(getattr(self, "routines", None), "status", {}) or {}
        label = getattr(self, "routine_status_label", None)
        if label is not None and label.winfo_exists() and not label.cget("text"):
            self._routine_note(status.get(routines_mod.HOOK) or self._routine_summary())
        label = getattr(self, "cast_status_label", None)
        if label is not None and label.winfo_exists() and not label.cget("text"):
            self._cast_note(status.get(routines_mod.CAST) or self._cast_summary())

    def _routine_summary(self) -> str:
        """What the routines card says before anything has fired."""
        hooks = self.s.get("prayer_routines_hooks") or {}
        set_up = [name for name in prayer_mod.DAILY if hooks.get(name)]
        if not set_up:
            return "No triggers yet."
        if not self.s.get("prayer_routines_enabled", False):
            return "%d trigger(s) saved, switched off." % len(set_up)
        return "Ready: %s." % ", ".join(set_up)

    def _cast_summary(self) -> str:
        """What the speaker card says before anything has played."""
        problem = cast_mod.available()
        if problem:
            return problem
        device = str(self.s.get("prayer_cast_device") or "").strip()
        table = routines_mod.media_table(self.s)
        if not device:
            return "No speaker chosen."
        if not table:
            return "No adhan chosen."
        if not self.s.get("prayer_cast_enabled", False):
            return "%s is set up, switched off." % device
        return "Ready: %s on %s." % (", ".join(sorted(table)), device)

    def refresh_prayers(self, force: bool = True) -> None:
        """Hook for app.py (re-reads the masjid's calendar)."""

    def _toggle_calendar(self) -> None:
        self.s["calendar_enabled"] = bool(self.var_calendar.get())
        self.refresh_calendar()
        self._paint(force=True)
        self._refresh_agenda()

    # --- alarms page -------------------------------------------------------
    def _page_alarms(self, page: tk.Frame) -> None:
        ui = self._ui
        card = self._card(page, "New alarm")
        form = card.row(pady=6)
        self.alarm_time = w.Field(form, ui, width=8, placeholder="09:00", mono=True)
        self.alarm_time.pack(side="left")
        self.alarm_label = w.Field(form, ui, width=22, placeholder="Label")
        self.alarm_label.pack(side="left", padx=(ui.px(8), 0))
        w.Button(form, ui, "Add alarm", command=self._add_alarm, kind="primary").pack(side="right")
        self.alarm_time.entry.bind("<Return>", lambda _e: self._add_alarm())
        self.alarm_label.entry.bind("<Return>", lambda _e: self._add_alarm())

        days = tk.Frame(card.body, bg=ui.p.card)
        days.pack(fill="x", pady=(ui.px(2), ui.px(4)))
        self.alarm_days = []
        for index, name in enumerate(alerts.DAY_NAMES):
            var = tk.BooleanVar(value=index < 5)
            w.Chip(days, ui, name, var).pack(side="left", padx=(0, ui.px(6)))
            self.alarm_days.append(var)
        w.label(
            card.body, ui,
            "9, 9:05 pm and 21:05 all work. With no days chosen the alarm rings once, "
            "then switches itself off.",
            9, colour=ui.p.muted, wraplength=ui.px(500), justify="left",
        ).pack(anchor="w", pady=(ui.px(4), ui.px(4)))

        card = self._card(page, "Alarms")
        self._alarm_toggle = w.Button(card.aside, ui, "On / off", command=self._toggle_alarm)
        self._alarm_toggle.pack(side="left")
        self._alarm_remove = w.Button(
            card.aside, ui, "Remove", command=self._remove_alarm, kind="danger",
        )
        self._alarm_remove.pack(side="left", padx=(ui.px(6), 0))
        self.alarm_box = w.RowList(
            card.body, ui, on_activate=lambda _k: self._toggle_alarm(),
            on_select=lambda _k: self._sync_alarm_buttons(), empty="No alarms yet",
            meta_width=9,
        )
        self.alarm_box.pack(fill="x", pady=(ui.px(6), 0))
        self._refresh_alarms()

    def _refresh_alarms(self) -> None:
        box = getattr(self, "alarm_box", None)
        if box is None or not box.winfo_exists():
            return
        now = datetime.now()
        items = []
        for alarm in self.scheduler.alarms:
            when = alarm.next_occurrence(now)
            if when is None:
                secondary = alarm.repeat_text()
            else:
                gap = (when.date() - now.date()).days
                day = "today" if gap == 0 else (
                    "tomorrow" if gap == 1 else "on %s" % when.strftime("%A")
                )
                secondary = "%s  ·  rings %s" % (alarm.repeat_text(), day)
            items.append(w.ListItem(
                key=alarm.id, meta=alarm.time_text(self.s["use_24h"]), primary=alarm.label,
                secondary=secondary, status="On" if alarm.enabled else "Off",
                kind="success" if alarm.enabled else "muted", dim=not alarm.enabled,
            ))
        box.set_items(items)
        self._sync_alarm_buttons()

    def _sync_alarm_buttons(self) -> None:
        has = self._selected("alarm_box", self.scheduler.alarms) is not None
        for name in ("_alarm_toggle", "_alarm_remove"):
            button = getattr(self, name, None)
            if button is not None and button.winfo_exists():
                button.set_enabled(has)

    def _selected(self, box_name: str, items: list):
        box = getattr(self, box_name, None)
        if box is None or not box.winfo_exists() or not items:
            return None
        key = box.selected_key
        return next((item for item in items if item.id == key), None)

    def _add_alarm(self) -> None:
        parsed = parse_clock_time(self.alarm_time.get())
        if parsed is None:
            self.alarm_time.set("")
            self.alarm_time.entry.focus_set()
            return
        hour, minute = parsed
        alarm = self.scheduler.add_alarm(
            alerts.Alarm(
                hour=hour, minute=minute,
                label=self.alarm_label.get().strip() or "Alarm",
                days=[i for i, var in enumerate(self.alarm_days) if var.get()],
            )
        )
        self.alarm_label.clear()
        self.alarm_box.selected_key = alarm.id
        self._refresh_alarms()

    def _toggle_alarm(self) -> None:
        alarm = self._selected("alarm_box", self.scheduler.alarms)
        if alarm is not None:
            alarm.enabled = not alarm.enabled
            self.scheduler.save()
            self._refresh_alarms()

    def _remove_alarm(self) -> None:
        alarm = self._selected("alarm_box", self.scheduler.alarms)
        if alarm is not None:
            self.scheduler.remove_alarm(alarm.id)
            self._refresh_alarms()

    # --- timers page -------------------------------------------------------
    def _page_timers(self, page: tk.Frame) -> None:
        ui = self._ui
        card = self._card(page, "New timer")
        form = card.row(pady=6)
        self.timer_length = w.Field(form, ui, width=8, placeholder="5m", mono=True)
        self.timer_length.pack(side="left")
        self.timer_label = w.Field(form, ui, width=22, placeholder="Label")
        self.timer_label.pack(side="left", padx=(ui.px(8), 0))
        w.Button(form, ui, "Add & start", command=self._add_timer, kind="primary").pack(side="right")
        self.timer_length.entry.bind("<Return>", lambda _e: self._add_timer())
        self.timer_label.entry.bind("<Return>", lambda _e: self._add_timer())
        w.label(
            card.body, ui, "5m, 90s, 1h30m or 25:00 all work. A bare number means minutes.",
            9, colour=ui.p.muted,
        ).pack(anchor="w", pady=(0, ui.px(4)))

        card = self._card(page, "Timers")
        self._timer_toggle = w.Button(card.aside, ui, "Start / pause", command=self._toggle_timer)
        self._timer_toggle.pack(side="left")
        self._timer_reset = w.Button(card.aside, ui, "Reset", command=self._reset_timer)
        self._timer_reset.pack(side="left", padx=(ui.px(6), 0))
        self._timer_remove = w.Button(
            card.aside, ui, "Remove", command=self._remove_timer, kind="danger",
        )
        self._timer_remove.pack(side="left", padx=(ui.px(6), 0))
        self.timer_box = w.RowList(
            card.body, ui, on_activate=lambda _k: self._toggle_timer(),
            on_select=lambda _k: self._sync_timer_buttons(), empty="No timers yet",
            meta_width=8,
        )
        self.timer_box.pack(fill="x", pady=(ui.px(6), 0))

        card = self._card(page, "Stopwatch")
        self._stopwatch_toggle = w.Button(
            card.aside, ui, "Start / stop", command=self._toggle_stopwatch_ui, kind="primary",
        )
        self._stopwatch_toggle.pack(side="left")
        w.Button(card.aside, ui, "Reset", command=self._reset_stopwatch_ui).pack(
            side="left", padx=(ui.px(6), 0)
        )
        watch = card.row(pady=4)
        self.stopwatch_label = w.label(
            watch, ui, "0:00", 22, "semibold", colour=ui.p.accent_text,
        )
        self.stopwatch_label.configure(font=ui.font(22, mono=True))
        self.stopwatch_label.pack(side="left")
        self.stopwatch_state = w.label(watch, ui, "", 9, colour=ui.p.muted)
        self.stopwatch_state.pack(side="left", padx=(ui.px(12), 0), pady=(ui.px(10), 0))

        card = self._card(page, "On the clock")
        self._switch_row(
            card, "Show a running timer on the clock", self.var_show_timer,
            lambda: self._toggle("show_timer", self.var_show_timer, True),
            "The countdown appears under the time while a timer runs.",
        )
        self._refresh_timers()

    def _toggle_stopwatch_ui(self) -> None:
        self.toggle_stopwatch()
        self._refresh_timers()

    def _reset_stopwatch_ui(self) -> None:
        self.reset_stopwatch()
        self._refresh_timers()

    def _refresh_timers(self) -> None:
        box = getattr(self, "timer_box", None)
        if box is None or not box.winfo_exists():
            return
        now = datetime.now()
        items = []
        for timer in self.scheduler.timers:
            if timer.running:
                status, kind = "Running", "accent"
            elif timer.fired:
                status, kind = "Done", "success"
            else:
                status, kind = "Paused", "muted"
            items.append(w.ListItem(
                key=timer.id, meta=alerts.format_duration(timer.seconds_left(now)),
                primary=timer.label,
                secondary="of %s" % alerts.format_duration(timer.total_seconds),
                status=status, kind=kind, dim=timer.fired,
            ))
        box.set_items(items)
        self._sync_timer_buttons()
        watch = getattr(self, "stopwatch_label", None)
        stopwatch = getattr(self.scheduler, "stopwatch", None)
        if watch is not None and watch.winfo_exists() and stopwatch is not None:
            watch.configure(text=alerts.format_duration(stopwatch.elapsed(now)))
            self.stopwatch_state.configure(text="running" if stopwatch.running else "stopped")
            self._stopwatch_toggle.set_text("Stop" if stopwatch.running else "Start")

    def _sync_timer_buttons(self) -> None:
        has = self._selected("timer_box", self.scheduler.timers) is not None
        for name in ("_timer_toggle", "_timer_reset", "_timer_remove"):
            button = getattr(self, name, None)
            if button is not None and button.winfo_exists():
                button.set_enabled(has)

    def _add_timer(self) -> None:
        seconds = alerts.parse_duration(self.timer_length.get() or "5m")
        if not seconds:
            self.timer_length.set("")
            self.timer_length.entry.focus_set()
            return
        timer = alerts.Timer(
            total_seconds=seconds, label=self.timer_label.get().strip() or "Timer"
        )
        timer.start()
        self.scheduler.add_timer(timer)
        self.timer_label.clear()
        self.timer_box.selected_key = timer.id
        self._refresh_timers()
        self._paint(force=True)

    def _toggle_timer(self) -> None:
        timer = self._selected("timer_box", self.scheduler.timers)
        if timer is None:
            return
        timer.pause() if timer.running else timer.start()
        self.scheduler.save()
        self._refresh_timers()
        self._paint(force=True)

    def _reset_timer(self) -> None:
        timer = self._selected("timer_box", self.scheduler.timers)
        if timer is not None:
            timer.reset()
            self.scheduler.save()
            self._refresh_timers()
            self._paint(force=True)

    def _remove_timer(self) -> None:
        timer = self._selected("timer_box", self.scheduler.timers)
        if timer is not None:
            self.scheduler.remove_timer(timer.id)
            self._refresh_timers()
            self._paint(force=True)
