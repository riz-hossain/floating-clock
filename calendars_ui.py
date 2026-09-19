"""The Calendars page: one row per organisation, plus its mark and colour.

Mixed into SettingsUI rather than living in settings_ui.py, which is long
enough already. Built on the same widget kit and palette as the rest of the
dialog, so it follows light and dark mode and the clock theme's accent.
"""

from __future__ import annotations

import logging
import dataclasses
import os
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog

from PIL import Image, ImageTk

from . import orgs as orgs_mod, palette as pal, settings as cfg, vault, widgets as w, win32util as w32
from .calendar_wizard import CalendarWizard

HELP = (
    "Add a calendar with the email address it belongs to: iCloud, Zoho, Fastmail, "
    "Yahoo and most other providers connect with an app password; Google with the "
    "calendar's secret iCal address."
)


class CalendarsPage(CalendarWizard):
    """Mixin: SettingsUI gets _page_calendars from here."""

    # --- the page -----------------------------------------------------------
    def _page_calendars(self, page: tk.Frame) -> None:
        ui = self._ui
        card = self._card(
            page, "Outlook",
            "Reads the classic Outlook desktop app on this PC over COM. Nothing "
            "leaves the machine and no sign-in is needed.",
        )
        self._switch_row(card, "Read my Outlook calendar", self.var_calendar, self._toggle_calendar)

        # Two lists, kept apart on purpose: the calendars the clock reads, and
        # the companies it merely recognises in meetings. One is about where
        # meetings come from, the other about how rows look.
        card = self._card(page, "Connected calendars", HELP)
        w.Button(
            card.aside, ui, "Add a calendar", command=self._add_calendar, kind="primary",
        ).pack(side="left")
        self.cal_rows = tk.Frame(card.body, bg=ui.p.card)
        self.cal_rows.pack(fill="x", pady=(ui.px(6), 0))
        self.cal_status = w.label(card.body, ui, "", 9, colour=ui.p.muted)
        self.cal_status.pack(anchor="w", pady=(ui.px(4), 0))

        card = self._card(
            page, "Google sign-in (optional)",
            "Not needed if you added a Google calendar by its iCal link above. This "
            "is for a Sign in with Google button instead of the link, which needs "
            "the app registered once in the Google Cloud console (SIGN-IN-SETUP.md "
            "has the steps). Paste the client id and secret from that registration "
            "here and the Add-a-calendar window offers the sign-in.",
        )
        for key, caption in (("google_client_id", "Client ID"),
                             ("google_client_secret", "Client secret")):
            slot = self._control_row(card, caption)
            field = w.Field(slot, ui, width=38, initial=self.s.get(key, ""))
            field.pack()
            if key == "google_client_secret":
                field.entry.configure(show="•")
            save = lambda _e=None, k=key, f=field: self.s.__setitem__(k, f.get().strip())
            field.entry.bind("<FocusOut>", save)
            field.entry.bind("<Return>", save)
            self._on_close_save(save)

        card = self._card(
            page, "Company marks",
            "Every company seen in your meetings, with the logo from its website. "
            "Click a mark to change its colour; Icon picks an image of your own.",
        )
        w.Button(card.aside, ui, "Refresh marks", command=self._refresh_marks).pack(side="left")
        self.var_org_marks = tk.BooleanVar(value=self.s["show_org_marks"])
        self._switch_row(
            card, "Show each company's mark on meeting rows", self.var_org_marks,
            self._toggle_org_marks,
        )
        self.mark_rows = tk.Frame(card.body, bg=ui.p.card)
        self.mark_rows.pack(fill="x", pady=(ui.px(6), 0))
        self._render_calendars()

    def _orgs(self) -> list:
        return orgs_mod.from_config(self.s["calendars"])

    def _save_orgs(self, orgs: list) -> None:
        logging.getLogger(__name__).info(
            "Calendars saved: %d connected, %d company marks",
            sum(1 for o in orgs if o.reads_a_calendar),
            sum(1 for o in orgs if not o.reads_a_calendar),
        )
        self.s["calendars"] = orgs_mod.to_config(orgs)
        cfg.save(self.s)
        self._render_calendars()
        self.reload_orgs()
        self.refresh_calendar()

    def _badge(self, parent, org) -> tk.Canvas:
        """The org's fetched mark, or a coloured tile with its initials."""
        ui = self._ui
        size = ui.px(26)
        canvas = tk.Canvas(
            parent, width=size, height=size, bg=ui.p.card, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        image = None
        if org.icon and os.path.exists(org.icon):
            try:
                with Image.open(org.icon) as mark:
                    flat = Image.new("RGB", mark.size, ui.p.card)
                    flat.paste(mark.convert("RGBA"), mask=mark.convert("RGBA").split()[3])
                    image = ImageTk.PhotoImage(flat.resize((size, size), Image.LANCZOS))
            except Exception:
                image = None
        if image is None:
            colour = org.resolved_colour()
            image = ui.rounded(size, size, ui.px(7), colour, ui.p.card)
            canvas.create_image(0, 0, anchor="nw", image=image)
            canvas.create_text(
                size // 2, size // 2, text=org.initials, font=ui.font(8, "semibold"),
                fill=pal.text_on(colour),
            )
        else:
            canvas.create_image(0, 0, anchor="nw", image=image)
        self._cal_images.append(image)
        return canvas

    def _render_calendars(self) -> None:
        rows = getattr(self, "cal_rows", None)
        if rows is None or not rows.winfo_exists():
            return
        self._cal_images: list = []
        orgs = self._orgs()
        calendars = [(i, o) for i, o in enumerate(orgs) if o.reads_a_calendar]
        marks = [(i, o) for i, o in enumerate(orgs) if not o.reads_a_calendar]
        self._render_list(
            rows, calendars, "No other calendars yet; only Outlook is being read.",
            actions=("Edit", "Icon", "Remove"),
        )
        mark_rows = getattr(self, "mark_rows", None)
        if mark_rows is not None and mark_rows.winfo_exists():
            self._render_list(
                mark_rows, marks, "No companies recognised yet. They appear as meetings "
                "with other organisations come in.", actions=("Icon", "Remove"),
            )

    def _render_list(self, rows, entries, empty_text: str, actions) -> None:
        """One list of organisations, each with its mark, a caption and actions.
        `entries` are (index in the full list, org), so an action can find its
        organisation whichever list it sits in."""
        ui = self._ui
        p = ui.p
        for child in rows.winfo_children():
            child.destroy()
        if not entries:
            w.label(rows, ui, empty_text, 10, colour=p.muted).pack(anchor="w", pady=ui.px(10))
            return
        for position, (index, org) in enumerate(entries):
            if position:
                w.divider(rows, ui).pack(fill="x")
            row = tk.Frame(rows, bg=p.card)
            row.pack(fill="x", pady=ui.px(7))
            badge = self._badge(row, org)
            badge.pack(side="left", padx=(ui.px(2), ui.px(12)))
            badge.bind("<Button-1>", lambda _e, i=index: self._pick_colour(i))

            texts = tk.Frame(row, bg=p.card)
            texts.pack(side="left", fill="x", expand=True)
            w.label(texts, ui, org.name, colour=p.fg if org.enabled else p.disabled).pack(anchor="w")
            if org.kind == "google":
                caption = "%s  ·  Google sign-in" % (org.account or org.domain)
            elif org.kind == "caldav":
                caption = "%s  ·  CalDAV" % (org.account or org.domain)
            elif org.ics_url:
                caption = "%s  ·  iCal link" % (org.account or org.domain or "calendar")
            elif org.auto:
                caption = "%s  ·  seen in your meetings" % org.domain
            else:
                caption = org.domain or "no calendar"
            if not org.enabled:
                caption += "  ·  switched off"
            w.label(texts, ui, caption, 9, colour=p.muted).pack(anchor="w")

            handlers = {
                "Edit": (lambda i=index: self._edit_calendar(i), "quiet"),
                "Icon": (lambda i=index: self._pick_icon(i), "quiet"),
                "Remove": (lambda i=index: self._remove_calendar(i), "danger"),
            }
            action_row = tk.Frame(row, bg=p.card)
            action_row.pack(side="right")
            for text in actions:
                command, kind = handlers[text]
                w.Button(
                    action_row, ui, text, command=command, kind=kind, padx=10, height=28,
                ).pack(side="left", padx=(ui.px(2), 0))

    def _status(self, text: str) -> None:
        label = getattr(self, "cal_status", None)
        if label is not None and label.winfo_exists():
            label.configure(text=text)

    # --- actions ----------------------------------------------------------
    def _toggle_org_marks(self) -> None:
        self.s["show_org_marks"] = bool(self.var_org_marks.get())
        self.refresh_calendar()

    def _add_calendar(self) -> None:
        self._add_calendar_wizard()

    def _edit_calendar(self, index: int) -> None:
        self._calendar_dialog(index)

    def _remove_calendar(self, index: int) -> None:
        orgs = self._orgs()
        if 0 <= index < len(orgs):
            gone = orgs.pop(index)
            if gone.kind in ("caldav", "google"):
                vault.delete(gone.id)   # its password or sign-in goes with it
            self._save_orgs(orgs)

    def _pick_colour(self, index: int) -> None:
        orgs = self._orgs()
        if not (0 <= index < len(orgs)):
            return
        chosen = colorchooser.askcolor(
            color=orgs[index].resolved_colour(), title="Colour for %s" % orgs[index].name
        )
        if chosen and chosen[1]:
            orgs[index] = dataclasses.replace(orgs[index], colour=chosen[1])
            self._save_orgs(orgs)

    def _pick_icon(self, index: int) -> None:
        orgs = self._orgs()
        if not (0 <= index < len(orgs)):
            return
        path = filedialog.askopenfilename(
            title="Icon for %s" % orgs[index].name,
            filetypes=[("Images", "*.png *.ico *.jpg *.jpeg *.gif *.bmp *.webp")],
        )
        if not path:
            return
        # Normalise it the same way a fetched one is, so hand-picked and
        # downloaded marks render identically.
        try:
            with open(path, "rb") as handle:
                png = orgs_mod._to_png(handle.read())
            target = orgs_mod.icon_path(
                cfg.config_dir(), orgs[index].domain or orgs[index].id
            )
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(png)
        except Exception as exc:
            self._status("That image could not be used (%s)." % exc)
            return
        orgs[index] = dataclasses.replace(orgs[index], icon=target)
        self._save_orgs(orgs)
        self._status("Icon updated for %s." % orgs[index].name)

    def _refresh_marks(self) -> None:
        """Re-fetch every favicon, on a worker so a slow site cannot freeze us."""
        orgs = self._orgs()
        if not orgs:
            return
        self._status("Fetching marks…")

        def work():
            filled = []
            for org in orgs:
                icon = (
                    orgs_mod.fetch_icon(org.domain, cfg.config_dir(), force=True)
                    if org.domain else ""
                )
                colour = org.colour or (
                    orgs_mod.dominant_colour(icon) if icon else ""
                )
                filled.append(dataclasses.replace(org, icon=icon, colour=colour))
            self.root.after(0, done, filled)

        def done(filled):
            # Merged into the list as it is now, in case a calendar was
            # added or removed while the marks were being fetched.
            self._save_orgs(orgs_mod.merge_marks(self._orgs(), orgs, filled))
            got = sum(1 for org in filled if org.icon)
            self._status("Marks updated: %d of %d found." % (got, len(filled)))

        threading.Thread(target=work, name="floating-clock-marks", daemon=True).start()

    # --- the add/edit dialog ---------------------------------------------
    def _calendar_dialog(self, index) -> None:
        orgs = self._orgs()
        editing = index is not None and 0 <= index < len(orgs)
        org = orgs[index] if editing else orgs_mod.Org(id="", name="")
        ui = self._ui
        p = ui.p
        px = ui.px

        parent = self._settings_win if self._settings_open() else self.root
        win = tk.Toplevel(parent)
        win.title("Edit calendar" if editing else "Add a calendar")
        win.configure(bg=p.window)
        win.transient(parent)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.withdraw()
        win.update_idletasks()
        w32.set_titlebar(self._toplevel_hwnd(win), dark=p.is_dark, colour=p.window, text=p.fg)

        frame = tk.Frame(win, bg=p.window)
        frame.pack(fill="both", expand=True, padx=px(24), pady=(px(18), px(20)))

        fields = {}
        for key, caption, hint in (
            ("name", "Company", "e.g. ZeuZ"),
            ("domain", "Website", "zeuz.ai, used to find the icon"),
            ("ics_url", "Secret iCal address", "https://calendar.google.com/…"),
        ):
            w.label(frame, ui, caption, 10, "semibold").pack(
                anchor="w", pady=(px(10) if fields else 0, px(5))
            )
            field = w.Field(
                frame, ui, width=46, initial=getattr(org, key, ""), placeholder=hint,
            )
            field.pack(fill="x")
            fields[key] = field

        enabled = tk.BooleanVar(value=org.enabled)
        row = tk.Frame(frame, bg=p.window)
        row.pack(fill="x", pady=(px(16), 0))
        w.label(row, ui, "Read this calendar").pack(side="left")
        w.Switch(row, ui, enabled).pack(side="right")

        problem = w.label(
            frame, ui, "", 9, colour=p.danger, wraplength=px(400), justify="left",
        )
        problem.pack(anchor="w", pady=(px(8), 0))

        def save() -> None:
            name = fields["name"].get().strip()
            url = fields["ics_url"].get().strip()
            if not name:
                problem.configure(text="Give the company a name.")
                return
            if url and not url.lower().startswith(("http://", "https://")):
                problem.configure(text="The address must start with https://")
                return
            taken = {o.id for i, o in enumerate(orgs) if not (editing and i == index)}
            updated = dataclasses.replace(
                org,
                id=org.id or orgs_mod.make_id(name, taken),
                name=name,
                domain=orgs_mod.normalise_domain(fields["domain"].get()),
                ics_url=url,
                kind=org.kind if org.kind == "caldav" else ("ics" if url else ""),
                enabled=bool(enabled.get()),
            )
            if editing:
                orgs[index] = updated
            else:
                orgs.append(updated)
            self._save_orgs(orgs)
            win.destroy()
            if updated.domain and not updated.icon:
                self._refresh_marks()

        buttons = tk.Frame(frame, bg=p.window)
        buttons.pack(fill="x", pady=(px(14), 0))
        w.Button(buttons, ui, "Save", command=save, kind="primary", min_width=92).pack(side="right")
        w.Button(buttons, ui, "Cancel", command=win.destroy, kind="quiet").pack(
            side="right", padx=(0, px(8))
        )

        win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
        win.geometry("+%d+%d" % (max(0, x), max(0, y)))
        if self.settings_visible:
            win.deiconify()
        fields["name"].entry.focus_set()
        win.bind("<Return>", lambda _e: save())
        win.bind("<Escape>", lambda _e: win.destroy())
