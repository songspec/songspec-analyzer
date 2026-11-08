#!/bin/bash
# Packages the macOS build into "SongSpec Analyzer.app" and a drag-to-Applications .dmg. Runs on macOS (the GitHub Actions
# workflow .github/workflows/macos.yml), after `cargo build --release -p songspec-app`.
#   tools/make_macos_app.sh <binary> <version> <out dir>
# The app is signed ad hoc (Apple Silicon needs a signature to run at all), not with a Developer ID: the first time it's opened,
# macOS asks to confirm in System Settings -> Privacy & Security -> Open Anyway.
set -euo pipefail
BIN="$1"; VERSION="$2"; OUT="$3"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="SongSpec Analyzer"
WORK="$(mktemp -d)"
APP="$WORK/stage/$NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$OUT"

cp "$BIN" "$APP/Contents/MacOS/SongSpecAnalyzer"
chmod 755 "$APP/Contents/MacOS/SongSpecAnalyzer"

# icon: assets/icon.png -> AppIcon.icns
ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
SRC="$ROOT/rust/songspec-app/assets/icon.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$SRC" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>org.songspec.analyzer</string>
  <key>CFBundleExecutable</key><string>SongSpecAnalyzer</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.music</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>AGPL-3.0-or-later. https://github.com/songspec/songspec-analyzer</string>
</dict>
</plist>
EOF

codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

ln -s /Applications "$WORK/stage/Applications"
DMG="$OUT/SongSpecAnalyzer-$VERSION-macOS-AppleSilicon.dmg"
rm -f "$DMG"
hdiutil create -volname "$NAME" -srcfolder "$WORK/stage" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
echo "$DMG"
