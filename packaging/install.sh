#!/usr/bin/env bash
# ==============================================================================
# RAAH Linux Desktop Application — User Installation Script
# ==============================================================================
# Registers RAAH in the user's Linux desktop environment:
# 1. Installs high-resolution icons to ~/.local/share/icons/hicolor/
# 2. Installs raah.desktop to ~/.local/share/applications/
# 3. Creates launcher symlink in ~/.local/bin/
# 4. Updates desktop and icon caches
# ==============================================================================

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "======================================================================"
echo " Installing RAAH Command Center Desktop Application"
echo "======================================================================"

USER_APPS_DIR="${HOME}/.local/share/applications"
USER_ICONS_BASE="${HOME}/.local/share/icons/hicolor"
USER_BIN_DIR="${HOME}/.local/bin"

# 1. Create target directories
mkdir -p "${USER_APPS_DIR}"
mkdir -p "${USER_BIN_DIR}"

# 2. Ensure executable permissions
chmod +x "${REPO_ROOT}/bin/raah-desktop"

# 3. Install Multi-Resolution Icons
echo "[1/4] Installing application icons..."
SIZES=(16 32 48 64 128 256 512)
for SZ in "${SIZES[@]}"; do
    SRC="${REPO_ROOT}/desktop/assets/icons/raah_${SZ}x${SZ}.png"
    DEST_DIR="${USER_ICONS_BASE}/${SZ}x${SZ}/apps"
    mkdir -p "${DEST_DIR}"
    if [ -f "${SRC}" ]; then
        cp -f "${SRC}" "${DEST_DIR}/raah.png"
        echo "  -> Installed ${SZ}x${SZ} icon"
    fi
done

# Install scalable SVG
SCALABLE_DIR="${USER_ICONS_BASE}/scalable/apps"
mkdir -p "${SCALABLE_DIR}"
cp -f "${REPO_ROOT}/desktop/assets/raah.svg" "${SCALABLE_DIR}/raah.svg"
echo "  -> Installed scalable vector SVG icon"

# 4. Create Desktop Entry in ~/.local/share/applications
echo "[2/4] Installing desktop menu entry..."
cat > "${USER_APPS_DIR}/raah.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=RAAH Command Center
GenericName=Emergency Medical Dispatch & Tactical Coordination Platform
Comment=AI/ML-powered dynamic ambulance dispatch, triage, and hospital coordination
Exec=${REPO_ROOT}/bin/raah-desktop
Icon=raah
Terminal=false
StartupNotify=true
StartupWMClass=raah-command-center
Categories=Science;MedicalSoftware;
Keywords=RAAH;Emergency;Dispatch;Ambulance;Triage;Tactical;EMS;Hospital;
Actions=Fullscreen;

[Desktop Action Fullscreen]
Name=Open Fullscreen
Exec=${REPO_ROOT}/bin/raah-desktop --fullscreen
EOF

chmod +x "${USER_APPS_DIR}/raah.desktop"
echo "  -> Created ${USER_APPS_DIR}/raah.desktop"

# 5. Create CLI Symlink in ~/.local/bin
echo "[3/4] Creating launcher command symlink..."
ln -sf "${REPO_ROOT}/bin/raah-desktop" "${USER_BIN_DIR}/raah-desktop"
echo "  -> Created symlink ${USER_BIN_DIR}/raah-desktop"

# 6. Update system desktop database and icon caches
echo "[4/4] Updating system desktop databases..."
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${USER_APPS_DIR}" || true
    echo "  -> Desktop database updated"
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${USER_ICONS_BASE}" >/dev/null 2>&1 || true
    echo "  -> Icon cache updated"
fi

echo "======================================================================"
echo " INSTALLATION COMPLETE!"
echo " RAAH Command Center is now available in your Application Menu."
echo " You can also launch it directly from the terminal with: raah-desktop"
echo "======================================================================"
