#!/bin/bash
# Build script: creates the .app bundle and packages it into a polished DMG
set -e

APP_NAME="Open Transcribe"
DMG_NAME="Open-Transcribe"
DIST_DIR="dist"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Building $APP_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# Check for create-dmg
if ! command -v create-dmg &>/dev/null; then
    echo "Installing create-dmg..."
    brew install create-dmg
fi

# 1. Clean previous builds
echo "Cleaning previous builds..."
rm -rf build dist

# 2. Build .app with py2app
echo "Building .app bundle..."
uv run python setup.py py2app

# 3. Verify the .app was created
if [ ! -d "$DIST_DIR/$APP_NAME.app" ]; then
    echo "Error: .app bundle not found. Build failed."
    exit 1
fi

echo ".app bundle created successfully."

# 4. Create polished DMG with create-dmg
echo "Creating DMG..."

DMG_FINAL="$DIST_DIR/${DMG_NAME}.dmg"
rm -f "$DMG_FINAL"

create-dmg \
    --volname "$APP_NAME" \
    --volicon "media/icon.icns" \
    --window-pos 200 120 \
    --window-size 540 380 \
    --icon-size 96 \
    --icon "$APP_NAME.app" 140 180 \
    --hide-extension "$APP_NAME.app" \
    --app-drop-link 400 180 \
    "$DMG_FINAL" \
    "$DIST_DIR/$APP_NAME.app"

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Build complete!"
echo "  DMG: $DMG_FINAL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
echo "To install:"
echo "  1. Open $DMG_FINAL"
echo "  2. Drag '$APP_NAME' to Applications"
echo "  3. Right-click the app → Open (first time only)"
