"""
RAAH Backend Process Supervisor
===============================

Manages the lifecycle of the RAAH FastAPI backend subprocess:
1. Discovers and validates Python interpreter with RAAH dependencies.
2. Checks port availability or detects existing running instances.
3. Launches Uvicorn in a dedicated POSIX process group (os.setsid).
4. Pipes logs to data/logs/desktop_backend.log.
5. Polls /health/ready until all subsystems (ML, DB, Simulation) are READY.
6. Executes deterministic clean shutdown (SIGTERM -> grace period -> SIGKILL).
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "data" / "logs"
DEFAULT_LOG_FILE = LOG_DIR / "desktop_backend.log"


class BackendStartupError(Exception):
    """Raised when backend subprocess fails to start or crashes during initialization."""

    def __init__(self, message: str, exit_code: Optional[int] = None, log_excerpt: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.log_excerpt = log_excerpt


class BackendSupervisor:
    """
    Supervises the RAAH FastAPI backend process for the desktop application.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        startup_timeout: float = 35.0,
        log_file: Optional[Path] = None,
        custom_python: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.startup_timeout = startup_timeout
        self.log_file = log_file or DEFAULT_LOG_FILE
        self.custom_python = custom_python

        self.process: Optional[subprocess.Popen] = None
        self._log_handle = None
        self.is_adopted_instance = False
        self.python_bin: Optional[str] = None

    @classmethod
    def load_env_defaults(cls) -> Tuple[str, int]:
        """Read host and port from .env if present, otherwise safe local defaults."""
        host = "127.0.0.1"
        port = 8000
        env_path = REPO_ROOT / ".env"

        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or not line or "=" not in line:
                            continue
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key == "RAAH_HOST" and val:
                            host = val
                        elif key == "RAAH_PORT" and val.isdigit():
                            port = int(val)
            except Exception:
                pass

        # For desktop app, prefer loopback if configured as 0.0.0.0
        if host == "0.0.0.0":
            host = "127.0.0.1"

        return host, port

    @staticmethod
    def _verify_python_candidate(candidate_path: str, timeout: int = 20) -> bool:
        """Verify that a Python binary can import RAAH core backend dependencies."""
        if not candidate_path or not os.path.isfile(candidate_path) or not os.access(candidate_path, os.X_OK):
            return False

        check_code = (
            "import fastapi, uvicorn, sklearn, joblib, pandas, pydantic, pydantic_settings; "
            "print('OK')"
        )
        try:
            res = subprocess.run(
                [candidate_path, "-c", check_code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return res.returncode == 0 and "OK" in res.stdout
        except Exception:
            return False

    def find_backend_python(self) -> str:
        """
        Locate the appropriate Python interpreter for running the RAAH backend.
        Checks candidate locations in priority order.
        """
        if self.custom_python and self._verify_python_candidate(self.custom_python):
            return self.custom_python

        env_py = os.environ.get("RAAH_PYTHON")
        if env_py and self._verify_python_candidate(env_py):
            return env_py

        candidates: List[str] = [
            # 1. Current runtime Python interpreter
            sys.executable,
            # 2. Known dedicated conda environments
            os.path.expanduser("~/miniconda3/envs/ai_env/bin/python"),
            os.path.expanduser("~/anaconda3/envs/ai_env/bin/python"),
            os.path.expanduser("~/.conda/envs/ai_env/bin/python"),
        ]

        # 2. Conda active prefix
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            candidates.append(os.path.join(conda_prefix, "bin", "python"))

        # 3. Virtualenv active
        virtual_env = os.environ.get("VIRTUAL_ENV")
        if virtual_env:
            candidates.append(os.path.join(virtual_env, "bin", "python"))

        # 4. Project-local virtualenvs
        candidates.extend([
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            str(REPO_ROOT / "venv" / "bin" / "python"),
        ])

        # 5. Active interpreter
        candidates.append(sys.executable)

        # 6. System PATH interpreters
        for name in ("python3", "python"):
            p = shutil.which(name)
            if p:
                candidates.append(p)

        # Filter duplicates while preserving order
        seen = set()
        unique_candidates = []
        for cand in candidates:
            if cand:
                norm = os.path.realpath(os.path.abspath(cand))
                if norm not in seen:
                    seen.add(norm)
                    unique_candidates.append(cand)

        for cand in unique_candidates:
            if cand and self._verify_python_candidate(cand):
                self.python_bin = cand
                return cand

        err_msg = (
            "Could not locate a Python environment with RAAH dependencies.\n"
            "Checked ai_env, virtualenvs, and active environment.\n"
            "Please ensure the Conda 'ai_env' or a virtual environment with requirements.txt is installed."
        )
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"\n[DESKTOP ERROR] {time.strftime('%Y-%m-%d %H:%M:%S')}: {err_msg}\n")
        except Exception:
            pass

        raise RuntimeError(err_msg)

    def check_health(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Query the readiness endpoint http://<host>:<port>/health/ready.
        Returns (is_ready, data_dict).
        """
        url = f"http://{self.host}:{self.port}/health/ready"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RAAH-Desktop-Supervisor/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    is_ready = data.get("status") == "READY" or data.get("ready") is True
                    return is_ready, data
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
                return False, data
            except Exception:
                return False, {"status": "HTTP_ERROR", "code": e.code}
        except Exception as e:
            return False, {"status": "CONNECTION_FAILED", "error": str(e)}

        return False, {"status": "UNKNOWN"}

    def is_port_listening(self) -> bool:
        """Check if anything is currently listening on host:port."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            res = s.connect_ex((self.host, self.port))
            return res == 0
        finally:
            s.close()

    def get_log_excerpt(self, max_lines: int = 25) -> str:
        """Retrieve recent lines from the backend log file for diagnostic display."""
        if not self.log_file.exists():
            return "Log file has not been created yet."
        try:
            with open(self.log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return "".join(lines[-max_lines:]) if lines else "(Log file is empty)"
        except Exception as e:
            return f"Failed to read log file: {e}"

    def start(self, progress_callback=None) -> bool:
        """
        Start the backend process and poll until readiness is confirmed.
        progress_callback: optional callable(status_msg, detail_msg)
        """
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback("Checking network port...", f"Testing {self.host}:{self.port}")

        # Check if already listening
        if self.is_port_listening():
            is_ready, info = self.check_health()
            if is_ready:
                if progress_callback:
                    progress_callback("Connected to running instance", f"Reusing RAAH backend at {self.host}:{self.port}")
                self.is_adopted_instance = True
                return True
            else:
                # Might be starting up or conflicting
                if "status" in info and info.get("status") in ("NOT_READY", "ALIVE"):
                    if progress_callback:
                        progress_callback("Waiting for existing instance...", f"Backend is currently initializing...")
                    return self._poll_readiness(progress_callback)
                else:
                    raise BackendStartupError(
                        f"Port {self.port} is already in use by an unrecognized process.",
                        log_excerpt=f"Connection succeeded but /health/ready returned: {info}",
                    )

        # Locate Python
        if progress_callback:
            progress_callback("Locating Python environment...", "Detecting Conda / virtualenv with RAAH dependencies...")

        py_bin = self.find_backend_python()

        # Open log file
        self._log_handle = open(self.log_file, "a", encoding="utf-8")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._log_handle.write(f"\n{'=' * 60}\n[DESKTOP LAUNCH] {timestamp} via {py_bin}\n{'=' * 60}\n")
        self._log_handle.flush()

        cmd = [
            py_bin,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]

        if progress_callback:
            progress_callback("Starting RAAH backend service...", f"Spawning Uvicorn on {self.host}:{self.port}...")

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["RAAH_HOST"] = self.host
        env["RAAH_PORT"] = str(self.port)

        # Spawn in a new process group
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            raise BackendStartupError(f"Failed to spawn backend process: {e}")

        # Poll readiness
        return self._poll_readiness(progress_callback)

    def _poll_readiness(self, progress_callback=None) -> bool:
        """Poll /health/ready until ready or timeout."""
        start_time = time.time()

        status_messages = [
            "Initializing clinical ML inference pipeline...",
            "Loading authoritative Jaipur geospatial dataset...",
            "Initializing authoritative DispatchState...",
            "Starting SQLite persistence & checkpoint workers...",
            "Validating Server-Sent Events broadcaster...",
            "Command Center systems operational...",
        ]
        msg_idx = 0

        while (time.time() - start_time) < self.startup_timeout:
            # Check if subprocess died
            if self.process and self.process.poll() is not None:
                exit_code = self.process.returncode
                log_snippet = self.get_log_excerpt(30)
                raise BackendStartupError(
                    f"Backend process terminated unexpectedly with exit code {exit_code}.",
                    exit_code=exit_code,
                    log_excerpt=log_snippet,
                )

            is_ready, data = self.check_health()
            if is_ready:
                if progress_callback:
                    progress_callback("Ready", "All RAAH subsystems initialized successfully.")
                return True

            if progress_callback:
                elapsed = int(time.time() - start_time)
                detail = status_messages[min(msg_idx, len(status_messages) - 1)]
                if elapsed > (msg_idx + 1) * 3:
                    msg_idx += 1
                progress_callback(
                    f"Connecting to RAAH Core ({elapsed}s)...",
                    detail,
                )

            time.sleep(0.3)

        # Timeout reached
        log_snippet = self.get_log_excerpt(30)
        raise BackendStartupError(
            f"Timed out waiting for backend to become ready after {self.startup_timeout:.0f} seconds.",
            log_excerpt=log_snippet,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """
        Gracefully terminate the backend process group with fallback to SIGKILL.
        """
        if self.is_adopted_instance:
            # Do not kill externally managed instances
            return

        if not self.process:
            return

        pid = self.process.pid
        try:
            # Check if still running
            if self.process.poll() is None:
                # Send SIGTERM to the process group
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)

                # Wait for clean exit
                start = time.time()
                while time.time() - start < timeout:
                    if self.process.poll() is not None:
                        break
                    time.sleep(0.1)

                # If still alive, force kill
                if self.process.poll() is None:
                    os.killpg(pgid, signal.SIGKILL)
                    self.process.wait(timeout=1.0)
        except ProcessLookupError:
            pass
        except Exception:
            pass
        finally:
            self.process = None
            if self._log_handle and not self._log_handle.closed:
                try:
                    self._log_handle.flush()
                    self._log_handle.close()
                except Exception:
                    pass
                self._log_handle = None
