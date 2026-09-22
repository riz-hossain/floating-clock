"""The right-click and tray menu on Qt, in the same shape as on Windows."""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets

from .. import render, themes


def _action(menu: QtWidgets.QMenu, label: str, handler, checked=None, shortcut: str = "",
            enabled: bool = True) -> QtGui.QAction:
    action = menu.addAction(label)
    if checked is not None:
        action.setCheckable(True)
        action.setChecked(bool(checked))
    if shortcut:
        action.setShortcut(QtGui.QKeySequence(shortcut))
        action.setShortcutVisibleInContextMenu(True)
    action.setEnabled(enabled)
    action.triggered.connect(lambda *_a: handler())
    return action


def build_menu(clock, persistent: bool = False) -> QtWidgets.QMenu:
    """A short top level, then one submenu per topic. Built fresh so every
    tick reflects the settings as they stand; a persistent tray menu is
    rebuilt each time it is about to show."""
    s = clock.s
    menu = QtWidgets.QMenu()
    if persistent:
        menu.aboutToShow.connect(lambda: _fill(menu, clock))
    else:
        _fill(menu, clock)
    return menu


def _fill(menu: QtWidgets.QMenu, clock) -> None:
    menu.clear()
    s = clock.s
    # Stopping an adhan, when one is due or playing, comes before everything else: it is urgent
    # in a way nothing else on this menu is, and it is not always there.
    stopping = clock.routines.stoppable()
    if stopping is not None:
        _action(menu, "Stop %s's adhan" % stopping.name, clock.stop_adhan)
    _action(menu, "Today's meetings", clock.toggle_meetings)
    _action(menu, "Settings…", clock.open_settings)
    menu.addSeparator()

    appearance = menu.addMenu("Appearance")
    theme_menu = appearance.addMenu("Theme")
    for name in themes.THEMES:
        _action(theme_menu, name, lambda n=name: clock.set_theme(n), checked=s["theme"] == name)
    opacity_menu = appearance.addMenu("Opacity")
    for pct in (100, 90, 80, 70, 60, 50, 40, 30, 20, 10):
        _action(opacity_menu, "%d%%" % pct, lambda p=pct: clock.set_opacity(p / 100),
                checked=int(round(s["opacity"] * 100)) == pct)
    size_menu = appearance.addMenu("Size")
    for size in (24, 32, 44, 52, 68, 88, 120):
        _action(size_menu, "%d px" % size, lambda z=size: clock.set_font_size(z),
                checked=int(s["font_size"]) == size)
    font_menu = appearance.addMenu("Font")
    for family in render.FONT_FILES:
        _action(font_menu, family, lambda f=family: clock.set_font_family(f),
                checked=s["font_family"] == family)
    appearance.addSeparator()
    for label, key in (("24-hour clock", "use_24h"), ("Show seconds", "show_seconds"),
                       ("Show date", "show_date"), ("Meeting progress bar", "seconds_bar"),
                       ("Monospaced digits", "tabular_digits"), ("Compact view", "compact")):
        _action(appearance, label, lambda k=key: clock.toggle_setting(k), checked=s[key])

    behaviour = menu.addMenu("Behaviour")
    for label, key in (("Brighten on hover", "hover_boost"), ("Lock position", "lock_position"),
                       ("Always on top", "topmost")):
        _action(behaviour, label, lambda k=key: clock.toggle_setting(k, repaint=False),
                checked=s[key])
    behaviour.addSeparator()
    _action(behaviour, "Peek at the centre periodically",
            lambda: clock.toggle_setting("peek_enabled", repaint=False), checked=s["peek_enabled"])
    _action(behaviour, "Peek now", clock.peek.start)

    calendar = menu.addMenu("Calendar")
    _action(calendar, "Read calendars", lambda: clock.toggle_setting("calendar_enabled"),
            checked=s["calendar_enabled"])
    calendar.addSeparator()
    _action(calendar, "Meeting reminders…", lambda: clock.open_settings("meetings"))
    _action(calendar, "Calendars…", lambda: clock.open_settings("calendars"))

    timers = menu.addMenu("Alarms & timers")
    _action(timers, "Alarms…", lambda: clock.open_settings("alarms"))
    _action(timers, "Timers…", lambda: clock.open_settings("timers"))
    timers.addSeparator()
    _action(timers, "Start / stop stopwatch", lambda: clock.activate("stopwatch"))
    _action(timers, "Reset stopwatch", _reset_stopwatch(clock))

    menu.addSeparator()
    _action(menu, "Reset position", _reset_position(clock))
    _action(menu, "Quit Floating Clock", clock.quit)


def _reset_stopwatch(clock):
    def run() -> None:
        clock.scheduler.stopwatch.reset()
        clock.scheduler.save()
        clock.paint(force=True)
    return run


def _reset_position(clock):
    def run() -> None:
        clock.s["x"] = clock.s["y"] = None
        clock._restore_position()
    return run
