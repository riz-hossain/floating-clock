"""The peek's whoosh: a soft glide in, a soft glide out.

Nothing is shipped or licensed: both sounds are synthesised once and cached
as WAV files next to the settings. Each is a quiet sine that slides up (on
the way in) or down (on the way back), sitting on a breath of filtered
noise for the "air" of something passing, shaped by a gentle attack and a
long release so it never startles. Playback is asynchronous, so the
animation never waits on it.
"""

from __future__ import annotations

import logging
import math
import os
import random
import struct
import wave

log = logging.getLogger(__name__)

RATE = 22050
DURATION = 0.42        # seconds; the trip itself takes ~0.6
PEAK = 0.22            # of full scale: soothing, not a notification
VERSION = 2            # bump to regenerate cached files after a recipe change


def _envelope(t: float, length: float, attack: float = 0.05, release: float = 0.22) -> float:
    if t < attack:
        return t / attack
    if t > length - release:
        return max(0.0, (length - t) / release)
    return 1.0


def samples(kind: str, rate: int = RATE, length: float = DURATION) -> list[float]:
    """Float samples in -1..1 for 'peek_in' (rising) or 'peek_out' (falling)."""
    rising = kind == "peek_in"
    low, high = 320.0, 640.0
    count = int(rate * length)
    out: list[float] = []
    phase = 0.0
    noise = 0.0
    rng = random.Random(7)   # the same breath every time, so the cache is stable
    for i in range(count):
        t = i / rate
        p = t / length
        # An eased sweep: quick to leave, slow to arrive, like the card itself.
        eased = 1 - (1 - p) ** 3
        freq = low + (high - low) * (eased if rising else 1 - eased)
        phase += 2 * math.pi * freq / rate
        tone = math.sin(phase) * 0.8 + math.sin(phase * 2) * 0.12
        # Filtered noise: a one-pole low-pass whose cutoff follows the sweep.
        cutoff = 0.02 + 0.10 * (eased if rising else 1 - eased)
        noise += (rng.uniform(-1, 1) - noise) * cutoff
        out.append((tone * 0.75 + noise * 0.9) * _envelope(t, length))
    peak = max(abs(v) for v in out) or 1.0
    return [v / peak * PEAK for v in out]


def write_wav(path: str, kind: str) -> str:
    data = samples(kind)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767)) for v in data
        ))
    return path


def path_for(kind: str, base_dir: str) -> str:
    return os.path.join(base_dir, "sounds", "%s-v%d.wav" % (kind, VERSION))


def ensure(kind: str, base_dir: str) -> str:
    """The cached WAV for `kind`, synthesised on first use. "" on failure."""
    target = path_for(kind, base_dir)
    if os.path.exists(target):
        return target
    try:
        return write_wav(target, kind)
    except OSError:
        log.debug("could not write %s", target, exc_info=True)
        return ""


def play(kind: str, base_dir: str) -> None:
    """Start the sound and return at once, on whichever platform this is."""
    target = ensure(kind, base_dir)
    if not target:
        return
    try:
        import winsound
    except ImportError:
        _play_elsewhere(target)
        return
    try:
        winsound.PlaySound(
            target, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:
        log.debug("could not play %s", target, exc_info=True)


def _play_elsewhere(target: str) -> None:
    """macOS ships afplay; Linux has paplay (PulseAudio/PipeWire) or aplay."""
    import shutil
    import subprocess
    import sys

    players = ["afplay"] if sys.platform == "darwin" else ["paplay", "aplay", "play"]
    for player in players:
        exe = shutil.which(player)
        if exe:
            try:
                subprocess.Popen([exe, target], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                continue
            return
