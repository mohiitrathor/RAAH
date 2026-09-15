#!/usr/bin/env bash
# ==============================================================================
# RAAH Linux Desktop Application Build & Asset Pipeline
# ==============================================================================

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "======================================================================"
echo " RAAH Linux Desktop Application — Build Pipeline"
echo "======================================================================"

# 1. Check required tools
echo "[1/5] Checking build dependencies..."
REQUIRED_TOOLS=("rsvg-convert" "desktop-file-validate")
for TOOL in "${REQUIRED_TOOLS[@]}"; do
    if ! command -v "${TOOL}" >/dev/null 2>&1; then
        echo "  [ERROR] Required build tool '${TOOL}' not found in PATH." >&2
        exit 1
    fi
    echo "  -> Found ${TOOL}"
done

# 2. Render multi-resolution icons from master SVG
echo "[2/5] Generating multi-resolution icon assets..."
mkdir -p "${REPO_ROOT}/desktop/assets/icons"
SIZES=(16 32 48 64 128 256 512)
for SZ in "${SIZES[@]}"; do
    TARGET="${REPO_ROOT}/desktop/assets/icons/raah_${SZ}x${SZ}.png"
    rsvg-convert -w "${SZ}" -h "${SZ}" "${REPO_ROOT}/desktop/assets/raah.svg" -o "${TARGET}"
    echo "  -> Generated ${TARGET} (${SZ}x${SZ})"
done
cp "${REPO_ROOT}/desktop/assets/icons/raah_256x256.png" "${REPO_ROOT}/desktop/assets/raah.png"

# 3. Validate FreeDesktop .desktop specification
echo "[3/5] Validating desktop specification..."
desktop-file-validate "${REPO_ROOT}/desktop/raah.desktop"
echo "  -> desktop/raah.desktop is valid."

# 4. Ensure launcher permissions
echo "[4/5] Verifying executable permissions..."
chmod +x "${REPO_ROOT}/bin/raah-desktop"
echo "  -> bin/raah-desktop set to executable (+x)."

# 5. Build distribution tarball
echo "[5/5] Creating release distribution tarball..."
mkdir -p "${REPO_ROOT}/dist"
DIST_ARCHIVE="${REPO_ROOT}/dist/raah-desktop-linux-x86_64.tar.gz"

tar --exclude='.git' \
    --exclude='.pytest_cache' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='dist' \
    --exclude='data/*.db*' \
    --exclude='data/logs*' \
    --warning=no-file-changed \
    -czf "${DIST_ARCHIVE}" \
    -C "$(dirname "${REPO_ROOT}")" "$(basename "${REPO_ROOT}")"

echo "  -> Created distribution archive: ${DIST_ARCHIVE}"
echo "======================================================================"
echo " BUILD SUCCESSFUL"
echo "======================================================================"
