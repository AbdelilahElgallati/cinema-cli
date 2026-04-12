import hashlib
import os
import sys
import shutil
import subprocess
import tempfile
import time
import requests
import urllib3
import atexit
import logging as _logging
import socket
import json
import threading
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows-specific imports for named pipes
if sys.platform == "win32":
    try:
        import win32pipe
        import win32file
        import pywintypes
    except ImportError:
        win32file = None
else:
    win32file = None

from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from src.config import SUCCESS, ACCENT, WARNING, console, OPENSUBTITLES_API_KEY, SUBDL_API_KEY
from src.ui.ui import clear
from src.utils import app_logger
from src.utils.subtitles import fetch_subtitles, fetch_subtitles_subdl, _fetch_from_opensubtitles, _looks_like_subtitle
from src.utils.system_tools import find_executable, is_tool_available
from src.utils.utils import normalize_lang

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
    
    handler.setFormatter(_logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
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
                0, None, win32file.OPEN_EXISTING, 0, None
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

# ─── Pipeline Constants ─────────────────────────────────────────────
SUBTITLE_TIMEOUT = 8  # 8s hard timeout per fallback request
TOTAL_PIPELINE_LIMIT = 15 # 15s total limit for mpv launch

# ─── Background Subtitle Handler ──────────────────────────────────────
def _background_subtitle_handler(ipc_path, title, subtitles, headers, meta, preferred_sub_lang, include_all_subs, fallback_langs, preferred_langs, already_found_paths):
    """Fetch fallback subtitles in background and inject via IPC."""
    # 1. Wait for IPC socket/pipe to be ready
    found_ipc = False
    for _ in range(30): # 3 seconds max
        if sys.platform == "win32":
            try:
                handle = win32file.CreateFile(
                    ipc_path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING, 0, None
                )
                win32file.CloseHandle(handle)
                found_ipc = True
                break
            except Exception:
                pass
        else:
            if os.path.exists(ipc_path):
                found_ipc = True
                break
        time.sleep(0.1)
    
    if not found_ipc:
        app_logger.debug(f"[IPC] Socket/Pipe {ipc_path} never appeared. Fallback subs will not be injected.")
        return

    # 2. Fetch fallbacks (Stages 2 & 3)
    try:
        # Call _prepare_subtitles with skip_fallbacks=False to get everything
        all_sub_paths, info = _prepare_subtitles(
            title, subtitles, headers, meta, preferred_sub_lang, 
            include_all_subs, fallback_langs, preferred_langs, skip_fallbacks=False
        )
        
        # 3. Inject only the new fallback subtitles
        for path in all_sub_paths:
            if path not in already_found_paths:
                # Basic label extraction from filename
                fname = os.path.basename(path).lower()
                lang_tag = "und"
                for l in ["ar", "en", "fr", "es", "de", "it", "pt", "ru"]:
                    if f"_{l}." in fname or f".{l}." in fname:
                        lang_tag = l
                        break
                
                label = f"Fallback ({lang_tag.upper()})"
                mpv_ipc_send(ipc_path, ["sub-add", path, "auto", label])
                app_logger.debug(f"[IPC] subtitle injected: {lang_tag} -> {path}")
                
    except Exception as e:
        app_logger.debug(f"[IPC] Background subtitle handler failed: {e}")

# ─── Supported Players ───────────────────────────────────────────────
SUPPORTED_PLAYERS = ["mpv", "vlc", "iina"]

# In-memory probe cache
_PROBE_CACHE = {}
_PROBE_TTL_SECONDS = 180
_PRESS_ENTER_PROMPT = "\nPress Enter to return..."

# Temporary directory for subtitles — cleaned up on exit
subtitle_tmp_dir = os.path.join(tempfile.gettempdir(), "cinema-cli-subs")
os.makedirs(subtitle_tmp_dir, exist_ok=True)
atexit.register(shutil.rmtree, subtitle_tmp_dir, ignore_errors=True)


def detect_available_players():
    """Return list of players found on the system."""
    found = []
    for p in SUPPORTED_PLAYERS:
        if find_executable(p):
            found.append(p)
        elif p == "vlc":
            vlc_paths = [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            ]
            for vp in vlc_paths:
                if os.path.isfile(vp):
                    found.append(p)
                    break
    return found


def _get_vlc_executable():
    """Resolve VLC executable path."""
    vlc_exe = find_executable("vlc")
    if vlc_exe:
        return vlc_exe
    for p in [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    ]:
        if os.path.isfile(p):
            return p
    return None


def _resolve_player(player):
    """Get the executable path for the requested player, or fall back."""
    player = (player or "mpv").lower().strip()
    if player == "vlc":
        exe = _get_vlc_executable()
        if exe:
            return "vlc", exe
    if player in ("mpv", "iina"):
        exe = find_executable(player)
        if exe:
            return player, exe
    for p in detect_available_players():
        if p == "vlc":
            return "vlc", _get_vlc_executable()
        return p, p
    return None, None


def _vtt_to_srt(vtt_text: str) -> str:
    """Convert WebVTT subtitle text to SRT format."""
    import re as _re
    lines = vtt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    srt_blocks = []
    cue_idx = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            i += 1
            continue
        if "-->" in line:
            ts = _re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", line)
            i += 1
            txt = []
            while i < len(lines) and lines[i].strip():
                txt.append(lines[i].rstrip())
                i += 1
            if txt:
                cue_idx += 1
                srt_blocks.append(f"{cue_idx}\n{ts}\n" + "\n".join(txt))
        else:
            i += 1
    return "\n\n".join(srt_blocks) + "\n" if srt_blocks else vtt_text


def _prepare_subtitles(title, subtitles, headers, meta, preferred_sub_lang, include_all_subs, fallback_langs=None, preferred_langs=None, skip_fallbacks=False):  # NOSONAR
    """Mandated 4-Stage Subtitle Pipeline with Parallelization."""
    _stream_log.debug(f"[DIAG-D] subtitle list received by player: {len(subtitles)} items")
    _stream_log.debug(f"[DIAG-D] first subtitle: {subtitles[0] if subtitles else 'EMPTY'}")
    sub_paths = []
    found_langs = set()
    pipeline_info = {
        "stage1": {"count": 0, "details": []},
        "stage2": {"searching": False, "result": "Skipped"},
        "stage3": {"searching": False, "result": "Skipped"},
        "final_count": 0
    }

    if preferred_sub_lang in ("none", ""):
        return [], pipeline_info

    primary = normalize_lang(preferred_sub_lang or "ar")
    temp_dir = subtitle_tmp_dir
    base_name = "".join(c for c in title if c.isalnum() or c in " _-").strip().replace(" ", "_")

    # ── STAGE 1: SOURCE SUBTITLES (Parallel) ──
    if subtitles:
        _stream_log.debug(f"[Stage1 input] {subtitles[:3]}")
        _stream_log.info(f"Stage 1: Processing {len(subtitles)} source subtitles")
        def download_one(s):
            url = s.get("url")
            lang_raw = s.get("lang") or s.get("language") or "und"
            l_code = normalize_lang(lang_raw)
            label = s.get("label") or lang_raw
            
            # Use hash of URL to prevent filename collisions for same language
            url_hash = hashlib.md5(url.encode()).hexdigest()[:6]
            local_path = os.path.join(temp_dir, f"{base_name}.src_{l_code}_{url_hash}.srt")
            
            try:
                # Use smarter headers: always User-Agent and Referer if available.
                # Providers often require the same Referer for subtitles as the video,
                # even if they are hosted on different domains.
                sub_headers = {"User-Agent": headers.get("User-Agent", "Mozilla/5.0")} if headers else {}
                if headers and "Referer" in headers:
                    sub_headers["Referer"] = headers["Referer"]
                if headers and "Origin" in headers:
                    sub_headers["Origin"] = headers["Origin"]

                # Honor TLS verification config
                from src.utils.storage import load_json_data
                from src.config import SETTINGS_FILE
                settings = load_json_data(SETTINGS_FILE) or {}
                verify_tls = settings.get("SUBTITLE_VERIFY_TLS", True)
                
                if not verify_tls:
                    _stream_log.warning(f"TLS verification disabled for subtitle download: {url}")

                r = requests.get(url, timeout=SUBTITLE_TIMEOUT, headers=sub_headers, verify=verify_tls)
                if r.status_code == 200 and _looks_like_subtitle(r.content):
                    content = r.content
                    decoded = None
                    for enc in ["utf-8", "utf-8-sig", "cp1256", "windows-1256"]:
                        try: decoded = content.decode(enc); break
                        except: continue
                    if decoded is None: decoded = content.decode("utf-8", errors="ignore")
                    if decoded.lstrip().startswith("WEBVTT") or ".vtt" in url.lower():
                        decoded = _vtt_to_srt(decoded)
                    with open(local_path, "w", encoding="utf-8-sig") as f:
                        f.write(decoded)
                    return l_code, label, local_path
                else:
                    _stream_log.warning(f"Failed to download sub {url}: Status {r.status_code} (Size: {len(r.content) if r.content else 0})")
            except Exception as e:
                _stream_log.error(f"Error downloading sub {url}: {e}")
            return l_code, label, None

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(download_one, s) for s in subtitles if isinstance(s, dict) and s.get("url")]
            for fut in as_completed(futures):
                l_code, label, path = fut.result()
                pipeline_info["stage1"]["details"].append((l_code, label, bool(path)))
                if path:
                    sub_paths.append(path)
                    found_langs.add(l_code)
        
        pipeline_info["stage1"]["count"] = len([p for p in sub_paths if "src_" in p])

    if skip_fallbacks:
        pipeline_info["stage2"]["result"] = "Deferred (Background)"
        pipeline_info["stage3"]["result"] = "Deferred (Background)"
        pipeline_info["final_count"] = len(sub_paths)
        return sub_paths, pipeline_info

    # ── STAGES 2 & 3: FALLBACKS (Parallel) ──
    # Request all preferred languages if include_all_subs is True
    search_langs = preferred_langs if (include_all_subs and preferred_langs) else [primary]
    
    # Check if we are still missing ANY preferred language
    missing_langs = [l for l in search_langs if l not in found_langs]
    
    if missing_langs:
        os_key = os.getenv("OPENSUBTITLES_API_KEY") or OPENSUBTITLES_API_KEY
        dl_key = os.getenv("SUBDL_API_KEY") or SUBDL_API_KEY
        
        def try_os():
            if not os_key: return "os", "⚠ API key not configured", []
            try:
                yr = meta.get("year"); sn = meta.get("season"); ep = meta.get("episode")
                # Fetch for all missing languages
                res = _fetch_from_opensubtitles(title, missing_langs, year=yr, season=sn, episode=ep, max_per_language=1, key=os_key)
                paths = []
                if res:
                    for sub in res:
                        l = sub.get("lang", "und")
                        p = os.path.join(temp_dir, f"{base_name}.fallback_os_{l}.srt")
                        with open(p, "wb") as f: f.write(sub["content"])
                        paths.append((l, p))
                    return "os", f"✓ Found {len(paths)} — downloaded", paths
                return "os", "✗ Not found", []
            except Exception as e:
                return "os", f"✗ Error: {str(e)[:20]}", []

        def try_subdl():
            if not dl_key: return "subdl", "⚠ API key not configured", []
            try:
                yr = meta.get("year"); sn = meta.get("season"); ep = meta.get("episode")
                # Fetch for all missing languages
                res = fetch_subtitles_subdl(title, missing_langs, year=yr, season=sn, episode=ep, max_per_language=1)
                paths = []
                if res:
                    for sub in res:
                        l = sub.get("lang", "und")
                        p = os.path.join(temp_dir, f"{base_name}.fallback_subdl_{l}.srt")
                        with open(p, "wb") as f: f.write(sub["content"])
                        paths.append((l, p))
                    return "subdl", f"✓ Found {len(paths)} — downloaded", paths
                return "subdl", "✗ Not found", []
            except Exception as e:
                return "subdl", f"✗ Error: {str(e)[:20]}", []

        pipeline_info["stage2"]["searching"] = True
        pipeline_info["stage3"]["searching"] = True
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [executor.submit(try_os), executor.submit(try_subdl)]
            for fut in as_completed(tasks):
                provider, result, paths = fut.result()
                if provider == "os": pipeline_info["stage2"]["result"] = result
                else: pipeline_info["stage3"]["result"] = result
                
                for l_code, path in paths:
                    if path and path not in sub_paths:
                        # Insert primary at start, others at end
                        if l_code == primary:
                            sub_paths.insert(0, path)
                        else:
                            sub_paths.append(path)
                        found_langs.add(l_code)
        
        if primary in found_langs:
            if "Found" in pipeline_info["stage2"]["result"] and "Found" in pipeline_info["stage3"]["result"]:
                pipeline_info["stage3"]["result"] = "Skipped (already found in OS)"
    else:
        pipeline_info["stage2"]["result"] = f"Skipped ({primary.upper()} found in Stage 1)"
        pipeline_info["stage3"]["result"] = f"Skipped ({primary.upper()} found in Stage 1)"

    # ── STAGE 4: SORTING & FINAL ──
    def sub_sort_key(p):
        fname = os.path.basename(p).lower()
        if f"_{primary}." in fname or f".{primary}." in fname: return 0
        return 1
    
    sub_paths = sorted(list(set(sub_paths)), key=sub_sort_key)
    pipeline_info["final_count"] = len(sub_paths)
    return sub_paths, pipeline_info


def _quality_to_ytdl_format(quality):
    """Convert a quality label to a yt-dlp format selector."""
    if not quality or quality in ("auto", "best"):
        return None
    q = quality.lower().replace("p", "").strip()
    height_map = {"4k": 2160, "2160": 2160, "1080": 1080, "720": 720, "480": 480, "360": 360, "240": 240}
    height = height_map.get(q)
    if height is None:
        try: height = int(q)
        except ValueError: return None
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"


def _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=False, quality=None, ipc_socket=None):
    """Build mpv command-line arguments."""
    mpv_exe = find_executable("mpv") or "mpv"
    primary = normalize_lang(preferred_sub_lang or "ar")
    
    # Set HLS bitrate based on requested quality for direct HLS playback.
    # mpv's --hls-bitrate selects the variant <= specified bitrate (bits/sec).
    # Common bitrates: 1080p (5M), 720p (2.5M), 480p (1.2M), 360p (0.5M)
    hls_bitrate = "max"
    if quality and not use_ytdl:
        q = str(quality).lower()
        if "2160" in q or "4k" in q: hls_bitrate = "20000000"
        elif "1080" in q: hls_bitrate = "5000000"
        elif "720" in q: hls_bitrate = "2500000"
        elif "480" in q: hls_bitrate = "1200000"
        elif "360" in q: hls_bitrate = "500000"
        elif "240" in q: hls_bitrate = "250000"

    args = [
        mpv_exe,
        f"--title={title}",
        "--fs",
        "--keep-open=yes",
        "--network-timeout=60",
        "--tls-verify=no",
        "--hwdec=no",
        "--framedrop=no",
        f"--hls-bitrate={hls_bitrate}",
        "--hr-seek=yes",
        "--hr-seek-framedrop=yes",
        "--audio-wait-open=0.5",
        "--audio-pitch-correction=yes",
        "--aid=auto",
        "--sid=auto",
        f"--slang={primary},ar,ara,arabic,en,eng,fr,fra,es,spa",
        "--cache=yes",
        "--demuxer-max-bytes=150M",
        "--demuxer-max-back-bytes=50M",
        "--demuxer-readahead-secs=30",
        "--cache-pause=yes",
        "--cache-pause-initial=yes",
        "--cache-pause-wait=5",
        "--sub-fix-timing=yes",
        "--sub-use-margins=yes",
        "--sub-ass-override=force",
        "--sub-auto=fuzzy",
        "--term-status-msg=STATUS: ${=time-pos} / ${=duration} | FPS=${estimated-vf-fps} | DROP=${drop-frame-count}",
    ]

    if ipc_socket:
        args.append(f"--input-ipc-server={ipc_socket}")

    if start_time > 0:
        args.append(f"--start={start_time}")

    if use_ytdl and is_tool_available("yt-dlp"):
        args.append("--ytdl")
        fmt = _quality_to_ytdl_format(quality)
        if fmt:
            args.append(f"--ytdl-format={fmt}")
        else:
            args.append("--ytdl-format=bestvideo+bestaudio/best")
            args.append("--ytdl-raw-options=format-sort=res,fps")

    # Reset TLS verification to 'no' for streaming (standard for these providers)
    # but honor global setting if explicitly requested.
    from src.utils.storage import load_json_data
    from src.config import SETTINGS_FILE
    _settings = load_json_data(SETTINGS_FILE) or {}
    _verify_tls = _settings.get("SUBTITLE_VERIFY_TLS", False) # Default False for stream compatibility
    
    for i, arg in enumerate(args):
        if arg.startswith("--tls-verify="):
            args[i] = f"--tls-verify={'yes' if _verify_tls else 'no'}"

    # Use explicit flags for User-Agent and Referer as they are more robust across builds
    is_proxied = "localhost:3010" in url.lower() or "127.0.0.1:3010" in url.lower()
    if headers and not is_proxied:
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua: args.append(f"--user-agent={ua}")
        ref = headers.get("Referer") or headers.get("referer")
        if ref: args.append(f"--referrer={ref}")
        
        # Add all OTHER headers to the fields list, avoiding UA/Referer duplicates
        fields = []
        for k, v in headers.items():
            k_low = k.lower()
            if k_low not in ["user-agent", "referer"] and "," not in str(v):
                fields.append(f"{k}: {v}")
        if fields:
            args.append(f"--http-header-fields={','.join(fields)}")
            if "--ytdl" in args:
                args.append(f"--ytdl-raw-options-append=http-header-fields={','.join(fields)}")

    for sp in sub_paths:
        args.append(f"--sub-file={sp}")

    args.append("--sub-forced-events-only=no")

    # The stream URL MUST be the final argument for consistent track/sub behavior
    args.append(url)

    return args


def _run_mpv(args):
    """Run mpv and parse playback stats."""
    app_logger.debug(f"Launching mpv with args: {args}")
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
        encoding="utf-8",
        errors="ignore",
    )

    position = 0
    duration = 0
    had_video = False
    no_video_explicit = False
    fps_values = []
    dropped_frames = 0

    while True:
        line = process.stdout.readline()
        if not line: break
        if "STATUS:" in line:
            try:
                status = line.split("STATUS:", 1)[1].strip()
                parts = [p.strip() for p in status.split("|") if p.strip()]
                if parts:
                    pos_dur = parts[0]
                    if "/" in pos_dur:
                        p_str, d_str = [x.strip() for x in pos_dur.split("/", 1)]
                        position, duration = float(p_str), float(d_str)
                for token in parts:
                    if token.upper().startswith("FPS="):
                        fps_values.append(float(token.split("=", 1)[1].strip()))
                    elif token.upper().startswith("DROP="):
                        dropped_frames = max(dropped_frames, int(token.split("=", 1)[1].strip()))
            except Exception as e:
                app_logger.debug(f"Error parsing mpv status line: {e}")
                pass
        low = line.lower()
        if "video:" in low and "no video" not in low: had_video = True
        if "video: no video" in low or "no video streams selected" in low: no_video_explicit = True

    process.wait()
    return {
        "position": position,
        "duration": duration,
        "finished": (duration > 0 and position > duration * 0.9),
        "had_video": had_video,
        "fps_avg": (sum(fps_values) / len(fps_values)) if fps_values else 0,
        "dropped_frames": dropped_frames,
        "no_video": no_video_explicit,
        "exit_code": process.returncode,
    }


def _build_vlc_args(vlc_exe, url, title, headers, sub_paths, start_time):
    """Build VLC command-line arguments."""
    args = [vlc_exe, url, f"--meta-title={title}", "--fullscreen", "--play-and-exit"]
    if start_time > 0: args.append(f"--start-time={start_time}")
    if headers:
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua: args.append(f"--http-user-agent={ua}")
        ref = headers.get("Referer") or headers.get("referer")
        if ref: args.append(f"--http-referrer={ref}")
    if sub_paths:
        args.append(f"--sub-file={sub_paths[0]}")
        for sp in sub_paths[1:]: args.append(f"--input-slave={sp}")
    return args


def _run_vlc(args):
    """Run VLC and wait for finish."""
    start = time.time()
    subprocess.run(args, capture_output=True, text=True)
    elapsed = time.time() - start
    return {"position": elapsed, "duration": elapsed, "finished": elapsed > 30}


def play_stream(url, title, subtitles=None, headers=None, meta=None, start_time=0, preferred_sub_lang='ar', include_all_subs=True, preferred_langs=None, player='mpv', fallback_langs=None, quality=None):  # NOSONAR
    """Plays a stream with the Mandated Subtitle Pipeline."""
    try:
        _stream_log.info(f"=== play_stream START: {title} ===")
        _stream_log.info(f"URL: {url}")
        _stream_log.info(f"Subtitles received: {len(subtitles) if subtitles else 0}")

        player_name, player_exe = _resolve_player(player)
        if not player_exe:
            _stream_log.error("No player executable found")
            return None

        clear()
        console.print(Panel(Align.center(f"[bold {SUCCESS}]Preparing playback...[/bold {SUCCESS}]\n[dim]{title}[/dim]"), border_style=SUCCESS))

        # 1. IPC setup (if mpv)
        ipc_path = None
        if player_name == "mpv":
            if sys.platform == "win32":
                ipc_path = f"\\\\.\\pipe\\mpv-cinema-{os.getpid()}"
            else:
                ipc_path = os.path.join(tempfile.gettempdir(), f"mpv-cinema-{os.getpid()}.sock")

        # 2. Run Pipeline - Stage 1 only (fast)
        sub_paths = []
        info = {}
        try:
            # We call with skip_fallbacks=True to launch mpv immediately with what we have
            sub_paths, info = _prepare_subtitles(
                title, subtitles, headers, meta, preferred_sub_lang, 
                include_all_subs, fallback_langs, preferred_langs, skip_fallbacks=True
            )
        except Exception as sub_err:
            _stream_log.exception(f"EXCEPTION in _prepare_subtitles (Stage 1): {sub_err}")
            app_logger.error(f"Subtitle pipeline crashed: {sub_err}", exc_info=True)
            info = {"stage1": {"count": 0, "details": []}, "stage2": {"searching": False, "result": f"Error: {sub_err}"}, "stage3": {"searching": False, "result": "Skipped"}, "final_count": 0}

        # Determine mode BEFORE info panel try block to avoid NameError if panel fails
        url_l = (url or "").lower()
        is_proxied = "localhost:3010" in url_l or "127.0.0.1:3010" in url_l
        known_ytdlp_sites = [
            "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
            "twitch.tv", "soundcloud.com"
        ]
        is_known_platform = any(s in url_l for s in known_ytdlp_sites)
        prefer_ytdl_val = is_tool_available("yt-dlp") and not is_proxied and is_known_platform

        # ── Stream Launch Info Panel ──
        try:
            domain = urlparse(url).netloc if url else "unknown"
            primary = normalize_lang(preferred_sub_lang or "ar")
            
            info_table = Table(box=None, show_header=False, padding=(0, 1))
            info_table.add_row("Title", ": " + title)
            info_table.add_row("Quality", ": " + str(quality or "best/auto"))
            info_table.add_row("Source", ": " + domain)
            info_table.add_row("Mode", ": " + ("yt-dlp" if prefer_ytdl_val else "direct"))
            
            info_table.add_section()
            info_table.add_row(f"[bold]Stage 1 — Source subtitles[/]", f": {info.get('stage1', {}).get('count', 0)} found")
            for l_code, label, success in info.get("stage1", {}).get("details", []):
                status = "[bold green]✓[/]" if success else "[red]✗ missing[/]"
                info_table.add_row("", f"  → {l_code} ({label})  {status}")

            info_table.add_section()
            info_table.add_row("[bold]Fallback subtitles (Background)[/]", ": Fetching in background...")
            info_table.add_row("", "  → Subtitles will be injected into mpv when ready.")

            info_table.add_section()
            info_table.add_row(f"[bold]Initial subtitles passed to {player_name}[/]", f": {len(sub_paths)} files")
            for i, p in enumerate(sub_paths, 1):
                fname = os.path.basename(p)
                tag = " [bold green]← preferred[/]" if primary in fname.lower() else ""
                info_table.add_row("", f"  {i}. {fname}{tag}")

            console.print(Panel(info_table, title="🎬 Stream Launch Info", border_style="cyan", padding=(1, 2)))
        except Exception as e:
            _stream_log.exception(f"EXCEPTION in printing launch panel: {e}")
            app_logger.debug(f"Info panel error: {e}")

        # TASK 1: PAUSE EXECUTION (only in debug mode)
        if "--debug" in sys.argv or os.getenv("PLAYER_DEBUG") == "1":
            input("Press Enter to launch the player...")

        # ── Player Launch ──
        if not url:
            _stream_log.error("Stream URL is empty!")
            console.print("[red]Error: Stream URL is empty![/red]")
            time.sleep(2)
            return False

        if player_name == "mpv":
            app_logger.debug(f"[LAUNCH] prefer_ytdl_val={prefer_ytdl_val} sub_paths={sub_paths}")
            mpv_args = _build_mpv_args(
                url, title, headers, sub_paths, preferred_sub_lang, 
                start_time, use_ytdl=prefer_ytdl_val, quality=quality if prefer_ytdl_val else None,
                ipc_socket=ipc_path
            )
            
            # Start background subtitle fetcher
            bg_thread = threading.Thread(
                target=_background_subtitle_handler,
                args=(ipc_path, title, subtitles, headers, meta, preferred_sub_lang, include_all_subs, fallback_langs, preferred_langs, sub_paths),
                daemon=True
            )
            bg_thread.start()
            
            app_logger.debug(f"[LAUNCH] sub_paths at launch: {sub_paths}")
            app_logger.debug(f"[LAUNCH] mpv args: {mpv_args}")
            stats = _run_mpv(mpv_args)
            
            # Cleanup IPC
            if ipc_path:
                if sys.platform != "win32":
                    try:
                        if os.path.exists(ipc_path):
                            os.unlink(ipc_path)
                    except Exception:
                        pass
            
            return stats
        elif player_name == "vlc":
            vlc_args = _build_vlc_args(player_exe, url, title, headers, sub_paths, start_time)
            stats = _run_vlc(vlc_args)
            return stats
        return None

    except Exception as fatal_err:
        import traceback
        _stream_log.error(f"FATAL traceback: {traceback.format_exc()}")
        _stream_log.exception(f"FATAL in play_stream: {fatal_err}")
        console.print(f"[red]Fatal stream error: {fatal_err}[/red]")
        input("Press Enter to continue...")
        return False



def play_video(url, title, preferred_sub_lang="ar", player="mpv"):
    """Play a direct video link or local file."""
    _, player_exe = _resolve_player(player)
    if not player_exe: return
    if player == "vlc":
        subprocess.run([player_exe, url, f"--meta-title={title}", "--fullscreen"], check=False)
    else:
        mpv_exe = find_executable("mpv") or "mpv"
        subprocess.run([mpv_exe, url, f"--title={title}", "--fs", "--keep-open=yes"], check=False)
