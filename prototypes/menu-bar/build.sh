#!/bin/sh
# PROTOTYPE, NOT SHIPPED. Spike for issue #111; see docs/decisions/0004-menu-bar-item.md.
#
# Builds the test menu bar app with tools that ship with macOS (swiftc needs the
# Command Line Tools) into ~/Library/Application Support/io.flibtest.menu/.
#
#   ./build.sh swift    flib-menu-test.app         (io.flibtest.menu.swift)
#   ./build.sh applet   flib-menu-test-applet.app  (io.flibtest.menu.applet)
#
# Built in a temporary folder next to the target and swapped in only when every
# step worked, then signed ad hoc, as flib's notifier is (notifier.py).
set -eu

kind=${1:?swift or applet}
here=$(cd "$(dirname "$0")" && pwd)
base="$HOME/Library/Application Support/io.flibtest.menu"
state="$base/state"
mkdir -p "$state"
work=$(mktemp -d "$base/.build-XXXXXX")
trap 'rm -rf "$work"' EXIT

case "$kind" in
swift)
    name=flib-menu-test
    id=io.flibtest.menu.swift
    xcode-select -p >/dev/null 2>&1 || { echo "no Command Line Tools" >&2; exit 2; }
    app="$work/$name.app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    # Always name the target: left alone, swiftc on this Mac picked minos 28.0,
    # newer than the running macOS, and Launch Services refused the app (-10825).
    /usr/bin/xcrun swiftc -O -target "$(uname -m)-apple-macos26.0" \
        -framework AppKit "$here/swift/main.swift" \
        -o "$app/Contents/MacOS/$name"
    /usr/bin/plutil -create xml1 "$app/Contents/Info.plist"
    for kv in CFBundleExecutable:$name CFBundlePackageType:APPL CFBundleName:$name \
        CFBundleShortVersionString:1 CFBundleVersion:1 LSMinimumSystemVersion:26.0; do
        /usr/bin/plutil -insert "${kv%%:*}" -string "${kv#*:}" "$app/Contents/Info.plist"
    done
    ;;
applet)
    name=flib-menu-test-applet
    id=io.flibtest.menu.applet
    app="$work/$name.app"
    /usr/bin/osacompile -s -o "$app" "$here/applet/menu.applescript"
    rm -f "$app/Contents/Resources/Assets.car"
    /usr/bin/plutil -remove CFBundleIconName "$app/Contents/Info.plist" 2>/dev/null || true
    ;;
*) echo "swift or applet" >&2; exit 2 ;;
esac

plist="$app/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleIdentifier -string "$id" "$plist"
/usr/bin/plutil -replace LSUIElement -bool true "$plist"
/usr/bin/plutil -replace FlibMenuStateDir -string "$state" "$plist"
cp "$here"/icons/*.svg "$app/Contents/Resources/"
/usr/bin/codesign --force --deep --sign - "$app"
/usr/bin/codesign --verify --deep --strict "$app"

target="$base/$name.app"
rm -rf "$target.old"
[ -e "$target" ] && mv "$target" "$target.old"
mv "$app" "$target"
rm -rf "$target.old"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$target"
echo "$target"
