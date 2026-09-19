"""The settings window on Qt.

Plain Qt widgets, styled from the same palette module the Windows dialog
uses, so light and dark mode and the theme's accent carry over. Pages:
Clock, Behaviour, Meetings, Calendars (the email-first add flow), Alarms,
Timers.
"""

from __future__ import annotations

import dataclasses
import threading
from datetime import datetime

from PySide6 import QtCore, QtGui, QtWidgets

import json

from .. import (
    alerts, caldav, google_oauth, ics, orgs as orgs_mod, palette as pal, providers, render,
    themes, vault,
)
from ..settings_ui import parse_clock_time

PAGES = (("clock", "Clock"), ("behaviour", "Behaviour"), ("meetings", "Meetings"),
         ("calendars", "Calendars"), ("alarms", "Alarms"), ("timers", "Timers"))


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
            QtCore.QTimer.singleShot(0, lambda: self.detected(found))
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
                    QtCore.QTimer.singleShot(0, lambda: self.say("Sign-in failed: %s" % exc))
                    return
                QtCore.QTimer.singleShot(0, lambda: show(token, calendars))
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
                QtCore.QTimer.singleShot(0, lambda: self.say("Could not connect: %s" % exc))
                return
            QtCore.QTimer.singleShot(0, lambda: self._show_calendars(calendars))
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
                    QtCore.QTimer.singleShot(0, lambda: self.say("That address does not work: %s" % exc))
                    return
                QtCore.QTimer.singleShot(0, lambda: self._save_ics(found, url))
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
