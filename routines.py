"""What a prayer's moment sets off: the trigger URLs, the speaker, and this computer itself.

Iqama times move through the year, so anything set to a fixed time is right for about a week.
Three ways out of that:

- A **trigger URL**. Alexa routines can be started by a device, and a trigger
  skill turns a plain web address into one, so the routine's "when" stops
  being a time. Google has no such starter of its own, but a Home Assistant
  webhook or an IFTTT applet ends in the same plain address, so the same box
  serves either. The clock only opens the address; what happens next belongs
  to the routine, and once it has, nothing here can reach it again.
- A **speaker**, cast to directly (cast.py). No routine, no account, no cloud
  in the path -- the clock plays the adhan on the Nest or Chromecast itself.
- **This computer's own speakers or headphones** (localaudio.py). No speaker
  and no assistant needed at all.

The last two are the clock's own doing, so they can be stopped once started, unlike a routine
that has already been handed to an assistant. If asked -- prayer_warn_seconds, 0 turns it off --
a prayer about to fire is announced this many seconds ahead, so a meeting is not interrupted by
surprise: `on_warn` tells the host, which shows something with a way to say no. That "no" and a
later, unprompted "stop" are the same thing here (stop_now): mark it as already fired, so it
never starts, and ask whatever might already be running to stop. Both halves are harmless when
there is nothing to do -- marking an already-fired prayer changes nothing, and stopping silence
is not a problem -- so the one call serves early and late alike, without needing to know which.

Both hosts own one Runner and poll it every second. All that differs between
Tk and Qt is how a status line gets back to the settings page, which is what
`on_status` is for: the host hops to its own UI thread inside it.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from . import cast as cast_mod, localaudio, prayer as prayer_mod

log = logging.getLogger(__name__)

HOOK = "routine"
CAST = "cast"
LOCAL = "local"
KINDS = (HOOK, CAST, LOCAL)

# Beyond this many remembered "already done" marks, the old ones are dropped.
# Three kinds, seven prayers, a couple of days: sixty is well clear of a
# day's worth and still small enough that the set never grows without bound.
KEEP_MARKS = 60
# How long "stop" stays offered after a prayer was last warned about or fired: a guess, since
# the clock does not track exactly when a cast or a local file finishes playing, generous enough
# to cover a normal adhan comfortably without leaving the option to stop something forever.
STOP_WINDOW_S = 300.0


def media_table(settings, prefix: str = "prayer_cast") -> dict:
    """Per-prayer audio for `prefix` ("prayer_cast" or "prayer_local"), with the
    default standing in for every blank.

    With no default set the table is left sparse, so prayer.hook_for's rule
    still applies and Friday's Jumuah borrows Dhuhr's adhan. With a default,
    every prayer has something and there is nothing to borrow.
    """
    default = str(settings.get("%s_media_default" % prefix) or "").strip()
    table = {name: str(value or "").strip()
             for name, value in (settings.get("%s_media" % prefix) or {}).items()
             if str(value or "").strip()}
    if not default:
        return table
    return {name: table.get(name) or default for name in prayer_mod.ORDER}


class Runner:
    """Fires each prayer's routine and cast as its moment arrives."""

    def __init__(self, settings: dict, on_status=None, on_warn=None) -> None:
        self.s = settings
        # on_status(kind, text) -- the host marshals to its own UI thread.
        self.on_status = on_status or (lambda kind, text: None)
        # on_warn(item, kinds, seconds_left) -- a prayer is about to fire; the host shows
        # something with a way to say no. Called once per prayer, however many kinds are due.
        self.on_warn = on_warn or (lambda item, kinds, seconds_left: None)
        self.status = {HOOK: "", CAST: "", LOCAL: ""}
        # One set for all three kinds; hook_key's prefix keeps them apart.
        self._fired: set[str] = set()
        self._warned: set[str] = set()          # item.key -> already warned about today
        # item.key -> (item, until) for whatever a warning or a firing said "stop" could still
        # reach; stop_now() acts on all of these and clears them, poll() drops the stale ones.
        self._pending: dict[str, tuple] = {}

    # --- the loop ----------------------------------------------------------
    def poll(self, prayers, now: datetime) -> None:
        """Fire whatever is due. Called every second by both hosts."""
        if not self.s.get("prayer_enabled", True):
            return
        prayers = list(prayers or ())
        if not prayers:
            return
        self._warn_due(prayers, now)
        if self.s.get("prayer_routines_enabled", False):
            self._fire_due(prayers, now, HOOK,
                           self.s.get("prayer_routines_lead_minutes", 10),
                           self.s.get("prayer_routines_hooks") or {},
                           self.fire_hook)
        if self.s.get("prayer_cast_enabled", False):
            self._fire_due(prayers, now, CAST,
                           self.s.get("prayer_cast_lead_minutes", 10),
                           media_table(self.s, "prayer_cast"),
                           self.fire_cast)
        if self.s.get("prayer_local_enabled", False):
            self._fire_due(prayers, now, LOCAL,
                           self.s.get("prayer_local_lead_minutes", 10),
                           media_table(self.s, "prayer_local"),
                           self.fire_local)
        if len(self._fired) > KEEP_MARKS:
            self._fired = prayer_mod.prune_keys(self._fired, now)
        if len(self._warned) > KEEP_MARKS:
            self._warned = prayer_mod.prune_keys(self._warned, now)
        if self._pending:
            self._pending = {k: v for k, v in self._pending.items() if v[1] > now}

    def _fire_due(self, prayers, now, kind, lead, table, fire) -> None:
        try:
            lead = float(lead)
        except (TypeError, ValueError):
            lead = 10.0
        for item, value in prayer_mod.hooks_due(prayers, now, lead, table,
                                                self._fired, kind):
            # Marked before the worker starts, not after: a thread that takes
            # eleven seconds to fail must not be started again each second in
            # between.
            self._fired.add(prayer_mod.hook_key(item, kind))
            if kind in (CAST, LOCAL):
                # A routine, once fired, is out of reach; a cast or a local
                # play is this clock's own doing and can still be stopped.
                self._remember(item, now)
            fire(item.name, value)

    # --- warning ahead of a prayer, and stopping it, early or late ---------
    def _kind_config(self, kind: str) -> tuple:
        """(enabled, lead_minutes, table) for one kind."""
        if kind == HOOK:
            return (bool(self.s.get("prayer_routines_enabled", False)),
                    self.s.get("prayer_routines_lead_minutes", 10),
                    self.s.get("prayer_routines_hooks") or {})
        if kind == CAST:
            return (bool(self.s.get("prayer_cast_enabled", False)),
                    self.s.get("prayer_cast_lead_minutes", 10),
                    media_table(self.s, "prayer_cast"))
        return (bool(self.s.get("prayer_local_enabled", False)),
                self.s.get("prayer_local_lead_minutes", 10),
                media_table(self.s, "prayer_local"))

    def _due_kinds(self, item) -> list:
        """[(kind, moment)] for every kind that would actually fire for this prayer."""
        found = []
        for kind in KINDS:
            enabled, lead, table = self._kind_config(kind)
            if not enabled or not prayer_mod.hook_for(item.name, table):
                continue
            try:
                lead = float(lead)
            except (TypeError, ValueError):
                lead = 10.0
            found.append((kind, item.iqama - timedelta(minutes=lead)))
        return found

    def _warn_due(self, prayers, now: datetime) -> None:
        try:
            warn_s = float(self.s.get("prayer_warn_seconds", 10) or 0)
        except (TypeError, ValueError):
            warn_s = 0.0
        if warn_s <= 0:
            return
        for item in prayers:
            if item.key in self._warned:
                continue
            due = self._due_kinds(item)
            if not due:
                continue
            soonest = min(moment for _kind, moment in due)
            until = (soonest - now).total_seconds()
            if 0 <= until <= warn_s:
                self._warned.add(item.key)
                self._remember(item, now)
                try:
                    self.on_warn(item, [kind for kind, _m in due], round(until))
                except Exception:
                    log.debug("could not warn about %s", item.name, exc_info=True)

    def _remember(self, item, now: datetime) -> None:
        """This prayer's occurrence is now something stop_now() should act on."""
        self._pending[item.key] = (item, now + timedelta(seconds=STOP_WINDOW_S))

    def stoppable(self):
        """The soonest prayer stop_now() would act on, or None."""
        if not self._pending:
            return None
        return min((item for item, _until in self._pending.values()), key=lambda p: p.iqama)

    def stop_now(self) -> None:
        """Say no to whatever is pending: prevent it firing if it has not, stop it if it has.

        Safe to call with nothing pending -- marking an already-fired prayer changes nothing,
        and cast.stop/localaudio.stop are themselves safe to call on silence.
        """
        if not self._pending:
            return
        for item, _until in list(self._pending.values()):
            for kind in KINDS:
                self._fired.add(prayer_mod.hook_key(item, kind))
        self._pending.clear()
        self._stop_playback()

    def _stop_playback(self) -> None:
        def work() -> None:
            device = str(self.s.get("prayer_cast_device") or "").strip()
            if device:
                try:
                    problem = cast_mod.stop(device)
                except Exception as exc:        # sockets and discovery have sharp edges
                    problem = _short(exc)
                if problem:
                    log.info("Stopping the speaker: %s", problem)
                self._say(CAST, "Stopped." if not problem else "Could not stop the speaker: %s" % problem)
            try:
                problem = localaudio.stop()
            except Exception as exc:
                problem = _short(exc)
            if problem:
                log.info("Stopping local playback: %s", problem)
            self._say(LOCAL, "Stopped." if not problem else "Could not stop here: %s" % problem)

        self._work(work, "stop")

    def next_due(self, prayers, now: datetime):
        """(prayer, moment, kind) for whatever fires next, or None.

        What the hourly heartbeat says is armed.
        """
        soonest = None
        if self.s.get("prayer_routines_enabled", False):
            found = prayer_mod.next_hook(
                prayers, now, float(self.s.get("prayer_routines_lead_minutes", 10) or 0),
                self.s.get("prayer_routines_hooks") or {})
            if found:
                soonest = (found[0], found[1], HOOK)
        if self.s.get("prayer_cast_enabled", False):
            found = prayer_mod.next_hook(
                prayers, now, float(self.s.get("prayer_cast_lead_minutes", 10) or 0),
                media_table(self.s, "prayer_cast"))
            if found and (soonest is None or found[1] < soonest[1]):
                soonest = (found[0], found[1], CAST)
        if self.s.get("prayer_local_enabled", False):
            found = prayer_mod.next_hook(
                prayers, now, float(self.s.get("prayer_local_lead_minutes", 10) or 0),
                media_table(self.s, "prayer_local"))
            if found and (soonest is None or found[1] < soonest[1]):
                soonest = (found[0], found[1], LOCAL)
        return soonest

    # --- the two things a moment can do ------------------------------------
    def fire_hook(self, name: str, url: str) -> None:
        """Call one trigger URL on a worker, and say how it went."""
        when = datetime.now().strftime("%H:%M")

        def work() -> None:
            try:
                # Retried inside the grace window: the moment a routine is due
                # is often the minute a laptop is still finding the network.
                problem, tries, reply = prayer_mod.fire_with_retries(url)
            except Exception as exc:    # a trigger thread must never die quietly
                problem, tries, reply = _short(exc), 1, ""
            detail = " after %d tries" % tries if tries > 1 else ""
            said = " -- reply: %s" % reply if reply else ""
            if problem:
                log.warning("Routine for %s failed at %s%s: %s%s",
                            name, when, detail, problem, said)
                self._say(HOOK, "%s at %s%s: %s" % (name, when, detail, problem))
            else:
                log.info("Routine for %s fired at %s%s%s", name, when, detail, said)
                self._say(HOOK, "%s fired at %s%s." % (name, when, detail))

        self._work(work, "routine")

    def fire_cast(self, name: str, media: str) -> None:
        """Play one prayer's adhan on the speaker, on a worker."""
        when = datetime.now().strftime("%H:%M")
        device = str(self.s.get("prayer_cast_device") or "").strip()
        try:
            volume = float(self.s.get("prayer_cast_volume", 0.6))
        except (TypeError, ValueError):
            volume = 0.6

        def work() -> None:
            try:
                problem = cast_mod.play(device, media, volume)
            except Exception as exc:   # discovery and sockets have sharp edges
                problem = _short(exc)
            if problem:
                log.warning("Adhan for %s did not play at %s on %s: %s",
                            name, when, device or "no speaker", problem)
                self._say(CAST, "%s at %s: %s" % (name, when, problem))
            else:
                log.info("Adhan for %s playing at %s on %s", name, when, device)
                self._say(CAST, "%s playing at %s on %s." % (name, when, device))

        self._work(work, "cast")

    def fire_local(self, name: str, media: str) -> None:
        """Play one prayer's adhan through this computer's own speakers, on a worker.

        No network, no account, no other device needed -- for wherever the other two
        do not reach, travelling most of all.
        """
        when = datetime.now().strftime("%H:%M")
        try:
            volume = float(self.s.get("prayer_local_volume", 0.6))
        except (TypeError, ValueError):
            volume = 0.6

        def work() -> None:
            try:
                problem = localaudio.play(media, volume)
            except Exception as exc:   # a bad file or a missing player must not kill the worker
                problem = _short(exc)
            if problem:
                log.warning("Adhan for %s did not play here at %s: %s", name, when, problem)
                self._say(LOCAL, "%s at %s: %s" % (name, when, problem))
            else:
                log.info("Adhan for %s playing here at %s", name, when)
                self._say(LOCAL, "%s playing here at %s." % (name, when))

        self._work(work, "local")

    # --- plumbing ----------------------------------------------------------
    def _work(self, target, what: str) -> None:
        """On a thread, because the moment a prayer is due is exactly when the
        clock must not stall: a hung service would freeze the digits."""
        threading.Thread(target=target, name="floating-clock-%s" % what,
                         daemon=True).start()

    def _say(self, kind: str, text: str) -> None:
        self.status[kind] = text
        try:
            self.on_status(kind, text)
        except Exception:
            log.debug("could not show the %s status", kind, exc_info=True)


def _short(exc: Exception) -> str:
    return str(exc).strip()[:90] or exc.__class__.__name__
