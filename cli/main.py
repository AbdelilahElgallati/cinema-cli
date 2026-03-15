import atexit
import json
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def start_local_backend(backend_url: str, timeout: int = 30):
    """Start local backend if backend_url points at localhost and wait until it's healthy.

    Returns subprocess.Process or None.
    """

    def _is_running(url: str) -> bool:
        try:
            req = Request(
                url.rstrip("/") + "/", headers={"User-Agent": "cinema-cli/1.0"}
            )
            with urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except Exception:
            return False

    try:
        host = backend_url.split("://")[-1].split(":")[0]
    except Exception:
        host = ""

    if host not in ("localhost", "127.0.0.1", ""):
        return None

    # If already running, nothing to do
    if _is_running(backend_url):
        return None

    def _backend_launch_env(url: str):
        env = os.environ.copy()
        try:
            parsed = urlparse(url or "")
            host = (parsed.hostname or "").lower()
            if host not in ("localhost", "127.0.0.1", ""):
                return env
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if port:
                env["PORT"] = str(port)
        except Exception:
            pass
        return env

    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "backend")
    )
    log_path = os.path.join(backend_dir, "backend.log")

    show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"

    # Ensure log directory exists and open log file for append
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logfile = open(log_path, "a+", encoding="utf-8")

    stdout = logfile
    stderr = logfile
    launch_env = _backend_launch_env(backend_url)

    proc = None
    try:
        proc = subprocess.Popen(
            "npm start",
            cwd=backend_dir,
            shell=True,
            stdout=stdout,
            stderr=stderr,
            env=launch_env,
        )
    except Exception:
        try:
            proc = subprocess.Popen(
                ["node", "index.js"],
                cwd=backend_dir,
                stdout=stdout,
                stderr=stderr,
                env=launch_env,
            )
        except Exception:
            logfile.close()
            return None

    # Optionally tail live logs to console while waiting
    stop_tailer = None
    tail_thread = None
    if show_logs:
        import threading

        stop_tailer = threading.Event()

        def _tail_file(path, stop_event):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    # Seek to near end
                    f.seek(0, os.SEEK_END)
                    while not stop_event.is_set():
                        line = f.readline()
                        if line:
                            try:
                                console.print(line.rstrip())
                            except Exception:
                                pass
                        else:
                            time.sleep(0.2)
            except Exception:
                return

        tail_thread = threading.Thread(
            target=_tail_file, args=(log_path, stop_tailer), daemon=True
        )
        tail_thread.start()

    # Wait until healthy or timeout while showing a friendly status
    from src.config import console

    with console.status("Starting backend, please wait...", spinner="dots"):
        waited = 0.0
        interval = 0.5
        while waited < timeout:
            if _is_running(backend_url):
                if stop_tailer:
                    stop_tailer.set()
                logfile.flush()
                logfile.close()
                return proc
            time.sleep(interval)
            waited += interval

    # Timeout reached; stop tailer if running and return proc (logs available in backend.log)
    if stop_tailer:
        stop_tailer.set()
    logfile.flush()
    logfile.close()
    return proc


from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout as PTLayout
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from rich import box
from rich.align import Align
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.table import Table

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.config import (
    ACCENT,
    APP_VERSION,
    BACKEND_URL,
    FAVORITES_FILE,
    HISTORY_FILE,
    PLAYBACK_FILE,
    PRIMARY,
    SECONDARY,
    SETTINGS_FILE,
    SUCCESS,
    TEXT,
    TMDB_API_KEY,
    WARNING,
    WATCH_LATER_FILE,
    console,
)
from src.ui.ui import (
    clear,
    format_item,
    multi_selection_menu,
    print_header,
    selection_menu,
    show_splash,
)
from src.utils.api import APIClient
from src.utils.download_manager import DownloadManager
from src.utils.player import play_stream, play_video, detect_available_players
from src.utils.source_strategy import (
    adaptive_quality_from_speed,
    filter_sources_for_quality,
    sort_manifest_qualities,
)
from src.utils.app_logger import log_event
from src.utils.storage import load_json_data, save_json_data
from src.utils.utils import generate_filename
from src.utils.validator import select_working_source, select_multiple_working_sources, verify_source


def _arg_value(argv, flag, default=None):
    if flag not in argv:
        return default
    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        return default
    return argv[idx + 1]


def _subtitle_trace(subtitles, preferred_langs, include_all):
    items = []
    for s in subtitles or []:
        if not isinstance(s, dict):
            continue
        url = s.get("url")
        if not isinstance(url, str) or not url:
            continue
        lang = str(s.get("lang") or s.get("language") or "und").lower()
        items.append({"lang": lang, "url": url})

    selected = []
    seen_lang = set()
    seen_url = set()
    wants = [str(x).lower() for x in (preferred_langs or []) if str(x).strip()]

    for lang in (wants if include_all else wants[:1]):
        for it in items:
            if it["lang"] == lang and it["lang"] not in seen_lang and it["url"] not in seen_url:
                selected.append(it)
                seen_lang.add(it["lang"])
                seen_url.add(it["url"])
                break

    if include_all:
        for it in items:
            if it["lang"] not in seen_lang and it["url"] not in seen_url:
                selected.append(it)
                seen_lang.add(it["lang"])
                seen_url.add(it["url"])

    if not selected and items:
        selected.append(items[0])

    return selected


def run_debug_source_command(argv):
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)

    tmdb_id = _arg_value(argv, "--tmdb-id")
    media_type = (_arg_value(argv, "--type", "movie") or "movie").lower()
    season = _arg_value(argv, "--season")
    episode = _arg_value(argv, "--episode")
    quality = _arg_value(argv, "--quality", "auto")

    if not tmdb_id or media_type not in ("movie", "tv"):
        console.print("[red]Usage: --debug-source --tmdb-id <id> --type <movie|tv> [--season N --episode N] [--quality Q][/red]")
        return 2
    if media_type == "tv" and (not season or not episode):
        console.print("[red]TV debug requires --season and --episode[/red]")
        return 2

    data = api.get_sources_api(
        tmdb_id,
        media_type,
        int(season) if season else None,
        int(episode) if episode else None,
        force_refresh=True,
    )
    files = data.get("files", []) if isinstance(data, dict) else []
    pipeline = data.get("pipeline", {}) if isinstance(data, dict) else {}
    quality_groups = data.get("quality_groups", {}) if isinstance(data, dict) else {}
    corr = data.get("correlation_id") if isinstance(data, dict) else ""

    filtered, mode = filter_sources_for_quality(files, quality)

    console.print(f"[bold {ACCENT}]Debug Source Trace[/bold {ACCENT}]")
    console.print(f"TMDB: {tmdb_id} | Type: {media_type}")
    if media_type == "tv":
        console.print(f"Season/Episode: S{season}E{episode}")
    console.print(f"Correlation ID: {corr or 'n/a'}")
    console.print(f"Pipeline stages: {pipeline.get('stages')}")
    console.print(f"Pipeline timings: {pipeline.get('timings_ms')}")
    console.print(f"Pipeline totals: {pipeline.get('totals')}")
    console.print(f"Quality groups: {list((quality_groups or {}).keys())}")
    console.print(f"Quality decision: requested='{quality}' mode='{mode}' -> {len(filtered)} source(s)")

    tbl = Table(title="Top Sources", box=box.SIMPLE)
    tbl.add_column("#", style="dim")
    tbl.add_column("Provider")
    tbl.add_column("Quality")
    tbl.add_column("Probe")
    tbl.add_column("Score")
    tbl.add_column("Source ID")
    for i, src in enumerate((filtered or files)[:8], start=1):
        probe = src.get("probe_result", {}) if isinstance(src, dict) else {}
        tbl.add_row(
            str(i),
            str(src.get("provider", "?")),
            str(src.get("quality", "?")),
            str(probe.get("status", "n/a")),
            str(src.get("score", "n/a")),
            str(src.get("source_id", "n/a")),
        )
    console.print(tbl)
    log_event("debug", f"debug-source done tmdb={tmdb_id} type={media_type}", correlation_id=corr or "")
    return 0


def run_debug_subtitle_command(argv):
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)

    tmdb_id = _arg_value(argv, "--tmdb-id")
    media_type = (_arg_value(argv, "--type", "movie") or "movie").lower()
    season = _arg_value(argv, "--season")
    episode = _arg_value(argv, "--episode")
    include_all = "--include-all" in argv

    if not tmdb_id or media_type not in ("movie", "tv"):
        console.print("[red]Usage: --debug-subtitle --tmdb-id <id> --type <movie|tv> [--season N --episode N] [--include-all][/red]")
        return 2
    if media_type == "tv" and (not season or not episode):
        console.print("[red]TV debug requires --season and --episode[/red]")
        return 2

    data = api.get_sources_api(
        tmdb_id,
        media_type,
        int(season) if season else None,
        int(episode) if episode else None,
        force_refresh=True,
    )
    subtitles = data.get("subtitles", []) if isinstance(data, dict) else []
    pipeline = data.get("pipeline", {}) if isinstance(data, dict) else {}
    corr = data.get("correlation_id") if isinstance(data, dict) else ""

    preferred_primary = settings.get("preferred_subtitle", "ar")
    preferred_langs = settings.get("preferred_subtitle_langs", [preferred_primary])
    if not isinstance(preferred_langs, list) or not preferred_langs:
        preferred_langs = [preferred_primary]
    if preferred_langs[0] != preferred_primary:
        preferred_langs = [preferred_primary] + [x for x in preferred_langs if x != preferred_primary]

    selected = _subtitle_trace(subtitles, preferred_langs, include_all)
    lang_counts = {}
    for sub in subtitles:
        if isinstance(sub, dict):
            lang = str(sub.get("lang") or sub.get("language") or "und").lower()
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

    console.print(f"[bold {ACCENT}]Debug Subtitle Trace[/bold {ACCENT}]")
    console.print(f"TMDB: {tmdb_id} | Type: {media_type}")
    if media_type == "tv":
        console.print(f"Season/Episode: S{season}E{episode}")
    console.print(f"Correlation ID: {corr or 'n/a'}")
    console.print(f"Pipeline timings: {pipeline.get('timings_ms')}")
    console.print(f"Available subtitle tracks: {len(subtitles)} | by lang: {lang_counts}")
    console.print(f"Preferred langs: {preferred_langs} | include_all={include_all}")
    console.print(f"Selection trace -> {len(selected)} track(s): {[s.get('lang') for s in selected]}")

    tbl = Table(title="Selected Subtitle Candidates", box=box.SIMPLE)
    tbl.add_column("#", style="dim")
    tbl.add_column("Lang")
    tbl.add_column("URL")
    for i, sub in enumerate(selected[:8], start=1):
        tbl.add_row(str(i), str(sub.get("lang", "und")), str(sub.get("url", ""))[:90])
    console.print(tbl)
    log_event("debug", f"debug-subtitle done tmdb={tmdb_id} type={media_type}", correlation_id=corr or "")
    return 0


def _auto_update_ytdlp():
    """Run yt-dlp -U in a background thread so startup is never blocked."""
    def _update():
        try:
            result = subprocess.run(
                ["yt-dlp", "-U", "--quiet"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                out = (result.stdout or "").strip()
                if out and "up to date" not in out.lower():
                    log_event("app", f"yt-dlp updated: {out}")
        except Exception:
            pass  # Never let an update failure crash the app

    threading.Thread(target=_update, daemon=True).start()


def startup_health_check():
    """
    Run a quick dependency + connectivity check and print a one-line summary.
    Never raises — missing items are flagged with ✗ but the app still starts.
    """
    import shutil as _shutil

    checks = []

    # ── Required tools ────────────────────────────────────────────────────────
    for tool in ("mpv", "ffmpeg", "yt-dlp"):
        found = bool(_shutil.which(tool))
        checks.append((tool, found, not found))   # (label, ok, critical)

    # aria2c is optional
    checks.append(("aria2c", bool(_shutil.which("aria2c")), False))

    # ── Backend / TMDB reachability (quick, 3 s timeout) ─────────────────────
    _tmdb_ok = False
    try:
        from urllib.request import urlopen, Request as _Req
        from urllib.error import URLError
        from src.config import TMDB_API_KEY
        _api_key = TMDB_API_KEY or ""
        if _api_key:
            _r = _Req(
                f"https://api.themoviedb.org/3/configuration?api_key={_api_key}",
                headers={"User-Agent": "cinema-cli/hc"},
            )
            with urlopen(_r, timeout=3) as _resp:
                _tmdb_ok = _resp.status == 200
    except Exception:
        pass
    checks.append(("TMDB API", _tmdb_ok, not _tmdb_ok))

    # ── Format ────────────────────────────────────────────────────────────────
    parts = []
    any_critical = False
    for label, ok, critical in checks:
        if ok:
            parts.append(f"[green]✓ {label}[/green]")
        elif critical:
            parts.append(f"[bold red]✗ {label}[/bold red]")
            any_critical = True
        else:
            parts.append(f"[dim]~ {label}[/dim]")

    console.print("  " + "   ".join(parts))

    if any_critical:
        missing = [label for label, ok, critical in checks if not ok and critical]
        console.print(
            f"\n[bold yellow]  ⚠  Missing: {', '.join(missing)}[/bold yellow]\n"
            f"[dim]  Run  python main.py --setup  or see README for install instructions.[/dim]\n"
        )
        time.sleep(2)


class CinemaCLI:
    def __init__(self):
        self.settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}

        # Attempt to ensure a local backend is running (only for localhost URLs)
        self._backend_proc = None
        self._maybe_start_backend(self.settings.get("backend", BACKEND_URL))

        # Defaults for settings
        if "filename_template" not in self.settings:
            self.settings["filename_template"] = "{title}.{year}"
        if "filename_template_tv" not in self.settings:
            self.settings["filename_template_tv"] = "{title}.S{season}E{episode}"
        if "library_dir" not in self.settings:
            self.settings["library_dir"] = os.path.expanduser("~/Downloads/CinemaCLI")
        if "preferred_subtitle" not in self.settings:
            self.settings["preferred_subtitle"] = "ar"
        if "preferred_subtitle_langs" not in self.settings:
            # Ordered multi-language list: primary first (Arabic default)
            self.settings["preferred_subtitle_langs"] = [
                self.settings.get("preferred_subtitle", "ar")
            ]
        if "preferred_player" not in self.settings:
            self.settings["preferred_player"] = "mpv"
        if "download_speed_limit" not in self.settings:
            self.settings["download_speed_limit"] = 0   # 0 = unlimited

        self.api = APIClient(self.settings)

        # IMPORTANT: ensure lists/dicts, not None
        self.history     = load_json_data(HISTORY_FILE)     or []
        self.favorites   = load_json_data(FAVORITES_FILE)   or []
        self.playback    = load_json_data(PLAYBACK_FILE)     or {}
        self.watch_later = load_json_data(WATCH_LATER_FILE) or []

        os.makedirs(self.settings["library_dir"], exist_ok=True)
        self.download_manager = DownloadManager(downloads_dir=self.settings["library_dir"], api_client=self.api, settings=self.settings)
        self.download_manager.start()

        # Check whether the backend (and TMDB) are reachable right now.
        # This flag is re-checked lazily on each network-requiring menu entry.
        self.backend_online: bool = self._check_backend_online()

        # Auto-update yt-dlp silently in the background (never blocks launch)
        _auto_update_ytdlp()

        # Ensure backend process is terminated on exit if we started it
        atexit.register(self._cleanup_backend)

    def _check_backend_online(self) -> bool:
        """Quick connectivity probe — returns True if both TMDB and the backend respond."""
        # Check TMDB (always needed, even without a local backend)
        try:
            from urllib.request import urlopen, Request as _Req
            tmdb_req = _Req(
                "https://api.themoviedb.org/3/configuration?api_key=" +
                (self.settings.get("tmdb_key") or TMDB_API_KEY),
                headers={"User-Agent": "cinema-cli/1.0"}
            )
            with urlopen(tmdb_req, timeout=4) as r:
                if r.status != 200:
                    return False
        except Exception:
            return False

        # Verify backend reachability using configured URL plus local fallbacks.
        backend_url = self.settings.get("backend", BACKEND_URL)
        candidates = [
            str(backend_url).rstrip("/"),
            str(BACKEND_URL).rstrip("/"),
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3010",
            "http://127.0.0.1:3010",
        ]
        seen = set()
        for c in candidates:
            if not c or c in seen:
                continue
            seen.add(c)
            if self._is_backend_running(c):
                # Stick to the reachable backend for this session.
                if self.settings.get("backend") != c:
                    self.settings["backend"] = c
                    try:
                        save_json_data(SETTINGS_FILE, self.settings)
                    except Exception:
                        pass
                return True
        return False

    def _show_offline_warning(self):
        """Print a one-time offline banner and re-probe connectivity."""
        self.backend_online = self._check_backend_online()  # re-check
        if not self.backend_online:
            console.print(
                f"[bold red]\n  ⚠  No internet / backend connection detected.[/bold red]\n"
                f"[dim]  Browse mode limited to: History, Watch Later, Local Library,\n"
                f"  Download Manager and Settings are still available.[/dim]\n"
            )
            time.sleep(2)

    def _is_backend_running(self, url: str) -> bool:
        try:
            req = Request(
                url.rstrip("/") + "/", headers={"User-Agent": "cinema-cli/1.0"}
            )
            with urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except (URLError, HTTPError, ValueError):
            return False

    def _maybe_start_backend(self, backend_url: str) -> None:
        # Only auto-start when pointing to localhost and not already running
        try:
            host = backend_url.split("://")[-1].split(":")[0]
        except Exception:
            host = ""

        if host not in ("localhost", "127.0.0.1", ""):
            return

        if self._is_backend_running(backend_url):
            return

        def _backend_launch_env(url: str):
            env = os.environ.copy()
            try:
                parsed = urlparse(url or "")
                host = (parsed.hostname or "").lower()
                if host not in ("localhost", "127.0.0.1", ""):
                    return env
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if port:
                    env["PORT"] = str(port)
            except Exception:
                pass
            return env

        backend_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend")
        )
        # Try to start via npm start; fallback to node index.js if npm not available
        try:
            # Allow showing backend logs when requested via env var
            show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"
            stdout = None if show_logs else subprocess.DEVNULL
            stderr = None if show_logs else subprocess.DEVNULL
            launch_env = _backend_launch_env(backend_url)

            # Use shell=True for cross-platform command resolution (npm on PATH)
            self._backend_proc = subprocess.Popen(
                "npm start",
                cwd=backend_dir,
                shell=True,
                stdout=stdout,
                stderr=stderr,
                env=launch_env,
            )
            # Wait briefly for server to come up
            for _ in range(10):
                if self._is_backend_running(backend_url):
                    return
                time.sleep(0.5)
        except Exception:
            try:
                self._backend_proc = subprocess.Popen(
                    ["node", "index.js"],
                    cwd=backend_dir,
                    stdout=stdout,
                    stderr=stderr,
                    env=launch_env,
                )
                for _ in range(10):
                    if self._is_backend_running(backend_url):
                        return
                    time.sleep(0.5)
            except Exception:
                # If starting fails, leave user to start backend manually
                return

    def _cleanup_backend(self):
        if self._backend_proc and self._backend_proc.poll() is None:
            try:
                self._backend_proc.terminate()
                time.sleep(0.2)
                if self._backend_proc.poll() is None:
                    self._backend_proc.kill()
            except Exception:
                pass

    def main_menu(self):
        # Import the active theme's highlight colour once
        try:
            from src.config import THEMES, _active_theme_name as _atn
            _hl_fg = THEMES[_atn].get("highlight_fg", "#FFFFFF")
        except Exception:
            _hl_fg = "#FFFFFF"

        while True:
            print_header("Main Menu")
            options = [
                {"name": "🔍 Search Movies & TV",   "action": self.handle_search},
                {"name": "🌍 Discovery",            "action": self.handle_discovery},
                {"name": "📈 Trending This Week",   "action": self.handle_trending},
                {"name": "🔥 Popular Content",      "action": self.handle_popular},
                {"name": "🎭 Browse by Genre",      "action": self.handle_genres},
                {"name": "⭐ My Favorites",         "action": self.handle_favorites},
                {"name": "🕒 Watch History",        "action": self.handle_history},
                {"name": "⏱️  Watch Later",          "action": self.handle_watch_later},
                {"name": "📁 Local Library",        "action": self.handle_local_library},
                {"name": "📥 Download Manager",     "action": self.handle_download_manager},
                {"name": "⚙️  Settings",            "action": self.handle_settings},
                {"name": "❌ Exit",                 "action": sys.exit},
            ]

            selected_index = 0
            kb = KeyBindings()

            @kb.add("up")
            def _(event):
                nonlocal selected_index
                selected_index = (selected_index - 1) % len(options)

            @kb.add("down")
            def _(event):
                nonlocal selected_index
                selected_index = (selected_index + 1) % len(options)

            @kb.add("enter")
            def _(event):
                event.app.exit(result=options[selected_index]["action"])

            @kb.add("q")
            def _(event):
                event.app.exit(result=sys.exit)

            @kb.add("b")
            def _(event):
                event.app.exit(result=None)

            def get_menu_text():
                res = []
                res.append(("class:header", "  ══  Main Menu  ══\n"))
                if not self.backend_online:
                    res.append(("class:offline", "  ⚠  OFFLINE — Browse/Search disabled  \n"))
                res.append(("class:border", "─" * 36 + "\n"))
                for i, opt in enumerate(options):
                    if i == selected_index:
                        res.append(("class:selected", f"  ▶  {opt['name']}\n"))
                    else:
                        res.append(("class:item", f"     {opt['name']}\n"))
                res.append(("class:border", "─" * 36 + "\n"))
                res.append(("class:help", "  ↑↓ Navigate   Enter Select   Q Quit  "))
                return res

            style = Style.from_dict(
                {
                    "header":   f"bold {PRIMARY}",
                    "border":   f"dim {PRIMARY}",
                    "selected": f"bg:{PRIMARY} fg:{_hl_fg} bold",
                    "item":     f"{TEXT}",
                    "help":     f"italic dim {TEXT}",
                    "offline":  "bold fg:ansired",
                }
            )

            app = Application(
                layout=PTLayout(Window(FormattedTextControl(get_menu_text))),
                key_bindings=kb,
                style=style,
            )
            action = app.run()
            if action:
                action()


    def handle_discovery(self):
        if not self.backend_online:
            self._show_offline_warning()
            if not self.backend_online:
                return
        print_header("Discovery")
        options = [
            {"name": "🆕 New Movies (In Theaters/Digital)", "val": "movies"},
            {"name": "📺 New Episodes (Airing Today)", "val": "episodes"},
            {"name": "🔥 Trending TV (Today)", "val": "trending_tv_today"},
            {"name": "🔥 Movie of the Day", "val": "movie_of_the_day"},
        ]

        while True:
            sel = selection_menu(
                options,
                "Discovery Options",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if not sel or sel["action"] == "back":
                break

            if sel["action"] == "select":
                choice = sel["value"]["val"]
                if choice == "movies":
                    self.browse_new_movies()
                elif choice == "episodes":
                    self.browse_new_episodes()
                elif choice == "trending_tv_today":
                    self.browse_trending_tv_today()
                elif choice == "movie_of_the_day":
                    self.browse_movie_of_the_day()

    def browse_new_movies(self):
        page = 1
        while True:
            data = self.api.get_new_movies(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "movie"

            # Navigation controls (use consistent "title")
            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"New Movies (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(val)
                continue
            if sel["action"] == "select":
                self.handle_media(val)

    def browse_new_episodes(self):
        page = 1
        while True:
            data = self.api.get_new_episodes(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "tv"

            # Navigation controls (FIX: use "title" consistently)
            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"New Episodes (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(val)
                continue
            if sel["action"] == "select":
                self.handle_media(val)

    def browse_trending_tv_today(self):
        page = 1
        while True:
            # You need an API method like: GET /trending/tv/day?page=page
            data = self.api.get_trending_tv_today(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "tv"

            # Navigation controls
            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"Trending TV Today (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(val)
                continue
            if sel["action"] == "select":
                self.handle_media(val)

    def browse_movie_of_the_day(self):
        page = 1
        while True:
            # TMDB: /trending/movie/day
            data = self.api.get_trending_movies_today(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "movie"

            # Navigation controls
            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"🔥 Movie of the Day (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(val)
                continue
            if sel["action"] == "select":
                self.handle_media(val)

    def handle_search(self):
        if not self.backend_online:
            self._show_offline_warning()
            if not self.backend_online:
                return
        print_header("Search")
        query = console.input(
            f"[bold {ACCENT}]Search for a movie or TV show: [/bold {ACCENT}]"
        )
        if not query.strip():
            return

        data = self.api.get_tmdb_data("search/multi", {"query": query})
        if not data or not data.get("results"):
            console.print("[yellow]No results found.[/yellow]")
            time.sleep(1.5)
            return

        results = [r for r in data["results"] if r.get("media_type") in ["movie", "tv"]]
        while True:
            sel = selection_menu(results, f"Search Results for '{query}'")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(sel["value"])
                continue
            if sel["action"] == "batch":
                movies = [r for r in results if r.get("media_type") == "movie"]
                if movies:
                    self.handle_batch_movie_download(movies)
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_trending(self):
        if not self.backend_online:
            self._show_offline_warning()
            if not self.backend_online:
                return
        data = self.api.get_tmdb_data("trending/all/week")
        if not data:
            return
        results = [r for r in data["results"] if r.get("media_type") in ["movie", "tv"]]
        while True:
            sel = selection_menu(results, "Trending This Week")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_popular(self):
        if not self.backend_online:
            self._show_offline_warning()
            if not self.backend_online:
                return
        types = [
            {"name": "🎬 Movies",   "val": "movie"},
            {"name": "📺 TV Shows", "val": "tv"},
        ]
        type_sel = selection_menu(
            types,
            "Popular — Content Type",
            show_details=False,
            formatter=lambda x: x["name"],
        )
        if not type_sel or type_sel["action"] != "select":
            return
        m_type = type_sel["value"]["val"]

        data = self.api.get_tmdb_data(f"{m_type}/popular")
        if not data:
            return
        results = data["results"]
        for r in results:
            r["media_type"] = m_type

        while True:
            sel = selection_menu(results, f"Popular {m_type.title()}s")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(sel["value"])
                continue
            if sel["action"] == "batch" and m_type == "movie":
                self.handle_batch_movie_download(results)
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])


    def handle_genres(self):
        if not self.backend_online:
            self._show_offline_warning()
            if not self.backend_online:
                return
        types = [
            {"name": "🎬 Movies",   "val": "movie"},
            {"name": "📺 TV Shows", "val": "tv"},
        ]
        type_sel = selection_menu(
            types,
            "Genres — Content Type",
            show_details=False,
            formatter=lambda x: x["name"],
        )
        if not type_sel or type_sel["action"] != "select":
            return
        m_type = type_sel["value"]["val"]

        data = self.api.get_tmdb_data(f"genre/{m_type}/list")
        if not data:
            return
        genres = data["genres"]

        genre_sel = selection_menu(
            genres,
            f"Select Genre ({m_type.title()}s)",
            show_details=False,
            formatter=lambda g: g["name"],
        )
        if not genre_sel or genre_sel["action"] != "select":
            return
        genre = genre_sel["value"]

        data = self.api.get_tmdb_data(
            f"discover/{m_type}", {"with_genres": genre["id"]}
        )
        if not data:
            return
        results = data["results"]
        for r in results:
            r["media_type"] = m_type
        while True:
            sel = selection_menu(results, f"{genre['name']} {m_type.title()}s")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])


    def handle_favorites(self):
        if not self.favorites:
            print_header("Favorites")
            console.print(
                "[yellow]No favorites yet. Press 'F' on any item to add it![/yellow]"
            )
            time.sleep(2)
            return
        while True:
            sel = selection_menu(self.favorites, "My Favorites")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                self.favorites = load_json_data(FAVORITES_FILE) or []
                if not self.favorites:
                    break
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_history(self):
        if not self.history:
            print_header("History")
            console.print("[yellow]Your watch history is empty.[/yellow]")
            time.sleep(2)
            return
        while True:
            sel = selection_menu(self.history, "Watch History")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_watch_later(self):
        """Watch Later / Planned list — add with W key from any media list."""
        if not self.watch_later:
            print_header("Watch Later")
            console.print(
                f"[{WARNING}]Your Watch Later list is empty.[/]\n"
                f"[dim {TEXT}]Press [bold]W[/bold] on any movie or show to add it here.[/dim {TEXT}]"
            )
            time.sleep(2.5)
            return

        while True:
            sel = selection_menu(self.watch_later, "Watch Later")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "watch_later":
                # W pressed again — remove from list
                self.toggle_watch_later(sel["value"])
                self.watch_later = load_json_data(WATCH_LATER_FILE) or []
                if not self.watch_later:
                    break
                continue
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def toggle_watch_later(self, item):
        item_id = item.get("id")
        exists = any(w.get("id") == item_id for w in self.watch_later)
        if exists:
            self.watch_later = [w for w in self.watch_later if w.get("id") != item_id]
            console.print(f"[{WARNING}]Removed from Watch Later.[/]")
        else:
            self.watch_later.insert(0, item)
            console.print(f"[{SUCCESS}]Added to Watch Later![/]")
        save_json_data(WATCH_LATER_FILE, self.watch_later)
        time.sleep(0.5)

    def update_history(self, media, stats, episode=None):
        if not self.history:
            self.history = []

        # Find entry in history; if missing, insert it
        existing = None
        for item in self.history:
            if item.get("id") == media.get("id"):
                existing = item
                break

        if not existing:
            self.history.insert(0, media)
            existing = self.history[0]

        existing["last_watched"] = time.time()

        if episode:
            existing["last_episode"] = {
                "season": episode.get("season_number"),
                "episode": episode.get("episode_number"),
                "name": episode.get("name"),
                "position": stats.get("position"),
                "duration": stats.get("duration"),
            }
        else:
            existing["position"] = stats.get("position")
            existing["duration"] = stats.get("duration")
            existing["finished"] = stats.get("finished")

        save_json_data(HISTORY_FILE, self.history)

    def handle_settings(self):
        # Language code to display name mapping
        _sub_names = {
            "ar": "Arabic", "en": "English", "fr": "French",
            "es": "Spanish", "de": "German", "tr": "Turkish",
            "pt": "Portuguese", "it": "Italian",
            "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi",
        }

        def _fmt_langs(codes):
            return ", ".join(_sub_names.get(c, c) for c in codes) if codes else "(none)"

        print_header("Settings")
        console.print(
            f"[bold {TEXT}]1. Backend URL:[/bold {TEXT}] {self.settings.get('backend', BACKEND_URL)}"
        )
        console.print(
            f"[bold {TEXT}]2. TMDB API Key:[/bold {TEXT}] {self.settings.get('tmdb_key', 'Using Default')}"
        )
        console.print(
            f"[bold {TEXT}]3. Movie Filename Template:[/bold {TEXT}] {self.settings.get('filename_template')}"
        )
        console.print(
            f"[bold {TEXT}]4. TV Filename Template:[/bold {TEXT}] {self.settings.get('filename_template_tv')}"
        )
        console.print(
            f"[bold {TEXT}]5. Library Directory:[/bold {TEXT}] {self.settings.get('library_dir')}"
        )

        pref_sub = self.settings.get('preferred_subtitle', 'ar')
        pref_sub_name = _sub_names.get(pref_sub, pref_sub)
        console.print(
            f"[bold {TEXT}]6. Primary Subtitle Language:[/bold {TEXT}] {pref_sub_name} ({pref_sub})"
        )

        pref_player = self.settings.get('preferred_player', 'mpv')
        available_players = detect_available_players()
        avail_str = ', '.join(available_players) if available_players else 'none found'
        console.print(
            f"[bold {TEXT}]7. Preferred Player:[/bold {TEXT}] {pref_player.upper()} (available: {avail_str})"
        )

        # Multi-language subtitle list (ordered; primary is always first)
        pref_langs = self.settings.get('preferred_subtitle_langs', [pref_sub])
        if not isinstance(pref_langs, list) or not pref_langs:
            pref_langs = [pref_sub]
        # Keep primary in sync as first entry
        if pref_langs[0] != pref_sub:
            pref_langs = [pref_sub] + [l for l in pref_langs if l != pref_sub]
        console.print(
            f"[bold {TEXT}]8. Preferred Subtitle Languages (ordered):[/bold {TEXT}] "
            f"{_fmt_langs(pref_langs)}"
        )
        console.print(
            f"[dim]   (Streaming: all tracks loaded in this order; Downloads: all embedded)[/dim]"
        )

        fb_langs = self.settings.get('fallback_subtitle_langs', ['ar', 'en'])
        if not isinstance(fb_langs, list):
            fb_langs = ['ar', 'en']
        console.print(
            f"[bold {TEXT}]9. OpenSubtitles Fallback Languages:[/bold {TEXT}] {_fmt_langs(fb_langs)}"
        )
        console.print(
            f"[dim]   (Used only when no subtitles come from the source provider)[/dim]"
        )

        current_theme = self.settings.get("theme", "cinema")
        console.print(
            f"[bold {TEXT}]10. Theme:[/bold {TEXT}] {current_theme.capitalize()}"
        )
        console.print(
            f"[dim]    (Applies on next launch — choose from cinema, blue, purple, green, gold, teal, rose, sunset, mint)[/dim]"
        )

        speed_limit = self.settings.get("download_speed_limit", 0)
        speed_display = f"{speed_limit} MB/s" if speed_limit else "Unlimited"
        console.print(
            f"[bold {TEXT}]11. Download Speed Limit:[/bold {TEXT}] {speed_display}"
        )
        console.print(
            f"[dim]    (Caps yt-dlp bandwidth; 0 = no limit. Example: 5 for 5 MB/s)[/dim]"
        )
        console.print(
            f"[bold {TEXT}]12. Export Settings to file[/bold {TEXT}]"
        )
        console.print(
            f"[bold {TEXT}]13. Import Settings from file[/bold {TEXT}]"
        )

        choice = console.input(
            f"\n[bold {ACCENT}]Select setting to change (1-13) or Enter to back: [/bold {ACCENT}]"
        )

        if choice == "1":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new backend URL: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["backend"] = new_val.strip()
        elif choice == "2":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new TMDB API Key: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["tmdb_key"] = new_val.strip()
        elif choice == "3":
            console.print("[dim]Tokens: {title}, {year}, {quality}, {provider}[/dim]")
            new_val = console.input(
                f"[bold {ACCENT}]Enter new Movie Template: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["filename_template"] = new_val.strip()
        elif choice == "4":
            console.print(
                "[dim]Tokens: {title}, {year}, {season}, {episode}, {quality}, {provider}[/dim]"
            )
            new_val = console.input(
                f"[bold {ACCENT}]Enter new TV Template: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["filename_template_tv"] = new_val.strip()
        elif choice == "5":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new Library Directory: [/bold {ACCENT}]"
            )
            if new_val.strip():
                p = os.path.expanduser(new_val.strip())
                os.makedirs(p, exist_ok=True)
                self.settings["library_dir"] = p
                self.download_manager.downloads_dir = p
        elif choice == "6":
            lang_options = [
                {"name": f"📝 {name}", "value": code}
                for code, name in _sub_names.items()
            ]
            sel = selection_menu(
                lang_options,
                "Primary Subtitle Language (shown first / default active track)",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if sel and sel["action"] == "select":
                new_primary = sel["value"]["value"]
                self.settings["preferred_subtitle"] = new_primary
                # Rebuild ordered list: new primary first, rest unchanged
                existing = self.settings.get("preferred_subtitle_langs", [])
                if not isinstance(existing, list):
                    existing = []
                others = [l for l in existing if l != new_primary]
                self.settings["preferred_subtitle_langs"] = [new_primary] + others
                console.print(
                    f"[green]Primary subtitle set to: {_sub_names.get(new_primary, new_primary)}[/green]"
                )
        elif choice == "7":
            player_options = [
                {"name": "🎬 MPV", "value": "mpv"},
                {"name": "📺 VLC", "value": "vlc"},
                {"name": "🍎 IINA (macOS)", "value": "iina"},
            ]
            sel = selection_menu(
                player_options,
                "Preferred Player",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if sel and sel["action"] == "select":
                chosen = sel["value"]["value"]
                self.settings["preferred_player"] = chosen
                console.print(
                    f"[green]Preferred player set to: {chosen.upper()}[/green]"
                )
        elif choice == "8":
            console.print(
                f"[dim]Enter languages in priority order, comma-separated.[/dim]\n"
                f"[dim]Example: ar,en,fr  — Arabic first, then English, then French[/dim]\n"
                f"[dim]Codes: ar en fr es de tr pt it zh ja ko hi[/dim]\n"
                f"[dim]Current: {_fmt_langs(self.settings.get('preferred_subtitle_langs', [pref_sub]))}[/dim]"
            )
            new_val = console.input(
                f"[bold {ACCENT}]Enter preferred subtitle languages (comma-separated): [/bold {ACCENT}]"
            )
            langs = [x.strip().lower() for x in (new_val or '').split(',') if x.strip()]
            seen = set()
            langs = [x for x in langs if not (x in seen or seen.add(x))]
            if langs:
                self.settings['preferred_subtitle_langs'] = langs
                self.settings['preferred_subtitle'] = langs[0]   # keep primary in sync
                console.print(f"[green]Preferred subtitle languages set to: {_fmt_langs(langs)}[/green]")
        elif choice == "9":
            console.print(
                f"[dim]Used only when the source has no subtitles at all. Example: ar,en[/dim]"
            )
            new_val = console.input(
                f"[bold {ACCENT}]Enter fallback subtitle languages (comma-separated): [/bold {ACCENT}]"
            )
            langs = [x.strip().lower() for x in (new_val or '').split(',') if x.strip()]
            seen = set()
            langs = [x for x in langs if not (x in seen or seen.add(x))]
            if langs:
                self.settings['fallback_subtitle_langs'] = langs
                console.print(f"[green]Fallback languages set to: {_fmt_langs(langs)}[/green]")
        elif choice == "10":
            from src.config import THEME_NAMES
            theme_opts = [
                {"name": f"🎨  {t.capitalize()}", "value": t}
                for t in THEME_NAMES
            ]
            sel = selection_menu(
                theme_opts,
                "Select Theme",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if sel and sel["action"] == "select":
                chosen_theme = sel["value"]["value"]
                self.settings["theme"] = chosen_theme
                # Apply the new theme live — no restart needed
                from src.config import apply_theme as _apply_theme
                _apply_theme(chosen_theme)
                # Refresh this module's own colour globals immediately
                import src.config as _cfg_mod
                for _cname in ("PRIMARY", "SECONDARY", "ACCENT", "SUCCESS", "WARNING", "TEXT"):
                    globals()[_cname] = getattr(_cfg_mod, _cname)
                console.print(
                    f"[green]Theme set to '{chosen_theme}' — applied immediately.[/green]"
                )
        elif choice == "11":
            console.print(
                f"[dim]Enter maximum download speed in MB/s (e.g. 5 for 5 MB/s). Enter 0 for unlimited.[/dim]"
            )
            new_val = console.input(
                f"[bold {ACCENT}]Speed limit in MB/s (0 = unlimited): [/bold {ACCENT}]"
            ).strip()
            try:
                limit = float(new_val)
                if limit < 0:
                    raise ValueError
                self.settings["download_speed_limit"] = limit
                display = f"{limit} MB/s" if limit else "Unlimited"
                console.print(f"[green]Download speed limit set to: {display}[/green]")
            except ValueError:
                console.print(f"[yellow]Invalid value — keeping current setting.[/yellow]")
        elif choice == "12":
            # ── Export ────────────────────────────────────────────────────────
            default_path = os.path.expanduser("~/cinema-cli-backup.json")
            dest = console.input(
                f"[bold {ACCENT}]Export path [{default_path}]: [/bold {ACCENT}]"
            ).strip() or default_path
            dest = os.path.expanduser(dest)
            try:
                export_bundle = {
                    "version": APP_VERSION,
                    "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "settings": dict(self.settings),
                }
                # Include provider health scores if they exist
                try:
                    from src.config import PROVIDER_SCORES_FILE
                    if os.path.exists(PROVIDER_SCORES_FILE):
                        with open(PROVIDER_SCORES_FILE, "r", encoding="utf-8") as _f:
                            export_bundle["provider_scores"] = json.load(_f)
                except Exception:
                    pass
                with open(dest, "w", encoding="utf-8") as fh:
                    json.dump(export_bundle, fh, indent=2, ensure_ascii=False)
                console.print(f"[green]Settings exported to: {dest}[/green]")
            except Exception as exc:
                console.print(f"[red]Export failed: {exc}[/red]")
            time.sleep(1.2)
            return
        elif choice == "13":
            # ── Import ────────────────────────────────────────────────────────
            src_path = console.input(
                f"[bold {ACCENT}]Path to backup file: [/bold {ACCENT}]"
            ).strip()
            if not src_path:
                return
            src_path = os.path.expanduser(src_path)
            if not os.path.isfile(src_path):
                console.print(f"[red]File not found: {src_path}[/red]")
                time.sleep(1)
                return
            try:
                with open(src_path, "r", encoding="utf-8") as fh:
                    bundle = json.load(fh)
                imported_settings = bundle.get("settings") or bundle  # support bare settings too
                if not isinstance(imported_settings, dict):
                    raise ValueError("No valid settings found in file")
                # Merge: imported values overwrite current; keep newer keys not in backup
                self.settings.update(imported_settings)
                save_json_data(SETTINGS_FILE, self.settings)
                # Restore provider health scores if present
                try:
                    from src.config import PROVIDER_SCORES_FILE
                    if "provider_scores" in bundle and isinstance(bundle["provider_scores"], dict):
                        with open(PROVIDER_SCORES_FILE, "w", encoding="utf-8") as _f:
                            json.dump(bundle["provider_scores"], _f, indent=2)
                        console.print("[green]Provider health scores restored.[/green]")
                except Exception:
                    pass
                console.print(
                    f"[green]Settings imported from: {src_path}[/green]\n"
                    f"[dim]Re-open Settings to see any theme change take effect.[/dim]"
                )
            except Exception as exc:
                console.print(f"[red]Import failed: {exc}[/red]")
            time.sleep(1.5)
            return
        else:
            return

        save_json_data(SETTINGS_FILE, self.settings)
        console.print("[green]Settings saved![/green]")
        time.sleep(1)

    def toggle_favorite(self, item):
        item_id = item.get("id")
        exists = any(f.get("id") == item_id for f in self.favorites)
        if exists:
            self.favorites = [f for f in self.favorites if f.get("id") != item_id]
            console.print("[yellow]Removed from favorites.[/yellow]")
        else:
            self.favorites.insert(0, item)
            console.print("[green]Added to favorites![/green]")
        save_json_data(FAVORITES_FILE, self.favorites)
        time.sleep(0.5)

    def handle_media(self, media):
        self.history = [h for h in self.history if h.get("id") != media.get("id")]
        self.history.insert(0, media)
        self.history = self.history[:50]
        save_json_data(HISTORY_FILE, self.history)

        m_type = media.get("media_type", "movie")
        if m_type == "movie":
            self.play_movie(media)
        else:
            self.show_seasons(media)

    def play_movie(self, media):
        title = media.get("title")
        tmdb_id = media.get("id")
        
        # Use enhanced source fetching with retry
        console.print(f"[bold {ACCENT}]Fetching sources for: {title}...[/bold {ACCENT}]")
        data = self.api.get_sources_enhanced(tmdb_id, "movie", min_sources=3)

        rel = media.get("release_date") or ""
        year = rel[:4] if isinstance(rel, str) and len(rel) >= 4 else None

        # ✅ FIX: include tmdb_id + type so playback resume works
        meta = {"year": year, "tmdb_id": tmdb_id, "type": "movie", "runtime": media.get("runtime")}

        stats = self.handle_sources(title, data, meta)
        if isinstance(stats, dict):
            self.update_history(media, stats, episode=None)

    def show_seasons(self, media):
        print_header(f"{media.get('name')} - Seasons")
        data = self.api.get_tmdb_data(f"tv/{media['id']}")
        if not data:
            return
        seasons = [s for s in data.get("seasons", []) if s.get("season_number") > 0]

        def fmt_season(x):
            name = x.get("name", "")
            air = x.get("air_date") or "????-??-??"
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else "????"
            rating = x.get("vote_average", 0)
            return f"{name} ({year}) | ⭐ {rating:.1f} | TV"

        while True:
            sel = selection_menu(
                seasons,
                f"{media.get('name')} Seasons",
                show_details=False,
                formatter=fmt_season,
            )
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "select":
                self.show_episodes(media, sel["value"])

    def autoplay_countdown(self, next_ep_name, timeout=10):
        """Displays a countdown panel. Returns True if finished, False if interrupted."""
        from rich.live import Live
        from rich.progress import Progress, BarColumn, TextColumn
        import select
        
        clear()
        progress = Progress(
            TextColumn("[bold yellow]Next episode starts in {task.fields[secs]} seconds...[/bold yellow]"),
            BarColumn(bar_width=40, complete_style=PRIMARY, finished_style=SUCCESS),
            TextColumn("[dim](Press any key to cancel)[/dim]")
        )
        
        task_id = progress.add_task("autoplay", total=timeout, secs=timeout)
        
        with Live(progress, console=console, refresh_per_second=10):
            start_time = time.time()
            while not progress.finished:
                elapsed = time.time() - start_time
                remaining = max(0, timeout - elapsed)
                progress.update(task_id, completed=elapsed, secs=int(remaining))
                
                # Cross-platform key check
                if sys.platform == "win32":
                    import msvcrt
                    if msvcrt.kbhit():
                        msvcrt.getch()
                        return False
                else:
                    if select.select([sys.stdin], [], [], 0)[0]:
                        sys.stdin.read(1)
                        return False
                
                if remaining <= 0:
                    break
                time.sleep(0.05)
                
        return True

    def show_episodes(self, media, season):
        s_num = season["season_number"]
        print_header(f"{media.get('name')} - Season {s_num}")
        data = self.api.get_tmdb_data(f"tv/{media['id']}/season/{s_num}")
        if not data:
            return
        episodes = data.get("episodes", [])

        def fmt_ep(x):
            name = x.get("name", "Unknown")
            air = x.get("air_date") or "N/A"
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else "N/A"
            rating = x.get("vote_average", 0)
            return f"{name} ({year}) | ⭐ {rating:.1f} | TV"

        selected_idx = 0
        while True:
            sel = selection_menu(
                episodes,
                f"Season {s_num} Episodes",
                show_details=True,
                formatter=fmt_ep,
                default_index=selected_idx,
            )
            if not sel or sel["action"] == "back":
                break

            if sel["action"] == "batch":
                self.handle_batch_download(media, season, episodes)
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(media)
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(media)
                continue

            if sel["action"] == "select":
                ep = sel["value"]
                selected_idx = episodes.index(ep)
                next_step_auto = False

                while True:
                    title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"
                    
                    # Use enhanced source fetching for TV episodes
                    console.print(f"[bold {ACCENT}]Fetching sources for: {title}...[/bold {ACCENT}]")
                    data = self.api.get_sources_enhanced(
                        media["id"], "tv", s_num, ep["episode_number"], min_sources=3
                    )

                    air = ep.get("air_date") or ""
                    year = air[:4] if isinstance(air, str) and len(air) >= 4 else None

                    meta = {
                        "year": year,
                        "season": s_num,
                        "episode": ep.get("episode_number"),
                        "tmdb_id": media["id"],
                        "type": "tv",
                        "runtime": ep.get("runtime"),
                    }

                    if next_step_auto:
                        stats = self.handle_sources(title, data, meta, autoplay=True)
                        next_step_auto = False
                    else:
                        stats = self.handle_sources(title, data, meta)

                    if isinstance(stats, dict):
                        self.update_history(media, stats, episode=ep)
                        
                        # --- Smart Autoplay Logic ---
                        if stats.get("finished") and selected_idx + 1 < len(episodes):
                            next_ep = episodes[selected_idx + 1]
                            next_title = f"E{next_ep['episode_number']} - {next_ep['name']}"
                            
                            if self.autoplay_countdown(next_title):
                                selected_idx += 1
                                ep = next_ep
                                next_step_auto = True
                                continue # Start next episode
                    
                    if not stats:
                        break

                    opts = [
                        "Next Episode",
                        "Previous Episode",
                        "Replay",
                        "Back to List",
                    ]
                    fin_sel = selection_menu(
                        opts,
                        "Finished Watching",
                        show_details=False,
                        formatter=lambda x: x,
                    )

                    if not fin_sel or fin_sel["action"] in ["back", "quit"]:
                        break

                    choice = fin_sel["value"]
                    if choice == "Next Episode":
                        if selected_idx + 1 < len(episodes):
                            selected_idx += 1
                            ep = episodes[selected_idx]
                        else:
                            console.print(
                                "[yellow]No next episode in this season.[/yellow]"
                            )
                            time.sleep(1)
                            break
                    elif choice == "Previous Episode":
                        if selected_idx > 0:
                            selected_idx -= 1
                            ep = episodes[selected_idx]
                        else:
                            console.print("[yellow]No previous episode.[/yellow]")
                            time.sleep(1)
                            break
                    elif choice == "Replay":
                        pass
                    elif choice == "Back to List":
                        break

    def handle_batch_download(self, media, season, episodes):
        s_num = season["season_number"]

        def fmt_ep(x):
            name = x.get("name", "Unknown")
            ep_num = x.get("episode_number", "?")
            return f"E{ep_num} - {name}"

        selected_episodes = multi_selection_menu(
            episodes, f"Select Episodes to Download (S{s_num})", formatter=fmt_ep
        )
        if not selected_episodes:
            return

        console.print(
            f"\n[bold {PRIMARY}]Preparing batch download for {len(selected_episodes)} episodes...[/bold {PRIMARY}]"
        )


        # Batch preferences (applied to all episodes in this batch)
        selected_quality = "auto"
        preferred_sub_lang = self.settings.get("preferred_subtitle", "ar")
        pref_langs = self.settings.get("preferred_subtitle_langs", [preferred_sub_lang])
        if not isinstance(pref_langs, list) or not pref_langs:
            pref_langs = [preferred_sub_lang]
        if pref_langs[0] != preferred_sub_lang:
            pref_langs = [preferred_sub_lang] + [l for l in pref_langs if l != preferred_sub_lang]
        include_all_subs = len(pref_langs) > 1

        try:
            first_ep = selected_episodes[0]
            first_title = f"{media.get('name')} S{s_num}E{first_ep['episode_number']} - {first_ep.get('name')}"
            first_data = self.api.get_sources_enhanced(media["id"], "tv", s_num, first_ep["episode_number"], min_sources=2)
            first_files = first_data.get("files", []) if isinstance(first_data, dict) else []
            first_subs = first_data.get("subtitles", []) if isinstance(first_data, dict) else []

            # Quality selection
            # Providers return HLS adaptive streams; resolution is inside the manifest.
            # Always offer standard resolution options; fold in any provider-tagged
            # qualities if they happen to be present.
            manifest_qualities = []
            for f in first_files:
                q = f.get("quality")
                if q and q not in manifest_qualities:
                    manifest_qualities.append(q)

            def quality_sort_key(q):
                q = (q or "").lower()
                if "4k" in q or "2160" in q: return 0
                if "1080" in q: return 1
                if "720" in q: return 2
                if "480" in q: return 3
                if "360" in q: return 4
                return 5

            manifest_qualities.sort(key=quality_sort_key)

            std_q_options = [
                {"name": "✨ Best Available (Auto)", "value": "auto"},
                {"name": "📺 4K (2160p)", "value": "4k"},
                {"name": "📺 1080p", "value": "1080p"},
                {"name": "📺 720p", "value": "720p"},
                {"name": "📺 480p", "value": "480p"},
                {"name": "📺 360p", "value": "360p"},
            ]
            for q in manifest_qualities:
                tag = q.lower().replace(" ", "")
                already = any(tag in opt["value"] for opt in std_q_options)
                if not already:
                    std_q_options.append({"name": f"📺 {q} (provider)", "value": q})

            q_sel = selection_menu(
                std_q_options,
                f"Batch Download - Select Quality (S{s_num})",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if q_sel and q_sel.get("action") == "select":
                selected_quality = q_sel["value"]["value"]

            # Subtitle language selection
            if first_subs:
                def _norm_lang(l):
                    l = (l or "").strip().lower()
                    if l in ["arabic","ara","ar"]:          return "ar"
                    if l in ["english","eng","en"]:         return "en"
                    if l in ["french","fra","fre","fr"]:    return "fr"
                    if l in ["spanish","spa","es"]:         return "es"
                    if l in ["german","deu","ger","de"]:    return "de"
                    if l in ["turkish","tur","tr"]:         return "tr"
                    if l in ["portuguese","por","pt"]:      return "pt"
                    if l in ["italian","ita","it"]:         return "it"
                    return l or "und"

                avail_codes = []
                for s in first_subs:
                    if isinstance(s, dict):
                        code = _norm_lang(s.get("lang") or s.get("language"))
                        if code not in avail_codes:
                            avail_codes.append(code)

                _lnames = {
                    "ar": "Arabic", "en": "English", "fr": "French", "es": "Spanish",
                    "de": "German", "tr": "Turkish", "pt": "Portuguese", "it": "Italian",
                    "und": "Unknown",
                }
                def _lang_label(c): return _lnames.get(c, c)

                pref_label = ", ".join(_lang_label(l) for l in pref_langs[:3])
                if len(pref_langs) > 3:
                    pref_label += f" +{len(pref_langs)-3}"

                lang_opts = [
                    {"name": f"🌐 All Preferred ({pref_label})", "value": "preferred"},
                    {"name": "🗂 All Available (embed every track)", "value": "all"},
                    {"name": f"📝 Primary only ({_lang_label(preferred_sub_lang)})", "value": "primary"},
                ]
                for code in avail_codes:
                    lang_opts.append({"name": f"📝 {_lang_label(code)}", "value": code})
                lang_opts.append({"name": "🚫 No subtitles", "value": "none"})

                lang_sel = selection_menu(
                    lang_opts,
                    "Batch Download - Select Subtitle Languages",
                    show_details=False,
                    formatter=lambda x: x["name"],
                )
                if lang_sel and lang_sel.get("action") == "select":
                    chosen = lang_sel["value"]["value"]
                    if chosen == "preferred":
                        include_all_subs = True      # pref_langs already set from settings
                    elif chosen == "all":
                        include_all_subs = True
                        pref_langs = avail_codes      # embed everything
                    elif chosen == "primary":
                        include_all_subs = False
                        pref_langs = [preferred_sub_lang]
                    elif chosen == "none":
                        preferred_sub_lang = "none"
                        pref_langs = []
                        include_all_subs = False
                    else:
                        preferred_sub_lang = chosen
                        pref_langs = [chosen]
                        include_all_subs = False
        except Exception as _pref_err:
            # Log preference selection error instead of silently swallowing it
            try:
                from src.utils.app_logger import log_event
                log_event("download", f"Batch preference selection error: {_pref_err}", level="WARNING")
            except Exception:
                pass
            console.print(f"[yellow]Preference selection encountered an issue; using defaults.[/yellow]")

        # ── Parallel source fetch for all selected episodes ────────────────
        console.print(
            f"[bold {PRIMARY}]Fetching sources for {len(selected_episodes)} episodes in parallel...[/bold {PRIMARY}]"
        )
        ep_data_map: dict = {}   # episode_number -> {files, subtitles}
        ep_sources:  dict = {}   # episode_number -> selected_source

        def _fetch_ep(ep):
            ep_num = ep.get("episode_number")
            try:
                data = self.api.get_sources_enhanced(
                    media["id"], "tv", s_num, ep_num, min_sources=2
                )
                files  = data.get("files", []) if isinstance(data, dict) else []
                subs   = data.get("subtitles", []) if isinstance(data, dict) else []
                # Quality filter
                if selected_quality != "auto" and files:
                    q_files = [f for f in files if f.get("quality") == selected_quality]
                    if q_files:
                        files = q_files
                    elif any(f.get("quality") for f in files):
                        # Tagged qualities exist but selected one is absent.
                        files = []
                src = select_working_source(files) if files else None
                return ep_num, files, subs, src
            except Exception as exc:
                return ep_num, [], [], None

        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _asc
        with _TPE(max_workers=min(4, len(selected_episodes))) as _pool:
            _futs = {_pool.submit(_fetch_ep, ep): ep for ep in selected_episodes}
            for _fut in _asc(_futs):
                ep_num, files, subs, src = _fut.result()
                ep_data_map[ep_num] = {"files": files, "subtitles": subs}
                ep_sources[ep_num]  = src
                status = f"[{SUCCESS}]✓[/]" if src else f"[red]✗[/]"
                console.print(f"  {status} E{ep_num}")

        for ep in selected_episodes:
            ep_num = ep.get("episode_number")
            title  = f"{media.get('name')} S{s_num}E{ep_num} - {ep.get('name')}"
            ep_info = ep_data_map.get(ep_num, {})
            files     = ep_info.get("files", [])
            subtitles = ep_info.get("subtitles", [])

            if not files:
                console.print(f"[yellow]No sources found for {title}. Skipping...[/yellow]")
                continue

            # Apply batch-selected subtitles
            if preferred_sub_lang == "none":
                subtitles = []

            air = ep.get("air_date") or ""
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else None
            meta = {
                "year": year,
                "season": s_num,
                "episode": ep.get("episode_number"),
                "tmdb_id": media.get("id"),
                "type": "tv",
                "runtime": ep.get("runtime"),
            }

            # Automated source selection for batch download
            selected_source = ep_sources.get(ep.get("episode_number"))

            if not selected_source:
                console.print(f"[red]No working source found for {title}. Skipping...[/red]")
                continue

            template = self.settings.get(
                "filename_template_tv", "{title}.S{season}E{episode}"
            )
            filename = generate_filename(template, title, meta, selected_source)
            # Tag filename with chosen quality (skip for auto/best)
            if selected_quality not in ("auto", "adaptive"):
                base, ext = os.path.splitext(filename)
                filename = f"{base}.{selected_quality}{ext}"

            console.print(
                f"[green]Queuing download: {selected_source.get('provider')} ({selected_source.get('quality') or selected_quality})...[/green]"
            )
            
            # Use top working source + other files as fallback
            # Compare by URL to avoid identity-based mismatch (dicts may be different objects with same content)
            _selected_url = selected_source.get("file", "")
            fallback_sources = [f for f in files if f.get("file", "") != _selected_url]
            
            self.download_manager.add_task(
                selected_source.get("file"),
                filename,
                title,
                subtitles,
                selected_source.get("headers"),
                meta=meta,
                fallback_sources=fallback_sources,
                api_params={
                    "tmdb_id": media.get("id"),
                    "media_type": "tv",
                    "season": s_num,
                    "episode": ep.get("episode_number")
                },
                preferred_sub_lang=preferred_sub_lang,
                include_all_subs=include_all_subs,
                preferred_sub_langs=pref_langs,
                fallback_sub_langs=self.settings.get('fallback_subtitle_langs', ['ar','en']),
                quality=selected_quality if selected_quality not in ("auto", "adaptive") else None,
            )

        console.print(f"\n[bold {SUCCESS}]Batch download queued![/bold {SUCCESS}]")
        time.sleep(2)

    def handle_batch_movie_download(self, movies):
        """Batch download for movies — mirrors handle_batch_download for TV."""
        movie_list = [m for m in movies if m.get("media_type") == "movie" or m.get("title")]
        if not movie_list:
            console.print(f"[{WARNING}]No movies in this list to batch-download.[/]")
            time.sleep(1.5)
            return

        def fmt_movie(m):
            title = m.get("title") or m.get("name", "Unknown")
            year  = (m.get("release_date") or "")[:4] or "N/A"
            rating = m.get("vote_average", 0)
            return f"{title} ({year}) | ⭐ {rating:.1f}"

        selected_movies = multi_selection_menu(
            movie_list, "Select Movies to Download", formatter=fmt_movie
        )
        if not selected_movies:
            return

        console.print(
            f"\n[bold {PRIMARY}]Preparing batch download for {len(selected_movies)} movies...[/bold {PRIMARY}]"
        )

        # Quality selection using first movie's sources as reference
        selected_quality = "auto"
        preferred_sub_lang = self.settings.get("preferred_subtitle", "ar")
        pref_langs = self.settings.get("preferred_subtitle_langs", [preferred_sub_lang])
        if not isinstance(pref_langs, list) or not pref_langs:
            pref_langs = [preferred_sub_lang]
        include_all_subs = len(pref_langs) > 1

        try:
            first_m  = selected_movies[0]
            first_id = first_m.get("id")
            first_data = self.api.get_sources_enhanced(first_id, "movie", min_sources=2)
            first_files = first_data.get("files", []) if isinstance(first_data, dict) else []
            first_subs  = first_data.get("subtitles", []) if isinstance(first_data, dict) else []

            std_q_options = [
                {"name": "✨ Best Available (Auto)", "value": "auto"},
                {"name": "📺 4K (2160p)",          "value": "4k"},
                {"name": "📺 1080p",               "value": "1080p"},
                {"name": "📺 720p",                "value": "720p"},
                {"name": "📺 480p",                "value": "480p"},
                {"name": "📺 360p",                "value": "360p"},
            ]
            q_sel = selection_menu(
                std_q_options, "Batch Download — Select Quality",
                show_details=False, formatter=lambda x: x["name"],
            )
            if q_sel and q_sel.get("action") == "select":
                selected_quality = q_sel["value"]["value"]

            # Subtitle language selection (same logic as TV batch)
            if first_subs:
                def _norm(l):
                    l = (l or "").strip().lower()
                    m2 = {"arabic":"ar","ara":"ar","ar":"ar","english":"en","eng":"en","en":"en",
                          "french":"fr","fra":"fr","fre":"fr","fr":"fr","spanish":"es","spa":"es","es":"es",
                          "german":"de","deu":"de","ger":"de","de":"de","turkish":"tr","tur":"tr","tr":"tr",
                          "portuguese":"pt","por":"pt","pt":"pt","italian":"it","ita":"it","it":"it"}
                    return m2.get(l, l or "und")
                _lnames = {"ar":"Arabic","en":"English","fr":"French","es":"Spanish",
                           "de":"German","tr":"Turkish","pt":"Portuguese","it":"Italian","und":"Unknown"}
                avail_codes = []
                for s in first_subs:
                    if isinstance(s, dict):
                        c = _norm(s.get("lang") or s.get("language"))
                        if c not in avail_codes:
                            avail_codes.append(c)
                pref_label = ", ".join(_lnames.get(l, l) for l in pref_langs[:3])
                lang_opts = [
                    {"name": f"🌐 All Preferred ({pref_label})", "value": "preferred"},
                    {"name": "🗂 All Available",                  "value": "all"},
                    {"name": f"📝 Primary only ({_lnames.get(preferred_sub_lang, preferred_sub_lang)})", "value": "primary"},
                ]
                for code in avail_codes:
                    lang_opts.append({"name": f"📝 {_lnames.get(code, code)}", "value": code})
                lang_opts.append({"name": "🚫 No subtitles", "value": "none"})
                lang_sel = selection_menu(
                    lang_opts, "Batch Download — Select Subtitle Languages",
                    show_details=False, formatter=lambda x: x["name"],
                )
                if lang_sel and lang_sel.get("action") == "select":
                    chosen = lang_sel["value"]["value"]
                    if chosen == "preferred":
                        include_all_subs = True
                    elif chosen == "all":
                        include_all_subs = True
                        pref_langs = avail_codes
                    elif chosen == "primary":
                        include_all_subs = False
                        pref_langs = [preferred_sub_lang]
                    elif chosen == "none":
                        preferred_sub_lang = "none"
                        pref_langs = []
                        include_all_subs = False
                    else:
                        preferred_sub_lang = chosen
                        pref_langs = [chosen]
                        include_all_subs = False
        except Exception:
            pass

        # ── Fetch sources for all selected movies in parallel ──
        console.print(
            f"[bold {PRIMARY}]Fetching sources for {len(selected_movies)} movies in parallel...[/bold {PRIMARY}]"
        )
        movie_data_map: dict = {}    # tmdb_id -> {files, subtitles}
        movie_sources:  dict = {}    # tmdb_id -> selected_source

        def _fetch_movie(m):
            mid = m.get("id")
            try:
                data = self.api.get_sources_enhanced(mid, "movie", min_sources=2)
                files = data.get("files", []) if isinstance(data, dict) else []
                subs  = data.get("subtitles", []) if isinstance(data, dict) else []
                if selected_quality != "auto" and files:
                    q_files = [f for f in files if f.get("quality") == selected_quality]
                    if q_files:
                        files = q_files
                    elif any(f.get("quality") for f in files):
                        # Tagged qualities exist but selected one is absent.
                        files = []
                src = select_working_source(files) if files else None
                return mid, files, subs, src
            except Exception:
                return mid, [], [], None

        from concurrent.futures import ThreadPoolExecutor as _TPE2, as_completed as _asc2
        with _TPE2(max_workers=min(4, len(selected_movies))) as _pool:
            _futs = {_pool.submit(_fetch_movie, m): m for m in selected_movies}
            for _fut in _asc2(_futs):
                mid, files, subs, src = _fut.result()
                movie_data_map[mid] = {"files": files, "subtitles": subs}
                movie_sources[mid]  = src
                status = f"[{SUCCESS}]✓[/]" if src else f"[red]✗[/]"
                title_label = next((m.get("title","?") for m in selected_movies if m.get("id") == mid), str(mid))
                console.print(f"  {status} {title_label}")

        # ── Queue each movie ──
        for m in selected_movies:
            mid   = m.get("id")
            title = m.get("title") or m.get("name", "Unknown")
            year  = (m.get("release_date") or "")[:4] or None
            info  = movie_data_map.get(mid, {})
            files = info.get("files", [])
            subs  = info.get("subtitles", [])
            src   = movie_sources.get(mid)

            if not files:
                console.print(f"[yellow]No sources for {title}. Skipping...[/yellow]")
                continue
            if not src:
                console.print(f"[red]No working source for {title}. Skipping...[/red]")
                continue

            if preferred_sub_lang == "none":
                subs = []

            meta = {"year": year, "tmdb_id": mid, "type": "movie", "runtime": m.get("runtime")}

            template = self.settings.get("filename_template", "{title}.{year}")
            filename = generate_filename(template, title, meta, src)
            if selected_quality not in ("auto", "adaptive"):
                base, ext = os.path.splitext(filename)
                filename = f"{base}.{selected_quality}{ext}"

            # Compare by URL to avoid identity-based mismatch
            _src_url = src.get("file", "")
            fallback_sources = [f for f in files if f.get("file", "") != _src_url]
            self.download_manager.add_task(
                src.get("file"),
                filename,
                title,
                subs,
                src.get("headers"),
                meta=meta,
                fallback_sources=fallback_sources,
                api_params={"tmdb_id": mid, "media_type": "movie"},
                preferred_sub_lang=preferred_sub_lang,
                include_all_subs=include_all_subs,
                preferred_sub_langs=pref_langs,
                fallback_sub_langs=self.settings.get("fallback_subtitle_langs", ["ar", "en"]),
                quality=selected_quality if selected_quality not in ("auto", "adaptive") else None,
            )

        console.print(f"\n[bold {SUCCESS}]Movie batch download queued![/bold {SUCCESS}]")
        time.sleep(2)

    def handle_sources(self, title, data, meta=None, autoplay=False):
        files = data.get("files", [])
        subtitles = data.get("subtitles", [])
        if not files:
            console.print("[red]No streams found.[/red]")
            time.sleep(1.5)
            return False

        # --- Helper: normalize language codes ---
        def _norm_lang(l):
            l = (l or "").strip().lower()
            if l in ["arabic", "ara", "ar"]:
                return "ar"
            if l in ["english", "eng", "en"]:
                return "en"
            if l in ["french", "fra", "fre", "fr"]:
                return "fr"
            if l in ["spanish", "spa", "es"]:
                return "es"
            if l in ["german", "deu", "ger", "de"]:
                return "de"
            if l in ["turkish", "tur", "tr"]:
                return "tr"
            if l in ["portuguese", "por", "pt"]:
                return "pt"
            if l in ["italian", "ita", "it"]:
                return "it"
            return l or "und"

        def _lang_label(code):
            names = {
                "ar": "Arabic", "en": "English", "fr": "French",
                "es": "Spanish", "de": "German", "tr": "Turkish",
                "pt": "Portuguese", "it": "Italian", "und": "Unknown",
            }
            return names.get(code, code)

        # --- Resume playback support ---
        start_time = 0
        playback_key = None
        if meta and meta.get("tmdb_id"):
            if meta.get("type") == "movie":
                playback_key = f"movie_{meta['tmdb_id']}"
            elif meta.get("type") == "tv":
                playback_key = (
                    f"tv_{meta['tmdb_id']}_s{meta['season']}_e{meta['episode']}"
                )

        if playback_key and playback_key in self.playback:
            info = self.playback[playback_key]
            pos = info.get("position", 0)
            dur = info.get("duration", 0)
            if not info.get("finished") and pos > 10 and (dur == 0 or pos < dur * 0.95):
                import math
                mins = math.floor(pos / 60)
                secs = int(pos % 60)
                res = selection_menu(
                    [f"Resume from {mins}:{secs:02d}", "Start from Beginning"],
                    "Resume Playback?",
                    show_details=False,
                    formatter=lambda x: x,
                )
                if res and res.get("action") == "select" and isinstance(res.get("value"), str) and res["value"].startswith("Resume"):
                    start_time = pos

        # --- Smart Local Playback ---
        local_file = None
        lib_path = self.settings.get("library_dir")
        if lib_path and os.path.exists(lib_path):
            from src.utils.library import scan_library
            lib_data = scan_library(lib_path)
            if meta and meta.get("type") == "movie":
                for m in lib_data.get("movies", []):
                    if m["title"].lower() == title.lower() or m["title"].lower() in title.lower():
                        local_file = m["path"]
                        break
            elif meta and meta.get("type") == "tv":
                show_name = title.split(" S")[0]
                if show_name in lib_data.get("tv", {}):
                    season_num = meta.get("season")
                    episode_num = meta.get("episode")
                    for ep in lib_data["tv"][show_name].get(season_num, []):
                        if ep["episode"] == episode_num:
                            local_file = ep["path"]
                            break

        # ── QUALITY SELECTION (once, before action) ──
        # Providers return HLS m3u8 adaptive streams — the resolution is embedded
        # inside the manifest, not as metadata on the file object.  We therefore
        # always offer the standard resolution options and let yt-dlp / mpv apply
        # the selection against the manifest at playback / download time.
        #
        # If a provider *does* supply quality metadata we fold those options in as
        # well, so any future provider upgrade is automatically surfaced in the UI.
        manifest_qualities = sort_manifest_qualities(files)

        selected_quality = "auto"
        if not autoplay:
            # Fixed standard resolution options (always available for HLS streams)
            std_options = [
                {"name": "✨ Best Available (Auto)", "value": "auto"},
                {"name": "🔄 Adaptive (match connection speed)", "value": "adaptive"},
                {"name": "📺 4K (2160p)", "value": "4k"},
                {"name": "📺 1080p", "value": "1080p"},
                {"name": "📺 720p", "value": "720p"},
                {"name": "📺 480p", "value": "480p"},
                {"name": "📺 360p", "value": "360p"},
            ]
            # Append any additional quality tags that providers explicitly reported
            for q in manifest_qualities:
                tag = q.lower().replace(" ", "")
                already_covered = any(tag in opt["value"] for opt in std_options)
                if not already_covered:
                    std_options.append({"name": f"📺 {q} (provider)", "value": q})

            q_sel = selection_menu(
                std_options,
                f"{title} - Select Quality",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if q_sel and q_sel["action"] == "select":
                selected_quality = q_sel["value"]["value"]

        # ── SUBTITLE SELECTION ──
        preferred_sub_lang = self.settings.get("preferred_subtitle", "ar")
        # Ordered multi-language list from settings (primary first)
        pref_langs = self.settings.get("preferred_subtitle_langs", [preferred_sub_lang])
        if not isinstance(pref_langs, list) or not pref_langs:
            pref_langs = [preferred_sub_lang]
        # Keep primary in sync as first entry
        if pref_langs[0] != preferred_sub_lang:
            pref_langs = [preferred_sub_lang] + [l for l in pref_langs if l != preferred_sub_lang]
        include_all_subs = len(pref_langs) > 1  # default: embed all preferred langs

        if subtitles:
            lang_codes = []
            for s in subtitles:
                if isinstance(s, dict) and s.get("url"):
                    code = _norm_lang(s.get("lang") or s.get("language"))
                    if code not in lang_codes:
                        lang_codes.append(code)

            if lang_codes:
                # Build a human-readable label for the "All Preferred" option
                _lnames = {
                    "ar": "Arabic", "en": "English", "fr": "French", "es": "Spanish",
                    "de": "German", "tr": "Turkish", "pt": "Portuguese", "it": "Italian",
                    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi",
                }
                def _ll(c): return _lnames.get(c, c)
                pref_label = ", ".join(_ll(l) for l in pref_langs[:3])
                if len(pref_langs) > 3:
                    pref_label += f" +{len(pref_langs)-3}"

                sub_options = [
                    {"name": f"🌐 All Preferred ({pref_label})", "value": "preferred"},
                    {"name": "🗂 All Available", "value": "all"},
                    {"name": f"📝 Primary only ({_lang_label(preferred_sub_lang)})", "value": "primary"},
                ]
                for code in lang_codes:
                    sub_options.append({"name": f"📝 {_lang_label(code)}", "value": code})
                sub_options.append({"name": "🚫 No subtitles", "value": "none"})

                sub_sel = selection_menu(
                    sub_options,
                    f"{title} - Select Subtitles",
                    show_details=False,
                    formatter=lambda x: x["name"],
                )
                if sub_sel and sub_sel.get("action") == "select":
                    choice = sub_sel["value"]["value"]
                    if choice == "preferred":
                        include_all_subs = True   # pass pref_langs to player/downloader
                    elif choice == "all":
                        include_all_subs = True
                        pref_langs = lang_codes    # use every available lang
                    elif choice == "primary":
                        include_all_subs = False
                        pref_langs = [preferred_sub_lang]
                    elif choice == "none":
                        subtitles = []
                        preferred_sub_lang = "none"
                        pref_langs = []
                        include_all_subs = False
                    else:
                        # User picked a specific language override
                        preferred_sub_lang = choice
                        pref_langs = [choice]
                        include_all_subs = False

        # ── Handle adaptive quality (speed test) ──
        if selected_quality == "adaptive":
            console.print(f"[bold {ACCENT}]Testing connection speed...[/bold {ACCENT}]")
            _adaptive_target = None
            # Probe neutral CDN endpoints — fast, publicly available, reliable
            _PROBE_URLS = [
                # Cloudflare 100 KB test file
                "https://speed.cloudflare.com/__down?bytes=102400",
                # HTTPBin 100 KB
                "https://httpbin.org/bytes/102400",
                # Fast.com netflix probe (bootstrap only — tiny, but latency signal)
                "https://api.fast.com/netflix/speedtest/v2?https=true&token=YXNkZmFzZGxmbnNkYWZoYXNkZmhrYWxm&urlCount=1",
            ]
            _speed_mbps = None
            for _test_url in _PROBE_URLS:
                try:
                    import time as _t
                    _req = Request(
                        _test_url,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    _start = _t.time()
                    with urlopen(_req, timeout=8) as _resp:
                        _chunk = _resp.read(131072)   # read up to 128 KB
                    _elapsed = _t.time() - _start
                    if _elapsed > 0 and len(_chunk) >= 8192:   # need at least 8 KB for a valid measurement
                        _speed_mbps = (len(_chunk) * 8) / (_elapsed * 1_000_000)
                        break                         # got a valid reading
                except Exception:
                    continue

            if _speed_mbps is not None:
                _adaptive_target = adaptive_quality_from_speed(_speed_mbps).replace("p", "")
                # Map to a matching quality string in the available options
                for q in manifest_qualities:
                    if _adaptive_target in q:
                        selected_quality = q
                        break
                if selected_quality == "adaptive":
                    selected_quality = _adaptive_target   # use the raw target as fallback format
                console.print(
                    f"[green]Connection: {_speed_mbps:.1f} Mbps → Quality: {selected_quality}[/green]"
                )
            else:
                selected_quality = "auto"
                console.print(f"[yellow]Speed test failed (all probes unreachable), using best available[/yellow]")
            time.sleep(1)


        # ── Filter files by selected quality ──
        filtered_files, _quality_mode = filter_sources_for_quality(files, selected_quality)
        if selected_quality not in ("auto", "adaptive"):
            if _quality_mode == "unavailable_tagged":
                console.print(
                    f"[yellow]Selected quality '{selected_quality}' is not available in provider-tagged sources.[/yellow]"
                )
                console.print(
                    f"[dim]Trying '{selected_quality}' via manifest enforcement anyway (tags can be incomplete).[/dim]"
                )
                filtered_files = files
            elif _quality_mode == "enforced_manifest":
                # Provider did not tag qualities; keep all files and enforce
                # quality via manifest selection in yt-dlp/mpv.
                console.print(
                    f"[dim]Provider did not expose quality tags; enforcing '{selected_quality}' via manifest selection.[/dim]"
                )

        while True:
            # ── ACTION SELECTION ──
            options = ["▶ Play", "⬇ Download"]
            if local_file:
                options.insert(0, "✨ Play Local (High Quality)")

            if autoplay and not local_file:
                act = {"action": "select", "value": "▶ Play"}
            elif autoplay and local_file:
                act = {"action": "select", "value": "✨ Play Local (High Quality)"}
            else:
                act = selection_menu(
                    options,
                    f"{title} - Choose Action",
                    show_details=False,
                    formatter=lambda x: x,
                )

            if not act or act["action"] in ["back", "quit"]:
                return False

            if act["value"] == "✨ Play Local (High Quality)":
                from src.utils.player import play_video
                psl = self.settings.get("preferred_subtitle", "ar")
                ppl = self.settings.get("preferred_player", "mpv")
                play_video(local_file, title, preferred_sub_lang=psl, player=ppl)
                return {"position": 0, "duration": 0, "finished": True}

            # ── Find working source with enhanced selection ──
            console.print(f"[bold {ACCENT}]Selecting source for: {title} ({selected_quality})...[/bold {ACCENT}]")
            
            # Get multiple working sources for fallback
            working_sources = select_multiple_working_sources(filtered_files, count=3)
            
            if not working_sources:
                # Try fetching fresh sources
                console.print(f"[yellow]No working sources, trying fresh fetch...[/yellow]")
                if meta and meta.get("tmdb_id"):
                    fresh_data = self.api.get_sources_api(
                        meta["tmdb_id"], 
                        meta.get("type", "movie"),
                        meta.get("season"),
                        meta.get("episode"),
                        force_refresh=True
                    )
                    if fresh_data and fresh_data.get("files"):
                        fresh_files = fresh_data["files"]
                        fresh_files, _fresh_mode = filter_sources_for_quality(fresh_files, selected_quality)
                        if _fresh_mode == "unavailable_tagged":
                            # Keep candidates and let manifest-level selection enforce quality.
                            fresh_files = fresh_data["files"]
                        filtered_files = fresh_files if fresh_files else filtered_files
                        working_sources = select_multiple_working_sources(fresh_files, count=3) if fresh_files else []
            
            if not working_sources:
                console.print(f"[bold red]No working source found for: {title}[/bold red]")
                time.sleep(2)
                return False
            
            selected = working_sources[0]
            fallback_sources = working_sources[1:] if len(working_sources) > 1 else []

            if act["value"] == "▶ Play":
                # Resolve subtitle args: when user chose "no subtitles", pass
                # empty values so the player doesn't try to fetch lang="none".
                _play_sub_lang = preferred_sub_lang if preferred_sub_lang != "none" else ""
                _play_subs = subtitles if preferred_sub_lang != "none" else []
                _play_pref_langs = pref_langs if preferred_sub_lang != "none" else []
                _play_include_all = include_all_subs if preferred_sub_lang != "none" else False

                _tried_urls = set()
                _auto_switch_count = 0
                _auto_switch_max = 3
                while True:   # quality/source-switch loop
                    _cur_url = selected.get("file", "")
                    if _cur_url:
                        _tried_urls.add(_cur_url)
                    stats = play_stream(
                        selected.get("file"),
                        title,
                        _play_subs,
                        selected.get("headers"),
                        meta,
                        start_time=start_time,
                        preferred_sub_lang=_play_sub_lang,
                        include_all_subs=_play_include_all,
                        preferred_langs=_play_pref_langs,
                        player=self.settings.get("preferred_player", "mpv"),
                        fallback_langs=self.settings.get('fallback_subtitle_langs', ['ar','en']),
                        quality=selected_quality if selected_quality not in ("auto", "adaptive") else None,
                    )

                    # If playback progressed but no video frames were detected,
                    # this source is likely audio-only/broken for the current player.
                    if isinstance(stats, dict) and stats.get("no_video"):
                        _auto_switch_count += 1
                        if _auto_switch_count > _auto_switch_max:
                            console.print(
                                "[red]Tried multiple fallback sources with no video. Returning to source menu.[/red]"
                            )
                            time.sleep(1.0)
                            return False

                        self.api.report_source_result(selected.get("provider"), False)
                        console.print("[yellow]Source has no video frames. Trying another source...[/yellow]")
                        time.sleep(0.8)

                        # Try already validated fallbacks first.
                        _next = None
                        for fs in fallback_sources:
                            _u = fs.get("file", "")
                            if _u and _u not in _tried_urls:
                                _next = fs
                                break

                        # If no fallback left, fetch fresh sources and pick an untried one.
                        if _next is None and meta and meta.get("tmdb_id"):
                            fresh_data = self.api.get_sources_api(
                                meta["tmdb_id"],
                                meta.get("type", "movie"),
                                meta.get("season"),
                                meta.get("episode"),
                                force_refresh=True,
                            )
                            _fresh_files = fresh_data.get("files", []) if isinstance(fresh_data, dict) else []
                            _fresh_files, _fresh_mode = filter_sources_for_quality(_fresh_files, selected_quality)
                            if _fresh_mode == "unavailable_tagged":
                                # Keep candidates and enforce selected quality at manifest/player layer.
                                _fresh_files = fresh_data.get("files", []) if isinstance(fresh_data, dict) else []
                            for fs in _fresh_files:
                                _u = fs.get("file", "")
                                if _u and _u not in _tried_urls:
                                    _next = fs
                                    break

                        if _next is not None:
                            selected = _next
                            start_time = 0
                            continue

                        console.print("[red]No alternate video-capable source found for this episode.[/red]")
                        return False

                    # Report provider result AFTER playback (not before)
                    if isinstance(stats, dict) and stats.get("duration", 0) > 0:
                        self.api.report_source_result(selected.get("provider"), True)
                    elif isinstance(stats, dict):
                        self.api.report_source_result(selected.get("provider"), False)

                    if isinstance(stats, dict) and playback_key:
                        self.playback[playback_key] = stats
                        save_json_data(PLAYBACK_FILE, self.playback)

                    # ── Post-play: offer Switch Quality / Replay / Done ──
                    pq_options = [
                        {"name": f"🔄 Switch Quality (current: {selected_quality})", "value": "switch"},
                        {"name": "▶  Replay at same quality",                        "value": "replay"},
                        {"name": "✅ Done — go back",                               "value": "done"},
                    ]
                    pq_sel = selection_menu(
                        pq_options,
                        f"{title} — Playback finished",
                        show_details=False,
                        formatter=lambda x: x["name"],
                    )
                    if not pq_sel or pq_sel.get("action") in ("back", "quit"):
                        break
                    pq_act = pq_sel.get("value", {}).get("value") if pq_sel.get("value") else None
                    if pq_act == "done" or pq_act is None:
                        break
                    if pq_act == "replay":
                        start_time = 0   # restart from beginning
                        continue
                    if pq_act == "switch":
                        std_options = [
                            {"name": "✨ Best Available (Auto)",          "value": "auto"},
                            {"name": "🔄 Adaptive (match connection speed)", "value": "adaptive"},
                            {"name": "📺 4K (2160p)",                    "value": "4k"},
                            {"name": "📺 1080p",                         "value": "1080p"},
                            {"name": "📺 720p",                          "value": "720p"},
                            {"name": "📺 480p",                          "value": "480p"},
                            {"name": "📺 360p",                          "value": "360p"},
                        ]
                        sq_sel = selection_menu(
                            std_options,
                            f"{title} — Switch Quality",
                            show_details=False,
                            formatter=lambda x: x["name"],
                        )
                        if sq_sel and sq_sel.get("action") == "select":
                            new_q = sq_sel["value"]["value"]
                            # Handle adaptive probe
                            if new_q == "adaptive":
                                console.print(f"[bold {ACCENT}]Testing connection speed...[/bold {ACCENT}]")
                                _PROBE_URLS2 = [
                                    "https://speed.cloudflare.com/__down?bytes=102400",
                                    "https://httpbin.org/bytes/102400",
                                ]
                                _spd = None
                                for _pu in _PROBE_URLS2:
                                    try:
                                        _rq = Request(_pu, headers={"User-Agent": "Mozilla/5.0"})
                                        _ts = time.time()
                                        with urlopen(_rq, timeout=8) as _rsp:
                                            _ck = _rsp.read(131072)
                                        _te = time.time() - _ts
                                        if _te > 0 and len(_ck) >= 8192:
                                            _spd = (len(_ck) * 8) / (_te * 1_000_000)
                                            break
                                    except Exception:
                                        continue
                                if _spd is not None:
                                    new_q = adaptive_quality_from_speed(_spd)
                                    console.print(f"[green]{_spd:.1f} Mbps → {new_q}[/green]")
                                else:
                                    new_q = "auto"
                                    console.print(f"[yellow]Speed test failed, using auto[/yellow]")
                                time.sleep(0.8)
                            selected_quality = new_q
                            # Refilter files for the new quality
                            filtered_files, _switch_mode = filter_sources_for_quality(files, selected_quality)
                            if _switch_mode == "unavailable_tagged":
                                filtered_files = files
                            working_sources = select_multiple_working_sources(filtered_files, count=3)
                            if working_sources:
                                selected = working_sources[0]
                                fallback_sources = working_sources[1:]
                            else:
                                console.print(f"[yellow]No sources at {selected_quality}, keeping current source.[/yellow]")
                                time.sleep(1)
                        start_time = 0
                        continue
                    break  # unknown action
                return stats or False

            elif act["value"] == "⬇ Download":
                template = self.settings.get("filename_template", "{title}.{year}")
                if meta and meta.get("type") == "tv":
                    template = self.settings.get(
                        "filename_template_tv", "{title}.S{season}E{episode}"
                    )

                filename = generate_filename(template, title, meta, selected)
                # Add quality tag to filename if a specific resolution was chosen
                if selected_quality not in ("auto", "adaptive"):
                    base, ext = os.path.splitext(filename)
                    filename = f"{base}.{selected_quality}{ext}"

                # Include api_params for source refresh during retries
                api_params = None
                if meta and meta.get("tmdb_id"):
                    api_params = {
                        "tmdb_id": meta.get("tmdb_id"),
                        "media_type": meta.get("type"),
                        "season": meta.get("season"),
                        "episode": meta.get("episode"),
                    }

                # Use pre-validated fallback sources, plus remaining unvalidated ones
                # Compare by URL to avoid identity-based mismatch
                _sel_url = selected.get("file", "")
                all_fallbacks = list(fallback_sources)  # Pre-validated working sources
                _fb_urls = {f.get("file", "") for f in all_fallbacks}
                _fb_urls.add(_sel_url)
                for f in filtered_files:
                    f_url = f.get("file", "")
                    if f_url and f_url not in _fb_urls:
                        all_fallbacks.append(f)
                        _fb_urls.add(f_url)

                self.download_manager.add_task(
                    selected.get("file"),
                    filename,
                    title,
                    subtitles,
                    selected.get("headers"),
                    meta=meta,
                    fallback_sources=all_fallbacks,
                    api_params=api_params,
                    preferred_sub_lang=preferred_sub_lang,
                    include_all_subs=include_all_subs,
                    preferred_sub_langs=pref_langs,
                    fallback_sub_langs=self.settings.get('fallback_subtitle_langs', ['ar','en']),
                    quality=selected_quality if selected_quality not in ("auto", "adaptive") else None,
                )
                return False

    def start_player(self, url, title):
        ppl = self.settings.get("preferred_player", "mpv").upper()
        print_header(title)
        console.print(
            f"1. ▶ Play with {ppl}\n2. ⬇ Download Video\n3. 🔗 Copy URL\n4. ⬅ Back"
        )
        choice = console.input(
            f"\n[bold {ACCENT}]Select action (1-4): [/bold {ACCENT}]"
        )

        if choice == "1":
            play_video(url, title, player=self.settings.get("preferred_player", "mpv"))
        elif choice == "2":
            template = self.settings.get("filename_template", "{title}.{year}")
            # generate_filename expects (template, title, meta, selected)
            filename = generate_filename(
                template,
                title,
                meta=None,
                selected={"provider": "direct", "quality": "auto"},
            )
            self.download_manager.add_task(url, filename, title, None, None, api_params=None)
        elif choice == "3":
            console.print(f"\n[bold]URL:[/bold] {url}")
            console.input("\nPress Enter to return...")
        else:
            return


    def handle_local_library(self):
        from src.utils.library import scan_library, format_size
        
        lib_path = self.settings.get("library_dir")
        if not os.path.exists(lib_path):
            console.print(f"[red]Library directory dose not exist: {lib_path}[/red]")
            time.sleep(2)
            return

        while True:
            clear()
            print_header("Local Library")
            console.print(f"[dim]Library Path: {lib_path}[/dim]\n")
            
            data = scan_library(lib_path)
            
            options = []
            if data["movies"]:
                options.append({"name": f"🎬 Movies ({len(data['movies'])})", "type": "movies_root"})
            if data["tv"]:
                options.append({"name": f"📺 TV Shows ({len(data['tv'])})", "type": "tv_root"})
            
            if not options:
                console.print("[yellow]Library is empty.[/yellow]")
                time.sleep(2)
                break
            
            sel = selection_menu(options, "Browse Offline Media", show_details=False, 
                                 formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back":
                break
                
            v = sel["value"]
            if v["type"] == "movies_root":
                self.handle_library_movies(data["movies"])
            elif v["type"] == "tv_root":
                self.handle_library_tv(data["tv"])

    def handle_library_movies(self, movies):
        from src.utils.library import format_size
        while True:
            sel = selection_menu(movies, "Local Movies", show_details=True, 
                                 formatter=lambda x: f"{x['title']} ({x.get('year', 'N/A')})")
            if not sel or sel["action"] == "back":
                break
            
            movie = sel["value"]
            if sel["action"] == "favorite":
                self.toggle_favorite(movie)
                continue
            if sel["action"] == "watch_later":
                self.toggle_watch_later(movie)
                continue
                
            while True:
                clear()
                from rich.text import Text as _T
                from rich.columns import Columns
                # Header
                hdr = _T()
                hdr.append("🎬  CINEMA CLI", style=f"bold {PRIMARY}")
                hdr.append("  │  ", style=f"dim {PRIMARY}")
                hdr.append(movie["title"], style=f"bold {ACCENT}")
                hdr.append("  │  ", style=f"dim {PRIMARY}")
                hdr.append(f"v{APP_VERSION}", style=f"dim {TEXT}")
                console.print(Panel(Align.center(hdr), border_style=PRIMARY, box=box.HEAVY, padding=(0, 2)))
                console.print("")
                # Details table
                dtbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), expand=False)
                dtbl.add_column("key",   style=f"bold {PRIMARY}",  no_wrap=True)
                dtbl.add_column("value", style=f"{TEXT}")
                dtbl.add_row("📁  File",       os.path.basename(movie["path"]))
                dtbl.add_row("📂  Path",       movie["path"])
                dtbl.add_row("💾  Size",       format_size(movie["size"]))
                dtbl.add_row("🖥  Resolution", movie.get("resolution") or "Unknown")
                subs = movie.get("subtitles", [])
                if subs:
                    sub_names = {"1":"Video","2":"Audio","3":"Subtitle"}
                    dtbl.add_row("💬  Subtitles", f"[{SUCCESS}]{len(subs)} track(s)[/{SUCCESS}]")
                    for s in subs:
                        dtbl.add_row("", f"[dim]  • {s}[/dim]")
                console.print(Panel(dtbl, border_style=f"dim {PRIMARY}", box=box.HEAVY, padding=(0, 1)))
                console.print("")
                # Action menu
                action_opts = [
                    {"name": "▶  Play",     "value": "play"},
                    {"name": "⬅  Back",     "value": "back"},
                ]
                act = selection_menu(action_opts, movie["title"], show_details=False, formatter=lambda x: x["name"])
                if not act or act["action"] in ("back", "quit") or act.get("value", {}).get("value") == "back":
                    break
                if act.get("value", {}).get("value") == "play":
                    play_video(movie["path"], movie["title"], player=self.settings.get("preferred_player", "mpv"))

    def handle_library_tv(self, tv_data):
        from src.utils.library import format_size
        while True:
            shows = [{"title": s, "seasons": d} for s, d in tv_data.items()]
            sel = selection_menu(shows, "Local TV Shows", show_details=False, formatter=lambda x: x["title"])
            if not sel or sel["action"] == "back":
                break
                
            show = sel["value"]
            while True:
                seasons = [{"num": sn, "eps": eps} for sn, eps in show["seasons"].items()]
                seasons.sort(key=lambda x: x["num"])
                s_sel = selection_menu(seasons, f"{show['title']} - Seasons", show_details=False, 
                                       formatter=lambda x: f"Season {x['num']} ({len(x['eps'])} Episodes)")
                if not s_sel or s_sel["action"] == "back":
                    break
                    
                season = s_sel["value"]
                while True:
                    e_sel = selection_menu(season["eps"], f"{show['title']} S{season['num']} Episodes", show_details=True,
                                           formatter=lambda x: f"E{x['episode']} - {x['filename']}")
                    if not e_sel or e_sel["action"] == "back":
                        break
                        
                    ep = e_sel["value"]
                    # If user chose 'favorite', we handle it (even though it's offline)
                    if e_sel["action"] == "favorite":
                        self.toggle_favorite(ep)
                        continue
                    if e_sel["action"] == "watch_later":
                        self.toggle_watch_later(ep)
                        continue

                    # For selecting, we go to details or play?
                    # Let's show details AND option to play
                    while True:
                        clear()
                        from rich.text import Text as _T2
                        ep_label = f"{show['title']} S{season['num']}E{ep['episode']}"
                        # Header
                        hdr2 = _T2()
                        hdr2.append("🎬  CINEMA CLI", style=f"bold {PRIMARY}")
                        hdr2.append("  │  ", style=f"dim {PRIMARY}")
                        hdr2.append(ep_label, style=f"bold {ACCENT}")
                        hdr2.append("  │  ", style=f"dim {PRIMARY}")
                        hdr2.append(f"v{APP_VERSION}", style=f"dim {TEXT}")
                        console.print(Panel(Align.center(hdr2), border_style=PRIMARY, box=box.HEAVY, padding=(0, 2)))
                        console.print("")
                        # Details table
                        etbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), expand=False)
                        etbl.add_column("key",   style=f"bold {PRIMARY}", no_wrap=True)
                        etbl.add_column("value", style=f"{TEXT}")
                        etbl.add_row("📁  File",       ep["filename"])
                        etbl.add_row("📂  Path",       ep["path"])
                        etbl.add_row("💾  Size",       format_size(ep["size"]))
                        etbl.add_row("🖥  Resolution", ep.get("resolution") or "Unknown")
                        subs = ep.get("subtitles", [])
                        if subs:
                            etbl.add_row("💬  Subtitles", f"[{SUCCESS}]{len(subs)} track(s)[/{SUCCESS}]")
                            for s in subs:
                                etbl.add_row("", f"[dim]  • {s}[/dim]")
                        console.print(Panel(etbl, border_style=f"dim {PRIMARY}", box=box.HEAVY, padding=(0, 1)))
                        console.print("")
                        # Action menu
                        ep_action_opts = [
                            {"name": "▶  Play",   "value": "play"},
                            {"name": "⬅  Back",   "value": "back"},
                        ]
                        ep_act = selection_menu(ep_action_opts, ep_label, show_details=False, formatter=lambda x: x["name"])
                        if not ep_act or ep_act["action"] in ("back", "quit") or ep_act.get("value", {}).get("value") == "back":
                            break
                        if ep_act.get("value", {}).get("value") == "play":
                            play_video(ep["path"], ep_label, player=self.settings.get("preferred_player", "mpv"))

    def handle_download_manager(self):
        import select
        from rich.rule import Rule
        from rich.text import Text

        selected_indices = set()

        def _status_icon(s):
            return {
                "downloading": "⬇",
                "pending":     "⏳",
                "muxing":      "🔄",
                "completed":   "✅",
                "error":       "❌",
            }.get(s, "?")

        def generate_queue_table():
            q = self.download_manager.get_queue()

            # ── Header ─────────────────────────────────────────────────────
            header = Text()
            header.append("🎬  CINEMA CLI", style=f"bold {PRIMARY}")
            header.append("  │  ", style=f"dim {PRIMARY}")
            header.append("Download Manager", style=f"bold {ACCENT}")
            header.append("  │  ", style=f"dim {PRIMARY}")
            header.append(f"v{APP_VERSION}", style=f"dim {TEXT}")

            hdr_panel = Panel(
                Align.center(header),
                border_style=PRIMARY,
                box=box.HEAVY,
                padding=(0, 2),
            )

            # ── Table ───────────────────────────────────────────────────────
            table = Table(
                expand=True,
                border_style=f"dim {PRIMARY}",
                box=box.SIMPLE_HEAD,
                header_style=f"bold {PRIMARY}",
                padding=(0, 1),
            )
            table.add_column("",          width=3,  justify="center")   # checkbox
            table.add_column("#",         width=4,  justify="center", style=f"dim {TEXT}")
            table.add_column("Title",     no_wrap=True, max_width=34, style=f"bold {TEXT}")
            table.add_column("Progress",  width=28)
            table.add_column("Size",      justify="center", width=18)
            table.add_column("Speed",     justify="right",  width=12, style=ACCENT)
            table.add_column("Status",    justify="center", width=16)

            for i, task in enumerate(q):
                s         = task["status"]
                is_active = s == "downloading"
                is_muxing = s == "muxing"
                is_done   = s == "completed"
                is_error  = s == "error"
                is_pending = s == "pending"

                # ── Checkbox ──
                chk = f"[bold {SUCCESS}]✓[/]" if i in selected_indices else f"[dim {TEXT}]○[/]"

                # ── Status badge ──
                _status_colors = {
                    "downloading": f"bold {PRIMARY}",
                    "muxing":      f"bold {ACCENT}",
                    "completed":   SUCCESS,
                    "error":       "bold red",
                    "pending":     f"dim {TEXT}",
                }
                _status_labels = {
                    "downloading": "⬇  ACTIVE",
                    "muxing":      "🔄 MUXING",
                    "completed":   "✅ DONE",
                    "error":       "❌ ERROR",
                }
                if is_pending:
                    pending_q = [t for t in q if t["status"] == "pending"]
                    _pos = next((j for j, t in enumerate(pending_q) if t["id"] == task["id"]), 0)
                    _slabel = f"⏳ #{_pos + 1}"
                elif is_error and "Validation" in task.get("error_log", ""):
                    _slabel = "❌ INVALID"
                else:
                    _slabel = _status_labels.get(s, s.upper())
                _scol = _status_colors.get(s, f"dim {TEXT}")
                status_cell = f"[{_scol}]{_slabel}[/{_scol}]"

                # ── Progress bar ──
                p         = task.get("progress", 0)
                bar_width = 20
                filled    = int((p / 100) * bar_width)
                bar_color = (
                    SUCCESS  if is_done   else
                    ACCENT   if is_muxing else
                    PRIMARY  if is_active else
                    f"dim {TEXT}"
                )
                bar = f"[{bar_color}]" + "━" * filled + "[/][dim]" + "─" * (bar_width - filled) + "[/]"
                progress_display = f"{bar} [{WARNING}]{p:5.1f}%[/{WARNING}]"

                # ── Size column ──
                dl_bytes    = task.get("_bytes_downloaded", 0)
                total_bytes = task.get("_bytes_total", 0)
                dl_human    = task.get("downloaded", "")
                total_human = task.get("total_size", "")

                if is_active or is_muxing:
                    if total_bytes > 0 and dl_bytes > 0:
                        downloaded_display = (
                            f"[{ACCENT}]{self.download_manager._bytes_to_human(dl_bytes)}[/{ACCENT}]"
                            f" / [{TEXT}]{self.download_manager._bytes_to_human(total_bytes)}[/{TEXT}]"
                        )
                    elif dl_human and dl_human not in ("0 B", "---", "0B"):
                        if total_human and total_human not in ("Unknown", "---", "0B"):
                            downloaded_display = f"[{ACCENT}]{dl_human}[/{ACCENT}] / [{TEXT}]{total_human}[/{TEXT}]"
                        else:
                            downloaded_display = f"[{ACCENT}]{dl_human}[/{ACCENT}]"
                    else:
                        downloaded_display = f"[dim]{'starting…' if is_active else 'processing…'}[/dim]"
                elif is_done:
                    size_str = (
                        self.download_manager._bytes_to_human(total_bytes) if total_bytes > 0
                        else (total_human if total_human not in ("Unknown", "---") else "")
                    )
                    downloaded_display = f"[{SUCCESS}]{size_str or 'Complete'}[/{SUCCESS}]"
                elif is_error:
                    downloaded_display = f"[bold red]Failed[/bold red]"
                else:
                    downloaded_display = f"[dim {TEXT}]waiting…[/dim {TEXT}]"

                # ── Speed ──
                if is_active:
                    speed_val = task.get("speed", "---")
                    if speed_val and speed_val not in ("---", "0 B/s", "0B/s"):
                        speed = f"[{ACCENT}]{speed_val}[/{ACCENT}]"
                    else:
                        speed = f"[dim {TEXT}]connecting[/dim {TEXT}]"
                elif is_muxing:
                    speed = f"[{ACCENT}]muxing[/{ACCENT}]"
                elif is_done:
                    speed = f"[{SUCCESS}]done[/{SUCCESS}]"
                elif is_error:
                    speed = "[bold red]---[/bold red]"
                else:
                    speed = f"[dim {TEXT}]---[/dim {TEXT}]"

                table.add_row(
                    chk, f"#{i+1}", task["title"],
                    progress_display, downloaded_display, speed, status_cell,
                )

            # ── Help bar ──────────────────────────────────────────────────
            n_sel = len(selected_indices)
            if n_sel:
                sel_hint = f"[bold {SUCCESS}]{n_sel} selected[/]  "
            else:
                sel_hint = ""
            help_text = (
                f"  {sel_hint}"
                f"[dim {TEXT}]1-9 Toggle   A All   N None   "
                f"Enter Actions   R Retry errors   C Clear done   Q Back[/dim {TEXT}]  "
            )

            queue_panel = Panel(
                table,
                border_style=PRIMARY,
                box=box.HEAVY,
                subtitle=help_text,
                subtitle_align="center",
                padding=(0, 1),
            )

            return Group(hdr_panel, "", queue_panel)

        def show_actions_for_selected():
            """Show themed action menu for selected items."""
            nonlocal selected_indices
            q     = self.download_manager.get_queue()
            items = [q[i] for i in sorted(selected_indices) if i < len(q)]
            if not items:
                return

            action_opts = [
                {"name": f"🗑  Remove {len(items)} item(s)",  "value": "remove"},
                {"name": f"🔄 Retry {len(items)} item(s)",   "value": "retry"},
                {"name": "⬅  Cancel",                        "value": "cancel"},
            ]
            sel = selection_menu(
                action_opts,
                f"Actions for {len(items)} selected download(s)",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if not sel or sel.get("action") != "select":
                return

            choice = sel["value"]["value"]
            if choice == "remove":
                for item in items:
                    self.download_manager.remove_task(item["id"])
                selected_indices.clear()
            elif choice == "retry":
                retried = 0
                for item in items:
                    if item["status"] in ("error", "completed", "pending"):
                        self.download_manager.retry_task(item["id"])
                        retried += 1
                selected_indices.clear()

        clear()
        with Live(
            generate_queue_table(),
            refresh_per_second=2,
            console=console,
            screen=True,
        ) as live:
            while True:
                live.update(generate_queue_table())

                key = None
                if sys.platform == "win32":
                    import msvcrt
                    if msvcrt.kbhit():
                        raw_key = msvcrt.getch()
                        if raw_key in [b'\x00', b'\xe0']:
                            msvcrt.getch()
                        elif raw_key == b'\r':
                            key = 'enter'
                        else:
                            try:
                                key = raw_key.decode('utf-8').lower()
                            except Exception:
                                pass
                else:
                    if select.select([sys.stdin], [], [], 0)[0]:
                        key = sys.stdin.read(1).lower()
                        if key == '\n':
                            key = 'enter'

                if key:
                    q = self.download_manager.get_queue()
                    if key in ('q', '\x1b'):
                        break
                    elif key == 'enter' and selected_indices:
                        live.stop()
                        show_actions_for_selected()
                        clear()
                        live.start()
                    elif key == 'c':
                        self.download_manager.clear_completed()
                        selected_indices.clear()
                    elif key == 'r':
                        for t in q:
                            if t["status"] == "error":
                                self.download_manager.retry_task(t["id"])
                    elif key == 'a':
                        selected_indices = set(range(len(q)))
                    elif key == 'n':
                        selected_indices.clear()
                    elif key.isdigit() and key != '0':
                        idx = int(key) - 1
                        if 0 <= idx < len(q):
                            if idx in selected_indices:
                                selected_indices.discard(idx)
                            else:
                                selected_indices.add(idx)

                time.sleep(0.1)
        clear()


if __name__ == "__main__":
    # ── First-run setup wizard ────────────────────────────────────────────────
    # Must run before --version/--help parsing so new users aren't confused
    # by an empty terminal when they forget dependencies.
    from src.utils.first_run import is_first_run, run_wizard
    _is_setup_flag = "--setup" in sys.argv
    if _is_setup_flag or is_first_run():
        run_wizard(force=_is_setup_flag)
        if _is_setup_flag:
            sys.exit(0)

    # ── Handle --version / --help before spinning up the TUI ─────────────────
    _argv = sys.argv[1:]
    if _argv and _argv[0] in ("--version", "-V", "-v"):
        from src.config import APP_VERSION
        print(f"Cinema CLI v{APP_VERSION}")
        sys.exit(0)
    if _argv and _argv[0] in ("--help", "-h"):
        print(
            f"Cinema CLI — a terminal-based movie & TV streaming client\n"
            f"\n"
            f"Usage:\n"
            f"  python main.py [OPTIONS]\n"
            f"\n"
            f"Options:\n"
            f"  --version, -V     Print version and exit\n"
            f"  --help,    -h     Show this help message and exit\n"
            f"  --setup           Re-run the first-run setup wizard\n"
            f"  --debug-source    Print source pipeline + quality trace for one title/episode\n"
            f"                    Required: --tmdb-id <id> --type <movie|tv>\n"
            f"                    TV only:  --season <n> --episode <n>\n"
            f"                    Optional: --quality <auto|1080p|720p|...>\n"
            f"  --debug-subtitle  Print subtitle decision trace for one title/episode\n"
            f"                    Required: --tmdb-id <id> --type <movie|tv>\n"
            f"                    TV only:  --season <n> --episode <n>\n"
            f"                    Optional: --include-all\n"
            f"\n"
            f"Keyboard shortcuts (inside the TUI):\n"
            f"  ↑ ↓ / j k        Navigate lists\n"
            f"  Enter             Select / Confirm\n"
            f"  F                 Toggle favourite\n"
            f"  W                 Toggle Watch Later\n"
            f"  D                 Batch download (search / browse results)\n"
            f"  B / Esc           Go back\n"
            f"  Q                 Quit\n"
        )
        sys.exit(0)

    if "--debug-source" in _argv:
        sys.exit(run_debug_source_command(_argv))

    if "--debug-subtitle" in _argv:
        sys.exit(run_debug_subtitle_command(_argv))

    # Ensure backend is started and healthy before instantiating the CLI.
    # Prefer user settings backend URL, then env/default config.
    _boot_settings = load_json_data(SETTINGS_FILE) or {}
    _backend_url = _boot_settings.get("backend") or os.getenv("BACKEND_URL") or BACKEND_URL
    start_local_backend(_backend_url, timeout=30)

    cli = CinemaCLI()
    try:
        show_splash()
        startup_health_check()
        cli.main_menu()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
