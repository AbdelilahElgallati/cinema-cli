import os

from src.config import APP_VERSION, BACKEND_URL, SETTINGS_FILE
from src.state.app_state import AppState
from src.utils.first_run import is_first_run, run_wizard
from src.utils.storage import load_json_data


class AppController:
    """Coordinates argument flow, startup sequence, and TUI lifecycle."""

    def __init__(
        self,
        state: AppState,
        *,
        start_local_backend,
        run_debug_source_command,
        run_debug_subtitle_command,
        run_smoke_command,
        startup_health_check,
        show_splash,
        cli_factory,
    ):
        self.state = state
        self.start_local_backend = start_local_backend
        self.run_debug_source_command = run_debug_source_command
        self.run_debug_subtitle_command = run_debug_subtitle_command
        self.run_smoke_command = run_smoke_command
        self.startup_health_check = startup_health_check
        self.show_splash = show_splash
        self.cli_factory = cli_factory

    def run(self) -> int:
        self._prepare_state()

        if self.state.setup_requested or is_first_run():
            run_wizard(force=self.state.setup_requested)
            if self.state.setup_requested:
                return 0

        if self._handle_simple_flags():
            return 0

        if self.state.needs_backend:
            self.start_local_backend(self.state.backend_url, timeout=60)

        if "--debug-source" in self.state.argv:
            return self.run_debug_source_command(self.state.argv)
        if "--debug-subtitle" in self.state.argv:
            return self.run_debug_subtitle_command(self.state.argv)
        if "--smoke" in self.state.argv:
            return self.run_smoke_command(self.state.argv)

        self.start_local_backend(self.state.backend_url, timeout=60)
        cli = self.cli_factory()
        try:
            self.show_splash()
            self.startup_health_check()
            cli.main_menu()
            return 0
        except KeyboardInterrupt:
            print("\nGoodbye!")
            return 0

    def _prepare_state(self):
        self.state.setup_requested = "--setup" in self.state.argv
        self.state.needs_backend = any(
            flag in self.state.argv for flag in ("--debug-source", "--debug-subtitle", "--smoke")
        )
        boot_settings = load_json_data(SETTINGS_FILE, default={}, expected_type=dict) or {}
        self.state.backend_url = (
            boot_settings.get("backend") or os.getenv("BACKEND_URL") or BACKEND_URL
        )

    def _handle_simple_flags(self) -> bool:
        argv = self.state.argv
        if argv and argv[0] in ("--version", "-V", "-v"):
            print(f"Cinema CLI v{APP_VERSION}")
            return True

        if argv and argv[0] == "--diagnostics":
            self._run_diagnostics()
            return True

        if argv and argv[0] in ("--help", "-h"):
            print(
                "Cinema CLI — a terminal-based movie & TV streaming client\n"
                "\n"
                "Usage:\n"
                "  python main.py [OPTIONS]\n"
                "\n"
                "Options:\n"
                "  --version, -V     Print version and exit\n"
                "  --help,    -h     Show this help message and exit\n"
                "  --debug           Enable unified timestamped [UI]/[SCRAPER] logs\n"
                "  --setup           Re-run the first-run setup wizard\n"
                "  --debug-source    Print source pipeline + quality trace for one title/episode\n"
                "                    Required: --tmdb-id <id> --type <movie|tv>\n"
                "                    TV only:  --season <n> --episode <n>\n"
                "                    Optional: --quality <auto|1080p|720p|...>\n"
                "  --debug-subtitle  Print subtitle decision trace for one title/episode\n"
                "                    Required: --tmdb-id <id> --type <movie|tv>\n"
                "                    TV only:  --season <n> --episode <n>\n"
                "                    Optional: --include-all\n"
                "  --smoke           Tiny automated smoke test for movie/tv source selection\n"
                "                    Optional: --movie-id <id> --tv-id <id> --season <n> --episode <n>\n"
                "                              --skip-validation --timeout <sec>\n"
                "\n"
                "Keyboard shortcuts (inside the TUI):\n"
                "  ↑ ↓ / j k        Navigate lists\n"
                "  Enter             Select / Confirm\n"
                "  F                 Toggle favourite\n"
                "  W                 Toggle Watch Later\n"
                "  D                 Batch download (search / browse results)\n"
                "  B / Esc           Go back\n"
                "  Q                 Quit\n"
            )
            return True

        return False

    def _run_diagnostics(self):
        import os
        import platform
        import urllib.request
        from src.utils.system_tools import is_tool_available, get_tool_version
        from src.config import DATA_DIR, SETTINGS_FILE, BACKEND_URL
        from src.utils.storage import load_json_data

        print("="*50)
        print(" CINEMA CLI — SYSTEM DIAGNOSTICS")
        print("="*50)
        
        print(f"OS: {platform.system()} {platform.release()} ({platform.architecture()[0]})")
        print(f"Data Dir: {DATA_DIR}")
        print(f"Settings File: {SETTINGS_FILE}")
        
        print("\n[1/4] Checking External Tools...")
        tools = ['node', 'npm', 'mpv', 'ffmpeg', 'yt-dlp', 'aria2c']
        for t in tools:
            found = is_tool_available(t)
            status = "PASS" if found else "FAIL"
            print(f"  [{status}] {t}")
            
        print("\n[2/4] Checking Python/Node Configuration...")
        import sys
        print(f"  [INFO] Python Executable: {sys.executable}")
        print(f"  [INFO] Python Version: {sys.version.split(' ')[0]}")
        
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
        nm_dir = os.path.join(backend_dir, "node_modules")
        if os.path.exists(nm_dir):
            print("  [PASS] node_modules directory exists")
        else:
            print("  [FAIL] node_modules directory missing. Run 'npm install' in backend/")
            
        print("\n[3/4] Checking Persistence & Config...")
        if os.path.exists(DATA_DIR):
            print("  [PASS] Data directory exists and is writable")
        else:
            print("  [FAIL] Data directory is missing")
            
        settings = load_json_data(SETTINGS_FILE, default={}, expected_type=dict)
        print(f"  [INFO] Loaded Settings Keys: {list(settings.keys())}")
        
        print("\n[4/4] Checking Backend Reachability...")
        backend_url = settings.get("backend", os.getenv("BACKEND_URL", BACKEND_URL))
        print(f"  [INFO] Target Backend URL: {backend_url}")
        
        try:
            req = urllib.request.Request(f"{backend_url.rstrip('/')}/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if 200 <= resp.status < 400:
                    print("  [PASS] Backend responded to /health")
                else:
                    print(f"  [FAIL] Backend responded with status {resp.status}")
        except Exception as e:
            print(f"  [FAIL] Backend unreachable: {e}")
            print("         If the app is not running, this is expected.")
            
        print("\nDiagnostics complete.")
