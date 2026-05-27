#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HOME}/.config/kanban-agent"
APP_DEST="${HOME}/Applications/HeadNurse.app"
OLD_PLIST="${HOME}/Library/LaunchAgents/com.kanban-agent.plist"

echo "Step 1: Verifying Python 3.11+ at /opt/homebrew/bin/python3"
/opt/homebrew/bin/python3 -c 'import sys; assert sys.version_info >= (3,11), sys.version'

echo "Step 2: Installing kanban-agent and dev deps"
/opt/homebrew/bin/python3 -m pip install -e ".[dev]" --break-system-packages

echo "Step 3: Rendering icons"
/opt/homebrew/bin/python3 scripts/render_icons.py

echo "Step 4: Building HeadNurse.app"
rm -rf build dist
/opt/homebrew/bin/python3 setup_app.py py2app

echo "Step 5: Installing app to ~/Applications"
mkdir -p "${HOME}/Applications"
rm -rf "${APP_DEST}"
cp -R dist/HeadNurse.app "${APP_DEST}"

echo "Step 6: Migrating from launchd if present"
if [ -f "${OLD_PLIST}" ]; then
    launchctl unload "${OLD_PLIST}" 2>/dev/null || true
    rm -f "${OLD_PLIST}"
    echo "  ✓ Removed old launchd plist"
fi

echo "Step 7: Ensuring config exists"
if [ ! -f "${CONFIG_DIR}/config.yaml" ]; then
    mkdir -p "${CONFIG_DIR}"
    cp config.example.yaml "${CONFIG_DIR}/config.yaml"
    echo "  ✓ Created ${CONFIG_DIR}/config.yaml — edit it before first run."
fi

echo ""
echo "✓ Done. Launch with: open ${APP_DEST}"
echo "  To auto-start at login: System Settings → General → Login Items → +,"
echo "  and add ${APP_DEST}."
