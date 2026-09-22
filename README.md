# Floating Clock

[![Build](https://github.com/riz-hossain/floating-clock/actions/workflows/build.yml/badge.svg)](https://github.com/riz-hossain/floating-clock/actions/workflows/build.yml)
[![Latest release](https://img.shields.io/github/v/release/riz-hossain/floating-clock)](https://github.com/riz-hossain/floating-clock/releases)

A floating, always-on-top desktop clock that keeps your next meeting, your
day's shape and your masjid's prayer times in front of you — and can make
the house follow them.

Windows, macOS and Linux. Windows runs a Tk-plus-Win32 host; macOS and Linux
run a Qt one — everything underneath is the same code.

## What it does

- **The clock** — a translucent, always-on-top card you can put anywhere,
  with a day bar that fills as the day goes and a peek that glides it to
  centre screen when you ask.
- **Meetings** — Outlook on Windows, or on any platform via CalDAV, iCal
  links, and Google or Microsoft sign-in. The next few sit under the time;
  one click joins.
- **Prayer times** — press *Find my masjid* or *Near me*, pick yours from
  the list, and the clock reads its times the way you would from its
  website — shows you what it found before keeping it, and rechecks every
  night. If a masjid has nothing online it can read, it offers the nearest
  one that does, clearly labelled as borrowed.
- **Routines** — trigger an Alexa routine at each prayer, play the adhan
  straight on a Google or Nest speaker, or play it through this computer's
  own speakers or headphones — no speaker or account needed — right at
  iqama instead of a fixed clock time. See [ROUTINES.md](ROUTINES.md).
- **Alarms, timers and a stopwatch**, with reminders before meetings and
  before each iqama.

## Installing

- **Windows** — download `FloatingClock-Setup-<version>.exe` from the
  [Releases](https://github.com/riz-hossain/floating-clock/releases) page. It
  installs per-user, so there is no administrator prompt.
- **macOS and Linux** — see [INSTALL-MAC-LINUX.md](INSTALL-MAC-LINUX.md).

Neither build is signed yet, so the first launch needs one extra step on each
platform; the release notes say which.

## Building

```powershell
.\packaging\build.ps1          # Windows: the app and a setup.exe
```

```bash
./packaging/build-unix.sh --dmg   # macOS
./packaging/build-unix.sh --tgz   # Linux
```

GitHub Actions builds all three on every push and publishes them on a `v*`
tag.

## Documentation

- [ROUTINES.md](ROUTINES.md) — prayer times, Alexa routines, and casting the
  adhan to a Google or Nest speaker.
- [INSTALL-MAC-LINUX.md](INSTALL-MAC-LINUX.md) — the Mac and Linux builds.
- [SIGN-IN-SETUP.md](SIGN-IN-SETUP.md) — registering the app with Google and
  Microsoft so the sign-in buttons work.

## Licence

Apache 2.0 — see [LICENSE](LICENSE).
