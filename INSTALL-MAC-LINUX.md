# Floating Clock on macOS and Linux

The Mac and Linux builds are made by GitHub Actions on real Mac and Linux
machines, from the same sources as the Windows app. Every push builds all
three and leaves them under the workflow run's **Artifacts**; a tag of the
form `v1.9.6` publishes them as a **Release**.

Windows runs a Tk-plus-Win32 host; macOS and Linux run a Qt one. Everything
underneath — the drawing, the day bar, the meetings, the calendars, the
alarms and timers, the prayer times and what they set off — is the same code.

## macOS

1. Download `FloatingClock-<version>-macos-arm64.dmg` (Apple silicon) or
   `-macos-intel.dmg` (Intel Macs).
2. Open it and drag **Floating Clock** to **Applications**.
3. First launch: the app is not signed with an Apple developer certificate,
   so macOS will say it cannot verify the developer. Right-click the app,
   choose **Open**, then **Open** again in the dialog. That is needed once.
   On recent macOS you may instead have to go to **System Settings → Privacy
   & Security** and press **Open Anyway** after the first refusal.
4. The clock appears on screen; its icon sits in the menu bar. Right-click
   the card, or click the menu-bar icon, for the menu and Settings.

Calendar passwords go into the macOS Keychain. Sounds play through the
system's `afplay`. The app has no Dock icon by design — it is a menu-bar app.

## Linux

1. Download `FloatingClock-<version>-linux-x86_64.tar.gz`, unpack it and run
   `FloatingClock/FloatingClock`.
2. Needs a desktop with a system tray (GNOME needs the AppIndicator
   extension; KDE, XFCE, Cinnamon and MATE have one built in). Without a
   tray the clock still works; right-click the card for the menu.
3. Calendar passwords go into the desktop's secret service (GNOME Keyring or
   KWallet). Sounds play through `paplay` or `aplay`.

## What is different from Windows

- **No Outlook.** Calendars come from the email-first setup: CalDAV, iCal
  links, and Google/Microsoft sign-in once those are registered
  (SIGN-IN-SETUP.md).
- **No global hotkeys yet.** The menu has everything.
- **Reminders arrive as system notifications** rather than the clock's own
  popup.

Prayer times, the routine triggers and casting the adhan to a Google or Nest
speaker all work the same as on Windows — see ROUTINES.md.

## Building it yourself

```bash
./packaging/build-unix.sh --dmg     # macOS: an .app and a .dmg
./packaging/build-unix.sh --tgz     # Linux: a folder and a tarball
```

Needs Python 3.10 or newer; the script installs the rest into whatever Python
it finds. Output lands in `packaging/dist`.

To check a build without launching it:

```bash
./packaging/dist/FloatingClock.app/Contents/MacOS/FloatingClock --check
```

That imports everything a real start needs and exits, which is what CI runs —
a bundle missing one lazily imported module otherwise looks fine until
somebody opens the settings window.
