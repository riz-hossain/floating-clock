#!/usr/bin/env bash
#
# Builds Floating Clock for macOS or Linux: the Qt host, bundled by
# PyInstaller. Windows has its own build.ps1 and its own Tk host; this is the
# other half of the same app, from the same sources.
#
#   ./build-unix.sh            # a .app on macOS, a folder on Linux
#   ./build-unix.sh --dmg      # macOS: also wrap the .app in a .dmg
#   ./build-unix.sh --tgz      # Linux: also tar the folder up
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$here")"
build_dir="$here/build"
dist_dir="$here/dist"
# PyInstaller must import the package as `floating_clock`, and the checkout is
# called floating-clock: a hyphen is not a module name. So the sources are
# staged under a correctly named folder, exactly as build.ps1 does.
stage_root="$build_dir/pkg"
package_dir="$stage_root/floating_clock"

want_dmg=0
want_tgz=0
for arg in "$@"; do
  case "$arg" in
    --dmg) want_dmg=1 ;;
    --tgz) want_tgz=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

python="${PYTHON:-python3}"
command -v "$python" >/dev/null || { echo "python3 not found on PATH" >&2; exit 1; }

version="$("$python" - "$repo_root" <<'PY'
import re, sys, io, os
text = io.open(os.path.join(sys.argv[1], "__init__.py"), encoding="utf-8").read()
print(re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1))
PY
)"
echo "Floating Clock $version"
echo "Python:   $("$python" -c 'import sys; print(sys.executable)')"
echo "Sources:  $repo_root"

echo
echo "[1/5] Installing build dependencies..."
"$python" -m pip install --quiet --upgrade \
  pillow pyinstaller PySide6 python-dateutil keyring pychromecast

echo "[2/5] Staging the package..."
rm -rf "$package_dir"
mkdir -p "$package_dir"
cp "$repo_root"/*.py "$package_dir/"
cp -R "$repo_root/qt" "$package_dir/qt"
cp -R "$repo_root/data" "$package_dir/data"
find "$package_dir" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "[3/5] Generating the icon..."
mkdir -p "$build_dir"
icon_png="$build_dir/FloatingClock.png"
PYTHONPATH="$stage_root" "$python" - "$icon_png" <<'PY'
import sys
from floating_clock import icon
image = icon.render_icon(1024)
image.save(sys.argv[1])
PY

icon_arg=()
if [[ "$OSTYPE" == darwin* ]]; then
  # .icns wants a whole iconset; iconutil is part of macOS.
  iconset="$build_dir/FloatingClock.iconset"
  rm -rf "$iconset"; mkdir -p "$iconset"
  for size in 16 32 64 128 256 512; do
    sips -z $size $size "$icon_png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double "$icon_png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$iconset" -o "$build_dir/FloatingClock.icns"
  icon_arg=(--icon "$build_dir/FloatingClock.icns")
else
  icon_arg=(--icon "$icon_png")
fi

echo "[4/5] Writing the entry point..."
entry="$build_dir/entry.py"
cat > "$entry" <<'PY'
"""PyInstaller entry point for the Qt host -- keeps the package importable."""
import sys

from floating_clock.qt.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
PY

echo "[5/5] Running PyInstaller..."
extra=()
if [[ "$OSTYPE" == darwin* ]]; then
  # A menu-bar app, not a Dock app: LSUIElement keeps the clock out of the
  # Dock and the app switcher, which is what a floating clock wants to be.
  plist="$build_dir/Info.plist.json"
  cat > "$plist" <<PY
{"LSUIElement": true, "CFBundleShortVersionString": "$version", "CFBundleVersion": "$version"}
PY
  extra=(--windowed --osx-bundle-identifier "ca.floatingclock.app")
else
  extra=(--windowed)
fi

"$python" -m PyInstaller \
  --noconfirm --clean "${extra[@]}" --onedir \
  --name FloatingClock \
  "${icon_arg[@]}" \
  --add-data "$package_dir/data/masjids.json:data" \
  --paths "$stage_root" \
  --distpath "$dist_dir" \
  --workpath "$build_dir/work" \
  --specpath "$build_dir" \
  --hidden-import dateutil.rrule \
  --hidden-import zoneinfo \
  --hidden-import keyring.backends.macOS \
  --hidden-import keyring.backends.SecretService \
  --hidden-import pychromecast \
  --hidden-import zeroconf \
  --exclude-module numpy \
  --exclude-module matplotlib \
  --exclude-module scipy \
  --exclude-module pandas \
  --exclude-module unittest \
  --exclude-module PIL.ImageQt \
  --exclude-module PyQt5 \
  --exclude-module PyQt6 \
  --exclude-module PySide2 \
  --exclude-module win32com \
  --exclude-module pywin32 \
  --exclude-module PySide6.QtWebEngineCore \
  --exclude-module PySide6.Qt3DCore \
  --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.QtQuick \
  --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtCharts \
  --exclude-module PySide6.QtDataVisualization \
  --exclude-module PySide6.QtPdf \
  --exclude-module PySide6.QtDesigner \
  --exclude-module PySide6.QtSql \
  --exclude-module PySide6.QtTest \
  --exclude-module tkinter \
  "$entry"

echo
if [[ "$OSTYPE" == darwin* ]]; then
  app="$dist_dir/FloatingClock.app"
  [[ -d "$app" ]] || { echo "build finished but $app is missing" >&2; exit 1; }
  # PyInstaller writes its own Info.plist, so LSUIElement goes in afterwards.
  /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$app/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$app/Contents/Info.plist"
  echo "Built $app"
  if [[ $want_dmg -eq 1 ]]; then
    arch="$(uname -m)"
    label="arm64"; [[ "$arch" == "x86_64" ]] && label="intel"
    dmg="$dist_dir/FloatingClock-$version-macos-$label.dmg"
    rm -f "$dmg"
    staging="$build_dir/dmg"
    rm -rf "$staging"; mkdir -p "$staging"
    cp -R "$app" "$staging/"
    ln -s /Applications "$staging/Applications"
    hdiutil create -volname "Floating Clock" -srcfolder "$staging" \
      -ov -format UDZO "$dmg" >/dev/null
    echo "Disk image: $dmg"
  fi
else
  folder="$dist_dir/FloatingClock"
  [[ -d "$folder" ]] || { echo "build finished but $folder is missing" >&2; exit 1; }
  echo "Built $folder"
  if [[ $want_tgz -eq 1 ]]; then
    tgz="$dist_dir/FloatingClock-$version-linux-$(uname -m).tar.gz"
    tar -czf "$tgz" -C "$dist_dir" FloatingClock
    echo "Archive: $tgz"
  fi
fi
