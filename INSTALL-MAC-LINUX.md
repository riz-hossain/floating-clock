# Floating Clock on macOS and Linux

The Mac and Linux builds are made by GitHub Actions on real Mac and Linux
machines, from the same code as the Windows app. Every push to `main` that
touches the clock produces fresh builds under the workflow's **Artifacts**;
a tag of the form `floating-clock-v1.6.0` publishes them as a **Release**.

## macOS

1. Download `FloatingClock-<version>-macos-arm64.dmg` (Apple silicon) or
   `-macos-intel.dmg` (Intel Macs).
2. Open it and drag **Floating Clock** to **Applications**.
3. First launch: the app is not signed with an Apple developer certificate,
   so macOS will say it cannot verify the developer. Right-click the app,
   choose **Open**, then **Open** again in the dialog. That is needed once.
4. The clock appears on screen; its icon sits in the menu bar. Right-click
   the card, or click the menu-bar icon, for the menu and Settings.

Calendar passwords go into the macOS Keychain. Sounds play through the
system's `afplay`.

## Linux

1. Download `FloatingClock-<version>-linux-x86_64.AppImage`, make it
   executable (`chmod +x`) and run it -- or unpack the `.tar.gz` and run
   `floating-clock/floating-clock`.
2. Needs a desktop with a system tray (GNOME needs the AppIndicator
   extension; KDE, XFCE, Cinnamon and MATE have one built in). Without a
   tray the clock still works; right-click the card for the menu.
3. Calendar passwords go into the desktop's secret service (GNOME Keyring or
   KWallet). Sounds play through `paplay` or `aplay`.

## What is different from Windows

- No Outlook: calendars come from the email-first setup (CalDAV, iCal
  links, and Google/Microsoft sign-in once those are registered).
- No global hotkeys yet; the menu has everything.
- Reminders arrive as system notifications rather than the clock's own
  popup.
