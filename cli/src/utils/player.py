import hashlib
import json
import logging as _logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import requests
import urllib3

from src.config import (
    DEFAULT_SUBTITLE_VERIFY_TLS,
    SETTINGS_FILE,
    console,
)
from src.utils import app_logger
from src.utils.storage import load_json_data
from src.utils.subtitles import (
    _looks_like_subtitle,
    fetch_subtitles,
)
from src.utils.system_tools import find_executable
from src.utils.utils import normalize_lang

# Windows-specific imports for named pipes
if sys.platform == "win32":
    try:
        import pywintypes
        import win32file
        import win32pipe
    except ImportError:
        win32file = None
else:
    win32file = None

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ─── Diagnostic Logger ─────────────────────────────────────────────
def _init_stream_log():
    try:
        log_dir = os.path.expanduser("~/.cinema-cli")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "stream_debug.log")
        handler = _logging.FileHandler(log_path, mode="a", encoding="utf-8")
    except Exception:
        handler = _logging.StreamHandler()

    handler.setFormatter(_logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    handler.setLevel(_logging.DEBUG)
    logger = _logging.getLogger("stream_debug")
    logger.setLevel(_logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(handler)
    return logger


_stream_log = _init_stream_log()


# ─── IPC Communication ─────────────────────────────────────────────
def mpv_ipc_send(ipc_path, command):
    """Send a command to mpv via IPC (Unix socket or Windows named pipe)."""
    payload = json.dumps({"command": command}).encode() + b"\n"
    try:
        if sys.platform == "win32":
            if win32file is None:
                app_logger.debug("[IPC] win32file not available, cannot send command.")
                return
            # Windows named pipe
            handle = win32file.CreateFile(
                ipc_path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            win32file.WriteFile(handle, payload)
            win32file.CloseHandle(handle)
        else:
            # Unix domain socket
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(ipc_path)
                s.sendall(payload)
    except Exception as e:
        app_logger.debug(f"[IPC] Failed to send command {command}: {e}")


class MpvIPCClient:
    """Typed wrapper around mpv IPC with path safety checks."""

    def __init__(self, ipc_path, temp_dir=None):
        self.ipc_path = ipc_path
        self.temp_dir = os.path.abspath(temp_dir or tempfile.gettempdir())

    def _is_allowed_temp_path(self, target_path):
        try:
            abs_path = os.path.abspath(target_path)
            return os.path.commonpath([abs_path, self.temp_dir]) == self.temp_dir
        except Exception:
            return False

    def add_subtitle(self, subtitle_path, flag="auto", label="Subtitle"):
        if not self._is_allowed_temp_path(subtitle_path):
            raise ValueError(f"Refusing subtitle path outside temp dir: {subtitle_path}")
        mpv_ipc_send(self.ipc_path, ["sub-add", subtitle_path, flag, label])


# ─── Pipeline Constants ─────────────────────────────────────────────
SUBTITLE_TIMEOUT = 8  # 8s hard timeout per fallback request
TOTAL_PIPELINE_LIMIT = 15  # 15s total limit for mpv launch


def _normalize_lang_list(values):
    out = []
    seen = set()
    for value in values or []:
        code = normalize_lang(value)
        if not code or code == "none" or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _build_subtitle_temp_path(source_tag, lang, ext):
    safe_lang = re.sub(r"[^a-z0-9_-]", "", str(lang).lower()) or "und"
    safe_ext = re.sub(r"[^a-z0-9]", "", str(ext).lower()) or "srt"
    return os.path.join(
        tempfile.gettempdir(),
        f"cinema_{source_tag}_{safe_lang}_{int(time.time() * 1000)}.{safe_ext}",
    )


def _is_valid_subtitle_payload(payload):
    if not payload or len(payload) < 20:
        return False
    head = payload[:2048].lower()
    if b"<html" in head or b"<!doctype" in head:
        return False
    return _looks_like_subtitle(payload)


# ─── Background Subtitle Handler ──────────────────────────────────────
def _background_subtitle_handler(
    ipc_path,
    title,
    subtitles,
    headers,
    meta,
    preferred_sub_lang,
    include_all_subs,
    fallback_langs,
    preferred_langs,
    already_found_paths,
    current_langs=None,
):
    """Fetch fallback subtitles in background and inject via IPC."""
    # 1. Wait for IPC socket/pipe to be ready
    found_ipc = False
    for _ in range(30):  # 3 seconds max
        if sys.platform == "win32" and win32file is not None:
            try:
                handle = win32file.CreateFile(
                    ipc_path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                win32file.CloseHandle(handle)
                found_ipc = True
                break
            except Exception:
                pass
        elif os.path.exists(ipc_path):
            found_ipc = True
            break
        time.sleep(0.1)

    if not found_ipc:
        app_logger.debug("[BG-SUB] IPC not found, aborting background subtitle injection")
        return

    ipc_client = MpvIPCClient(ipc_path, tempfile.gettempdir())

    # 2. Fetch from subtitle providers (often slow) and inject via IPC
    if meta:
        app_logger.debug("[BG-SUB] Starting background subtitle fetch...")
        desired_langs = _normalize_lang_list(
            preferred_langs if include_all_subs else [preferred_sub_lang]
        )
        if not desired_langs:
            desired_langs = _normalize_lang_list([preferred_sub_lang])

        found_langs = set(_normalize_lang_list(current_langs or []))
        missing_langs = [lang for lang in desired_langs if lang not in found_langs]

        if not missing_langs and found_langs:
            return

        if not missing_langs:
            return

        # Tracks whether we've already selected a track in this background session
        selected_any = False

        if missing_langs:
            try:
                fetched_all = fetch_subtitles(
                    title or "",
                    missing_langs,
                    year=meta.get("year"),
                    season=meta.get("season"),
                    episode=meta.get("episode"),
                    max_per_language=1,
                )
            except Exception as e:
                app_logger.debug(f"[BG-SUB] Background subtitle fetch failed: {e}")
                fetched_all = []

            for chosen in fetched_all:
                path = chosen.get("path") or _subtitle_result_to_temp_file(chosen)
                if not path or not os.path.exists(path) or path in already_found_paths:
                    continue

                already_found_paths.add(path)
                lang = normalize_lang(chosen.get("lang") or "und")
                app_logger.debug(f"[BG-SUB] Injecting background sub: {path} ({lang})")

                label_prefix = "Preferred" if lang in desired_langs else "Fallback"
                label = f"{label_prefix} ({lang})"

                # Smart selection logic:
                # 1. If we have NO initial subtitles (current_langs is empty) AND this is our FIRST background sub: SELECT it.
                # 2. If this is the primary preferred_sub_lang: SELECT it.
                # 3. Otherwise: just ADD it (don't select).
                should_select = False
                if not selected_any and not current_langs:
                    should_select = True
                elif lang == normalize_lang(preferred_sub_lang):
                    should_select = True

                flag = "select" if should_select else "auto"
                try:
                    ipc_client.add_subtitle(path, flag, label)
                except ValueError as path_err:
                    app_logger.debug(
                        f"[BG-SUB] Skipped subtitle outside temp directory: {path_err}"
                    )
                    continue

                if should_select:
                    selected_any = True

                if not include_all_subs:
                    break


def _vtt_to_srt(vtt_path):
    """Simple VTT to SRT converter."""
    srt_path = vtt_path.replace(".vtt", ".srt")
    try:
        with open(vtt_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        with open(srt_path, "w", encoding="utf-8") as f:
            count = 1
            skip = True
            for line in lines:
                if "WEBVTT" in line:
                    continue
                if "-->" in line:
                    skip = False
                    f.write(f"{count}\n")
                    # Convert . to , in timestamps
                    f.write(line.replace(".", ","))
                    count += 1
                elif not skip:
                    f.write(line)
        return srt_path
    except Exception as e:
        app_logger.debug(f"VTT to SRT conversion failed: {e}")
        return vtt_path


def _subtitle_result_to_temp_file(sub):
    """Convert subtitle result dict ({content, ext}) to a local temp file path."""
    if not isinstance(sub, dict):
        return None
    content = sub.get("content")
    if not content:
        return None

    ext = (sub.get("ext") or "srt").lower()
    if ext not in ("srt", "vtt", "ass", "ssa"):
        ext = "srt"
    lang = normalize_lang(sub.get("lang") or sub.get("language") or "und")
    target_path = _build_subtitle_temp_path("fallback", lang, ext)

    try:
        if not _is_valid_subtitle_payload(content):
            return None
        with open(target_path, "wb") as tmp:
            tmp.write(content)
        if ext == "vtt":
            return _vtt_to_srt(target_path)
        return target_path
    except Exception as e:
        app_logger.debug(f"Failed to materialize subtitle result: {e}")
        return None


def _prepare_subtitles(  # NOSONAR
    subtitles,
    headers,
    meta,
    preferred_sub_lang,
    include_all_subs=False,
    fallback_langs=None,
    preferred_langs=None,
    title=None,
    skip_fallbacks=False,
):
    """
    Prepare subtitles for the player.
    Returns (list of local paths, list of languages, already_found_set)
    """
    paths = []
    langs = []
    already_found = set()

    desired_langs = _normalize_lang_list(
        preferred_langs if include_all_subs else [preferred_sub_lang]
    )
    if not desired_langs:
        primary = normalize_lang(preferred_sub_lang)
        if primary and primary != "none":
            desired_langs = [primary]

    # Get TLS verification setting from config
    verify_tls = DEFAULT_SUBTITLE_VERIFY_TLS
    try:
        settings = load_json_data(SETTINGS_FILE)
        if settings and "verify_subtitle_tls" in settings:
            verify_tls = bool(settings["verify_subtitle_tls"])
    except Exception:
        pass

    # 1. Fetch primary/provider subtitles (often fast)
    with console.status("[bold cyan]Fetching subtitles...[/bold cyan]", spinner="dots"):
        try:
            # Stage 1: Provider subtitles for the requested language order
            selected_provider_subs = []
            if subtitles:
                by_lang = {}
                for sub in subtitles:
                    if not isinstance(sub, dict) or not sub.get("url"):
                        continue
                    sub_lang = normalize_lang(
                        sub.get("lang") or sub.get("language") or sub.get("code")
                    )
                    if desired_langs and sub_lang not in desired_langs:
                        continue
                    if sub_lang not in by_lang:
                        by_lang[sub_lang] = sub

                selected_provider_subs = [
                    by_lang[lang] for lang in desired_langs if lang in by_lang
                ]

            for sub in selected_provider_subs:
                url = sub.get("url")
                if not url:
                    continue
                try:
                    r = requests.get(url, headers=headers, timeout=5, verify=verify_tls)
                    r.raise_for_status()
                    if not _is_valid_subtitle_payload(r.content):
                        continue
                    ext = ".vtt" if "vtt" in url.lower() else ".srt"
                    sub_lang = normalize_lang(
                        sub.get("lang") or sub.get("language") or sub.get("code") or "und"
                    )
                    target_path = _build_subtitle_temp_path("provider", sub_lang, ext.lstrip("."))
                    with open(target_path, "wb") as tmp_file:
                        tmp_file.write(r.content)
                    path = _vtt_to_srt(target_path) if ext == ".vtt" else target_path
                    if path and os.path.exists(path) and path not in already_found:
                        paths.append(path)
                        langs.append(sub_lang)
                        already_found.add(path)
                except Exception as e:
                    app_logger.debug(f"Provider sub download failed: {e}")

            # Stage 2: Fill missing requested languages using OpenSubtitles -> SubDL fallback chain.
            if skip_fallbacks:
                return paths, langs, already_found

            found_langs = set(langs)
            missing_langs = [lang for lang in desired_langs if lang not in found_langs]

            if missing_langs:
                search_title = title
                if not search_title and isinstance(meta, dict):
                    search_title = meta.get("title") or meta.get("name")

                if search_title and meta:
                    fetched_subs_all = fetch_subtitles(
                        search_title,
                        missing_langs,
                        year=meta.get("year"),
                        season=meta.get("season"),
                        episode=meta.get("episode"),
                        max_per_language=1,
                    )

                    for chosen in fetched_subs_all:
                        path = chosen.get("path") or _subtitle_result_to_temp_file(chosen)
                        sub_lang = normalize_lang(chosen.get("lang") or "und")
                        if not path or not os.path.exists(path):
                            continue
                        if path in already_found:
                            continue
                        if include_all_subs and sub_lang in found_langs:
                            continue

                        paths.append(path)
                        langs.append(sub_lang)
                        already_found.add(path)
                        found_langs.add(sub_lang)

                        if not include_all_subs and paths:
                            break

        except Exception as e:
            app_logger.debug(f"Subtitle preparation failed: {e}")

    return paths, langs, already_found


def detect_available_players():
    """Detect which media players are installed on the system."""
    players = []
    if find_executable("mpv"):
        players.append("mpv")
    if find_executable("vlc"):
        players.append("vlc")
    if sys.platform == "darwin" and find_executable("iina"):
        players.append("iina")
    return players


def _quality_to_ytdl_format(quality):
    """Convert human quality (1080p) to yt-dlp format string."""
    if not quality or quality in ("auto", "best", "adaptive"):
        return None

    if str(quality).lower() == "4k":
        return "bestvideo[height<=2160]+bestaudio/best[height<=2160]"

    # Extract numeric height
    h = "".join(filter(str.isdigit, str(quality)))
    if not h:
        return None

    return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"


def _resolve_player(player_name):
    """Find executable for player name."""
    return find_executable(player_name) or player_name


def _build_mpv_args(
    url,
    title,
    headers,
    sub_paths,
    preferred_sub_lang,
    start_time,
    use_ytdl=False,
    quality=None,
    preferred_langs=None,
    ipc_path=None,
):
    """Helper to build mpv command arguments."""
    mpv_exe = _resolve_player("mpv")

    cmd = [mpv_exe]

    if use_ytdl:
        cmd.append("--ytdl")
        ytdl_format = _quality_to_ytdl_format(quality)
        if ytdl_format:
            cmd.append(f"--ytdl-format={ytdl_format}")
        else:
            cmd.append("--ytdl-format=bestvideo+bestaudio/best")
            cmd.append("--ytdl-raw-options=format-sort=res,fps")

    cmd.append(url)
    cmd.extend(
        [
            f"--force-media-title={title}",
            f"--title={title}",
            "--fs",
            "--keep-open=yes",
            "--hls-bitrate=max",
        ]
    )

    if ipc_path:
        cmd.append(f"--input-ipc-server={ipc_path}")

    # Pass preferred subtitle language order to mpv
    if preferred_langs:
        slang = ",".join(preferred_langs)
        cmd.append(f"--slang={slang}")
    elif preferred_sub_lang and preferred_sub_lang != "none":
        cmd.append(f"--slang={preferred_sub_lang}")

    if start_time > 0:
        cmd.append(f"--start={int(start_time)}")

    if headers:
        header_str = ",".join([f"{k}: {v}" for k, v in headers.items()])
        cmd.append(f"--http-header-fields={header_str}")

    for p in sub_paths:
        cmd.append(f"--sub-file={p}")

    return cmd


def play_stream(  # NOSONAR
    url,
    title,
    subtitles=None,
    headers=None,
    meta=None,
    start_time=0,
    preferred_sub_lang="ar",
    include_all_subs=False,
    player="mpv",
    fallback_langs=None,
    preferred_langs=None,
    quality=None,
):
    """
    Launch the media player with the given stream and subtitles.
    Supports smart fallback subtitle injection via IPC for mpv.
    """
    if not url:
        return False

    player = (player or "mpv").lower()

    # 1. Prepare local/fast subtitles
    sub_paths, sub_langs, already_found = _prepare_subtitles(
        subtitles,
        headers,
        meta,
        preferred_sub_lang,
        include_all_subs,
        fallback_langs,
        preferred_langs,
        title=title,
    )

    # 2. Build player command
    if player == "vlc":
        cmd = [find_executable("vlc") or "vlc", url, f"--meta-title={title}", "--fullscreen"]
        if start_time > 0:
            cmd.append(f"--start-time={int(start_time)}")
        for p in sub_paths:
            cmd.append(f"--sub-file={p}")

        try:
            subprocess.run(cmd, check=False)
            return {"finished": True}
        except Exception as e:
            console.print(f"[red]Error launching VLC: {e}[/red]")
            return False

    # Default to mpv
    if sys.platform == "win32":
        ipc_path = r"\\.\pipe\mpv-cinema-" + hashlib.md5(title.encode()).hexdigest()[:8]
    else:
        ipc_path = os.path.join(tempfile.gettempdir(), f"mpv-cinema-{os.getpid()}.sock")

    use_ytdl = (quality and quality != "auto") or ".m3u8" not in url.lower()
    cmd = _build_mpv_args(
        url,
        title,
        headers,
        sub_paths,
        preferred_sub_lang,
        start_time,
        use_ytdl=use_ytdl,
        quality=quality,
        preferred_langs=preferred_langs,
        ipc_path=ipc_path,
    )

    # Log command for debugging
    _stream_log.debug(f"Launching mpv: {' '.join(cmd)}")

    # 3. Start background subtitle fetcher (slow providers)
    bg_thread = threading.Thread(
        target=_background_subtitle_handler,
        args=(
            ipc_path,
            title,
            subtitles,
            headers,
            meta,
            preferred_sub_lang,
            include_all_subs,
            fallback_langs,
            preferred_langs,
            already_found,
            sub_langs,
        ),
        daemon=True,
    )
    bg_thread.start()

    # 4. Launch mpv and track stats
    try:
        start_ts = time.time()
        proc = subprocess.Popen(cmd)

        # Monitor process
        while proc.poll() is None:
            time.sleep(1)

        end_ts = time.time()
        duration_played = end_ts - start_ts

        # Cleanup local subtitles
        for p in sub_paths:
            try:
                os.remove(p)
            except:
                pass
        if sys.platform != "win32":
            try:
                os.remove(ipc_path)
            except:
                pass

        return {
            "position": duration_played,
            "finished": duration_played > 300,
        }

    except Exception as e:
        _stream_log.error(f"mpv execution failed: {e}\n{traceback.format_exc()}")
        console.print(f"[red]Error launching mpv: {e}[/red]")
        return False


def play_video(url, title, preferred_sub_lang="ar", player="mpv"):
    """Simple video player for local files.
    Automatically detects and loads matching subtitle files in the same directory.
    """
    player = (player or "mpv").lower()

    # Try to find matching subtitles in the same directory
    sub_paths = []
    try:
        if os.path.isfile(url):
            base_dir = os.path.dirname(url)
            filename = os.path.basename(url)
            file_base = os.path.splitext(filename)[0]

            if os.path.exists(base_dir):
                file_base_esc = re.escape(file_base)
                # Matches Movie.srt or Movie.en.srt
                pattern = re.compile(
                    rf"^{file_base_esc}(\.[a-z]{{2,3}})?\.(srt|vtt|ass|ssa)$", re.IGNORECASE
                )
                for f in os.listdir(base_dir):
                    if pattern.match(f):
                        sub_paths.append(os.path.join(base_dir, f))
    except Exception:
        pass

    if player == "vlc":
        vlc_exe = _resolve_player("vlc")
        cmd = [vlc_exe, url, f"--meta-title={title}", "--fullscreen"]
        for p in sub_paths:
            cmd.append(f"--sub-file={p}")
        subprocess.run(cmd, check=False)
    else:
        mpv_exe = _resolve_player("mpv")
        cmd = [mpv_exe, url, f"--title={title}", "--fs", "--keep-open=yes"]
        for p in sub_paths:
            cmd.append(f"--sub-file={p}")

        # If we have a preferred lang, tell mpv to try selecting it
        if preferred_sub_lang and preferred_sub_lang != "none":
            code = normalize_lang(preferred_sub_lang)
            if code and code != "und":
                cmd.append(f"--slang={code}")

        subprocess.run(cmd, check=False)
