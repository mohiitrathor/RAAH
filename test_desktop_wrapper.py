"""
RAAH Linux Desktop Wrapper & Integration Test Suite
===================================================

Verifies:
1. FreeDesktop XDG .desktop file specification validity.
2. Master SVG emblem and multi-resolution icon assets.
3. Executable launcher script permissions and structure.
4. Python environment discovery for RAAH dependencies.
5. Splash screen and diagnostic error view HTML generators.
6. End-to-end BackendSupervisor lifecycle:
   - Subprocess spawning in dedicated process group
   - Polling /health/ready to confirmation
   - Clean shutdown (SIGTERM -> state flush -> exit) with zero orphans.
7. Packaging scripts and Arch PKGBUILD integrity.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.supervisor import BackendSupervisor
from desktop.splash import get_splash_html, get_error_html


def get_free_port() -> int:
    """Find a random available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestDesktopWrapper(unittest.TestCase):
    """Test suite for RAAH Desktop Application wrapper."""

    def test_01_desktop_file_validity(self):
        """desktop/raah.desktop must be fully compliant with desktop-file-validate."""
        desktop_file = ROOT / "desktop" / "raah.desktop"
        self.assertTrue(desktop_file.exists(), "raah.desktop does not exist")

        if shutil.which("desktop-file-validate"):
            res = subprocess.run(
                ["desktop-file-validate", str(desktop_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                res.returncode, 0,
                f"desktop-file-validate failed: {res.stdout}\n{res.stderr}"
            )

        with open(desktop_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Name=RAAH Command Center", content)
        self.assertIn("Icon=raah", content)
        self.assertIn("StartupWMClass=raah-command-center", content)
        self.assertIn("Type=Application", content)
        self.assertIn("Categories=", content)

    def test_02_icon_assets(self):
        """Master SVG and all multi-resolution PNG icons must exist and be valid."""
        svg_file = ROOT / "desktop" / "assets" / "raah.svg"
        self.assertTrue(svg_file.exists(), "Master SVG raah.svg missing")
        self.assertGreater(svg_file.stat().st_size, 500, "SVG file too small")

        # Check standard sizes
        icons_dir = ROOT / "desktop" / "assets" / "icons"
        self.assertTrue(icons_dir.exists(), "Icons directory missing")

        sizes = [16, 32, 48, 64, 128, 256, 512]
        for sz in sizes:
            png_file = icons_dir / f"raah_{sz}x{sz}.png"
            self.assertTrue(png_file.exists(), f"Missing icon: raah_{sz}x{sz}.png")
            self.assertGreater(png_file.stat().st_size, 100, f"Icon raah_{sz}x{sz}.png is empty")

        main_png = ROOT / "desktop" / "assets" / "raah.png"
        self.assertTrue(main_png.exists(), "desktop/assets/raah.png missing")

    def test_03_launcher_permissions(self):
        """bin/raah-desktop must be executable."""
        launcher = ROOT / "bin" / "raah-desktop"
        self.assertTrue(launcher.exists(), "bin/raah-desktop missing")
        self.assertTrue(os.access(launcher, os.X_OK), "bin/raah-desktop not executable")

    def test_04_python_discovery(self):
        """BackendSupervisor must discover an interpreter with RAAH backend dependencies."""
        supervisor = BackendSupervisor()
        py_bin = supervisor.find_backend_python()
        self.assertTrue(py_bin, "Failed to locate Python interpreter")
        self.assertTrue(os.path.isfile(py_bin), f"Discovered Python {py_bin} is not a file")
        self.assertTrue(
            BackendSupervisor._verify_python_candidate(py_bin),
            f"Discovered Python {py_bin} failed module verification"
        )

    def test_05_splash_and_error_views(self):
        """Splash and error views must generate well-formed offline HTML."""
        splash = get_splash_html("Initializing test...", "Details test...")
        self.assertIn("status-label", splash)
        self.assertIn("details-label", splash)
        self.assertIn("Initializing test...", splash)
        self.assertIn("RAAH", splash)

        err = get_error_html("Test Title", "Test Message", "Test Details", "test.log")
        self.assertIn("Test Title", err)
        self.assertIn("Test Message", err)
        self.assertIn("Test Details", err)
        self.assertIn("test.log", err)

    def test_06_packaging_scripts(self):
        """Packaging scripts must exist, be executable, and PKGBUILD must be valid."""
        for script in ["build.sh", "install.sh", "uninstall.sh"]:
            p = ROOT / "packaging" / script
            self.assertTrue(p.exists(), f"packaging/{script} missing")
            self.assertTrue(os.access(p, os.X_OK), f"packaging/{script} not executable")

        pkgbuild = ROOT / "packaging" / "PKGBUILD"
        self.assertTrue(pkgbuild.exists(), "PKGBUILD missing")
        with open(pkgbuild, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pkgname=raah-desktop", content)
        self.assertIn("webkit2gtk-4.1", content)

    def test_07_backend_supervisor_live_lifecycle(self):
        """
        End-to-end supervisor test:
        1. Start backend on ephemeral port.
        2. Poll readiness until READY.
        3. Verify health endpoint.
        4. Clean shutdown and verify no port listeners remain.
        """
        test_port = get_free_port()
        log_file = ROOT / "data" / "logs" / f"test_supervisor_{test_port}.log"

        supervisor = BackendSupervisor(
            host="127.0.0.1",
            port=test_port,
            startup_timeout=30.0,
            log_file=log_file,
        )

        progress_log = []
        def on_progress(status, details):
            progress_log.append((status, details))

        try:
            # 1. Start backend
            ready = supervisor.start(progress_callback=on_progress)
            self.assertTrue(ready, "Supervisor start() did not report ready")
            self.assertIsNotNone(supervisor.process, "Subprocess was not spawned")
            self.assertIsNone(supervisor.process.poll(), "Subprocess died prematurely")

            # 2. Check health endpoint directly
            is_ready, data = supervisor.check_health()
            self.assertTrue(is_ready, f"Direct health check not ready: {data}")
            self.assertEqual(data.get("status"), "READY")

            # 3. Verify port is listening
            self.assertTrue(supervisor.is_port_listening())

        finally:
            # 4. Clean shutdown
            supervisor.stop(timeout=5.0)

            # Wait briefly to confirm port is released
            time.sleep(0.5)
            self.assertFalse(
                supervisor.is_port_listening(),
                f"Port {test_port} still listening after supervisor.stop()!"
            )
            if supervisor.process:
                self.assertIsNotNone(
                    supervisor.process.poll(),
                    "Process still alive after supervisor.stop()!"
                )

        # Cleanup test log file
        if log_file.exists():
            try:
                log_file.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
