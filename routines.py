"""What a prayer's moment sets off: the trigger URLs, and the speaker.

Iqama times move through the year, so anything set to a fixed time is right
for about a week. Two ways out of that, and the clock offers both because the
assistants differ:

- A **trigger URL**. Alexa routines can be started by a device, and a trigger
  skill turns a plain web address into one, so the routine's "when" stops
  being a time. Google has no such starter of its own, but a Home Assistant
  webhook or an IFTTT applet ends in the same plain address, so the same box
  serves either. The clock only opens the address; what happens next belongs
  to the routine.
- A **speaker**, cast to directly (cast.py). No routine, no account, no cloud
  in the path -- the clock plays the adhan on the Nest or Chromecast itself.

Both hosts own one Runner and poll it every second. All that differs between
Tk and Qt is how a status line gets back to the settings page, which is what
`on_status` is for: the host hops to its own UI thread inside it.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from . import cast as cast_mod, prayer as prayer_mod

log = logging.getLogger(__name__)

HOOK = "routine"
CAST = "cast"

# Beyond this many remembered "already done" marks, the old ones are dropped.
# Two kinds, seven prayers, a couple of days: forty is well clear of a day's
# worth and still small enough that the set never grows without bound.
KEEP_MARKS = 40


def media_table(settings) -> dict:
    """Per-prayer audio, with the default standing in for every blank.

    With no default set the table is left sparse, so prayer.hook_for's rule
    still applies and Friday's Jumuah borrows Dhuhr's adhan. With a default,
    every prayer has something and there is nothing to borrow.
    """
    default = str(settings.get("prayer_cast_media_default") or "").strip()
    table = {name: str(value or "").strip()
             for name, value in (settings.get("prayer_cast_media") or {}).items()
             if str(value or "").strip()}
    if not default:
        return table
    return {name: table.get(name) or default for name in prayer_mod.ORDER}


class Runner:
    """Fires each prayer's routine and cast as its moment arrives."""

    def __init__(self, settings: dict, on_status=None) -> None:
        self.s = settings
        # on_status(kind, text) -- the host marshals to its own UI thread.
        self.on_status = on_status or (lambda kind, text: None)
        self.status = {HOOK: "", CAST: ""}
        # One set for both kinds; hook_key's prefix keeps them apart.
        self._fired: set[str] = set()

    # --- the loop ----------------------------------------------------------
    def poll(self, prayers, now: datetime) -> None:
        """Fire whatever is due. Called every second by both hosts."""
        if not self.s.get("prayer_enabled", True):
            return
        prayers = list(prayers or ())
        if not prayers:
            return
        if self.s.get("prayer_routines_enabled", False):
            self._fire_due(prayers, now, HOOK,
                           self.s.get("prayer_routines_lead_minutes", 10),
                           self.s.get("prayer_routines_hooks") or {},
                           self.fire_hook)
        if self.s.get("prayer_cast_enabled", False):
            self._fire_due(prayers, now, CAST,
                           self.s.get("prayer_cast_lead_minutes", 10),
                           media_table(self.s),
                           self.fire_cast)
        if len(self._fired) > KEEP_MARKS:
            self._fired = prayer_mod.prune_keys(self._fired, now)

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
            fire(item.name, value)

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
                media_table(self.s))
            if found and (soonest is None or found[1] < soonest[1]):
                soonest = (found[0], found[1], CAST)
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
