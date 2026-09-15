#!/usr/bin/env bash
# ==============================================================================
# RAAH Linux Desktop Application — Uninstaller Script
# ==============================================================================

set -eo pipefail

echo "======================================================================"
echo " Uninstalling RAAH Command Center Desktop Integration"
echo "======================================================================"

USER_APPS_DIR="${HOME}/.local/share/applications"
USER_ICONS_BASE="${HOME}/.local/share/icons/hicolor"
USER_BIN_DIR="${HOME}/.local/bin"

# 1. Remove Desktop file
if [ -f "${USER_APPS_DIR}/raah.desktop" ]; then
    rm -f "${USER_APPS_DIR}/raah.desktop"
    echo "  -> Removed ${USER_APPS_DIR}/raah.desktop"
fi

# 2. Remove Binary symlink
if [ -L "${USER_BIN_DIR}/raah-desktop" ]; then
    rm -f "${USER_BIN_DIR}/raah-desktop"
    echo "  -> Removed ${USER_BIN_DIR}/raah-desktop"
fi

# 3. Remove Icons
SIZES=(16 32 48 64 128 256 512)
for SZ in "${SIZES[@]}"; do
    TARGET="${USER_ICONS_BASE}/${SZ}x${SZ}/apps/raah.png"
    if [ -f "${TARGET}" ]; then
        rm -f "${TARGET}"
        echo "  -> Removed ${SZ}x${SZ} icon"
    fi
done

if [ -f "${USER_ICONS_BASE}/scalable/apps/raah.svg" ]; then
    rm -f "${USER_ICONS_BASE}/scalable/apps/raah.svg"
    echo "  -> Removed scalable SVG icon"
fi

# 4. Refresh caches
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${USER_APPS_DIR}" || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${USER_ICONS_BASE}" >/dev/null 2>&1 || true
fi

echo "======================================================================"
echo " RAAH Desktop integration removed."
echo "======================================================================"
