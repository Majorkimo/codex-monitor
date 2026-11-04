#!/bin/bash
# Builds, then packages dist/Codex-Monitor-<version>-macos-<arch>.zip plus a .sha256 file.
# The archive is NOT Developer ID signed or notarized (the compiler may apply an ad hoc signature);
# see docs/RELEASE.md before publishing it.
set -euo pipefail
cd "$(dirname "$0")"

./build.sh

VERSION="$(tr -d '[:space:]' < VERSION)"
ARCH="$(uname -m)"
APP="dist/Codex Monitor.app"
NAME="Codex-Monitor-$VERSION-macos-$ARCH.zip"
ZIP="dist/$NAME"

rm -f "$ZIP" "$ZIP.sha256"
# ditto keeps the executable bit and bundle layout; plain `zip` of a folder may not.
ditto -c -k --keepParent "$APP" "$ZIP"

CHECK="$(mktemp -d dist/.verify.XXXXXX)"
trap 'rm -rf "$CHECK"' EXIT
ditto -x -k "$ZIP" "$CHECK"
BINARY="$CHECK/Codex Monitor.app/Contents/MacOS/CodexMonitor"
[ -x "$BINARY" ] || { echo "Packaged executable lost its executable bit." >&2; exit 1; }
[ "$(lipo -archs "$BINARY")" = "$ARCH" ] || { echo "Packaged binary is not $ARCH only." >&2; exit 1; }

(cd dist && shasum -a 256 "$NAME" > "$NAME.sha256")
echo "Packaged $ZIP ($ARCH only, no Developer ID signature, not notarized)"
cat "$ZIP.sha256"
