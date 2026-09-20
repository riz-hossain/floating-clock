"""The settings window on Qt.

Plain Qt widgets, styled from the same palette module the Windows dialog
uses, so light and dark mode and the theme's accent carry over. Pages:
Clock, Behaviour, Meetings, Calendars (the email-first add flow), Prayer,
Alarms, Timers.
"""

from __future__ import annotations

import dataclasses
import threading
from datetime import datetime

from PySide6 import QtCore, QtGui, QtWidgets

import json

from .. import (
    alerts, caldav, cast as cast_mod, google_oauth, ics, orgs as orgs_mod,
    masjids as masjids_mod, palette as pal, prayer as prayer_mod, providers, render,
    routines as routines_mod, settings as cfg, themes, vault,
)
from ..timetext import parse_clock_time
from . import ui

PAGES = (("clock", "Clock"), ("behaviour", "Behaviour"), ("meetings", "Meetings"),
         ("calendars", "Calendars"), ("prayer", "Prayer"), ("alarms", "Alarms"),
         ("timers", "Timers"))

# Under each trigger box. Maghrib is the one real exception in the set: its
# azan follows the sun, and the masjid's iqama is a few minutes after sunset,
# so firing early against the iqama would call it before the sun had gone.
ROUTINE_ROW_NOTES = {
    "Dhuhr": "Friday's Jumuah uses this one too.",
    "Maghrib": "Best left empty. Maghrib is called at sunset, not at the iqama, so "
               "the assistant's own sunset trigger is the one to use for it.",
}
CAST_ROW_NOTES = {
    "Dhuhr": "Friday's Jumuah uses this one too.",
    "Maghrib": "Maghrib is called at sunset, a few minutes before the iqama here, so "
               "playing early against the iqama would call it before sunset.",
}


def stylesheet(p: pal.Palette) -> str:
    return """
    QDialog, QWidget#page { background: %(window)s; color: %(fg)s; }
    QLabel { color: %(fg)s; }
    QLabel#muted { color: %(muted)s; }
    QLabel#title { font-size: 18px; font-weight: 600; }
    QListWidget#nav { background: %(window)s; border: none; outline: 0; font-size: 13px; }
    QListWidget#nav::item { padding: 8px 12px; border-radius: 6px; }
    QListWidget#nav::item:selected { background: %(control)s; color: %(fg)s; }
    QGroupBox { border: 1px solid %(card_border)s; border-radius: 10px; margin-top: 14px;
                padding: 10px; background: %(card)s; }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: %(muted)s; }
    QLineEdit, QComboBox, QSpinBox { background: %(field)s; color: %(fg)s; border: 1px solid %(field_border)s;
                                     border-radius: 6px; padding: 5px 8px; }
    QPushButton { background: %(control)s; color: %(fg)s; border: none; border-radius: 14px;
                  padding: 6px 16px; }
    QPushButton#primary { background: %(accent)s; color: %(on_accent)s; }
    QPushButton#danger { color: %(danger)s; }
    QCheckBox { spacing: 8px; }
    QSlider::groove:horizontal { height: 4px; background: %(trough)s; border-radius: 2px; }
    QSlider::handle:horizontal { width: 16px; margin: -6px 0; border-radius: 8px; background: %(accent)s; }
    QListWidget { background: %(field)s; border: 1px solid %(field_border)s; border-radius: 6px; }
    """ % dict(window=p.window, fg=p.fg, muted=p.muted, control=p.control, card=p.card,
               card_border=p.card_border, field=p.field, field_border=p.field_border,
               accent=p.accent, on_accent=p.on_accent, danger=p.danger, trough=p.trough)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, clock) -> None:
        super().__init__(None)
        self.clock = clock
        self.s = clock.s
        self.setWindowTitle("Floating Clock")
        self.setMinimumSize(760, 540)
        mode = pal.effective_mode(self.s.get("ui_mode", "auto"))
        self.p = pal.resolve(mode, themes.get(self.s["theme"]).accent)
        self.setStyleSheet(stylesheet(self.p))

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.nav = QtWidgets.QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(170)
        for _key, title in PAGES:
            self.nav.addItem(title)
        self.nav.currentRowChanged.connect(self._show_row)
        layout.addWidget(self.nav)

        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack, 1)
        for key, title in PAGES:
            page = QtWidgets.QWidget()
            page.setObjectName("page")
            body = QtWidgets.QVBoxLayout(page)
            body.setContentsMargins(18, 8, 18, 8)
            heading = QtWidgets.QLabel(title)
            heading.setObjectName("title")
            body.addWidget(heading)
            getattr(self, "_page_" + key)(body)
            body.addStretch(1)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        self.nav.setCurrentRow(0)

    def closeEvent(self, event) -> None:
        """Write the settings out when the window closes.

        The Qt host otherwise only saves on quit, so a clock that was force
        quit -- or stopped by an upgrade -- lost whatever was changed here.
        """
        try:
            cfg.save(self.s)
        except Exception:
            pass
        super().closeEvent(event)

    def show_page(self, key: str) -> None:
        keys = [k for k, _t in PAGES]
        if key in keys:
            self.nav.setCurrentRow(keys.index(key))

    def _show_row(self, row: int) -> None:
        self.stack.setCurrentIndex(row)

    # --- helpers ---------------------------------------------------------------
    def _group(self, body, title: str, subtitle: str = "") -> QtWidgets.QVBoxLayout:
        box = QtWidgets.QGroupBox(title)
        inner = QtWidgets.QVBoxLayout(box)
        if subtitle:
            note = QtWidgets.QLabel(subtitle)
            note.setObjectName("muted")
            note.setWordWrap(True)
            inner.addWidget(note)
        body.addWidget(box)
        return inner

    def _check(self, layout, text: str, key: str, repaint: bool = True, after=None) -> None:
        box = QtWidgets.QCheckBox(text)
        box.setChecked(bool(self.s[key]))

        def changed(state) -> None:
            if bool(self.s[key]) != box.isChecked():
                self.clock.toggle_setting(key, repaint=repaint)
            if after:
                after()
        box.stateChanged.connect(changed)
        layout.addWidget(box)

    def _slider(self, layout, text: str, key: str, low, high, step, apply, integer=True) -> None:
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(text)
        value = QtWidgets.QLabel()
        value.setObjectName("muted")
        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        scale = 1 if integer else 100
        slider.setRange(int(low * scale), int(high * scale))
        slider.setSingleStep(int(step * scale))
        slider.setValue(int(float(self.s[key]) * scale))

        def show(v) -> None:
            value.setText(("%d" if integer else "%.2f") % (v / scale))
        show(slider.value())

        def moved(v) -> None:
            show(v)
            apply(v / scale)
        slider.valueChanged.connect(moved)
        row.addWidget(label)
        row.addWidget(slider, 1)
        row.addWidget(value)
        layout.addLayout(row)

    # --- pages -------------------------------------------------------------------
    def _page_clock(self, body) -> None:
        g = self._group(body, "Appearance")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Theme"))
        theme = QtWidgets.QComboBox()
        theme.addItems(list(themes.THEMES))
        theme.setCurrentText(self.s["theme"])
        theme.currentTextChanged.connect(self.clock.set_theme)
        row.addWidget(theme, 1)
        row.addWidget(QtWidgets.QLabel("Font"))
        font = QtWidgets.QComboBox()
        font.addItems(list(render.FONT_FILES))
        font.setCurrentText(self.s["font_family"])
        font.currentTextChanged.connect(self.clock.set_font_family)
        row.addWidget(font, 1)
        g.addLayout(row)
        self._slider(g, "Size", "font_size", 14, 200, 2, self.clock.set_font_size)
        self._slider(g, "Opacity", "opacity", 0.10, 1.0, 0.05, self.clock.set_opacity, integer=False)
        g = self._group(body, "Face")
        for text, key in (("24-hour clock", "use_24h"), ("Show seconds", "show_seconds"),
                          ("Show the date", "show_date"), ("Progress bar", "seconds_bar"),
                          ("Monospaced digits", "tabular_digits"), ("Compact view", "compact")):
            self._check(g, text, key)
        g = self._group(body, "Progress bar")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Shows"))
        mode = QtWidgets.QComboBox()
        modes = [("day", "My day, with meeting blocks"), ("meeting", "Countdown to the next meeting"),
                 ("seconds", "The passing minute")]
        for _k, label in modes:
            mode.addItem(label)
        mode.setCurrentIndex([k for k, _l in modes].index(self.s.get("bar_mode", "day")))
        mode.currentIndexChanged.connect(lambda i: (self.s.__setitem__("bar_mode", modes[i][0]),
                                                    self.clock.paint(force=True)))
        row.addWidget(mode, 1)
        g.addLayout(row)
        hours = QtWidgets.QHBoxLayout()
        for text, key in (("My day starts", "day_start_hour"), ("and work ends", "day_end_hour")):
            hours.addWidget(QtWidgets.QLabel(text))
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0, 23.75)
            spin.setSingleStep(0.5)
            spin.setValue(float(self.s[key]))
            spin.valueChanged.connect(lambda v, k=key: (self.s.__setitem__(k, float(v)),
                                                        self.clock.paint(force=True)))
            hours.addWidget(spin)
        g.addLayout(hours)

    def _page_behaviour(self, body) -> None:
        g = self._group(body, "Window")
        for text, key in (("Always on top", "topmost"), ("Brighten on hover", "hover_boost"),
                          ("Lock position", "lock_position")):
            self._check(g, text, key, repaint=False)
        g = self._group(body, "Peek", "Every so often the clock glides to the middle of the "
                        "screen, waits a moment, then slides back.")
        self._check(g, "Peek at the centre periodically", "peek_enabled", repaint=False)
        self._check(g, "Play a soft whoosh as it comes and goes", "peek_sound", repaint=False)
        self._slider(g, "Every (minutes)", "peek_interval_minutes", 1, 120, 1,
                     lambda v: (self.s.__setitem__("peek_interval_minutes", int(v)),
                                self.clock.peek.schedule()))
        self._slider(g, "Hold (seconds)", "peek_hold_seconds", 0.3, 10, 0.1,
                     lambda v: self.s.__setitem__("peek_hold_seconds", float(v)), integer=False)
        self._slider(g, "Zoom", "peek_zoom", 1.0, 2.0, 0.05,
                     lambda v: self.s.__setitem__("peek_zoom", float(v)), integer=False)
        button = QtWidgets.QPushButton("Peek now")
        button.clicked.connect(self.clock.peek.start)
        g.addWidget(button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

    def _page_meetings(self, body) -> None:
        g = self._group(body, "Meetings on the clock",
                        "Which calendars are read is set on the Calendars page.")
        self._check(g, "Show the next meetings under the time", "show_next_meeting")
        self._check(g, "Show each organisation's mark", "show_org_marks",
                    after=self.clock.refresh_calendar)
        self._check(g, "Give every company seen in a meeting a mark of its own", "auto_org_marks",
                    repaint=False)
        self._slider(g, "Remind me (minutes before)", "meeting_lead_minutes", 0, 60, 1,
                     lambda v: self.s.__setitem__("meeting_lead_minutes", int(v)))
        self._check(g, "Play a sound with reminders", "alerts_sound", repaint=False)

    def _page_calendars(self, body) -> None:
        g = self._group(body, "Reading")
        self._check(g, "Read the calendars below", "calendar_enabled")
        g = self._group(body, "Google sign-in", "Paste the client id and secret from the one-time "
                        "registration in the Google Cloud console (SIGN-IN-SETUP.md).")
        for key, caption in (("google_client_id", "Client ID"), ("google_client_secret", "Client secret")):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(caption))
            field = QtWidgets.QLineEdit(self.s.get(key, ""))
            if key == "google_client_secret":
                field.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            field.editingFinished.connect(lambda k=key, f=field: self.s.__setitem__(k, f.text().strip()))
            row.addWidget(field, 1)
            g.addLayout(row)
        g = self._group(body, "Calendars", "Add a calendar with the email address it belongs to. "
                        "iCloud, Zoho, Fastmail, Yahoo and most others connect with an app "
                        "password; Google with the calendar's secret iCal address.")
        self.cal_list = QtWidgets.QListWidget()
        g.addWidget(self.cal_list)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add a calendar")
        add.setObjectName("primary")
        add.clicked.connect(self._add_calendar)
        remove = QtWidgets.QPushButton("Remove")
        remove.setObjectName("danger")
        remove.clicked.connect(self._remove_calendar)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        g.addLayout(row)
        self.cal_status = QtWidgets.QLabel("")
        self.cal_status.setObjectName("muted")
        self.cal_status.setWordWrap(True)
        g.addWidget(self.cal_status)
        self._render_calendars()

    def _render_calendars(self) -> None:
        self.cal_list.clear()
        for org in orgs_mod.from_config(self.s["calendars"]):
            if org.kind == "google":
                how = "%s · Google sign-in" % (org.account or org.domain)
            elif org.kind == "caldav":
                how = "%s · CalDAV" % (org.account or org.domain)
            elif org.ics_url:
                how = "%s · iCal link" % (org.account or org.domain or "calendar")
            elif org.auto:
                how = "%s · seen in your meetings" % org.domain
            else:
                how = org.domain or "no calendar"
            item = QtWidgets.QListWidgetItem("%s   —   %s" % (org.name, how))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, org.id)
            self.cal_list.addItem(item)

    def _save_orgs(self, orgs: list) -> None:
        self.s["calendars"] = orgs_mod.to_config(orgs)
        self._render_calendars()
        self.clock.reload_orgs()
        self.clock.refresh_calendar()

    def _remove_calendar(self) -> None:
        item = self.cal_list.currentItem()
        if item is None:
            return
        wanted = item.data(QtCore.Qt.ItemDataRole.UserRole)
        orgs = orgs_mod.from_config(self.s["calendars"])
        keep = []
        for org in orgs:
            if org.id == wanted:
                if org.kind in ("caldav", "google"):
                    vault.delete(org.id)
                continue
            keep.append(org)
        self._save_orgs(keep)

    def _add_calendar(self) -> None:
        AddCalendarDialog(self).exec()

    # --- prayer ------------------------------------------------------------------
    def _page_prayer(self, body) -> None:
        g = self._group(
            body, "Iqama times",
            "Iqama is when the congregation stands, and every masjid sets its own. "
            "Paste your masjid's website and the clock reads its timetable straight "
            "off it; an iqamah iCal address works too. Leave it empty for %s, which "
            "is built in. The times are saved on this Mac, so they still show when "
            "you are offline." % prayer_mod.SOURCE_NAME)
        self._check(g, "Follow a masjid's iqama times", "prayer_enabled",
                    after=lambda: self.clock.refresh_prayers(force=False))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Masjid"))
        self.prayer_url = QtWidgets.QLineEdit(self.s.get("prayer_ics_url", ""))
        self.prayer_url.setPlaceholderText(
            "your masjid's website, or an iCal address")
        self.prayer_url.editingFinished.connect(self._set_prayer_url)
        row.addWidget(self.prayer_url, 1)
        apply_it = QtWidgets.QPushButton("Apply")
        apply_it.clicked.connect(self._apply_prayer_url)
        row.addWidget(apply_it)
        find = QtWidgets.QPushButton("Find my masjid\u2026")
        find.clicked.connect(self._open_masjid_picker)
        row.addWidget(find)
        g.addLayout(row)
        self._slider(g, "Remind me (minutes before)", "prayer_lead_minutes", 0, 60, 1,
                     lambda v: self.s.__setitem__("prayer_lead_minutes", int(v)))
        self._slider(g, "Show on the clock this long before", "prayer_show_minutes",
                     0, 720, 5, lambda v: self.s.__setitem__("prayer_show_minutes", int(v)))

        g = self._group(body, "Today")
        self.prayer_list = QtWidgets.QListWidget()
        g.addWidget(self.prayer_list)
        self.prayer_status = QtWidgets.QLabel("")
        self.prayer_status.setObjectName("muted")
        self.prayer_status.setWordWrap(True)
        g.addWidget(self.prayer_status)

        # --- trigger URLs ---
        g = self._group(
            body, "Routine triggers",
            "No assistant lets you change a routine's time from outside, so the "
            "routine stops using a time at all and starts from a plain web address "
            "instead. Alexa: add a trigger skill (URL Routine Trigger is free) and "
            "set each routine's WHEN to its trigger. Google: a Home Assistant "
            "webhook or an IFTTT applet ends in the same kind of address -- or skip "
            "routines and use the speaker below, which needs no account at all.")
        self._check(g, "Call a trigger address at each prayer", "prayer_routines_enabled",
                    repaint=False)
        self._slider(g, "Fire this long before iqama", "prayer_routines_lead_minutes",
                     0, 60, 1,
                     lambda v: self.s.__setitem__("prayer_routines_lead_minutes", int(v)))
        self.routine_fields = {}
        hooks = self.s.get("prayer_routines_hooks") or {}
        for name in prayer_mod.DAILY:
            row = QtWidgets.QHBoxLayout()
            row.addWidget(self._row_label(name, ROUTINE_ROW_NOTES.get(name, "")))
            field = QtWidgets.QLineEdit(str(hooks.get(name) or ""))
            field.setPlaceholderText("https://…")
            field.editingFinished.connect(lambda n=name: self._set_routine_hook(n))
            self.routine_fields[name] = field
            row.addWidget(field, 1)
            test = QtWidgets.QPushButton("Test")
            test.clicked.connect(lambda _c=False, n=name: self._test_routine(n))
            row.addWidget(test)
            g.addLayout(row)
        same = QtWidgets.QPushButton("Same for all")
        same.clicked.connect(self._routine_same_for_all)
        g.addWidget(same)
        self.routine_status = QtWidgets.QLabel("")
        self.routine_status.setObjectName("muted")
        self.routine_status.setWordWrap(True)
        g.addWidget(self.routine_status)

        # --- the speaker ---
        g = self._group(
            body, "Google or Nest speaker",
            "Google Home has no trigger address to point a routine at, so for Google "
            "the clock skips the routine and plays the adhan on the speaker itself, "
            "over your network. Nothing to link and no skill to enable. The audio is "
            "your own file or link: nothing is shipped with the clock. This Mac has "
            "to be awake and on the same network as the speaker.")
        self._check(g, "Play the adhan on a speaker at each prayer", "prayer_cast_enabled",
                    repaint=False)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Speaker"))
        self.cast_device = QtWidgets.QLineEdit(str(self.s.get("prayer_cast_device") or ""))
        self.cast_device.setPlaceholderText("the name shown in the Google Home app")
        self.cast_device.editingFinished.connect(self._set_cast_device)
        row.addWidget(self.cast_device, 1)
        find = QtWidgets.QPushButton("Find speakers")
        find.clicked.connect(self._find_speakers)
        row.addWidget(find)
        g.addLayout(row)
        self._slider(g, "Play this long before iqama", "prayer_cast_lead_minutes", 0, 60, 1,
                     lambda v: self.s.__setitem__("prayer_cast_lead_minutes", int(v)))
        self._slider(g, "Speaker volume", "prayer_cast_volume", 0.0, 1.0, 0.05,
                     lambda v: self.s.__setitem__("prayer_cast_volume", float(v)),
                     integer=False)

        self.cast_media_fields = {}
        for name, caption, note in (
            ("", "Adhan", "Used for every prayer unless one below says otherwise."),
        ) + tuple((n, n, CAST_ROW_NOTES.get(n, "")) for n in prayer_mod.DAILY):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(self._row_label(caption, note))
            initial = (str(self.s.get("prayer_cast_media_default") or "") if not name
                       else str((self.s.get("prayer_cast_media") or {}).get(name) or ""))
            field = QtWidgets.QLineEdit(initial)
            field.setPlaceholderText("a file, or https://…" if not name else "same as above")
            field.editingFinished.connect(lambda n=name: self._set_cast_media(n))
            self.cast_media_fields[name] = field
            row.addWidget(field, 1)
            choose = QtWidgets.QPushButton("Choose…")
            choose.clicked.connect(lambda _c=False, n=name: self._choose_cast_file(n))
            row.addWidget(choose)
            test = QtWidgets.QPushButton("Test")
            test.clicked.connect(lambda _c=False, n=name: self._test_cast(n))
            row.addWidget(test)
            g.addLayout(row)
        self.cast_status = QtWidgets.QLabel("")
        self.cast_status.setObjectName("muted")
        self.cast_status.setWordWrap(True)
        g.addWidget(self.cast_status)

        self.refresh_prayer_page()
        self.refresh_routine_status()

    def _row_label(self, text: str, note: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        if note:
            label.setToolTip(note)
        label.setMinimumWidth(70)
        return label

    def _set_prayer_url(self) -> None:
        url = self.prayer_url.text().strip()
        if url == str(self.s.get("prayer_ics_url") or ""):
            return
        self.s["prayer_ics_url"] = url
        cfg.save(self.s)
        self.prayer_status.setText("Reading %s…" % (url or prayer_mod.SOURCE_NAME))
        self.clock.refresh_prayers(force=True)

    def _open_masjid_picker(self) -> None:
        MasjidPicker(self).exec()

    def _apply_prayer_url(self) -> None:
        """Apply reads it again even when the address has not changed, so the
        button always visibly does something."""
        self._set_prayer_url()
        self.prayer_status.setText("Reading…")
        self.clock.refresh_prayers(force=True)

    def refresh_prayer_page(self) -> None:
        """Redraw today's times. Called when a load lands."""
        listing = getattr(self, "prayer_list", None)
        if listing is None:
            return
        listing.clear()
        now = datetime.now()
        if not self.s.get("prayer_enabled", True):
            self.prayer_status.setText("Switched off.")
            return
        today = prayer_mod.on_day(self.clock.prayers, now.date())
        for item in today:
            listing.addItem("%-8s %s" % (item.name, item.time_text(bool(self.s["use_24h"]))))
        if not today:
            listing.addItem("No times for today in that calendar.")
        self.prayer_status.setText(
            self.clock.prayer_status or "Reading the calendar…")

    def refresh_routine_status(self) -> None:
        """Show the last word from the Runner on both cards."""
        status = getattr(getattr(self.clock, "routines", None), "status", {}) or {}
        label = getattr(self, "routine_status", None)
        if label is not None:
            label.setText(status.get(routines_mod.HOOK) or self._routine_summary())
        label = getattr(self, "cast_status", None)
        if label is not None:
            label.setText(status.get(routines_mod.CAST) or self._cast_summary())

    def _routine_summary(self) -> str:
        hooks = self.s.get("prayer_routines_hooks") or {}
        set_up = [name for name in prayer_mod.DAILY if hooks.get(name)]
        if not set_up:
            return "No triggers yet."
        if not self.s.get("prayer_routines_enabled", False):
            return "%d trigger(s) saved, switched off." % len(set_up)
        return "Ready: %s." % ", ".join(set_up)

    def _cast_summary(self) -> str:
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

    # --- trigger URLs ------------------------------------------------------------
    def _set_routine_hook(self, name: str) -> None:
        field = self.routine_fields.get(name)
        if field is None:
            return
        url = field.text().strip()
        problem = prayer_mod.check_hook(url)
        if problem:
            self.routine_status.setText("%s: %s" % (name, problem))
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
        self.routine_status.setText(
            "%s trigger saved." % name if url else "%s trigger cleared." % name)

    def _routine_same_for_all(self) -> None:
        """Copy the one address that is filled in to every prayer."""
        for name in prayer_mod.DAILY:
            self._set_routine_hook(name)
        hooks = dict(self.s.get("prayer_routines_hooks") or {})
        url = next((hooks[name] for name in prayer_mod.DAILY if hooks.get(name)), "")
        if not url:
            self.routine_status.setText("Paste a trigger link into one of the boxes first.")
            return
        for name in prayer_mod.DAILY:
            hooks[name] = url
            field = self.routine_fields.get(name)
            if field is not None:
                field.setText(url)
        self.s["prayer_routines_hooks"] = hooks
        cfg.save(self.s)
        self.routine_status.setText("Every prayer now uses that one trigger.")

    def _test_routine(self, name: str) -> None:
        self._set_routine_hook(name)
        url = prayer_mod.hook_for(name, self.s.get("prayer_routines_hooks") or {})
        if not url:
            self.routine_status.setText("Paste %s's trigger link first." % name)
            return
        self.routine_status.setText("Calling %s's trigger…" % name)
        self.clock.routines.fire_hook(name, url)

    # --- the speaker -------------------------------------------------------------
    def _set_cast_device(self) -> None:
        name = self.cast_device.text().strip()
        if name == str(self.s.get("prayer_cast_device") or ""):
            return
        self.s["prayer_cast_device"] = name
        cfg.save(self.s)
        self.cast_status.setText("Speaker saved." if name else "Speaker cleared.")

    def _find_speakers(self) -> None:
        """List what is on the network, so the name need not be typed blind.

        On a worker: discovery listens for several seconds, and the settings
        window must not freeze while it does.
        """
        problem = cast_mod.available()
        if problem:
            self.cast_status.setText(problem)
            return
        self.cast_status.setText("Looking for speakers…")

        def work() -> None:
            names, trouble = cast_mod.discover()

            def show() -> None:
                if trouble:
                    self.cast_status.setText("Could not look: %s" % trouble)
                elif not names:
                    self.cast_status.setText(
                        "No speakers answered. They have to be on the same network "
                        "as this Mac, and switched on.")
                else:
                    if len(names) == 1 and not self.cast_device.text().strip():
                        self.cast_device.setText(names[0])
                        self._set_cast_device()
                    self.cast_status.setText("Found: %s." % ", ".join(names))
            ui.post(show)

        threading.Thread(target=work, name="floating-clock-cast-find",
                         daemon=True).start()

    def _set_cast_media(self, name: str) -> None:
        """Save one prayer's adhan -- or, for "", the one every prayer uses."""
        field = self.cast_media_fields.get(name)
        if field is None:
            return
        media = field.text().strip()
        if media:
            problem = cast_mod.check_media(media)
            if problem:
                self.cast_status.setText("%s: %s" % (name or "Adhan", problem))
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
        self.cast_status.setText(
            "%s adhan saved." % (name or "Default") if media
            else "%s adhan cleared." % (name or "Default"))

    def _choose_cast_file(self, name: str) -> None:
        chosen, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose the adhan to play" if not name else "Choose %s's adhan" % name,
            "", "Audio (*.mp3 *.m4a *.aac *.wav *.ogg *.flac);;All files (*)")
        if not chosen:
            return
        field = self.cast_media_fields.get(name)
        if field is not None:
            field.setText(chosen)
            self._set_cast_media(name)

    def _test_cast(self, name: str) -> None:
        self._set_cast_media(name)
        self._set_cast_device()
        media = (prayer_mod.hook_for(name, routines_mod.media_table(self.s))
                 if name else str(self.s.get("prayer_cast_media_default") or "").strip())
        if not media:
            self.cast_status.setText("Choose the audio first.")
            return
        if not str(self.s.get("prayer_cast_device") or "").strip():
            self.cast_status.setText("Name the speaker first, or press Find speakers.")
            return
        self.cast_status.setText("Starting on %s…" % self.s["prayer_cast_device"])
        self.clock.routines.fire_cast(name or "Adhan", media)

    def _page_alarms(self, body) -> None:
        g = self._group(body, "Alarms", "Type a time such as 7:30, 07:30 or 7:30 pm.")
        self.alarm_list = QtWidgets.QListWidget()
        g.addWidget(self.alarm_list)
        row = QtWidgets.QHBoxLayout()
        self.alarm_time = QtWidgets.QLineEdit()
        self.alarm_time.setPlaceholderText("7:30 am")
        self.alarm_label = QtWidgets.QLineEdit()
        self.alarm_label.setPlaceholderText("Label")
        add = QtWidgets.QPushButton("Add alarm")
        add.setObjectName("primary")
        add.clicked.connect(self._add_alarm)
        remove = QtWidgets.QPushButton("Remove")
        remove.setObjectName("danger")
        remove.clicked.connect(self._remove_alarm)
        for widget in (self.alarm_time, self.alarm_label, add, remove):
            row.addWidget(widget)
        g.addLayout(row)
        self._render_alarms()

    def _render_alarms(self) -> None:
        self.alarm_list.clear()
        for alarm in self.clock.scheduler.alarms:
            item = QtWidgets.QListWidgetItem("%s   %s%s" % (
                alarm.time_text(self.s["use_24h"]), alarm.label, "" if alarm.enabled else "  (off)"))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, alarm.id)
            self.alarm_list.addItem(item)

    def _add_alarm(self) -> None:
        parsed = parse_clock_time(self.alarm_time.text())
        if parsed is None:
            self.alarm_time.setStyleSheet("border: 1px solid %s" % self.p.danger)
            return
        hour, minute = parsed
        self.clock.scheduler.add_alarm(alerts.Alarm(
            hour=hour, minute=minute, label=self.alarm_label.text().strip() or "Alarm"))
        self.clock.scheduler.save()
        self.alarm_time.clear()
        self.alarm_label.clear()
        self._render_alarms()
        self.clock.paint(force=True)

    def _remove_alarm(self) -> None:
        item = self.alarm_list.currentItem()
        if item is not None:
            self.clock.scheduler.remove_alarm(item.data(QtCore.Qt.ItemDataRole.UserRole))
            self.clock.scheduler.save()
            self._render_alarms()
            self.clock.paint(force=True)

    def _page_timers(self, body) -> None:
        g = self._group(body, "Timers", "A length such as 25m, 1h30m or 90 (seconds).")
        self.timer_list = QtWidgets.QListWidget()
        g.addWidget(self.timer_list)
        row = QtWidgets.QHBoxLayout()
        self.timer_length = QtWidgets.QLineEdit()
        self.timer_length.setPlaceholderText("25m")
        self.timer_label = QtWidgets.QLineEdit()
        self.timer_label.setPlaceholderText("Label")
        add = QtWidgets.QPushButton("Start timer")
        add.setObjectName("primary")
        add.clicked.connect(self._add_timer)
        remove = QtWidgets.QPushButton("Remove")
        remove.setObjectName("danger")
        remove.clicked.connect(self._remove_timer)
        for widget in (self.timer_length, self.timer_label, add, remove):
            row.addWidget(widget)
        g.addLayout(row)
        g2 = self._group(body, "Stopwatch")
        row = QtWidgets.QHBoxLayout()
        self.stopwatch_label = QtWidgets.QLabel("0:00")
        start = QtWidgets.QPushButton("Start / stop")
        start.clicked.connect(lambda: (self.clock.activate("stopwatch"), self._tick()))
        reset = QtWidgets.QPushButton("Reset")
        reset.clicked.connect(lambda: (self.clock.scheduler.stopwatch.reset(),
                                       self.clock.scheduler.save(), self._tick()))
        row.addWidget(self.stopwatch_label)
        row.addWidget(start)
        row.addWidget(reset)
        row.addStretch(1)
        g2.addLayout(row)
        self._ticker = QtCore.QTimer(self)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start(1000)
        self._render_timers()

    def _render_timers(self) -> None:
        self.timer_list.clear()
        now = datetime.now()
        for timer in self.clock.scheduler.timers:
            item = QtWidgets.QListWidgetItem("%s   %s%s" % (
                alerts.format_duration(timer.seconds_left(now)), timer.label,
                "" if timer.running else "  (paused)"))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, timer.id)
            self.timer_list.addItem(item)

    def _tick(self) -> None:
        if not hasattr(self, "timer_list"):
            return
        self._render_timers()
        watch = self.clock.scheduler.stopwatch
        self.stopwatch_label.setText("%s%s" % (
            alerts.format_duration(watch.elapsed(datetime.now())), "" if watch.running else "  (stopped)"))

    def _add_timer(self) -> None:
        seconds = alerts.parse_duration(self.timer_length.text())
        if not seconds:
            self.timer_length.setStyleSheet("border: 1px solid %s" % self.p.danger)
            return
        timer = alerts.Timer(total_seconds=int(seconds), label=self.timer_label.text().strip() or "Timer")
        timer.start()
        self.clock.scheduler.add_timer(timer)
        self.clock.scheduler.save()
        self.timer_length.clear()
        self.timer_label.clear()
        self._render_timers()
        self.clock.paint(force=True)

    def _remove_timer(self) -> None:
        item = self.timer_list.currentItem()
        if item is not None:
            self.clock.scheduler.remove_timer(item.data(QtCore.Qt.ItemDataRole.UserRole))
            self.clock.scheduler.save()
            self._render_timers()
            self.clock.paint(force=True)


class MasjidPicker(QtWidgets.QDialog):
    """Search for a masjid by name or town and pick one from the list.

    The address box still works for anyone who has a link, but nobody should
    have to go and find one: this looks the masjid up, and knows which of the
    things it finds can actually supply times.
    """

    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.owner = owner
        self.rows: list = []
        self.busy = False
        self.setWindowTitle("Find your masjid")
        self.setMinimumSize(560, 460)
        self.setStyleSheet(owner.styleSheet())

        body = QtWidgets.QVBoxLayout(self)
        body.setContentsMargins(18, 16, 18, 16)

        heading = QtWidgets.QLabel("Search by name or town")
        heading.setObjectName("title")
        body.addWidget(heading)

        note = QtWidgets.QLabel(
            "Masjids on mawaqit.net come with their congregation times. The "
            "rest are from a directory that ships with the clock, and their "
            "times depend on what their own website publishes.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        body.addWidget(note)

        row = QtWidgets.QHBoxLayout()
        self.box = QtWidgets.QLineEdit()
        self.box.setPlaceholderText("Waterloo, or your masjid's name")
        self.box.returnPressed.connect(self.look)
        row.addWidget(self.box, 1)
        search = QtWidgets.QPushButton("Search")
        search.setObjectName("primary")
        search.clicked.connect(self.look)
        row.addWidget(search)
        body.addLayout(row)

        self.listing = QtWidgets.QListWidget()
        self.listing.itemDoubleClicked.connect(lambda _item: self.use())
        body.addWidget(self.listing, 1)

        self.status = QtWidgets.QLabel(
            "Type a town or a masjid's name, then press Search.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        body.addWidget(self.status)

        buttons = QtWidgets.QHBoxLayout()
        use = QtWidgets.QPushButton("Use this masjid")
        use.setObjectName("primary")
        use.clicked.connect(self.use)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(use)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        body.addLayout(buttons)

    def look(self) -> None:
        text = self.box.text().strip()
        if not text or self.busy:
            return
        self.busy = True
        self.status.setText("Searching…")

        def work() -> None:
            try:
                found, trouble = masjids_mod.search(text)
            except Exception as exc:        # a search must never crash
                found, trouble = [], str(exc)[:90] or exc.__class__.__name__
            ui.post(lambda: self.show_results(found, trouble))

        threading.Thread(target=work, name="floating-clock-masjid-search",
                         daemon=True).start()

    def show_results(self, found, trouble: str) -> None:
        self.busy = False
        self.rows = found
        self.listing.clear()
        for entry in found:
            label = "%s\n    %s" % (entry.get("name") or "",
                                    masjids_mod.describe(entry))
            item = QtWidgets.QListWidgetItem(label)
            if not masjids_mod.address_for(entry):
                # Nothing to read times from, so it is shown but not inviting.
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.listing.addItem(item)
        if trouble:
            self.status.setText("Found %d. (mawaqit.net: %s)" % (len(found), trouble))
        elif found:
            self.status.setText(
                "Found %d. Pick one, then press Use this masjid." % len(found))
        else:
            self.status.setText(
                "Nothing matched. Try the town instead of the masjid's name.")

    def use(self) -> None:
        index = self.listing.currentRow()
        if index < 0 or index >= len(self.rows):
            self.status.setText("Pick one from the list first.")
            return
        if self.busy:
            return
        entry = self.rows[index]
        address = masjids_mod.address_for(entry)
        name = str(entry.get("name") or "that masjid")
        if not address:
            self.status.setText("%s publishes no times the clock can read." % name)
            return
        # Prove it before saving it. Most masjid websites publish nothing the
        # clock can read, and saving one of those would replace times that
        # work with none at all.
        self.busy = True
        self.status.setText("Checking %s…" % name)

        def work() -> None:
            try:
                prayers, status = masjids_mod.verify(address)
            except Exception as exc:            # a check must never crash
                prayers, status = [], str(exc)[:90] or exc.__class__.__name__
            ui.post(lambda: self.verified(address, name, prayers, status))

        threading.Thread(target=work, name="floating-clock-masjid-verify",
                         daemon=True).start()

    def verified(self, address: str, name: str, prayers, status: str) -> None:
        self.busy = False
        if not prayers:
            self.status.setText("%s: %s  Nothing was changed." % (
                name, status.replace("Could not read the prayer times: ", "")))
            return
        self.owner.prayer_url.setText(address)
        self.owner._set_prayer_url()
        self.accept()


class AddCalendarDialog(QtWidgets.QDialog):
    """Email first, then whatever that provider needs."""

    def __init__(self, owner: SettingsDialog) -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Add a calendar")
        self.setStyleSheet(owner.styleSheet())
        self.setMinimumWidth(480)
        self.detection: providers.Detection | None = None
        self.calendars: list = []
        self.password = ""
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Your email address for this calendar"))
        row = QtWidgets.QHBoxLayout()
        self.email = QtWidgets.QLineEdit()
        self.email.setPlaceholderText("you@company.com")
        go = QtWidgets.QPushButton("Continue")
        go.setObjectName("primary")
        go.clicked.connect(self.detect)
        row.addWidget(self.email, 1)
        row.addWidget(go)
        layout.addLayout(row)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.body = QtWidgets.QVBoxLayout()
        layout.addLayout(self.body)
        self.email.returnPressed.connect(self.detect)

    def say(self, text: str) -> None:
        self.status.setText(text)

    def _clear(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def detect(self) -> None:
        address = self.email.text().strip()
        if "@" not in address:
            self.say("That does not look like an email address.")
            return
        self.say("Looking up %s…" % address.rsplit("@", 1)[-1])

        def work():
            found = providers.detect(address)
            ui.post(lambda: self.detected(found))
        threading.Thread(target=work, daemon=True).start()

    def detected(self, found: providers.Detection) -> None:
        self.detection = found
        provider = found.provider
        where = provider.name + (" (mail for %s is hosted there)" % found.domain if found.hosted else "")
        self.say("%s: %s." % (found.email, where))
        self._clear()
        if provider.kind in ("caldav", "unknown"):
            self._caldav_step(found)
        elif provider.kind == "google" and self.owner.s.get("google_client_id"):
            self._google_step(found)
        else:
            self._ics_step(found)

    def _google_step(self, found) -> None:
        note = QtWidgets.QLabel("Google opens its own sign-in page in your browser; the clock "
                                "only receives permission to read the calendars you choose.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.body.addWidget(note)
        go = QtWidgets.QPushButton("Sign in with Google")
        go.setObjectName("primary")
        self.body.addWidget(go, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        self.choices = QtWidgets.QListWidget()
        self.body.addWidget(self.choices)
        add = QtWidgets.QPushButton("Add")
        add.setObjectName("primary")
        self.body.addWidget(add, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        state = {"token": None}

        def start() -> None:
            self.say("Waiting for the sign-in in your browser…")

            def work():
                try:
                    token = google_oauth.sign_in(
                        self.owner.s.get("google_client_id", ""),
                        self.owner.s.get("google_client_secret", ""), login_hint=found.email)
                    calendars = google_oauth.list_calendars(token["access_token"])
                except google_oauth.GoogleError as exc:
                    ui.post(lambda: self.say("Sign-in failed: %s" % exc))
                    return
                ui.post(lambda: show(token, calendars))
            threading.Thread(target=work, daemon=True).start()

        def show(token, calendars) -> None:
            state["token"] = token
            self.calendars = calendars
            self.choices.clear()
            for calendar in calendars:
                item = QtWidgets.QListWidgetItem(calendar.name)
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.CheckState.Checked if calendar.primary
                                   else QtCore.Qt.CheckState.Unchecked)
                self.choices.addItem(item)
            self.say("Signed in as %s. Tick the calendars to read." % (token.get("email") or found.email))

        def finish() -> None:
            token = state["token"]
            if token is None:
                self.say("Sign in first.")
                return
            chosen = [c for i, c in enumerate(self.calendars)
                      if self.choices.item(i).checkState() == QtCore.Qt.CheckState.Checked]
            if not chosen:
                self.say("Tick at least one calendar.")
                return
            who = token.get("email") or found.email
            current = orgs_mod.from_config(self.owner.s["calendars"])
            taken = {o.id for o in current}
            for calendar in chosen:
                org = orgs_mod.Org(id=orgs_mod.make_id(calendar.name, taken), name=calendar.name,
                                   domain=found.domain if found.hosted else found.provider.domain,
                                   colour=calendar.colour, kind="google", account=who,
                                   caldav_url=calendar.id)
                taken.add(org.id)
                if not vault.store(org.id, who, json.dumps(token)):
                    self.say("The keychain would not store the sign-in; nothing was added.")
                    return
                current.append(org)
            self.owner._save_orgs(current)
            self.accept()

        go.clicked.connect(start)
        add.clicked.connect(finish)

    def _caldav_step(self, found) -> None:
        provider = found.provider
        hint = QtWidgets.QLabel(provider.password_hint)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        self.secret = QtWidgets.QLineEdit()
        self.secret.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("App password")
        find = QtWidgets.QPushButton("Find calendars")
        find.setObjectName("primary")
        find.clicked.connect(lambda: self._search(found))
        row.addWidget(self.secret, 1)
        row.addWidget(find)
        self.body.addLayout(row)
        self.choices = QtWidgets.QListWidget()
        self.body.addWidget(self.choices)
        add = QtWidgets.QPushButton("Add")
        add.setObjectName("primary")
        add.clicked.connect(lambda: self._add_caldav(found))
        self.body.addWidget(add, 0, QtCore.Qt.AlignmentFlag.AlignRight)

    def _search(self, found) -> None:
        password = self.secret.text()
        if not password:
            self.say("Enter the app password first.")
            return
        self.password = password
        self.say("Asking for your calendars…")

        def work():
            try:
                calendars = caldav.discover(found.email, password, found.provider.caldav_base)
            except caldav.CalDavError as exc:
                ui.post(lambda: self.say("Could not connect: %s" % exc))
                return
            ui.post(lambda: self._show_calendars(calendars))
        threading.Thread(target=work, daemon=True).start()

    def _show_calendars(self, calendars) -> None:
        self.calendars = calendars
        self.choices.clear()
        for calendar in calendars:
            item = QtWidgets.QListWidgetItem(calendar.name)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Checked)
            self.choices.addItem(item)
        self.say("Found %d calendar%s. Untick any you do not want." % (
            len(calendars), "" if len(calendars) == 1 else "s"))

    def _add_caldav(self, found) -> None:
        chosen = [c for i, c in enumerate(self.calendars)
                  if self.choices.item(i).checkState() == QtCore.Qt.CheckState.Checked]
        if not chosen:
            self.say("Find the calendars first, then pick at least one.")
            return
        current = orgs_mod.from_config(self.owner.s["calendars"])
        taken = {o.id for o in current}
        for calendar in chosen:
            org = orgs_mod.Org(id=orgs_mod.make_id(calendar.name, taken), name=calendar.name,
                               domain=found.provider.domain or found.domain, colour=calendar.colour,
                               kind="caldav", account=found.email, caldav_url=calendar.url)
            taken.add(org.id)
            if not vault.store(org.id, found.email, self.password):
                self.say("The keychain would not store the password; nothing was added.")
                return
            current.append(org)
        self.owner._save_orgs(current)
        self.accept()

    def _ics_step(self, found) -> None:
        provider = found.provider
        hint = QtWidgets.QLabel(provider.password_hint)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        if provider.kind == "google":
            import webbrowser

            open_button = QtWidgets.QPushButton("Open this calendar's settings in Google")
            open_button.clicked.connect(
                lambda: webbrowser.open(providers.google_settings_url(found.email)))
            self.body.addWidget(open_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
            if found.hosted:
                admin_button = QtWidgets.QPushButton("Admin console: sharing settings")
                admin_button.clicked.connect(lambda: webbrowser.open(providers.ADMIN_SHARING_URL))
                self.body.addWidget(admin_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        self.link = QtWidgets.QLineEdit()
        self.link.setPlaceholderText("https://…  (secret iCal address)")
        self.body.addWidget(self.link)
        self.name = QtWidgets.QLineEdit("%s (%s)" % (provider.name.split(" /")[0], found.email))
        self.body.addWidget(self.name)
        add = QtWidgets.QPushButton("Add")
        add.setObjectName("primary")
        add.clicked.connect(lambda: self._add_ics(found))
        self.body.addWidget(add, 0, QtCore.Qt.AlignmentFlag.AlignRight)

    def _add_ics(self, found) -> None:
        url = self.link.text().strip()
        if url or found.provider.kind == "google":
            problem = ics.check_address(url)
            if problem:
                self.say(problem)
                return
        if url.lower().startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if url:
            self.say("Checking the address…")

            def work():
                try:
                    ics.fetch(url)
                except ics.IcsError as exc:
                    ui.post(lambda: self.say("That address does not work: %s" % exc))
                    return
                ui.post(lambda: self._save_ics(found, url))
            threading.Thread(target=work, daemon=True).start()
            return
        self._save_ics(found, url)

    def _save_ics(self, found, url: str) -> None:
        current = orgs_mod.from_config(self.owner.s["calendars"])
        name = self.name.text().strip() or found.email
        org = orgs_mod.Org(id=orgs_mod.make_id(name, {o.id for o in current}), name=name,
                           domain=found.domain if found.hosted or found.provider.kind == "unknown"
                           else found.provider.domain,
                           ics_url=url, kind="ics" if url else "", account=found.email)
        current.append(org)
        self.owner._save_orgs(current)
        self.accept()
