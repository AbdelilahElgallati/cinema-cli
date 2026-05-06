import argparse
import atexit
import json
import logging
import math
import os
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _asc
from concurrent.futures import ThreadPoolExecutor as _TPE2, as_completed as _asc2
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from datetime import datetime

if sys.platform == "win32":
    import msvcrt
else:
    msvcrt = None


USER_AGENT = "cinema-cli/1.0"
MOVIE_FILENAME_TEMPLATE = "{title}.{year}"
TV_FILENAME_TEMPLATE = "{title}.S{season}E{episode}"
NEXT_PAGE_TITLE = "➡️ Next Page"
PREV_PAGE_TITLE = "⬅️ Previous Page"
ACTION_PLAY = "▶ Play"
ACTION_PLAY_LOCAL = "✨ Play Local (High Quality)"
CLI_BRAND_TITLE = "🎬  CINEMA CLI"
CLI_SEPARATOR = "  │  "
DEBUG_MODE = False


def _emit_unified_log(prefix: str, message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = str(message or "").rstrip()
    if not text:
        return
    print(f"[{timestamp}] [{prefix}] {text}", flush=True)


def _start_prefixed_stream_reader(stream, prefix: str):
    if stream is None:
        return

    def _reader():
        try:
            for raw in iter(stream.readline, ""):
                line = str(raw).rstrip("\r\n")
                if line:
                    _emit_unified_log(prefix, line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()


def _attach_backend_debug_streams(proc):
    if not DEBUG_MODE or proc is None:
        return
    _start_prefixed_stream_reader(proc.stdout, "SCRAPER")
    _start_prefixed_stream_reader(proc.stderr, "SCRAPER")


def _enable_debug_mode():
    global DEBUG_MODE
    if DEBUG_MODE:
        return
    DEBUG_MODE = True
    os.environ["CINEMA_DEBUG"] = "1"

    class _UnifiedUIHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
            except Exception:
                msg = record.getMessage()
            _emit_unified_log("UI", msg)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)
    unified_handler = _UnifiedUIHandler()
    unified_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root_logger.addHandler(unified_handler)

    original_log_event = app_logger.log_event

    def _debug_log_event(category: str, message: str, level: str = "INFO", correlation_id: str = ""):
        original_log_event(category, message, level=level, correlation_id=correlation_id)
        corr = f" corr={correlation_id}" if correlation_id else ""
        _emit_unified_log("UI", f"{level} [{category}]{corr} {message}")

    app_logger.log_event = _debug_log_event
    _emit_unified_log("UI", "Debug mode enabled")


def start_local_backend(backend_url: str, timeout: int = 30):  # NOSONAR
    """Start local backend if backend_url points at localhost and wait until it's healthy.

    Returns subprocess.Process or None.
    """

    def _probe_urls(base_url: str):
        base = str(base_url or "").rstrip("/")
        return [f"{base}/", f"{base}/health", f"{base}/proxy/status"]

    def _is_running(url: str) -> bool:
        for probe_url in _probe_urls(url):
            try:
                req = Request(
                    probe_url, 
                    headers={
                        "User-Agent": USER_AGENT,
                        "Connection": "close",
                        "Accept": "*/*"
                    }
                )
                with urlopen(req, timeout=2) as resp:
                    if 200 <= int(getattr(resp, "status", 0)) < 400:
                        return True
            except Exception as e:
                app_logger.debug(f"Suppressed error in _is_running: {e}", exc_info=True)
                continue
        return False

    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    try:
        host = backend_url.split("://")[-1].split(":")[0]
    except Exception:
        host = ""

    if host not in ("localhost", "127.0.0.1", ""):
        return None

    # If already running, nothing to do
    if _is_running(backend_url):
        return None

    def _backend_launch_env(port: int):
        env = os.environ.copy()
        env["PORT"] = str(port)
        return env

    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "backend")
    )
    log_path = os.path.join(backend_dir, "backend.log")

    show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"

    # Ensure log directory exists and open log file for append
    logfile = None
    if not DEBUG_MODE:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        logfile = open(log_path, "a+", encoding="utf-8")
        stdout = logfile
        stderr = logfile
        popen_extra = {}
    else:
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
        popen_extra = {"text": True, "bufsize": 1}
    parsed_backend_url = urlparse(backend_url or "http://localhost:3010")
    launch_host = parsed_backend_url.hostname or "localhost"
    launch_scheme = parsed_backend_url.scheme or "http"
    launch_port = _find_free_port()
    launch_url = f"{launch_scheme}://{launch_host}:{launch_port}"
    launch_env = _backend_launch_env(launch_port)

    npm_command = ["npm.cmd", "start"] if os.name == "nt" else ["npm", "start"]
    proc = None
    try:
        proc = subprocess.Popen(
            npm_command,
            cwd=backend_dir,
            stdout=stdout,
            stderr=stderr,
            env=launch_env,
            **popen_extra,
        )
        _attach_backend_debug_streams(proc)
    except Exception:
        try:
            proc = subprocess.Popen(
                ["node", "index.js"],
                cwd=backend_dir,
                stdout=stdout,
                stderr=stderr,
                env=launch_env,
                **popen_extra,
            )
            _attach_backend_debug_streams(proc)
        except Exception:
            if logfile:
                logfile.close()
            return None

    # Optionally tail live logs to console while waiting
    stop_tailer = None
    tail_thread = None
    if show_logs and not DEBUG_MODE:
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
                            except Exception as e:
                                app_logger.debug(f"Suppressed error in _tail_file print: {e}", exc_info=True)
                        else:
                            time.sleep(0.2)
            except Exception as e:
                app_logger.debug(f"Suppressed error in _tail_file: {e}", exc_info=True)
                return

        tail_thread = threading.Thread(
            target=_tail_file, args=(log_path, stop_tailer), daemon=True
        )
        tail_thread.start()

    # Wait until healthy or timeout while showing a friendly status
    with console.status("Starting backend, please wait...", spinner="dots"):
        waited = 0.0
        interval = 0.5
        while waited < 60:
            if _is_running(launch_url):
                os.environ["BACKEND_URL"] = launch_url
                if stop_tailer:
                    stop_tailer.set()
                if logfile:
                    logfile.flush()
                    logfile.close()
                return proc
            time.sleep(interval)
            waited += interval

    # Timeout reached; stop tailer if running and return proc (logs available in backend.log)
    if stop_tailer:
        stop_tailer.set()
    if logfile:
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
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

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
    MAX_CONCURRENT_SOURCE_FETCHES,
    THEME_NAMES,
    THEMES,
    _active_theme_name as _atn,
    apply_theme as _apply_theme,
    PROVIDER_SCORES_FILE,
)
from src.controllers.app_controller import AppController
from src.state.app_state import AppState
import src.config as _cfg_mod
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
    build_quality_menu_options,
    filter_sources_for_quality,
    sort_manifest_qualities,
)
from src.utils.storage import load_json_data, save_json_data
from src.utils.system_tools import is_tool_available
from src.utils.utils import generate_filename, normalize_lang
from src.utils.validator import select_working_source, select_multiple_working_sources, verify_source
from src.utils.library import scan_library, clear_library_cache, format_size, get_media_details
from src.utils.first_run import _load_env_key, _write_env, is_first_run, run_wizard
from src.utils import app_logger


def _arg_value(argv, flag, default=None):
    if flag not in argv:
        return default
    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        return default
    return argv[idx + 1]


def _subtitle_trace(subtitles, preferred_langs, include_all):  # NOSONAR
    items = []
    for s in subtitles or []:
        if not isinstance(s, dict):
            continue
        url = s.get("url")
        if not isinstance(url, str) or not url:
            continue
        # Use normalized language code
        raw_lang = str(s.get("lang") or s.get("language") or "und")
        lang = normalize_lang(raw_lang)
        items.append({"lang": lang, "url": url})

    selected = []
    seen_lang = set()
    seen_url = set()
    # Normalize the "wants" list
    wants = []
    for x in (preferred_langs or []):
        code = normalize_lang(str(x))
        if code != "und" and code not in wants:
            wants.append(code)

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


def run_debug_source_command(argv):  # NOSONAR
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
    app_logger.log_event("debug", f"debug-source done tmdb={tmdb_id} type={media_type}", correlation_id=corr or "")
    return 0


def run_debug_subtitle_command(argv):  # NOSONAR
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
    app_logger.log_event("debug", f"debug-subtitle done tmdb={tmdb_id} type={media_type}", correlation_id=corr or "")
    return 0


def run_smoke_command(argv):  # NOSONAR
    """Run a tiny end-to-end smoke check for source selection flows.

    It validates both stream and download source selection logic for:
    - one movie
    - one TV episode
    """
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    # Keep smoke runs short even when backend/network is unavailable.
    api.timeout = (3, 5)

    movie_id = _arg_value(argv, "--movie-id")
    tv_id = _arg_value(argv, "--tv-id")
    season_arg = _arg_value(argv, "--season")
    episode_arg = _arg_value(argv, "--episode")
    timeout_arg = _arg_value(argv, "--timeout")
    skip_validation = "--skip-validation" in argv

    try:
        per_source_timeout = int(timeout_arg) if timeout_arg else 2
    except ValueError:
        console.print("[red]--timeout must be an integer (seconds)[/red]")
        return 2
    per_source_timeout = max(1, min(per_source_timeout, 8))

    # Stable defaults avoid extra TMDB metadata calls in smoke runs.
    movie_fallback_candidates = [
        ("550", "Fight Club"),
        ("603", "The Matrix"),
        ("155", "The Dark Knight"),
        ("27205", "Inception"),
    ]
    if not movie_id:
        movie_id = movie_fallback_candidates[0][0]

    tv_fallback_candidates = [
        ("1396", 1, 1, "Breaking Bad"),
        ("1399", 1, 1, "Game of Thrones"),
        ("100088", 1, 1, "The Last of Us"),
        ("108978", 1, 1, "Reacher"),
    ]

    if not tv_id:
        tv_id = tv_fallback_candidates[0][0]
        auto_season, auto_episode = tv_fallback_candidates[0][1], tv_fallback_candidates[0][2]
    else:
        auto_season, auto_episode = None, None

    try:
        season_default = int(auto_season) if auto_season else 1
        episode_default = int(auto_episode) if auto_episode else 1
        season = int(season_arg) if season_arg else season_default
        episode = int(episode_arg) if episode_arg else episode_default
    except ValueError:
        console.print("[red]--season and --episode must be integers[/red]")
        return 2

    if not movie_id or not tv_id:
        console.print("[red]Smoke test could not resolve default movie/tv IDs. Pass --movie-id and --tv-id manually.[/red]")
        return 2

    def _pick_working_tv_target():
        """Pick first TV target that returns any source rows.

        Used only when --tv-id is not explicitly provided.
        """
        for cid, cseason, cepisode, cname in tv_fallback_candidates:
            probe = api._fetch_sources_with_retry(
                cid,
                "tv",
                cseason,
                cepisode,
                max_retries=0,
                force_refresh=False,
            )
            files = probe.get("files", []) if isinstance(probe, dict) else []
            if files:
                return cid, cseason, cepisode, cname
        return tv_fallback_candidates[0]

    def _pick_working_movie_target():
        """Pick first movie target that returns any source rows.

        Used only when --movie-id is not explicitly provided.
        """
        for cid, cname in movie_fallback_candidates:
            probe = api._fetch_sources_with_retry(
                cid,
                "movie",
                None,
                None,
                max_retries=0,
                force_refresh=False,
            )
            files = probe.get("files", []) if isinstance(probe, dict) else []
            if files:
                return cid, cname
        return movie_fallback_candidates[0]

    def _check_case(label, tmdb_id, media_type, season_num=None, episode_num=None):
        data = api._fetch_sources_with_retry(
            tmdb_id,
            media_type,
            season_num,
            episode_num,
            max_retries=0,
            force_refresh=False,
        )
        files = data.get("files", []) if isinstance(data, dict) else []
        quality_groups = data.get("quality_groups", {}) if isinstance(data, dict) else {}

        if not files:
            return {
                "label": label,
                "stream_ok": False,
                "download_ok": False,
                "details": "No sources returned",
            }

        available_qualities = sort_manifest_qualities(files)
        requested_quality = available_qualities[0] if available_qualities else "auto"
        filtered_files, mode = filter_sources_for_quality(files, requested_quality)
        candidate_files = filtered_files if filtered_files else files

        stream_candidates = select_multiple_working_sources(
            candidate_files,
            count=2,
            skip_validation=skip_validation,
            max_parallel=2,
            timeout_per_source=per_source_timeout,
        )
        stream_ok = bool(stream_candidates)

        download_candidate = select_working_source(
            candidate_files,
            skip_validation=skip_validation,
            max_parallel=2,
            timeout_per_source=per_source_timeout,
        )
        download_ok = bool(download_candidate)

        details = (
            f"sources={len(files)} | quality={requested_quality} | mode={mode} | "
            f"groups={len(quality_groups or {})} | skip_validation={skip_validation}"
        )
        return {
            "label": label,
            "stream_ok": stream_ok,
            "download_ok": download_ok,
            "details": details,
        }

    if not _arg_value(argv, "--movie-id"):
        selected_movie_id, selected_movie_name = _pick_working_movie_target()
        movie_id = selected_movie_id
        console.print(f"[dim]Smoke movie target selected: {selected_movie_name} (tmdb={movie_id})[/dim]")

    if not _arg_value(argv, "--tv-id"):
        selected_tv_id, selected_season, selected_episode, selected_name = _pick_working_tv_target()
        tv_id = selected_tv_id
        if not season_arg:
            season = int(selected_season)
        if not episode_arg:
            episode = int(selected_episode)
        console.print(f"[dim]Smoke TV target selected: {selected_name} (tmdb={tv_id} s={season} e={episode})[/dim]")

    console.print("[bold {0}]Running smoke test for stream/download selection...[/bold {0}]".format(ACCENT))
    movie_result = _check_case("Movie", str(movie_id), "movie")
    tv_result = _check_case("TV", str(tv_id), "tv", season, episode)

    table = Table(title="Selection Flow Smoke Test", box=box.SIMPLE)
    table.add_column("Case")
    table.add_column("Stream")
    table.add_column("Download")
    table.add_column("Details")

    def _mark(ok):
        return f"[{SUCCESS}]PASS[/{SUCCESS}]" if ok else "[red]FAIL[/red]"

    table.add_row(
        f"Movie (tmdb={movie_id})",
        _mark(movie_result["stream_ok"]),
        _mark(movie_result["download_ok"]),
        movie_result["details"],
    )
    table.add_row(
        f"TV (tmdb={tv_id} s={season} e={episode})",
        _mark(tv_result["stream_ok"]),
        _mark(tv_result["download_ok"]),
        tv_result["details"],
    )
    console.print(table)

    all_ok = (
        movie_result["stream_ok"]
        and movie_result["download_ok"]
        and tv_result["stream_ok"]
        and tv_result["download_ok"]
    )
    return 0 if all_ok else 1


def build_subtitle_menu_options(  # NOSONAR
    preferred_sub_lang,
    pref_langs,
    available_codes=None,
    fallback_langs=None,
):
    available_codes = available_codes or []
    fallback_langs = fallback_langs or []
    lang_names = {
        "ar": "Arabic", "en": "English", "fr": "French", "es": "Spanish",
        "de": "German", "tr": "Turkish", "pt": "Portuguese", "it": "Italian",
        "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi",
        "und": "Unknown",
    }

    def label(code):
        return lang_names.get(code, code)

    def normalize_codes(codes):
        normalized = []
        seen = set()
        for code in codes:
            code_str = str(code or "").strip().lower()
            if not code_str or code_str == "none" or code_str in seen:
                continue
            seen.add(code_str)
            normalized.append(code_str)
        return normalized

    pref_langs = normalize_codes(pref_langs)
    fallback_langs = normalize_codes(fallback_langs)
    available_codes = normalize_codes(available_codes)
    configured_codes = normalize_codes(pref_langs + fallback_langs)
    # Union of both sets to ensure we don't miss configured languages if provider returns some
    all_codes = sorted(list(set(available_codes) | set(configured_codes)))

    pref_count = len(pref_langs)
    pref_label = ", ".join(label(lang) for lang in pref_langs[:3])
    if pref_count > 3:
        pref_label += f" +{pref_count-3}"
    if not pref_label:
        pref_label = label(preferred_sub_lang)

    options = [
        {"name": f"🌐 All Preferred ({pref_count} langs: {pref_label})", "value": "preferred"},
        {"name": f"📝 Primary only ({label(preferred_sub_lang)})", "value": "primary"},
    ]

    if all_codes:
        all_label = "🗂 All Available" if available_codes else "🗂 All Available (from settings)"
        options.insert(1, {"name": all_label, "value": "all"})
        for code in all_codes:
            options.append({"name": f"📝 {label(code)}", "value": code})

    options.append({"name": "🚫 No subtitles", "value": "none"})
    return options, all_codes


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
                    app_logger.log_event("app", f"yt-dlp updated: {out}")
        except Exception as e:
            app_logger.debug(f"Suppressed error in _auto_update_ytdlp: {e}", exc_info=True)

    threading.Thread(target=_update, daemon=True).start()


def startup_health_check():
    """
    Run a quick dependency + connectivity check and print a one-line summary.
    Never raises — missing items are flagged with ✗ but the app still starts.
    """
    checks = []

    # ── Required tools ────────────────────────────────────────────────────────
    for tool in ("mpv", "ffmpeg", "yt-dlp"):
        found = is_tool_available(tool)
        checks.append((tool, found, not found))   # (label, ok, critical)

    # aria2c is optional
    checks.append(("aria2c", is_tool_available("aria2c"), False))

    # ── Backend / TMDB reachability (quick, 3 s timeout) ─────────────────────
    _tmdb_ok = False
    try:
        _api_key = TMDB_API_KEY or ""
        if _api_key:
            _r = Request(
                f"https://api.themoviedb.org/3/configuration?api_key={_api_key}",
                headers={"User-Agent": "cinema-cli/hc"},
            )
            with urlopen(_r, timeout=3) as _resp:
                _tmdb_ok = _resp.status == 200
    except Exception:
        pass
    checks.append(("TMDB API", _tmdb_ok, not _tmdb_ok))

    # ── Backend reachability (quick, 3 s timeout) ───────────────────────────
    _backend_ok = False
    try:
        _backend_url = os.getenv("BACKEND_URL") or BACKEND_URL
        if _backend_url:
            _r = Request(
                f"{_backend_url.rstrip('/')}/health",
                headers={"User-Agent": "cinema-cli/hc"},
            )
            with urlopen(_r, timeout=3) as _resp:
                _backend_ok = _resp.status == 200
    except Exception:
        pass
    checks.append(("Backend", _backend_ok, not _backend_ok))

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
            "[dim]  Run  python main.py --setup  or see README for install instructions.[/dim]\n"
        )
        time.sleep(2)


class CinemaCLI:
    def __init__(self):
        self.settings = load_json_data(SETTINGS_FILE, default={}, expected_type=dict) or {}

        changed = False
        defaults = {
            "backend": os.getenv("BACKEND_URL") or BACKEND_URL,
            "filename_template": MOVIE_FILENAME_TEMPLATE,
            "filename_template_tv": TV_FILENAME_TEMPLATE,
            "library_dir": os.path.expanduser("~/Downloads/CinemaCLI"),
            "preferred_subtitle": "ar",
            "preferred_player": "mpv",
            "download_speed_limit": 0,
        }
        for key, default_value in defaults.items():
            if key not in self.settings:
                self.settings[key] = default_value
                changed = True

        langs = self.settings.get("preferred_subtitle_langs")
        if not isinstance(langs, list) or not langs:
            self.settings["preferred_subtitle_langs"] = [self.settings.get("preferred_subtitle", "ar")]
            changed = True

        if changed:
            save_json_data(SETTINGS_FILE, self.settings)

        # Attempt to ensure a local backend is running (only for localhost URLs)
        self._backend_proc = None
        self._maybe_start_backend(self.settings.get("backend", BACKEND_URL))

        self.api = APIClient(self.settings)

        # IMPORTANT: ensure lists/dicts, not None
        self.history     = load_json_data(HISTORY_FILE)     or []
        self.favorites   = load_json_data(FAVORITES_FILE)   or []
        self.playback    = load_json_data(PLAYBACK_FILE)     or {}
        self.watch_later = load_json_data(WATCH_LATER_FILE) or []
        self.episode_positions = {}

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

    def _check_backend_online(self) -> bool:  # NOSONAR
        """Quick connectivity probe — returns True if both TMDB and the backend respond."""
        # Check TMDB (always needed, even without a local backend)
        try:
            tmdb_req = Request(
                "https://api.themoviedb.org/3/configuration?api_key=" +
                (self.settings.get("tmdb_key") or TMDB_API_KEY),
                headers={"User-Agent": USER_AGENT}
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
                "[bold red]\n  ⚠  No internet / backend connection detected.[/bold red]\n"
                "[dim]  Browse mode limited to: History, Watch Later, Local Library,\n"
                "  Download Manager and Settings are still available.[/dim]\n"
            )
            time.sleep(2)

    def _is_backend_running(self, url: str) -> bool:
        probes = [
            url.rstrip("/") + "/",
            url.rstrip("/") + "/health",
            url.rstrip("/") + "/proxy/status",
        ]
        for probe_url in probes:
            try:
                req = Request(probe_url, headers={"User-Agent": USER_AGENT})
                with urlopen(req, timeout=1) as resp:
                    if 200 <= int(getattr(resp, "status", 0)) < 400:
                        return True
            except (URLError, ValueError):
                continue
        return False

    def _maybe_start_backend(self, backend_url: str) -> None:  # NOSONAR
        # Only auto-start when pointing to localhost and not already running
        try:
            host = backend_url.split("://")[-1].split(":")[0]
        except Exception:
            host = ""

        if host not in ("localhost", "127.0.0.1", ""):
            return

        if self._is_backend_running(backend_url):
            return

        def _find_free_port() -> int:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                sock.listen(1)
                return int(sock.getsockname()[1])

        def _backend_launch_env(port: int):
            env = os.environ.copy()
            env["PORT"] = str(port)
            return env

        backend_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend")
        )
        # Try to start via npm start; fallback to node index.js if npm not available
        try:
            # Allow showing backend logs when requested via env var
            show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"
            if DEBUG_MODE:
                stdout = subprocess.PIPE
                stderr = subprocess.PIPE
                popen_extra = {"text": True, "bufsize": 1}
            else:
                stdout = None if show_logs else subprocess.DEVNULL
                stderr = None if show_logs else subprocess.DEVNULL
                popen_extra = {}
            parsed_backend_url = urlparse(backend_url or "http://localhost:3010")
            launch_host = parsed_backend_url.hostname or "localhost"
            launch_scheme = parsed_backend_url.scheme or "http"
            launch_port = _find_free_port()
            launch_url = f"{launch_scheme}://{launch_host}:{launch_port}"
            launch_env = _backend_launch_env(launch_port)
            npm_command = ["npm.cmd", "start"] if os.name == "nt" else ["npm", "start"]

            self._backend_proc = subprocess.Popen(
                npm_command,
                cwd=backend_dir,
                stdout=stdout,
                stderr=stderr,
                env=launch_env,
                **popen_extra,
            )
            _attach_backend_debug_streams(self._backend_proc)
            # Wait briefly for server to come up
            for _ in range(30):
                if self._is_backend_running(launch_url):
                    self.settings["backend"] = launch_url
                    os.environ["BACKEND_URL"] = launch_url
                    try:
                        save_json_data(SETTINGS_FILE, self.settings)
                    except Exception:
                        pass
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
                    **popen_extra,
                )
                _attach_backend_debug_streams(self._backend_proc)
                for _ in range(10):
                    if self._is_backend_running(launch_url):
                        self.settings["backend"] = launch_url
                        os.environ["BACKEND_URL"] = launch_url
                        try:
                            save_json_data(SETTINGS_FILE, self.settings)
                        except Exception:
                            pass
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

    def print_dashboard(self):
        """Render a stunning, professional dashboard with stats and system status."""
        clear()
        
        # 1. System Status Panel
        status_color = SUCCESS if self.backend_online else "bold red"
        status_label = "● ONLINE" if self.backend_online else "○ OFFLINE"
        
        # 2. Stats Columns
        stats_table = Table.grid(expand=True)
        stats_table.add_column(justify="left", ratio=1)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="right", ratio=1)
        
        stats_table.add_row(
            Text.from_markup(f"[bold {PRIMARY}]⭐ Favorites:[/] {len(self.favorites)}"),
            Text.from_markup(f"[bold {PRIMARY}]🕒 History:[/] {len(self.history)}"),
            Text.from_markup(f"[bold {PRIMARY}]⏱️  Later:[/] {len(self.watch_later)}")
        )

        # 3. Active Downloads Info
        active_tasks = [t for t in self.download_manager.get_queue() if t["status"] in ("downloading", "muxing", "pending")]
        dl_text = f"[bold {ACCENT}]📥 Active Downloads:[/] {len(active_tasks)}" if active_tasks else f"[dim]No active downloads[/dim]"

        # Build Top Banner
        header = Text.from_markup(f"🎬 [bold {PRIMARY}]CINEMA CLI[/] [dim {TEXT}]v{APP_VERSION}[/]")
        
        top_panel = Panel(
            Group(
                Align.center(header),
                Rule(style=f"dim {PRIMARY}"),
                stats_table,
                Rule(style="dim"),
                Columns([
                    Text.from_markup(f"[{status_color}]{status_label}[/] [dim]│[/] {self.settings.get('backend', BACKEND_URL)}"),
                    Align.right(Text.from_markup(dl_text))
                ], expand=True)
            ),
            border_style=PRIMARY,
            box=box.ROUNDED,
            padding=(0, 2)
        )
        
        console.print(top_panel)
        console.print()

    def main_menu(self):
        # Use the active theme's highlight colour
        try:
            _hl_fg = THEMES[_atn].get("highlight_fg", "#FFFFFF")
        except Exception as e:
            app_logger.debug(f"Suppressed error in main_menu theme color: {e}", exc_info=True)
            _hl_fg = "#FFFFFF"

        if not hasattr(self, "backend_online_last_check"):
            self.backend_online_last_check = 0

        while True:
            current_time = time.time()
            if current_time - self.backend_online_last_check > 5:
                self.backend_online = self._check_backend_online()
                self.backend_online_last_check = current_time
            self.print_dashboard()
            
            options = [
                {"name": "🔍 Search Movies & TV",   "action": self.handle_search, "icon": "🔍"},
                {"name": "🌍 Discovery",            "action": self.handle_discovery, "icon": "🌍"},
                {"name": "📈 Trending This Week",   "action": self.handle_trending, "icon": "📈"},
                {"name": "🔥 Popular Content",      "action": self.handle_popular, "icon": "🔥"},
                {"name": "🎭 Browse by Genre",      "action": self.handle_genres, "icon": "🎭"},
                {"name": "⭐ My Favorites",         "action": self.handle_favorites, "icon": "⭐"},
                {"name": "🕒 Watch History",        "action": self.handle_history, "icon": "🕒"},
                {"name": "⏱️  Watch Later",          "action": self.handle_watch_later, "icon": "⏱️ "},
                {"name": "📁 Local Library",        "action": self.handle_local_library, "icon": "📁"},
                {"name": "📥 Download Manager",     "action": self.handle_download_manager, "icon": "📥"},
                {"name": "⚙️  Settings",            "action": self.handle_settings, "icon": "⚙️ "},
                {"name": "❌ Exit",                 "action": sys.exit, "icon": "❌"},
            ]

            selected_index = 0
            kb = KeyBindings()

            @kb.add("k")
            @kb.add("up")
            def _(event, opts=options):  # NOSONAR
                nonlocal selected_index
                selected_index = (selected_index - 1) % len(opts)

            @kb.add("j")
            @kb.add("down")
            def _(event, opts=options):  # NOSONAR
                nonlocal selected_index
                selected_index = (selected_index + 1) % len(opts)

            @kb.add("enter")
            def _(event, opts=options):  # NOSONAR
                event.app.exit(result=opts[selected_index]["action"])  # NOSONAR

            @kb.add("q")
            def _(event):
                event.app.exit(result=sys.exit)

            def get_menu_text(opts=options):  # NOSONAR
                res = []
                res.append(("class:header", "  ╭── Select an option ──╮\n"))
                res.append(("class:border", "  " + "─" * 36 + "\n"))
                for i, opt in enumerate(opts):
                    name = opt['name']
                    if i == selected_index:  # NOSONAR
                        res.append(("class:selected", f"  ▶  {name}\n"))
                    else:
                        res.append(("class:item", f"     {name}\n"))
                res.append(("class:border", "  " + "─" * 36 + "\n"))
                res.append(("class:help", "  ↑↓ Navigate   Enter Select   Q Quit  "))
                return res

            style = Style.from_dict(
                {
                    "header":   f"bold {PRIMARY}",
                    "border":   f"dim {PRIMARY}",
                    "selected": f"bg:{PRIMARY} fg:{_hl_fg} bold",
                    "item":     f"{TEXT}",
                    "help":     f"italic dim {TEXT}",
                }
            )

            app = Application(
                layout=PTLayout(Window(FormattedTextControl(get_menu_text))),
                key_bindings=kb,
                style=style,
                full_screen=False, # Persistent dashboard above
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

    def browse_new_movies(self):  # NOSONAR
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
                    {"id": "next_page", "title": NEXT_PAGE_TITLE, "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": PREV_PAGE_TITLE, "special": True}
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

    def browse_new_episodes(self):  # NOSONAR
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
                    {"id": "next_page", "title": NEXT_PAGE_TITLE, "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": PREV_PAGE_TITLE, "special": True}
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

    def browse_trending_tv_today(self):  # NOSONAR
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
                    {"id": "next_page", "title": NEXT_PAGE_TITLE, "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": PREV_PAGE_TITLE, "special": True}
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

    def browse_movie_of_the_day(self):  # NOSONAR
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
                    {"id": "next_page", "title": NEXT_PAGE_TITLE, "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": PREV_PAGE_TITLE, "special": True}
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

    def handle_search(self):  # NOSONAR
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
        if not results:
            console.print(f"[yellow]No movie or TV results found for '{query}'.[/yellow]")
            time.sleep(1.5)
            return

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

    def handle_popular(self):  # NOSONAR
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


    def handle_genres(self):  # NOSONAR
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

    def handle_settings(self):  # NOSONAR
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
        tmdb_key = self.settings.get('tmdb_key', 'Using Default')
        if tmdb_key == 'Using Default':
            tmdb_display = tmdb_key
        else:
            key_len = len(tmdb_key)
            if key_len <= 2:
                tmdb_display = '*' * key_len
            elif key_len <= 8:
                tmdb_display = tmdb_key[0] + '*' * (key_len - 2) + tmdb_key[-1]
            else:
                tmdb_display = f"{tmdb_key[:4]}...{tmdb_key[-4:]}"

        console.print(
            f"[bold {TEXT}]2. TMDB API Key:[/bold {TEXT}] {tmdb_display}"
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
            "[dim]   (Streaming: all tracks loaded in this order; Downloads: all embedded)[/dim]"
        )

        fb_langs = self.settings.get('fallback_subtitle_langs', ['ar', 'en'])
        if not isinstance(fb_langs, list):
            fb_langs = ['ar', 'en']
        console.print(
            f"[bold {TEXT}]9. OpenSubtitles Fallback Languages:[/bold {TEXT}] {_fmt_langs(fb_langs)}"
        )
        console.print(
            "[dim]   (Used only when no subtitles come from the source provider)[/dim]"
        )

        current_theme = self.settings.get("theme", "cinema")
        
        osub_key = _load_env_key("OPENSUBTITLES_API_KEY")
        if not osub_key:
            osub_display = "Not Set"
        elif len(osub_key) > 8:
            osub_display = f"{osub_key[:4]}...{osub_key[-4:]}"
        else:
            osub_display = "Set"
        console.print(
            f"[bold {TEXT}]10. OpenSubtitles API Key:[/bold {TEXT}] {osub_display}"
        )

        console.print(
            f"[bold {TEXT}]11. Theme:[/bold {TEXT}] {current_theme.capitalize()}"
        )
        console.print(
            "[dim]    (Applies immediately — choose from cinema, blue, purple, green, gold, teal, rose, sunset, mint)[/dim]"
        )

        speed_limit = self.settings.get("download_speed_limit", 0)
        speed_display = f"{speed_limit} MB/s" if speed_limit else "Unlimited"
        console.print(
            f"[bold {TEXT}]12. Download Speed Limit:[/bold {TEXT}] {speed_display}"
        )
        console.print(
            "[dim]    (Caps yt-dlp bandwidth; 0 = no limit. Example: 5 for 5 MB/s)[/dim]"
        )
        console.print(
            f"[bold {TEXT}]13. Export Settings to file[/bold {TEXT}]"
        )
        console.print(
            f"[bold {TEXT}]14. Import Settings from file[/bold {TEXT}]"
        )

        choice = console.input(
            f"\n[bold {ACCENT}]Select setting to change (1-14) or Enter to back: [/bold {ACCENT}]"
        )

        if choice == "1":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new backend URL: [/bold {ACCENT}]"
            )
            new_val = new_val.strip()
            if new_val:
                old_val = self.settings.get("backend", "")
                self.settings["backend"] = new_val
                if old_val != new_val:
                    console.print(f"[dim]Restarting local backend on {new_val} (if applicable)...[/dim]")
                    self._cleanup_backend()
                    self._maybe_start_backend(new_val)
                    console.print("[green]Backend URL updated. Service restarted on new port.[/green]")
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
                "[dim]Enter languages in priority order, comma-separated.[/dim]\n"
                "[dim]Example: ar,en,fr  — Arabic first, then English, then French[/dim]\n"
                "[dim]Codes: ar en fr es de tr pt it zh ja ko hi[/dim]\n"
                f"[dim]Current: {_fmt_langs(self.settings.get('preferred_subtitle_langs', [pref_sub]))}[/dim]"
            )
            new_val = console.input(
                f"[bold {ACCENT}]Enter preferred subtitle languages (comma-separated): [/bold {ACCENT}]"
            )
            # Normalize and deduplicate
            raw_langs = [x.strip() for x in (new_val or '').split(',') if x.strip()]
            langs = []
            seen = set()
            for l in raw_langs:
                code = normalize_lang(l)
                if code != "und" and code not in seen:
                    langs.append(code)
                    seen.add(code)

            if langs:
                self.settings['preferred_subtitle_langs'] = langs
                self.settings['preferred_subtitle'] = langs[0]   # keep primary in sync
                console.print(f"[green]Preferred subtitle languages set to: {_fmt_langs(langs)}[/green]")
        elif choice == "9":
            console.print(
                "[dim]Used only when the source has no subtitles at all. Example: ar,en[/dim]"
            )
            new_val = console.input(
                f"[bold {ACCENT}]Enter fallback subtitle languages (comma-separated): [/bold {ACCENT}]"
            )
            # Normalize and deduplicate
            raw_langs = [x.strip() for x in (new_val or '').split(',') if x.strip()]
            langs = []
            seen = set()
            for l in raw_langs:
                code = normalize_lang(l)
                if code != "und" and code not in seen:
                    langs.append(code)
                    seen.add(code)

            if langs:
                self.settings['fallback_subtitle_langs'] = langs
                console.print(f"[green]Fallback languages set to: {_fmt_langs(langs)}[/green]")
        elif choice == "10":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new OpenSubtitles API Key (or empty to clear): [/bold {ACCENT}]"
            ).strip()
            _write_env({"OPENSUBTITLES_API_KEY": new_val})
            os.environ["OPENSUBTITLES_API_KEY"] = new_val
            console.print("[green]OpenSubtitles API Key updated![/green]")
        elif choice == "11":
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
                _apply_theme(chosen_theme)
                # Refresh this module's own colour globals immediately
                for _cname in ("PRIMARY", "SECONDARY", "ACCENT", "SUCCESS", "WARNING", "TEXT"):
                    globals()[_cname] = getattr(_cfg_mod, _cname)
                console.print(
                    f"[green]Theme set to '{chosen_theme}' — applied immediately.[/green]"
                )
        elif choice == "12":
            console.print(
                "[dim]Enter maximum download speed in MB/s (e.g. 5 for 5 MB/s). Enter 0 for unlimited.[/dim]"
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
                console.print("[yellow]Invalid value — keeping current setting.[/yellow]")
        elif choice == "13":
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
        elif choice == "14":
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
                    if "provider_scores" in bundle and isinstance(bundle["provider_scores"], dict):
                        with open(PROVIDER_SCORES_FILE, "w", encoding="utf-8") as _f:
                            json.dump(bundle["provider_scores"], _f, indent=2)
                        console.print("[green]Provider health scores restored.[/green]")
                except Exception:
                    pass
                console.print(
                    f"[green]Settings imported from: {src_path}[/green]\n"
                    "[dim]Re-open Settings to see any theme change take effect.[/dim]"
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
        clear()
        progress = Progress(
            TextColumn(f"[bold yellow]{next_ep_name} starts in {{task.fields[secs]}} seconds...[/bold yellow]"),
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

    def show_episodes(self, media, season):  # NOSONAR
        s_num = season["season_number"]
        print_header(f"{media.get('name')} - Season {s_num}")
        data = self.api.get_tmdb_data(f"tv/{media['id']}/season/{s_num}")
        if not data:
            return
        episodes = data.get("episodes", [])
        season_key = f"{media.get('id')}_s{s_num}"

        def fmt_ep(x):
            name = x.get("name", "Unknown")
            ep_no = x.get("episode_number")
            ep_label = f"E{int(ep_no):02d}" if isinstance(ep_no, int) else "E?"
            air = x.get("air_date") or "N/A"
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else "N/A"
            rating = x.get("vote_average", 0)
            return f"{ep_label} - {name} ({year}) | ⭐ {rating:.1f} | TV"

        selected_idx = self.episode_positions.get(season_key, 0)
        if selected_idx < 0 or selected_idx >= len(episodes):
            selected_idx = 0
        while True:
            sel = selection_menu(
                episodes,
                f"Season {s_num} Episodes",
                show_details=True,
                formatter=fmt_ep,
                default_index=selected_idx,
                allow_jump=True,
            )
            if not sel or sel["action"] == "back":
                break

            if sel["action"] == "jump":
                jump_val = console.input(
                    f"[bold {ACCENT}]Jump to episode number: [/bold {ACCENT}]"
                ).strip()
                try:
                    target_ep_num = int(jump_val)
                except ValueError:
                    console.print("[yellow]Invalid number.[/yellow]")
                    time.sleep(0.8)
                    continue
                found_idx = next(
                    (i for i, e in enumerate(episodes) if e.get("episode_number") == target_ep_num),
                    None,
                )
                if found_idx is None:
                    console.print(f"[yellow]Episode {target_ep_num} not found in this season.[/yellow]")
                    time.sleep(0.8)
                    continue
                selected_idx = found_idx
                self.episode_positions[season_key] = selected_idx
                continue

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
                self.episode_positions[season_key] = selected_idx
                next_step_auto = False

                while True:
                    title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"
                    
                    # Fetch sources ONCE here
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

                    # Pass the 'data' directly to handle_sources
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
                                self.episode_positions[season_key] = selected_idx
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
                            self.episode_positions[season_key] = selected_idx
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
                            self.episode_positions[season_key] = selected_idx
                            ep = episodes[selected_idx]
                        else:
                            console.print("[yellow]No previous episode.[/yellow]")
                            time.sleep(1)
                            break
                    elif choice == "Replay":
                        continue
                    elif choice == "Back to List":
                        break

    def handle_batch_download(self, media, season, episodes):  # NOSONAR
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
        preferred_sub_lang = normalize_lang(preferred_sub_lang)
        pref_langs = self.settings.get("preferred_subtitle_langs", [preferred_sub_lang])
        fallback_langs = self.settings.get("fallback_subtitle_langs", ["ar", "en"])
        if not isinstance(pref_langs, list) or not pref_langs:
            pref_langs = [preferred_sub_lang]
        # Ensure all codes are normalized
        pref_langs = [normalize_lang(l) for l in pref_langs if l and l != "none"]
        fallback_langs = [normalize_lang(l) for l in fallback_langs if l and l != "none"]
        
        if pref_langs and pref_langs[0] != preferred_sub_lang:
            if preferred_sub_lang != "none":
                pref_langs = [preferred_sub_lang] + [l for l in pref_langs if l != preferred_sub_lang]
        elif not pref_langs and preferred_sub_lang != "none":
             pref_langs = [preferred_sub_lang]
        include_all_subs = len(pref_langs) > 1

        try:
            # Try fetching sources for the first few episodes to get quality/subtitle options
            first_data = None
            for ep_to_try in selected_episodes[:3]:
                console.print(f"[dim]  Gathering available options from E{ep_to_try['episode_number']}...[/dim]")
                first_data = self.api.get_sources_enhanced(media["id"], "tv", s_num, ep_to_try["episode_number"], min_sources=2, quiet=True)
                if first_data and first_data.get("files"):
                    break
            
            if not first_data:
                first_data = {"files": [], "subtitles": []}

            first_files = first_data.get("files", []) if isinstance(first_data, dict) else []
            first_subs = first_data.get("subtitles", []) if isinstance(first_data, dict) else []

            # Quality selection: show only qualities actually exposed by this episode.
            std_q_options = build_quality_menu_options(first_files)

            q_sel = selection_menu(
                std_q_options,
                f"Batch Download - Select Quality (S{s_num})",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if q_sel and q_sel.get("action") == "select":
                selected_quality = q_sel["value"]["value"]

            avail_codes = []
            for s in first_subs:
                if isinstance(s, dict):
                    code = normalize_lang(s.get("lang") or s.get("language"))
                    if code not in avail_codes:
                        avail_codes.append(code)

            lang_opts, all_lang_codes = build_subtitle_menu_options(
                preferred_sub_lang,
                pref_langs,
                avail_codes,
                fallback_langs,
            )
            lang_sel = multi_selection_menu(
                lang_opts,
                "Batch Download - Select Subtitle Languages",
                formatter=lambda x: x["name"],
            )
            if lang_sel:
                values = [x["value"] for x in lang_sel]
                if "none" in values:
                    preferred_sub_lang = "none"
                    pref_langs = []
                    include_all_subs = False
                elif "preferred" in values:
                    include_all_subs = True
                    # pref_langs remains as loaded from settings
                elif "all" in values:
                    include_all_subs = True
                    pref_langs = all_lang_codes or pref_langs
                elif "primary" in values:
                    include_all_subs = False
                    pref_langs = [preferred_sub_lang]
                else:
                    pref_langs = values
                    preferred_sub_lang = pref_langs[0]
                    include_all_subs = len(pref_langs) > 1
            else:
                include_all_subs = len(pref_langs) > 1
        except Exception as _pref_err:
            # Log preference selection error instead of silently swallowing it
            try:
                app_logger.log_event("download", f"Batch preference selection error: {_pref_err}", level="WARNING")
            except Exception:
                pass
            console.print("[yellow]Preference selection encountered an issue; using defaults.[/yellow]")

        # ── Parallel source fetch for all selected episodes ────────────────
        console.print(
            f"[bold {PRIMARY}]Fetching sources for {len(selected_episodes)} episodes in parallel...[/bold {PRIMARY}]"
        )
        ep_data_map: dict = {}   # episode_number -> {files, subtitles}
        ep_sources:  dict = {}   # episode_number -> selected_source

        _fetch_semaphore = threading.Semaphore(MAX_CONCURRENT_SOURCE_FETCHES)

        def _fetch_ep(ep):
            with _fetch_semaphore:
                ep_num = ep.get("episode_number")
                try:
                    data = self.api.get_sources_enhanced(
                        media["id"], "tv", s_num, ep_num, min_sources=2, quiet=True
                    )
                    files  = data.get("files", []) if isinstance(data, dict) else []
                    subs   = data.get("subtitles", []) if isinstance(data, dict) else []
                    mode = "auto"
                    # Quality filter
                    if selected_quality != "auto" and files:
                        files, mode = filter_sources_for_quality(files, selected_quality)
                    src = select_working_source(files) if files else None
                    return ep_num, files, subs, src, mode
                except Exception as e:
                    try:
                        app_logger.log_event("api", f"Parallel fetch error for E{ep_num}: {e}", level="ERROR")
                    except:
                        pass
                    return ep_num, [], [], None, "auto"

        with _TPE(max_workers=min(MAX_CONCURRENT_SOURCE_FETCHES * 2, len(selected_episodes))) as _pool:
            _futs = {_pool.submit(_fetch_ep, ep): ep for ep in selected_episodes}
            for _fut in _asc(_futs):
                ep_num, files, subs, src, mode = _fut.result()
                ep_data_map[ep_num] = {"files": files, "subtitles": subs, "mode": mode}
                ep_sources[ep_num]  = src
                status = f"[{SUCCESS}]✓[/]" if src else "[red]✗[/]"
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

            template = self.settings.get("filename_template_tv", TV_FILENAME_TEMPLATE)
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
            _mode = ep_info.get("mode", "auto")
            
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
                quality=selected_quality if (_mode == "enforced_manifest" and selected_quality not in ("auto", "adaptive")) else None,
            )

        console.print(f"\n[bold {SUCCESS}]Batch download queued![/bold {SUCCESS}]")
        time.sleep(2)

    def handle_batch_movie_download(self, movies):  # NOSONAR
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
        fallback_langs = self.settings.get("fallback_subtitle_langs", ["ar", "en"])
        if not isinstance(pref_langs, list) or not pref_langs:
            pref_langs = [preferred_sub_lang]
        if not isinstance(fallback_langs, list):
            fallback_langs = ["ar", "en"]
        include_all_subs = len(pref_langs) > 1

        try:
            # Try fetching sources for the first few movies to get quality/subtitle options
            first_data = None
            for m_to_try in selected_movies[:3]:
                console.print(f"[dim]  Gathering available options from {m_to_try.get('title','')}...[/dim]")
                first_data = self.api.get_sources_enhanced(m_to_try.get("id"), "movie", min_sources=2, quiet=True)
                if first_data and first_data.get("files"):
                    break
            
            if not first_data:
                first_data = {"files": [], "subtitles": []}

            first_files = first_data.get("files", []) if isinstance(first_data, dict) else []
            first_subs  = first_data.get("subtitles", []) if isinstance(first_data, dict) else []

            std_q_options = build_quality_menu_options(first_files)
            q_sel = selection_menu(
                std_q_options, "Batch Download — Select Quality",
                show_details=False, formatter=lambda x: x["name"],
            )
            if q_sel and q_sel.get("action") == "select":
                selected_quality = q_sel["value"]["value"]

            # Subtitle language selection (same logic as TV batch)
            avail_codes = []
            for s in first_subs:
                if isinstance(s, dict):
                    c = normalize_lang(s.get("lang") or s.get("language"))
                    if c not in avail_codes:
                        avail_codes.append(c)
            lang_opts, all_lang_codes = build_subtitle_menu_options(
                preferred_sub_lang,
                pref_langs,
                avail_codes,
                fallback_langs,
            )
            lang_sel = multi_selection_menu(
                lang_opts, "Batch Download — Select Subtitle Languages",
                formatter=lambda x: x["name"],
            )
            if lang_sel:
                values = [x["value"] for x in lang_sel]
                if "none" in values:
                    preferred_sub_lang = "none"
                    pref_langs = []
                    include_all_subs = False
                elif "preferred" in values:
                    include_all_subs = True
                elif "all" in values:
                    include_all_subs = True
                    pref_langs = all_lang_codes or pref_langs
                elif "primary" in values:
                    include_all_subs = False
                    pref_langs = [preferred_sub_lang]
                else:
                    pref_langs = values
                    preferred_sub_lang = pref_langs[0]
                    include_all_subs = len(pref_langs) > 1
            else:
                include_all_subs = len(pref_langs) > 1
        except Exception:
            console.print("[yellow]Batch preference selection encountered an issue; using defaults.[/yellow]")

        # ── Fetch sources for all selected movies in parallel ──
        console.print(
            f"[bold {PRIMARY}]Fetching sources for {len(selected_movies)} movies in parallel...[/bold {PRIMARY}]"
        )
        movie_data_map: dict = {}    # tmdb_id -> {files, subtitles}
        movie_sources:  dict = {}    # tmdb_id -> selected_source

        _fetch_semaphore = threading.Semaphore(MAX_CONCURRENT_SOURCE_FETCHES)

        def _fetch_movie(m):
            with _fetch_semaphore:
                mid = m.get("id")
                try:
                    data = self.api.get_sources_enhanced(mid, "movie", min_sources=2)
                    files = data.get("files", []) if isinstance(data, dict) else []
                    subs  = data.get("subtitles", []) if isinstance(data, dict) else []
                    mode = "auto"
                    if selected_quality != "auto" and files:
                        files, mode = filter_sources_for_quality(files, selected_quality)
                    src = select_working_source(files) if files else None
                    return mid, files, subs, src, mode
                except Exception:
                    return mid, [], [], None, "auto"

        with _TPE2(max_workers=min(MAX_CONCURRENT_SOURCE_FETCHES * 2, len(selected_movies))) as _pool:
            _futs = {_pool.submit(_fetch_movie, m): m for m in selected_movies}
            for _fut in _asc2(_futs):
                mid, files, subs, src, mode = _fut.result()
                movie_data_map[mid] = {"files": files, "subtitles": subs, "mode": mode}
                movie_sources[mid]  = src
                status = f"[{SUCCESS}]✓[/]" if src else "[red]✗[/]"
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

            template = self.settings.get("filename_template", MOVIE_FILENAME_TEMPLATE)
            filename = generate_filename(template, title, meta, src)
            if selected_quality not in ("auto", "adaptive"):
                base, ext = os.path.splitext(filename)
                filename = f"{base}.{selected_quality}{ext}"

            # Compare by URL to avoid identity-based mismatch
            _src_url = src.get("file", "")
            fallback_sources = [f for f in files if f.get("file", "") != _src_url]
            _mode = movie_data_map.get(mid, {}).get("mode", "auto")
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
                quality=selected_quality if (_mode == "enforced_manifest" and selected_quality not in ("auto", "adaptive")) else None,
            )

        console.print(f"\n[bold {SUCCESS}]Movie batch download queued![/bold {SUCCESS}]")
        time.sleep(2)

    def handle_sources(self, title, data, meta=None, autoplay=False):  # NOSONAR
        files = data.get("files", [])
        subtitles = data.get("subtitles", [])
        if not files:
            console.print("[red]No streams found.[/red]")
            time.sleep(1.5)
            return False

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
            lib_data = scan_library(lib_path, include_details=False)
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
        manifest_qualities = sort_manifest_qualities(files)

        selected_quality = "auto"
        if not autoplay:
            std_options = build_quality_menu_options(files, include_adaptive=True)

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
        preferred_sub_lang = normalize_lang(preferred_sub_lang)
        pref_langs = self.settings.get("preferred_subtitle_langs", [preferred_sub_lang])
        fallback_langs = self.settings.get("fallback_subtitle_langs", ["ar", "en"])
        if not isinstance(pref_langs, list) or not pref_langs:
            pref_langs = [preferred_sub_lang]
        if not isinstance(fallback_langs, list):
            fallback_langs = ["ar", "en"]
            
        # Ensure all codes are normalized to 2-letter standard
        pref_langs = [normalize_lang(l) for l in pref_langs if l and l != "none"]
        fallback_langs = [normalize_lang(l) for l in fallback_langs if l and l != "none"]
        
        # Keep primary in sync as first entry
        if pref_langs and pref_langs[0] != preferred_sub_lang:
            if preferred_sub_lang != "none":
                pref_langs = [preferred_sub_lang] + [l for l in pref_langs if l != preferred_sub_lang]
        elif not pref_langs and preferred_sub_lang != "none":
             pref_langs = [preferred_sub_lang]
        include_all_subs = len(pref_langs) > 1  # default: embed all preferred langs

        lang_codes = []
        for s in subtitles:
            if isinstance(s, dict) and s.get("url"):
                code = normalize_lang(s.get("lang") or s.get("language"))
                if code not in lang_codes:
                    lang_codes.append(code)

        sub_options, all_lang_codes = build_subtitle_menu_options(
            preferred_sub_lang,
            pref_langs,
            lang_codes,
            fallback_langs,
        )
        sub_title = f"{title} - Select Subtitles"
        if not lang_codes:
            sub_title += " (fallback search only)"

        sub_sel = multi_selection_menu(
            sub_options,
            sub_title,
            formatter=lambda x: x["name"],
        )

        if sub_sel:
            # Check if any special action was selected
            values = [x["value"] for x in sub_sel]
            
            if "none" in values:
                subtitles = []
                preferred_sub_lang = "none"
                pref_langs = []
                include_all_subs = False
            else:
                # Prioritize explicit language selections if any
                actual_langs = [v for v in values if v not in ("all", "preferred", "primary")]
                
                if actual_langs:
                    pref_langs = actual_langs
                    preferred_sub_lang = pref_langs[0]
                    include_all_subs = len(pref_langs) > 1
                elif "all" in values:
                    include_all_subs = True
                    pref_langs = all_lang_codes or pref_langs
                elif "preferred" in values:
                    include_all_subs = True
                    # pref_langs remains as loaded from settings
                elif "primary" in values:
                    include_all_subs = False
                    pref_langs = [preferred_sub_lang]
        else:
            # Default to primary or settings if nothing selected
            include_all_subs = len(pref_langs) > 1

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
                    _req = Request(
                        _test_url,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    _start = time.time()
                    with urlopen(_req, timeout=8) as _resp:
                        _chunk = _resp.read(131072)   # read up to 128 KB
                    _elapsed = time.time() - _start
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
                console.print("[yellow]Speed test failed (all probes unreachable), using best available[/yellow]")
            time.sleep(1)


        # ── Filter files by selected quality ──
        filtered_files, _quality_mode = filter_sources_for_quality(files, selected_quality)
        if selected_quality not in ("auto", "adaptive"):
            if _quality_mode == "fallback_tagged":
                console.print(
                    f"[yellow]Selected quality '{selected_quality}' is unavailable exactly; using closest provider-tagged sources.[/yellow]"
                )
            elif _quality_mode == "enforced_manifest":
                # Provider did not tag qualities; keep all files and enforce
                # quality via manifest selection in yt-dlp/mpv.
                console.print(
                    f"[dim]Provider did not expose quality tags; enforcing '{selected_quality}' via manifest selection.[/dim]"
                )

        while True:
            # ── ACTION SELECTION ──
            options = [ACTION_PLAY, "⬇ Download"]
            if local_file:
                options.insert(0, ACTION_PLAY_LOCAL)

            if autoplay and not local_file:
                act = {"action": "select", "value": ACTION_PLAY}
            elif autoplay and local_file:
                act = {"action": "select", "value": ACTION_PLAY_LOCAL}
            else:
                act = selection_menu(
                    options,
                    f"{title} - Choose Action",
                    show_details=False,
                    formatter=lambda x: x,
                )

            if not act or act["action"] in ["back", "quit"]:
                return False

            if act["value"] == ACTION_PLAY_LOCAL:
                psl = self.settings.get("preferred_subtitle", "ar")
                ppl = self.settings.get("preferred_player", "mpv")
                play_video(local_file, title, preferred_sub_lang=psl, player=ppl)
                return {"position": 0, "duration": 0, "finished": True}

            # ── Find working source with enhanced selection ──
            console.print(f"[bold {ACCENT}]Selecting source for: {title} ({selected_quality})...[/bold {ACCENT}]")
            
            # Get multiple working sources for fallback
            working_sources = select_multiple_working_sources(filtered_files, count=3)
            
            if not working_sources:
                console.print(f"[bold red]No working source found for: {title}[/bold red]")
                time.sleep(2)
                return False
            
            selected = working_sources[0]
            fallback_sources = working_sources[1:] if len(working_sources) > 1 else []

            if act["value"] == ACTION_PLAY:
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
                        quality=selected_quality if selected_quality not in ("auto", "adaptive", "best") else None,
                    )

                    instant_fail = (
                        isinstance(stats, dict)
                        and not stats.get("finished")
                        and float(stats.get("position", 0) or 0) <= 2
                        and float(stats.get("duration", 0) or 0) <= 2
                    )

                    if instant_fail:
                        _auto_switch_count += 1
                        self.api.report_source_result(selected.get("provider"), False)
                        console.print("[yellow]Playback ended immediately. Trying another source...[/yellow]")
                        time.sleep(0.8)

                        _next = None
                        for fs in fallback_sources:
                            _u = fs.get("file", "")
                            if _u and _u not in _tried_urls:
                                _next = fs
                                break

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
                            for fs in _fresh_files:
                                _u = fs.get("file", "")
                                if _u and _u not in _tried_urls:
                                    _next = fs
                                    break

                        if _next is not None and _auto_switch_count <= _auto_switch_max:
                            selected = _next
                            start_time = 0
                            continue

                        console.print("[red]No alternate playable source found.[/red]")
                        return False

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

                    # Surface frame/quality telemetry when available.
                    if isinstance(stats, dict) and stats.get("duration", 0) > 0:
                        fps_avg = float(stats.get("fps_avg") or 0)
                        dropped = int(stats.get("dropped_frames") or 0)
                        if fps_avg > 0:
                            console.print(
                                f"[dim]Playback quality stats: avg_fps={fps_avg:.1f}, dropped_frames={dropped}[/dim]"
                            )
                            if dropped >= 30:
                                console.print(
                                    "[yellow]High frame drops detected. Try another source or lower quality.[/yellow]"
                                )

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
                    pq_act = (pq_sel.get("value") or {}).get("value") if pq_sel and pq_sel.get("action") == "select" else None
                    if pq_act == "done" or pq_act is None:
                        break
                    if pq_act == "replay":
                        start_time = 0   # restart from beginning
                        continue
                    if pq_act == "switch":
                        std_options = build_quality_menu_options(files, include_adaptive=True)
                        sq_sel = selection_menu(
                            std_options,
                            f"{title} — Switch Quality",
                            show_details=False,
                            formatter=lambda x: x["name"],
                        )
                        if sq_sel and sq_sel.get("action") == "select":
                            new_q = sq_sel["value"]["value"]
                            selected_quality = new_q
                            # Refilter files for the new quality
                            filtered_files, _switch_mode = filter_sources_for_quality(files, selected_quality)
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
                template = self.settings.get("filename_template", MOVIE_FILENAME_TEMPLATE)
                if meta and meta.get("type") == "tv":
                    template = self.settings.get("filename_template_tv", TV_FILENAME_TEMPLATE)

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
            template = self.settings.get("filename_template", MOVIE_FILENAME_TEMPLATE)
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
        
        lib_path = self.settings.get("library_dir")
        if not os.path.exists(lib_path):
            console.print(f"[red]Library directory dose not exist: {lib_path}[/red]")
            time.sleep(2)
            return

        while True:
            clear()
            print_header("Local Library")
            console.print(f"[dim]Library Path: {lib_path}[/dim]\n")
            
            with console.status("Scanning local library...", spinner="dots"):
                data = scan_library(lib_path, include_details=False)
            
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

    def _confirm_delete(self, prompt_text: str) -> bool:
        ans = console.input(f"[bold red]{prompt_text} (y/N): [/bold red]").strip().lower()
        return ans in ("y", "yes")

    def _prune_empty_dirs(self, path: str, root_dir: str) -> None:
        """Remove empty parent directories up to root_dir."""
        try:
            current = os.path.dirname(path)
            root_abs = os.path.abspath(root_dir)
            while current and os.path.abspath(current).startswith(root_abs):
                if os.path.abspath(current) == root_abs:
                    break
                if os.listdir(current):
                    break
                os.rmdir(current)
                current = os.path.dirname(current)
        except Exception:
            return

    def _delete_media_file(self, file_path: str) -> bool:
        try:
            if not file_path or not os.path.exists(file_path):
                return False
            os.remove(file_path)
            self._prune_empty_dirs(file_path, self.settings.get("library_dir", ""))
            try:
                clear_library_cache(self.settings.get("library_dir", ""))
            except Exception:
                pass
            return True
        except Exception as exc:
            console.print(f"[red]Delete failed: {exc}[/red]")
            return False

    def handle_library_movies(self, movies):  # NOSONAR
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
                # Header
                hdr = Text()
                hdr.append(CLI_BRAND_TITLE, style=f"bold {PRIMARY}")
                hdr.append(CLI_SEPARATOR, style=f"dim {PRIMARY}")
                hdr.append(movie["title"], style=f"bold {ACCENT}")
                hdr.append(CLI_SEPARATOR, style=f"dim {PRIMARY}")
                hdr.append(f"v{APP_VERSION}", style=f"dim {TEXT}")
                console.print(Panel(Align.center(hdr), border_style=PRIMARY, box=box.HEAVY, padding=(0, 2)))
                console.print("")
                if "resolution" not in movie or "subtitles" not in movie:
                    with console.status("Loading media details...", spinner="dots"):
                        movie.update(get_media_details(movie["path"]))
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
                    dtbl.add_row("💬  Subtitles", f"[{SUCCESS}]{len(subs)} track(s)[/{SUCCESS}]")
                    for s in subs:
                        dtbl.add_row("", f"[dim]  • {s}[/dim]")
                console.print(Panel(dtbl, border_style=f"dim {PRIMARY}", box=box.HEAVY, padding=(0, 1)))
                console.print("")
                # Action menu
                action_opts = [
                    {"name": "▶  Play",     "value": "play"},
                    {"name": "🗑  Delete Movie", "value": "delete"},
                    {"name": "⬅  Back",     "value": "back"},
                ]
                act = selection_menu(action_opts, movie["title"], show_details=False, formatter=lambda x: x["name"])
                if not act or act["action"] in ("back", "quit") or (act.get("value") or {}).get("value") == "back":
                    break
                if (act.get("value") or {}).get("value") == "play":
                    play_video(movie["path"], movie["title"], player=self.settings.get("preferred_player", "mpv"))
                elif (act.get("value") or {}).get("value") == "delete":
                    if self._confirm_delete(f"Delete movie '{movie['title']}'"):
                        deleted = self._delete_media_file(movie.get("path"))
                        if deleted:
                            console.print(f"[green]Deleted: {movie['title']}[/green]")
                            time.sleep(1)
                            return
                        console.print("[yellow]Movie file was not found or could not be deleted.[/yellow]")
                        time.sleep(1)

    def handle_library_tv(self, tv_data):  # NOSONAR
        while True:
            shows = [{"title": s, "seasons": d} for s, d in tv_data.items()]
            sel = selection_menu(shows, "Local TV Shows", show_details=False, formatter=lambda x: x["title"])
            if not sel or sel["action"] == "back":
                break
                
            show = sel["value"]
            while True:
                seasons = [{"num": sn, "eps": eps} for sn, eps in show["seasons"].items()]
                seasons.sort(key=lambda x: x["num"])
                season_options = [{"num": -1, "eps": [], "delete_show": True}]
                season_options.extend(seasons)

                def _season_fmt(x):
                    if x.get("delete_show"):
                        return "🗑  Delete Entire TV Show"
                    return f"Season {x['num']} ({len(x['eps'])} Episodes)"

                s_sel = selection_menu(
                    season_options,
                    f"{show['title']} - Seasons",
                    show_details=False,
                    formatter=_season_fmt,
                )
                if not s_sel or s_sel["action"] == "back":
                    break
                    
                season = s_sel["value"]
                if season.get("delete_show"):
                    if self._confirm_delete(f"Delete entire show '{show['title']}'"):
                        deleted_count = 0
                        for _, eps in show["seasons"].items():
                            for ep_item in eps:
                                if self._delete_media_file(ep_item.get("path")):
                                    deleted_count += 1
                        console.print(f"[green]Deleted {deleted_count} episode file(s) from {show['title']}.[/green]")
                        time.sleep(1)
                        return
                    continue

                while True:
                    episode_options = [{"episode": -1, "filename": "Delete This Season", "delete_season": True, "path": ""}]
                    episode_options.extend(season["eps"])

                    def _episode_fmt(x):
                        if x.get("delete_season"):
                            return "🗑  Delete Entire Season"
                        return f"E{x['episode']} - {x['filename']}"

                    e_sel = selection_menu(
                        episode_options,
                        f"{show['title']} S{season['num']} Episodes",
                        show_details=True,
                        formatter=_episode_fmt,
                    )
                    if not e_sel or e_sel["action"] == "back":
                        break
                        
                    ep = e_sel["value"]
                    if ep.get("delete_season"):
                        if self._confirm_delete(f"Delete season {season['num']} of '{show['title']}'"):
                            deleted_count = 0
                            for ep_item in season["eps"]:
                                if self._delete_media_file(ep_item.get("path")):
                                    deleted_count += 1
                            console.print(f"[green]Deleted {deleted_count} episode file(s) from season {season['num']}.[/green]")
                            time.sleep(1)
                            break
                        continue

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
                        ep_label = f"{show['title']} S{season['num']}E{ep['episode']}"
                        # Header
                        hdr2 = Text()
                        hdr2.append(CLI_BRAND_TITLE, style=f"bold {PRIMARY}")
                        hdr2.append(CLI_SEPARATOR, style=f"dim {PRIMARY}")
                        hdr2.append(ep_label, style=f"bold {ACCENT}")
                        hdr2.append(CLI_SEPARATOR, style=f"dim {PRIMARY}")
                        hdr2.append(f"v{APP_VERSION}", style=f"dim {TEXT}")
                        console.print(Panel(Align.center(hdr2), border_style=PRIMARY, box=box.HEAVY, padding=(0, 2)))
                        console.print("")
                        if "resolution" not in ep or "subtitles" not in ep:
                            with console.status("Loading media details...", spinner="dots"):
                                ep.update(get_media_details(ep["path"]))
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
                            {"name": "🗑  Delete Episode", "value": "delete"},
                            {"name": "⬅  Back",   "value": "back"},
                        ]
                        ep_act = selection_menu(ep_action_opts, ep_label, show_details=False, formatter=lambda x: x["name"])
                        if not ep_act or ep_act["action"] in ("back", "quit") or (ep_act.get("value") or {}).get("value") == "back":
                            break
                        if (ep_act.get("value") or {}).get("value") == "play":
                            auto_play = True
                            while auto_play:
                                auto_play = False
                                play_video(ep["path"], ep_label, player=self.settings.get("preferred_player", "mpv"))
                                
                                cur_idx = next((i for i, e in enumerate(season["eps"]) if e["path"] == ep["path"]), -1)
                                post_opts = [{"name": "🔄  Replay", "value": "replay"}]
                                if cur_idx >= 0 and cur_idx < len(season["eps"]) - 1:
                                    post_opts.append({"name": "⏭️  Next Episode", "value": "next"})
                                if cur_idx > 0:
                                    post_opts.append({"name": "⏮️  Previous Episode", "value": "prev"})
                                post_opts.append({"name": "⬅  Go Back to Details", "value": "back"})
                                
                                post_act = selection_menu(post_opts, "Playback Finished", show_details=False, formatter=lambda x: x["name"])
                                post_val = (post_act.get("value") or {}).get("value") if post_act and post_act.get("action") == "select" else "back"
                                
                                if post_val == "replay":
                                    auto_play = True
                                elif post_val == "next":
                                    ep = season["eps"][cur_idx + 1]
                                    ep_label = f"{show['title']} S{season['num']}E{ep['episode']}"
                                    auto_play = True
                                elif post_val == "prev":
                                    ep = season["eps"][cur_idx - 1]
                                    ep_label = f"{show['title']} S{season['num']}E{ep['episode']}"
                                    auto_play = True
                        elif (ep_act.get("value") or {}).get("value") == "delete":
                            if self._confirm_delete(f"Delete episode S{season['num']}E{ep['episode']} from '{show['title']}'"):
                                deleted = self._delete_media_file(ep.get("path"))
                                if deleted:
                                    console.print(f"[green]Deleted episode S{season['num']}E{ep['episode']}[/green]")
                                    time.sleep(1)
                                    break
                                console.print("[yellow]Episode file was not found or could not be deleted.[/yellow]")
                                time.sleep(1)

    def handle_download_manager(self):  # NOSONAR

        selected_indices = set()

        def generate_queue_table():
            q = self.download_manager.get_queue()

            # ── Header ─────────────────────────────────────────────────────
            header = Text()
            header.append(CLI_BRAND_TITLE, style=f"bold {PRIMARY}")
            header.append(CLI_SEPARATOR, style=f"dim {PRIMARY}")
            header.append("Download Manager", style=f"bold {ACCENT}")
            header.append(CLI_SEPARATOR, style=f"dim {PRIMARY}")
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
                if is_done:
                    bar_color = SUCCESS
                elif is_muxing:
                    bar_color = ACCENT
                elif is_active:
                    bar_color = PRIMARY
                else:
                    bar_color = f"dim {TEXT}"
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
                    if total_bytes > 0:
                        size_str = self.download_manager._bytes_to_human(total_bytes)
                    elif total_human not in ("Unknown", "---"):
                        size_str = total_human
                    else:
                        size_str = ""
                    downloaded_display = f"[{SUCCESS}]{size_str or 'Complete'}[/{SUCCESS}]"
                elif is_error:
                    downloaded_display = "[bold red]Failed[/bold red]"
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
                for item in items:
                    if item["status"] in ("error", "completed", "pending"):
                        self.download_manager.retry_task(item["id"])
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
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debug", action="store_true")
    parsed_args, passthrough_argv = parser.parse_known_args(sys.argv[1:])
    if parsed_args.debug:
        _enable_debug_mode()

    app_state = AppState(argv=passthrough_argv)
    controller = AppController(
        app_state,
        start_local_backend=start_local_backend,
        run_debug_source_command=run_debug_source_command,
        run_debug_subtitle_command=run_debug_subtitle_command,
        run_smoke_command=run_smoke_command,
        startup_health_check=startup_health_check,
        show_splash=show_splash,
        cli_factory=CinemaCLI,
    )
    sys.exit(controller.run())
