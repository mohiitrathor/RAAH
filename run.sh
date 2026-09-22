#!/usr/bin/env bash
# ==============================================================================
# RAAH Intelligent EMS Platform — Unified Launcher & CLI
# ==============================================================================
# Single entrypoint for running the Command Center, executing live demo flows,
# running benchmarks, executing test suites, and launching the desktop client.
# ==============================================================================

set -eo pipefail

# 1. Dynamically resolve repository root directory
SOURCE="${BASH_SOURCE[0]}"
while [ -h "${SOURCE}" ]; do
    DIR="$(cd -P "$(dirname "${SOURCE}")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "${SOURCE}")"
    [[ ${SOURCE} != /* ]] && SOURCE="${DIR}/${SOURCE}"
done
REPO_DIR="$(cd -P "$(dirname "${SOURCE}")" >/dev/null 2>&1 && pwd)"
cd "${REPO_DIR}"

# Ensure runtime directories exist
mkdir -p "${REPO_DIR}/data/logs" "${REPO_DIR}/data/replays" "${REPO_DIR}/data/regression"

# 2. Discover Python interpreter dynamically (no hardcoded machine paths)
PYTHON=""

is_valid_python() {
    local cand="$1"
    [ -n "${cand}" ] && [ -x "${cand}" ] && "${cand}" -c "import fastapi, uvicorn, sklearn, pandas" 2>/dev/null
}

# 1. Direct explicit override
if [ -n "${RAAH_PYTHON}" ] && [ -x "${RAAH_PYTHON}" ]; then
    PYTHON="${RAAH_PYTHON}"
fi

# 2. Check virtual/conda environments if active and valid
if [ -z "${PYTHON}" ] && [ -n "${VIRTUAL_ENV}" ] && is_valid_python "${VIRTUAL_ENV}/bin/python"; then
    PYTHON="${VIRTUAL_ENV}/bin/python"
fi
if [ -z "${PYTHON}" ] && [ -n "${CONDA_PREFIX}" ] && is_valid_python "${CONDA_PREFIX}/bin/python"; then
    PYTHON="${CONDA_PREFIX}/bin/python"
fi

# 3. Discover candidates across common environments
if [ -z "${PYTHON}" ]; then
    CANDIDATES=(
        "${HOME}/miniconda3/envs/ai_env/bin/python"
        "${HOME}/anaconda3/envs/ai_env/bin/python"
        "${HOME}/.conda/envs/ai_env/bin/python"
        "${REPO_DIR}/.venv/bin/python"
        "${REPO_DIR}/venv/bin/python"
        "$(command -v python3 2>/dev/null || true)"
        "$(command -v python 2>/dev/null || true)"
    )
    for CAND in "${CANDIDATES[@]}"; do
        if is_valid_python "${CAND}"; then
            PYTHON="${CAND}"
            break
        fi
    done
fi

# 4. Fallback to any executable python if dependencies check failed
if [ -z "${PYTHON}" ]; then
    for CAND in "${CANDIDATES[@]}"; do
        if [ -n "${CAND}" ] && [ -x "${CAND}" ]; then
            PYTHON="${CAND}"
            break
        fi
    done
fi

if [ -z "${PYTHON}" ]; then
    echo "[RAAH ERROR] Unable to locate a valid Python interpreter." >&2
    echo "Please set RAAH_PYTHON or activate your virtual/conda environment." >&2
    exit 1
fi

# 3. Validate core dependencies
validate_env() {
    is_valid_python "${PYTHON}" || {
        echo "[RAAH ERROR] Python environment (${PYTHON}) is missing required dependencies." >&2
        echo "Please install dependencies via: pip install -r requirements.txt" >&2
        exit 1
    }
}

# 4. Display help banner
show_help() {
    cat << 'EOF'
==============================================================================
  RAAH: Real-Time Ambulance Allocation & Hospital Redirection Platform
==============================================================================

Usage: ./run.sh [COMMAND] [OPTIONS]

Commands:
  start | api | serve    Start the FastAPI backend & Command Center dashboard
  demo                   Execute automated live end-to-end demo flow
  benchmark              Run dispatch engine and system performance benchmarks
  test [ARGS...]         Run full pytest validation suite
  desktop                Launch native Linux GTK/WebKit desktop wrapper
  docker                 Display Docker run and build instructions
  help | --help | -h     Display this help screen

Examples:
  ./run.sh               # Launch backend server on http://localhost:8000
  ./run.sh demo          # Run live surge & divert demonstration
  ./run.sh benchmark     # Run dispatch performance & quality benchmark
  ./run.sh test          # Run test suite with pytest -q
  ./run.sh desktop       # Open dedicated desktop window

EOF
}

COMMAND="${1:-start}"
shift || true

case "${COMMAND}" in
    start|api|serve)
        validate_env
        echo "=============================================================================="
        echo "  RAAH TACTICAL COMMAND CENTER — INITIALIZING BACKEND"
        echo "=============================================================================="
        echo "  Python Runtime:   ${PYTHON}"
        echo "  Repository Root:  ${REPO_DIR}"
        echo "  Dashboard URL:    http://localhost:8000"
        echo "  API Documentation:http://localhost:8000/docs"
        echo "  Health Endpoint:  http://localhost:8000/health"
        echo "=============================================================================="
        echo "Press Ctrl+C to terminate backend server."
        echo
        exec "${PYTHON}" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 "$@"
        ;;

    demo)
        validate_env
        # Check if backend is running on 8000
        if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
            echo "[RAAH] Backend is not running on http://127.0.0.1:8000."
            echo "[RAAH] Starting temporary backend daemon for demo execution..."
            "${PYTHON}" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 >"${REPO_DIR}/data/logs/demo_api.log" 2>&1 &
            DEMO_PID=$!
            trap "kill ${DEMO_PID} 2>/dev/null || true" EXIT
            # Wait for backend readiness
            for i in {1..30}; do
                if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
                    break
                fi
                sleep 0.5
            done
        fi
        exec "${PYTHON}" "${REPO_DIR}/scripts/demo_runner.py" "$@"
        ;;

    benchmark)
        validate_env
        exec "${PYTHON}" "${REPO_DIR}/scripts/benchmark_suite.py" "$@"
        ;;

    test)
        validate_env
        exec "${PYTHON}" -m pytest -q "$@"
        ;;

    desktop)
        if [ -x "${REPO_DIR}/bin/raah-desktop" ]; then
            exec "${REPO_DIR}/bin/raah-desktop" "$@"
        else
            echo "[RAAH ERROR] Desktop launcher '${REPO_DIR}/bin/raah-desktop' not executable." >&2
            exit 1
        fi
        ;;

    docker)
        echo "=============================================================================="
        echo "  RAAH DOCKER CONTAINERIZATION INSTRUCTIONS"
        echo "=============================================================================="
        echo "To build and run RAAH in Docker:"
        echo "  docker compose up --build"
        echo
        echo "To run container standalone:"
        echo "  docker build -t raah:latest ."
        echo "  docker run -p 8000:8000 raah:latest"
        echo "=============================================================================="
        ;;

    help|--help|-h)
        show_help
        ;;

    *)
        echo "[RAAH ERROR] Unknown command: ${COMMAND}" >&2
        echo "Run './run.sh --help' for available commands." >&2
        exit 1
        ;;
esac
