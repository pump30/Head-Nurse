#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HOME}/.config/kanban-agent"
APP_DEST="${HOME}/Applications/HeadNurse.app"
OLD_PLIST="${HOME}/Library/LaunchAgents/com.kanban-agent.plist"

# Pick a Python 3.11+ that has setuptools available. py2app + pyobjc are
# fussy about which interpreter; we prefer 3.13 because newer (3.14+) is
# often missing prebuilt wheels for the deps we need.
PYTHON=""
for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys, setuptools; assert sys.version_info >= (3,11)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Could not find Python 3.11+ with setuptools." >&2
    echo "       Install via: brew install python@3.13" >&2
    exit 1
fi

echo "Step 1: Using ${PYTHON}"

echo "Step 2: Installing kanban-agent and dev deps"
"$PYTHON" -m pip install -e ".[dev]" --break-system-packages

echo "Step 3: Rendering icons"
"$PYTHON" scripts/render_icons.py

echo "Step 4: Building HeadNurse.app"
rm -rf build dist
"$PYTHON" setup_app.py py2app

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
