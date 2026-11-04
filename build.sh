#!/bin/bash
# Builds dist/Codex Monitor.app for this Mac's architecture. No Developer ID signing or notarization (the linker applies an ad hoc signature).
set -euo pipefail
cd "$(dirname "$0")"

MIN_MACOS="12.0"
BUNDLE_ID="com.bits.codexmonitor"
DIST="dist"
APP="$DIST/Codex Monitor.app"

VERSION="$(tr -d '[:space:]' < VERSION)"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "VERSION must look like 1.2.3 (got '$VERSION')." >&2; exit 1; }

command -v swiftc >/dev/null || { echo "Install Xcode Command Line Tools first (xcode-select --install)." >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10 or newer must be installed and first on PATH (python.org or Homebrew)." >&2; exit 1; }

ARCH="$(uname -m)"
case "$ARCH" in
  arm64|x86_64) ;;
  *) echo "Unsupported host architecture: $ARCH" >&2; exit 1 ;;
esac

# Only ever replace a previous Codex Monitor build.
if [ -e "$APP" ]; then
  existing="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null || true)"
  [ "$existing" = "$BUNDLE_ID" ] || { echo "Refusing to replace '$APP': it is not a Codex Monitor bundle." >&2; exit 1; }
fi

mkdir -p "$DIST"
STAGE="$(mktemp -d "$DIST/.build.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
STAGED="$STAGE/Codex Monitor.app"
RESOURCES="$STAGED/Contents/Resources"
mkdir -p "$STAGED/Contents/MacOS" "$RESOURCES/vendor/divoom_minitoo_codex"

swiftc -O -target "$ARCH-apple-macos$MIN_MACOS" Sources/Window.swift -o "$STAGED/Contents/MacOS/CodexMonitor"
cp Sources/feed.py Sources/monitor_client.py Sources/claude_usage.py Sources/claude_statusline.py \
  Scripts/connect_claude.py VERSION LICENSE THIRD_PARTY_NOTICES.md "$RESOURCES/"
cp vendor/divoom_minitoo_codex/{__init__.py,appserver.py,LICENSE} "$RESOURCES/vendor/divoom_minitoo_codex/"
cat > "$RESOURCES/BUILD_INFO.txt" <<INFO
Codex Monitor $VERSION
Architecture: $ARCH
Minimum macOS: $MIN_MACOS
Signing: no Developer ID signing or notarization (ad hoc linker signature only)
INFO

cat > "$STAGED/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>CodexMonitor</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleName</key><string>Codex Monitor</string>
  <key>CFBundleDisplayName</key><string>Codex Monitor</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>NSHumanReadableCopyright</key><string>Copyright (c) 2026 Bits Solutions. MIT License.</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
plutil -lint "$STAGED/Contents/Info.plist"

rm -rf "$APP"
mv "$STAGED" "$APP"
echo "Built $APP ($VERSION, $ARCH, macOS $MIN_MACOS+, no Developer ID signing or notarization)"
