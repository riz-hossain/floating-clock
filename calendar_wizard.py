"""Add a calendar from an email address.

Type the address, and the clock works out who hosts the calendar and asks
for exactly what that provider needs: an app password for a CalDAV host
(iCloud, Zoho, Fastmail, Yahoo, your own server), the secret iCal address
for Google until sign-in through Google exists, and nothing at all for a
Microsoft account already read through Outlook on this PC.
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser

import json

from . import (
    caldav, google_oauth, ics, orgs as orgs_mod, providers, vault, widgets as w,
    win32util as w32,
)

ADMIN_HELP = (
    "If you are the Workspace admin, you can allow it at admin.google.com/ac/appsettings/435070579839/sharing: click External sharing options for primary calendars, choose Share all information but outsiders cannot change calendars, and Save. The secret address then appears on the calendar page within a few minutes."
)
GOOGLE_HELP = (
    "The button opens this calendar's settings page in Google Calendar. Scroll all "
    "the way down: the last section, below Event labels, is \"Integrate calendar\", "
    "and its second box is the \"Secret address in iCal format\". Not the shareable "
    "link, and not the public address. Treat it like a password: anyone holding it "
    "can read the calendar. On a Workspace account the admin may have hidden it; "
    "then use Sign in with Google instead. " + ADMIN_HELP
)
MICROSOFT_HELP = (
    "If Outlook is installed on this PC its calendar is already being read. For "
    "an Outlook.com calendar you can also publish it (Settings → Calendar → Shared "
    "calendars → Publish a calendar) and paste the ICS link here."
)
UNKNOWN_HELP = (
    "Most calendar servers other than Google and Microsoft speak CalDAV. Enter "
    "the app password (or password) for this address and the clock will look "
    "for your calendars."
)


class CalendarWizard:
    """Mixin for CalendarsPage: the email-first 'Add a calendar' window."""

    def _add_calendar_wizard(self) -> None:
        ui = self._ui
        p = ui.p
        px = ui.px
        parent = self._settings_win if self._settings_open() else self.root

        win = tk.Toplevel(parent)
        win.title("Add a calendar")
        win.configure(bg=p.window)
        win.transient(parent)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.withdraw()
        win.update_idletasks()
        w32.set_titlebar(self._toplevel_hwnd(win), dark=p.is_dark, colour=p.window, text=p.fg)

        frame = tk.Frame(win, bg=p.window)
        frame.pack(fill="both", expand=True, padx=px(24), pady=(px(18), px(20)))
        state: dict = {"detection": None, "calendars": [], "password": ""}

        w.label(frame, ui, "Your email address for this calendar", 10, "semibold").pack(anchor="w")
        w.label(
            frame, ui, "The clock works out who hosts it and asks only for what that "
            "provider needs.", 9, colour=p.muted, wraplength=px(440), justify="left",
        ).pack(anchor="w", pady=(px(2), px(6)))
        email_row = tk.Frame(frame, bg=p.window)
        email_row.pack(fill="x")
        email = w.Field(email_row, ui, width=34, placeholder="you@company.com")
        email.pack(side="left", fill="x", expand=True)
        detect_button = w.Button(
            email_row, ui, "Continue", kind="primary", min_width=96,
        )
        detect_button.pack(side="left", padx=(px(8), 0))

        status = w.label(frame, ui, "", 9, colour=p.muted, wraplength=px(440), justify="left")
        status.pack(anchor="w", pady=(px(8), 0))
        problem = w.label(frame, ui, "", 9, colour=p.danger, wraplength=px(440), justify="left")
        problem.pack(anchor="w")
        body = tk.Frame(frame, bg=p.window)
        body.pack(fill="x")

        def say(text: str, bad: bool = False) -> None:
            (problem if bad else status).configure(text=text)
            (status if bad else problem).configure(text="")

        def clear_body() -> None:
            for child in body.winfo_children():
                child.destroy()

        def relayout() -> None:
            win.update_idletasks()
            if self.settings_visible:
                win.deiconify()

        # --- step 1: who hosts it ---------------------------------------
        def detect() -> None:
            address = email.get().strip()
            if "@" not in address or "." not in address.rsplit("@", 1)[-1]:
                say("That does not look like an email address.", bad=True)
                return
            say("Looking up %s…" % address.rsplit("@", 1)[-1])
            detect_button.configure(state="disabled") if hasattr(detect_button, "configure") else None

            def work():
                found = providers.detect(address)
                self.root.after(0, detected, found)

            threading.Thread(target=work, name="floating-clock-detect", daemon=True).start()

        def detected(found: providers.Detection) -> None:
            state["detection"] = found
            provider = found.provider
            where = provider.name
            if found.hosted:
                where += " (mail for %s is hosted there)" % found.domain
            say("%s: %s." % (found.email, where))
            clear_body()
            if provider.kind == "caldav" or provider.kind == "unknown":
                caldav_step(found)
            elif provider.kind == "google" and self.s.get("google_client_id"):
                google_step(found)
            elif provider.kind == "google":
                w.label(
                    body, ui, "Sign in with Google needs a one-time registration -- see "
                    "\"Google sign-in\" on the Calendars page. Until then, the secret "
                    "iCal address works.", 9, colour=p.muted, wraplength=px(440),
                    justify="left",
                ).pack(anchor="w", pady=(px(10), 0))
                ics_step(found, GOOGLE_HELP, "Secret iCal address")
            else:
                ics_step(found, MICROSOFT_HELP, "Published calendar (ICS) link, if any")
            relayout()

        # --- step 2a: CalDAV --------------------------------------------
        def caldav_step(found: providers.Detection) -> None:
            provider = found.provider
            w.label(body, ui, "App password", 10, "semibold").pack(anchor="w", pady=(px(12), px(2)))
            w.label(
                body, ui, provider.password_hint or UNKNOWN_HELP, 9, colour=p.muted,
                wraplength=px(440), justify="left",
            ).pack(anchor="w", pady=(0, px(6)))
            row = tk.Frame(body, bg=p.window)
            row.pack(fill="x")
            secret = w.Field(row, ui, width=34)
            secret.entry.configure(show="•")
            secret.pack(side="left", fill="x", expand=True)
            find = w.Button(row, ui, "Find calendars", kind="primary", min_width=120)
            find.pack(side="left", padx=(px(8), 0))
            picked = tk.Frame(body, bg=p.window)
            picked.pack(fill="x")

            def search() -> None:
                password = secret.get()
                if not password:
                    say("Enter the app password first.", bad=True)
                    return
                state["password"] = password
                say("Asking %s for your calendars…" % (provider.name if provider.kind != "unknown" else found.domain))

                def work():
                    try:
                        calendars = caldav.discover(found.email, password, provider.caldav_base)
                    except caldav.CalDavError as exc:
                        self.root.after(0, say, "Could not connect: %s" % exc, True)
                        return
                    self.root.after(0, show_calendars, calendars)

                threading.Thread(target=work, name="floating-clock-caldav", daemon=True).start()

            def show_calendars(calendars) -> None:
                state["calendars"] = calendars
                for child in picked.winfo_children():
                    child.destroy()
                say("Found %d calendar%s. Choose the ones to read." % (
                    len(calendars), "" if len(calendars) == 1 else "s"))
                choices = []
                for calendar in calendars:
                    line = tk.Frame(picked, bg=p.window)
                    line.pack(fill="x", pady=(px(6), 0))
                    var = tk.BooleanVar(value=True)
                    w.label(line, ui, calendar.name).pack(side="left")
                    w.Switch(line, ui, var).pack(side="right")
                    choices.append((calendar, var))
                buttons = tk.Frame(picked, bg=p.window)
                buttons.pack(fill="x", pady=(px(14), 0))
                w.Button(
                    buttons, ui, "Add", kind="primary", min_width=92,
                    command=lambda: add_caldav([c for c, v in choices if v.get()]),
                ).pack(side="right")
                w.Button(buttons, ui, "Cancel", command=win.destroy, kind="quiet").pack(
                    side="right", padx=(0, px(8)))
                relayout()

            def add_caldav(chosen) -> None:
                if not chosen:
                    say("Pick at least one calendar.", bad=True)
                    return
                current = self._orgs()
                taken = {org.id for org in current}
                for calendar in chosen:
                    org = orgs_mod.Org(
                        id=orgs_mod.make_id(calendar.name, taken), name=calendar.name,
                        domain=provider.domain or found.domain, colour=calendar.colour,
                        kind="caldav", account=found.email, caldav_url=calendar.url,
                    )
                    taken.add(org.id)
                    if not vault.store(org.id, found.email, state["password"]):
                        say("Windows would not store the password; nothing was added.", bad=True)
                        return
                    current.append(org)
                self._save_orgs(current)
                win.destroy()
                self._status("Added %d calendar%s from %s." % (
                    len(chosen), "" if len(chosen) == 1 else "s", found.email))
                self._refresh_marks()

            find.command = search
            secret.entry.bind("<Return>", lambda _e: search())
            secret.entry.focus_set()

        # --- step 2g: Sign in with Google -------------------------------
        def google_step(found: providers.Detection) -> None:
            provider = found.provider
            w.label(
                body, ui, "Google opens its own sign-in page in your browser; the clock "
                "only ever receives permission to read the calendars you choose.",
                9, colour=p.muted, wraplength=px(440), justify="left",
            ).pack(anchor="w", pady=(px(10), px(8)))
            go = w.Button(body, ui, "Sign in with Google", kind="primary", min_width=170)
            go.pack(anchor="w")
            picked = tk.Frame(body, bg=p.window)
            picked.pack(fill="x")

            def start() -> None:
                say("Waiting for the sign-in in your browser…")

                def work():
                    try:
                        token = google_oauth.sign_in(
                            self.s.get("google_client_id", ""),
                            self.s.get("google_client_secret", ""), login_hint=found.email,
                        )
                        calendars = google_oauth.list_calendars(token["access_token"])
                    except google_oauth.GoogleError as exc:
                        self.root.after(0, say, "Sign-in failed: %s" % exc, True)
                        return
                    self.root.after(0, show, token, calendars)

                threading.Thread(target=work, name="floating-clock-google", daemon=True).start()

            def show(token: dict, calendars) -> None:
                for child in picked.winfo_children():
                    child.destroy()
                who = token.get("email") or found.email
                say("Signed in as %s. Choose the calendars to read." % who)
                choices = []
                for calendar in calendars:
                    line = tk.Frame(picked, bg=p.window)
                    line.pack(fill="x", pady=(px(6), 0))
                    var = tk.BooleanVar(value=calendar.primary)
                    w.label(line, ui, calendar.name).pack(side="left")
                    w.Switch(line, ui, var).pack(side="right")
                    choices.append((calendar, var))
                buttons = tk.Frame(picked, bg=p.window)
                buttons.pack(fill="x", pady=(px(14), 0))
                w.Button(
                    buttons, ui, "Add", kind="primary", min_width=92,
                    command=lambda: add([c for c, v in choices if v.get()], token, who),
                ).pack(side="right")
                w.Button(buttons, ui, "Cancel", command=win.destroy, kind="quiet").pack(
                    side="right", padx=(0, px(8)))
                relayout()

            def add(chosen, token: dict, who: str) -> None:
                if not chosen:
                    say("Pick at least one calendar.", bad=True)
                    return
                current = self._orgs()
                taken = {org.id for org in current}
                for calendar in chosen:
                    org = orgs_mod.Org(
                        id=orgs_mod.make_id(calendar.name, taken), name=calendar.name,
                        domain=found.domain if found.hosted else provider.domain,
                        colour=calendar.colour, kind="google", account=who,
                        caldav_url=calendar.id,
                    )
                    taken.add(org.id)
                    if not vault.store(org.id, who, json.dumps(token)):
                        say("Windows would not store the sign-in; nothing was added.", bad=True)
                        return
                    current.append(org)
                self._save_orgs(current)
                win.destroy()
                self._status("Added %d Google calendar%s for %s." % (
                    len(chosen), "" if len(chosen) == 1 else "s", who))
                self._refresh_marks()

            go.command = start

        # --- step 2b: an ICS link ---------------------------------------
        def ics_step(found: providers.Detection, help_text: str, caption: str) -> None:
            provider = found.provider
            w.label(body, ui, help_text, 9, colour=p.muted, wraplength=px(440), justify="left").pack(
                anchor="w", pady=(px(10), px(8)))
            if provider.kind == "google":
                links = tk.Frame(body, bg=p.window)
                links.pack(anchor="w", pady=(0, px(10)))
                w.Button(
                    links, ui, "Open this calendar's settings in Google",
                    command=lambda: webbrowser.open(providers.google_settings_url(found.email)),
                ).pack(side="left")
                if found.hosted:
                    # A Workspace address: the admin may need to allow the
                    # secret address first, and this is where that lives.
                    w.Button(
                        links, ui, "Admin console: sharing settings", kind="quiet",
                        command=lambda: webbrowser.open(providers.ADMIN_SHARING_URL),
                    ).pack(side="left", padx=(px(8), 0))
            w.label(body, ui, caption, 10, "semibold").pack(anchor="w", pady=(0, px(4)))
            link = w.Field(body, ui, width=46, placeholder="https://…")
            link.pack(fill="x")
            w.label(body, ui, "Name", 10, "semibold").pack(anchor="w", pady=(px(10), px(4)))
            default = "%s (%s)" % (provider.name.split(" /")[0], found.email)
            name = w.Field(body, ui, width=46, initial=default)
            name.pack(fill="x")
            buttons = tk.Frame(body, bg=p.window)
            buttons.pack(fill="x", pady=(px(14), 0))

            def add() -> None:
                url = link.get().strip()
                if url or provider.kind == "google":
                    problem_text = ics.check_address(url)
                    if problem_text:
                        say(problem_text, bad=True)
                        return
                if url.lower().startswith("webcal://"):
                    url = "https://" + url[len("webcal://"):]
                if not url:
                    save(url)
                    return
                # Read it once before keeping it: a link that returns no
                # calendar would otherwise sit there failing every poll.
                say("Checking the address…")

                def work():
                    try:
                        ics.fetch(url)
                    except ics.IcsError as exc:
                        self.root.after(0, say, "That address does not work: %s" % exc, True)
                        return
                    self.root.after(0, save, url)

                threading.Thread(target=work, name="floating-clock-ics-check", daemon=True).start()

            def save(url: str) -> None:
                current = self._orgs()
                org = orgs_mod.Org(
                    id=orgs_mod.make_id(name.get().strip() or default, {o.id for o in current}),
                    name=name.get().strip() or default,
                    domain=found.domain if found.hosted or provider.kind == "unknown" else provider.domain,
                    ics_url=url, kind="ics" if url else "", account=found.email,
                )
                current.append(org)
                self._save_orgs(current)
                win.destroy()
                self._status("Added %s." % org.name)
                self._refresh_marks()

            w.Button(buttons, ui, "Add", kind="primary", min_width=92, command=add).pack(side="right")
            w.Button(buttons, ui, "Cancel", command=win.destroy, kind="quiet").pack(
                side="right", padx=(0, px(8)))
            link.entry.focus_set()

        detect_button.command = detect
        email.entry.bind("<Return>", lambda _e: detect())
        win.bind("<Escape>", lambda _e: win.destroy())

        win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
        win.geometry("+%d+%d" % (max(0, x), max(0, y)))
        if self.settings_visible:
            win.deiconify()
        email.entry.focus_set()
        self._wizard_win = win
        # For tests, which cannot press keys in a window that is never shown.
        self._wizard_email, self._wizard_detect = email, detect
