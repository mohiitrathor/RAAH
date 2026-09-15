"""
RAAH Tactical Operations Command Center — Desktop Application Wrapper
====================================================================

Native GTK3 + WebKitGTK desktop application for Arch Linux:
1. Displays high-tech tactical splash screen on startup.
2. Supervises the FastAPI backend (discovering Python, starting Uvicorn).
3. Polls /health/ready until ML model, SQLite store, and simulator are active.
4. Seamlessly navigates to the Command Center dashboard.
5. Captures errors visibly with diagnostic traceback and logs.
6. Guarantees clean backend shutdown upon closing the window.
"""

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from desktop.supervisor import BackendSupervisor, BackendStartupError
from desktop.splash import get_splash_html, get_error_html

# Ensure GTK & WebKit versions before importing
try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, Gdk, WebKit2, GLib
    GTK_AVAILABLE = True
except Exception as e:
    GTK_AVAILABLE = False
    GTK_ERROR = str(e)


class RAAHApplication:
    """Main RAAH Desktop Application Window."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        custom_python: Optional[str] = None,
        test_mode: bool = False,
        fullscreen: bool = False,
    ):
        self.host = host
        self.port = port
        self.test_mode = test_mode
        self.fullscreen_on_start = fullscreen

        self.supervisor = BackendSupervisor(
            host=host,
            port=port,
            custom_python=custom_python,
        )

        self.window: Optional[Gtk.Window] = None
        self.webview: Optional[WebKit2.WebView] = None
        self._is_ready = False
        self._shutting_down = False

    def build_ui(self) -> Gtk.Window:
        """Create and configure the native GTK application window."""
        # Enable dark theme
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        # Set program and application name for desktop integration
        GLib.set_prgname("raah-command-center")
        GLib.set_application_name("RAAH Command Center")

        # Main Window
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("RAAH — Tactical Operations Command Center")
        self.window.set_default_size(1440, 900)
        self.window.set_position(Gtk.WindowPosition.CENTER)

        # Set Icon
        icon_path = REPO_ROOT / "desktop" / "assets" / "icons" / "raah_256x256.png"
        if icon_path.exists():
            try:
                self.window.set_icon_from_file(str(icon_path))
            except Exception:
                pass

        # Create WebKit WebView
        self.webview = WebKit2.WebView()
        wk_context = self.webview.get_context()
        if wk_context:
            wk_context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

        wk_settings = self.webview.get_settings()
        wk_settings.set_enable_developer_extras(True)
        wk_settings.set_enable_webgl(True)
        wk_settings.set_enable_smooth_scrolling(True)
        wk_settings.set_enable_javascript(True)
        wk_settings.set_user_agent("RAAH-Desktop/1.0 (X11; Linux x86_64)")

        # Keyboard shortcuts
        self.window.connect("key-press-event", self._on_key_press)
        self.window.connect("delete-event", self._on_delete_event)
        self.window.connect("destroy", self._on_destroy)

        # Add WebView to window
        self.window.add(self.webview)

        # Load initial splash view
        splash_html = get_splash_html(
            status_text="Starting RAAH subsystems...",
            details="Connecting to backend at %s:%d..." % (self.host, self.port),
        )
        self.webview.load_html(splash_html, "file:///")

        if self.fullscreen_on_start:
            self.window.fullscreen()

        return self.window

    def _on_key_press(self, widget, event) -> bool:
        """Handle desktop keyboard shortcuts."""
        keyval = event.keyval
        state = event.state

        # F11: Fullscreen toggle
        if keyval == Gdk.KEY_F11:
            if self.window.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                self.window.unfullscreen()
            else:
                self.window.fullscreen()
            return True

        # Ctrl+R or F5: Reload (bypass cache)
        ctrl_pressed = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if (ctrl_pressed and keyval in (Gdk.KEY_r, Gdk.KEY_R)) or keyval == Gdk.KEY_F5:
            if self._is_ready and self.webview:
                self.webview.reload_bypass_cache()
            return True

        # Ctrl+Q: Clean Quit
        if ctrl_pressed and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.quit()
            return True

        # F12 or Ctrl+Shift+I: Open WebKit Inspector
        shift_pressed = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_F12 or (ctrl_pressed and shift_pressed and keyval in (Gdk.KEY_i, Gdk.KEY_I)):
            inspector = self.webview.get_inspector()
            if inspector:
                inspector.show()
            return True

        return False

    def _update_splash(self, status: str, details: str):
        """Update splash text in the active WebView via DOM manipulation."""
        if not self.webview or self._is_ready:
            return False

        js = """
        (function() {
            var s = document.getElementById('status-label');
            var d = document.getElementById('details-label');
            if (s) s.innerText = %s;
            if (d) d.innerText = %s;
        })();
        """ % (json.dumps(status), json.dumps(details))

        try:
            self.webview.run_javascript(js, None, None, None)
        except Exception:
            pass
        return False

    def _on_backend_ready(self):
        """Transition from splash screen to the live Command Center dashboard."""
        self._is_ready = True
        target_url = "http://%s:%d/dashboard/" % (self.host, self.port)
        if self.webview:
            self.webview.load_uri(target_url)
        return False

    def _on_backend_failed(self, error_title: str, error_msg: str, excerpt: str):
        """Display diagnostic error view if startup fails."""
        if not self.webview:
            return False

        error_html = get_error_html(
            title=error_title,
            error_message=error_msg,
            details=excerpt,
            log_file_path=str(self.supervisor.log_file),
        )
        self.webview.load_html(error_html, "file:///")
        return False

    def _startup_worker(self):
        """Background thread executing backend supervisor startup and health polling."""
        def progress_cb(status, details):
            GLib.idle_add(self._update_splash, status, details)

        try:
            self.supervisor.start(progress_callback=progress_cb)
            GLib.idle_add(self._on_backend_ready)
        except BackendStartupError as e:
            GLib.idle_add(
                self._on_backend_failed,
                "Backend Startup Failed",
                str(e),
                e.log_excerpt or self.supervisor.get_log_excerpt(25),
            )
        except Exception as e:
            GLib.idle_add(
                self._on_backend_failed,
                "Unexpected Operational Error",
                str(e),
                self.supervisor.get_log_excerpt(25),
            )

    def start(self):
        """Start the desktop application and launch the supervisor thread."""
        if not GTK_AVAILABLE:
            raise RuntimeError(
                "GTK 3 or WebKit2 is not available: %s\n"
                "Please run on a system with PyGObject and WebKitGTK installed." % GTK_ERROR
            )

        self.build_ui()
        self.window.show_all()

        # Start backend supervision thread
        thread = threading.Thread(target=self._startup_worker, daemon=True)
        thread.start()

        if not self.test_mode:
            # Install Unix signal handlers for clean terminal Ctrl+C
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.quit)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
            Gtk.main()

    def _on_delete_event(self, widget, event) -> bool:
        """Window close button clicked."""
        self.quit()
        return True

    def _on_destroy(self, widget):
        """Window destroyed."""
        self.quit()

    def quit(self) -> bool:
        """Perform clean shutdown of the supervisor and terminate the GTK main loop."""
        if self._shutting_down:
            return False
        self._shutting_down = True

        # Stop backend process group
        try:
            self.supervisor.stop(timeout=5.0)
        except Exception:
            pass

        if not self.test_mode:
            Gtk.main_quit()
        return False


def main():
    """Main CLI entrypoint for desktop launcher."""
    default_host, default_port = BackendSupervisor.load_env_defaults()

    parser = argparse.ArgumentParser(description="RAAH Command Center Desktop Application")
    parser.add_argument("--host", default=default_host, help="Backend bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=default_port, help="Backend bind port (default: 8000)")
    parser.add_argument("--python", default=None, help="Path to custom Python binary with RAAH dependencies")
    parser.add_argument("--fullscreen", action="store_true", help="Launch in fullscreen mode")
    parser.add_argument("--test-mode", action="store_true", help="Initialize and verify without entering Gtk.main()")

    args = parser.parse_args()

    app = RAAHApplication(
        host=args.host,
        port=args.port,
        custom_python=args.python,
        test_mode=args.test_mode,
        fullscreen=args.fullscreen,
    )

    try:
        app.start()
    except Exception as e:
        sys.stderr.write("[RAAH-DESKTOP ERROR] %s\n" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
