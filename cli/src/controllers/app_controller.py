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
