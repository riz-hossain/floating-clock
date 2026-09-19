"""The peek: the clock glides to the middle of the screen, holds long enough
to be read, and glides back to where it was parked.

Two things use the trip. The periodic peek is the gentle one, on the hour
and the half hour. The nudge is the urgent one: a meeting is a minute out,
so the card flies to the centre and shakes there until it is clicked --
a toast can sit unseen behind a full-screen window, a clock shivering in
the middle of the screen cannot.

Useful when the clock lives in a corner you have stopped noticing. The whole
move is driven off the already-rendered card bitmap -- scaling a premultiplied
image is just a resize, so no re-render is needed per frame.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta

from PIL import Image

from . import settings as cfg, sounds, win32util as w32

log = logging.getLogger(__name__)

DEFAULT_TRAVEL_MS = 620   # each leg of the journey, when unset
FRAME_MS = 16        # ~60fps
# Fire this close to a boundary and the boundary counts as already gone, so a
# reschedule landing a hair early cannot fire twice for the same slot.
BOUNDARY_EPSILON_S = 1.0
# A floor on the Tk delay only, not on the interval: the boundary maths already
# keeps slots at least a minute apart (the interval setting is clamped to >= 1),
# and a delay shorter than this only ever means we armed just before a boundary.
MIN_DELAY_MS = 250
# A slot reached this long after it was due was missed -- the PC was asleep,
# or the event loop was held up -- and is skipped rather than peeking at
# some odd minute nobody was watching for; the next slot is armed instead.
LATE_GRACE_S = 120.0
# The shake: a quick side-to-side shiver, in card pixels at 100% scale.
# Fast enough to read as urgency, small enough not to look broken.
NUDGE_SHAKE_HZ = 5.5
NUDGE_SHAKE_PX = 11.0


def seconds_until_boundary(now: datetime, interval_minutes: float) -> float:
    """Seconds from `now` to the next wall-clock multiple of the interval.

    Anchoring to the wall clock rather than to a relative countdown is what
    makes "every 30 min" mean :00 and :30 -- the times a person actually
    expects to be nudged. A relative timer instead anchors to whenever it was
    last armed, so it drifts to a different pair of minutes after every
    restart, and never lines up with anything.

    It also makes rearming free: the interval slider re-arms on every pixel of
    a drag, and recomputing the same boundary is a no-op where restarting a
    countdown would push the next peek out by a full interval each time.
    """
    # Work in seconds throughout: converting to minutes and back multiplies the
    # rounding error up into microseconds of drift on every boundary.
    step = max(60.0, float(interval_minutes) * 60.0)
    seconds_today = (
        now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1_000_000
    )
    remaining = step - (seconds_today % step)
    if remaining <= BOUNDARY_EPSILON_S:
        remaining += step
    return remaining


def ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


class Peek:
    """Runs one out-hold-back trip, then schedules the next one.

    The clock keeps ticking underneath: every frame re-reads the owner's
    current card bitmap, so the seconds bar and digits stay live mid-flight.
    """

    def __init__(self, owner) -> None:
        self.owner = owner
        self.active = False
        # "peek" for the periodic trip, "nudge" for a meeting about to start.
        self.mode = "peek"
        self._after_id = None
        self._timer_id = None
        # When the next peek is due, on the wall clock. The Tk timer is only
        # the wake-up call; the clock's tick checks this every quarter second,
        # so a timer that runs late (or not at all) cannot move the slot.
        self._due: datetime | None = None
        self._home = (0, 0)
        self._parked = (0, 0)
        self._target = (0, 0)
        self._phase = ""
        self._started = 0.0
        # Set when the trip began with the clock minimized to the tray: there
        # is no parked card on screen to glide from, so it fades in at the
        # centre instead, and hides itself again when it is done.
        self._from_tray = False

    # --- scheduling --------------------------------------------------------
    def schedule(self, now: datetime | None = None) -> None:
        """(Re)arm the next peek on the next wall-clock boundary."""
        self.cancel_timer()
        self._due = None
        if not self.owner.s["peek_enabled"]:
            return
        now = now or datetime.now()
        seconds = seconds_until_boundary(now, float(self.owner.s["peek_interval_minutes"]))
        self._due = now + timedelta(seconds=seconds)
        delay = max(MIN_DELAY_MS, int(seconds * 1000))
        self._timer_id = self.owner.root.after(delay, self._fire)

    def poll(self, now: datetime | None = None) -> None:
        """Called from the clock's tick. Fires a slot whose time has come
        whether or not the Tk timer has -- and notices one slept through."""
        if self._due is None:
            return
        now = now or datetime.now()
        if now >= self._due:
            self.cancel_timer()
            self._fire(now)

    def cancel_timer(self) -> None:
        if self._timer_id is not None:
            try:
                self.owner.root.after_cancel(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

    def _fire(self, now: datetime | None = None) -> None:
        self._timer_id = None
        due, self._due = self._due, None
        now = now or datetime.now()
        late = (now - due).total_seconds() if due is not None else 0.0
        if due is not None and late > LATE_GRACE_S:
            log.info("Peek slot %s reached %.0fs late (asleep?); skipped",
                     due.strftime("%H:%M"), late)
        else:
            log.info("Peek at %s for the %s slot", now.strftime("%H:%M:%S"),
                     due.strftime("%H:%M") if due is not None else "manual")
            self.start()
        self.schedule(now)

    # --- the trip ----------------------------------------------------------
    def nudge(self) -> None:
        """Fly to the centre and shake: something is about to start.

        A periodic peek already in flight gives way to it. The nudge is the
        more urgent of the two, and two trips at once would fight over the
        window's position.
        """
        if self.active:
            if self.mode == "nudge":
                return          # already shaking for something
            self.finish()
        self.start(mode="nudge")

    def start(self, mode: str = "peek") -> None:
        owner = self.owner
        if self.active or owner.s["click_through"]:
            # Click-through means the user cannot grab it anyway; flying it
            # across the screen would just be in the way.
            return
        image = getattr(owner, "_image", None)
        if image is None:
            return
        self.mode = mode

        self._parked = owner._position()
        self._from_tray = bool(owner.s.get("minimized", False))
        zoom = float(owner.s["peek_zoom"])
        width, height = image.size
        left, top, right, bottom = w32.work_area(owner.hwnd)
        self._target = (
            int((left + right) / 2 - width * zoom / 2),
            int((top + bottom) / 2 - height * zoom / 2),
        )
        if self._from_tray:
            # Minimized: nothing is parked anywhere, so "home" is the centre
            # at normal size and the trip is a fade-and-zoom in place. The
            # window stays hidden until the first frame has been pushed.
            self._home = (
                int((left + right) / 2 - width / 2),
                int((top + bottom) / 2 - height / 2),
            )
            w32.show_window(owner.hwnd, True)
        else:
            self._home = self._parked
        self.active = True
        self._phase = "out"
        self._started = time.perf_counter()
        self._whoosh("peek_in")
        self._frame()

    def _whoosh(self, kind: str) -> None:
        """A soft glide as the card sets off and as it heads home."""
        if self.owner.s.get("peek_sound", True):
            sounds.play(kind, cfg.config_dir())

    def _frame(self) -> None:
        owner = self.owner
        if not self.active:
            return
        image = getattr(owner, "_image", None)
        if image is None:
            self.finish()
            return

        elapsed = (time.perf_counter() - self._started) * 1000
        zoom = float(owner.s["peek_zoom"])
        hold_ms = float(owner.s["peek_hold_seconds"]) * 1000
        if self.mode == "nudge":
            # A nudge holds for as long as it shakes; that is the whole point
            # of it, and it is a different length from a peek's pause.
            hold_ms = max(500.0, float(owner.s.get("nudge_shake_seconds", 5.0)) * 1000)
        travel_ms = max(60.0, float(owner.s.get("peek_travel_ms", DEFAULT_TRAVEL_MS)))

        if self._phase == "out":
            progress = min(1.0, elapsed / travel_ms)
            if progress >= 1.0:
                self._phase = "hold"
                self._started = time.perf_counter()
        elif self._phase == "hold":
            progress = 1.0
            if elapsed >= hold_ms:
                self._phase = "back"
                self._started = time.perf_counter()
                self._whoosh("peek_out")
        else:
            progress = 1.0 - min(1.0, elapsed / travel_ms)
            if elapsed >= travel_ms:
                self.finish()
                return

        eased = ease_in_out_cubic(progress)
        scale = 1.0 + (zoom - 1.0) * eased
        width, height = image.size
        frame = (
            image if scale <= 1.001
            else image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))), Image.BILINEAR
            )
        )
        # Track the target continuously: the card can change size mid-flight
        # (a new footer, a ticking timer), and recomputing keeps it centred.
        left, top, right, bottom = w32.work_area(owner.hwnd)
        target = (
            int((left + right) / 2 - frame.width / 2),
            int((top + bottom) / 2 - frame.height / 2),
        )
        x = self._home[0] + (target[0] - self._home[0]) * eased
        y = self._home[1] + (target[1] - self._home[1]) * eased
        if self._phase == "hold" and self.mode == "nudge":
            x, y = self._shake(x, y, elapsed / 1000.0, hold_ms / 1000.0)

        if self._from_tray:
            # From nothing to fully opaque and back: there is no resting
            # opacity to start from when the card was not on screen at all.
            opacity = eased
        else:
            opacity = owner._target_opacity
            # Fade up to fully opaque at the centre so it is actually readable.
            opacity += (1.0 - opacity) * eased
        w32.push_layered_bitmap(owner.hwnd, frame, opacity, position=(int(x), int(y)))

        self._after_id = owner.root.after(FRAME_MS, self._frame)

    def _shake(self, x: float, y: float, t: float, total: float) -> tuple[float, float]:
        """A shiver about the centre, `t` seconds into a shake of `total`.

        Mostly sideways, with a smaller and faster vertical wobble so it reads
        as a shake rather than a slide, and easing off over the last stretch
        so the card is still again before it flies home.
        """
        settle = max(0.35, total * 0.25)
        envelope = min(1.0, max(0.0, (total - t) / settle))
        swing = NUDGE_SHAKE_PX * getattr(self.owner, "_scale", 1.0) * envelope
        angle = 2 * math.pi * NUDGE_SHAKE_HZ * t
        return x + swing * math.sin(angle), y + swing * 0.3 * math.sin(angle * 2)

    def finish(self, land: bool = True) -> None:
        """Land back where it was parked and hand the window back to the
        normal paint path. Hides the window again if the clock is minimized.

        `land=False` leaves the card where it is: used when the user grabs a
        peek that came from the tray, since the spot it was parked at is not
        one they can see, and wherever they grabbed it is where they want it.
        """
        if not self.active:
            return
        self.active = False
        if self._after_id is not None:
            try:
                self.owner.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        hide = bool(self.owner.s.get("minimized", False))
        if hide:
            # Hide before moving, or the card flashes at its parked spot.
            w32.show_window(self.owner.hwnd, False)
        if land or hide:
            w32.move_window(self.owner.hwnd, *self._parked)
        self._from_tray = False
        self.mode = "peek"
        self.owner._paint(force=True)

    def abort(self) -> None:
        """Cancel mid-flight -- used when the user grabs the clock."""
        if self.active:
            self.finish(land=not self._from_tray)
