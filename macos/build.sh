#!/bin/bash
# Build trawl.app against this checkout's .venv and install it in /Applications.
# Python code runs from the checkout, so pulling changes needs a restart of the app, not a rebuild;
# only launcher.c or Info.plist changes do. Needs `pip install -e ".[app]"` first. LIBDIR and its
# rpath cover Pythons whose python-config points elsewhere (conda's).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
python="$root/.venv/bin/python"
config="$("$python" -c 'import sys; print(f"{sys.base_prefix}/bin/python{sys.version_info[0]}.{sys.version_info[1]}-config")')"
libdir="$("$python" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
app="/Applications/trawl.app"

[ -f "$root/macos/AppIcon.icns" ] || swift "$root/macos/make_icon.swift" "$root/assets/logo.svg" "$root/macos/AppIcon.icns"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$root/macos/Info.plist" "$app/Contents/Info.plist"
cp "$root/macos/AppIcon.icns" "$app/Contents/Resources/AppIcon.icns"
# shellcheck disable=SC2046  # The config flags must split into separate arguments.
clang -O2 -DVENV_PYTHON="\"$python\"" "$root/macos/launcher.c" \
    $("$config" --includes) $("$config" --embed --ldflags) -L"$libdir" -Wl,-rpath,"$libdir" \
    -o "$app/Contents/MacOS/trawl"
codesign --force --sign - --identifier com.yuxuanyan.trawl "$app"

echo "Installed $app"
