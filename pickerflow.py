"""What the masjid picker says and offers at each moment: the one part both windows share.

The picker is a small state machine. It shows a list of masjids, or it is busy (searching, checking
one masjid, or finding the masjids near this computer), or it has found times and waits for the
person's yes, or it tried and found nothing. What the main button says and whether it can be pressed,
whether Stop and Back are on show, and what the line under the list says all follow from that and
from nothing else. So they are worked out here, once, and the Windows window (Tk) and the Mac and
Linux window (Qt) only apply them.

The main button says Done once there is something to be done with: the point is that a masjid whose
times were found can be kept with one obvious press. It used to say "Use this masjid" a second time,
and the timeline that had taken the list's place grew the window past the bottom of the screen, so
that the button could not be pressed at all.

Nothing here imports a toolkit.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import whereami

# --- what the picker is doing ------------------------------------------------------------------
LIST = "list"              # the results (or none yet) are on show and nothing is running
SEARCHING = "searching"    # the results are on show and a search is running
WORKING = "working"        # the timeline is on show and a check, or the look for masjids near you, is running
FOUND = "found"            # the timeline is on show and times were found, waiting for a yes
FAILED = "failed"          # the timeline is on show and nothing was found, or it was stopped

# What pressing the main button does.
CHECK, KEEP, AGAIN, NOTHING = "check", "keep", "again", "nothing"


@dataclass(frozen=True)
class Controls:
    main: str = "Use this masjid"      # what the main button says
    action: str = CHECK                # what pressing it does
    enabled: bool = True               # whether it can be pressed
    stop: bool = False                 # Stop is on show
    back: bool = False                 # Back to results is on show
    look: bool = True                  # Search and Near me can be pressed
    timeline: bool = False             # the timeline is on show, in place of the list


def controls(mode: str, picked: bool = False) -> Controls:
    """What is on show and what can be pressed in `mode`; `picked` is whether a masjid is chosen in the list."""
    if mode == SEARCHING:
        return Controls(enabled=False, action=NOTHING, look=False)
    if mode == WORKING:
        return Controls(enabled=False, action=NOTHING, look=False, stop=True, timeline=True)
    if mode == FOUND:
        return Controls(main="Done", action=KEEP, back=True, timeline=True)
    if mode == FAILED:
        return Controls(main="Try again", action=AGAIN, back=True, timeline=True)
    return Controls(enabled=picked)


# --- what it says -------------------------------------------------------------------------------
WELCOME = "Type a town or a masjid's name and press Search, or press Near me to look around where this computer is."
PICK_FIRST = "Pick one from the list first."
STOPPING = "Stopping, after the step it is on…"
LOOKING = "Searching…"
LOCATING = "Finding where this computer is, then the masjids around it. This can take a few seconds; Stop gives up."


def checking(name: str) -> str:
    return "Checking %s. This can take a minute; Stop gives up." % name


def found(count: int, trouble: str = "") -> str:
    """The line under the results of a search."""
    if trouble:
        return "Found %d. (%s)" % (count, trouble)
    if count:
        return "Found %d. Pick one, then press Use this masjid." % count
    return "Nothing matched. Try the town, or a postal code."


def around(count: int, where: "whereami.Where", radius_km: float, trouble: str = "") -> str:
    """The line under the results of a look at what is near this computer: what it is near, and how sure that is."""
    place = whereami.describe(where)
    if count:
        text = "Found %d masjid%s within %d km of %s. Pick one, then press Use this masjid." % (
            count, "" if count == 1 else "s", radius_km, place)
    else:
        text = "No masjids within %d km of %s. Try a town, or a postal code." % (radius_km, place)
    if where.source != "windows" and count:
        text += " Not the right place? Type a town or a postal code and press Search."
    if where.why_not:
        text += " (Windows would not say where this computer is: %s.)" % where.why_not
    if trouble:
        text += " (%s)" % trouble
    return text


def unlocated(reason: str) -> str:
    """The line when where this computer is could not be worked out, or the person stopped the look for it."""
    reason = (reason or "").strip().rstrip(". ")
    if reason.lower() == "stopped":
        return "Stopped."
    if not reason:
        return "Could not find where you are. Type a town or a postal code above and press Search instead."
    return "Could not find where you are: %s. Type a town or a postal code above and press Search instead." % reason


# --- how big, and where ------------------------------------------------------------------------
WIDTH, HEIGHT = 600, 640           # the size it would like, in pixels at 100%
MIN_WIDTH, MIN_HEIGHT = 440, 420
SIDES = 40                         # air kept at the sides of the screen
TOP_AND_BOTTOM = 100               # a title bar above the window, and air
TITLE_BAR = 40                     # the title bar above the window, and the border below it
FRAME = 20                         # the borders at its two sides, which are outside the size it is given
SHORT = 520                        # below this many pixels tall (at 100%), the explanation above the search box goes


def size(width: int, height: int, scale: float = 1.0) -> tuple[int, int]:
    """(width, height) in pixels for a screen (or a monitor's work area) `width` by `height`.

    The usual size, but never more than the screen has room for. The window does not follow its
    contents: left to grow with a long list of masjids, or with the timeline, it went on down past
    the bottom of the screen and took its buttons with it.
    """
    return (max(1, min(round(WIDTH * scale), width - round(SIDES * scale))),
            max(1, min(round(HEIGHT * scale), height - round(TOP_AND_BOTTOM * scale))))


def smallest(width: int, height: int, scale: float = 1.0) -> tuple[int, int]:
    """The least it may be dragged down to, and never more than it is to begin with."""
    return min(round(MIN_WIDTH * scale), width), min(round(MIN_HEIGHT * scale), height)


def place(parent: tuple, shape: tuple, area: tuple, scale: float = 1.0) -> tuple[int, int]:
    """(x, y) for the picker's top-left corner: over the middle of the window that opened it, and
    wholly inside the usable part of the monitor, with room above for its title bar.

    `parent` is that window's (x, y, width, height); `shape` is (width, height); `area` is the
    monitor's (left, top, right, bottom), which need not start at 0: monitors sit left of and above
    the main one too.
    """
    left, top, right, bottom = area
    x, y, wide, _high = parent
    width, height = shape
    x = x + (wide - width) // 2
    y = y + round(60 * scale)
    return (max(left, min(x, right - width - round(FRAME * scale))),
            max(top, min(y, bottom - height - round(TITLE_BAR * scale))))


def short(height: int, scale: float = 1.0) -> bool:
    """Whether the window is too short to spare room for the explanation above the search box."""
    return height < round(SHORT * scale)
