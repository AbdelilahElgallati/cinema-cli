import os
import shutil
import subprocess
import tempfile
import time
import requests
import urllib3
import atexit
import logging as _logging
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

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
_fh = _logging.FileHandler(
    "D:/My_Projects/cinema-cli/stream_debug.log", 
    mode="a", encoding="utf-8"
)
_fh.setFormatter(_logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
_fh.setLevel(_logging.DEBUG)
_stream_log = _logging.getLogger("stream_debug")
_stream_log.setLevel(_logging.DEBUG)
if not _stream_log.handlers:
    _stream_log.addHandler(_fh)

# ─── Pipeline Constants ─────────────────────────────────────────────
SUBTITLE_TIMEOUT = 8  # 8s hard timeout per fallback request
TOTAL_PIPELINE_LIMIT = 15 # 15s total limit for mpv launch

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


def _prepare_subtitles(title, subtitles, headers, meta, preferred_sub_lang, include_all_subs, fallback_langs=None, preferred_langs=None):  # NOSONAR
    """Mandated 4-Stage Subtitle Pipeline with Parallelization."""
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
            
            local_path = os.path.join(temp_dir, f"{base_name}.src_{l_code}.srt")
            try:
                # Use smarter headers: always User-Agent, but only Referer if same domain
                # Subtitles are often on different CDNs that block the video's Referer
                sub_headers = {"User-Agent": headers.get("User-Agent", "Mozilla/5.0")} if headers else {}
                if headers and "Referer" in headers:
                    from urllib.parse import urlparse as _up
                    if _up(url).netloc == _up(headers["Referer"]).netloc:
                        sub_headers["Referer"] = headers["Referer"]

                r = requests.get(url, timeout=SUBTITLE_TIMEOUT, headers=sub_headers, verify=False)
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

    # ── STAGES 2 & 3: FALLBACKS (Parallel) ──
    if primary not in found_langs:
        os_key = os.getenv("OPENSUBTITLES_API_KEY") or OPENSUBTITLES_API_KEY
        dl_key = os.getenv("SUBDL_API_KEY") or SUBDL_API_KEY
        
        def try_os():
            if not os_key: return "os", "⚠ API key not configured", None
            try:
                yr = meta.get("year"); sn = meta.get("season"); ep = meta.get("episode")
                res = _fetch_from_opensubtitles(title, [primary], year=yr, season=sn, episode=ep, max_per_language=1, key=os_key)
                if res:
                    p = os.path.join(temp_dir, f"{base_name}.fallback_os_{primary}.srt")
                    with open(p, "wb") as f: f.write(res[0]["content"])
                    return "os", "✓ Found — downloaded", p
                return "os", "✗ Not found", None
            except Exception as e:
                return "os", f"✗ Error: {str(e)[:20]}", None

        def try_subdl():
            if not dl_key: return "subdl", "⚠ API key not configured", None
            try:
                yr = meta.get("year"); sn = meta.get("season"); ep = meta.get("episode")
                res = fetch_subtitles_subdl(title, [primary], year=yr, season=sn, episode=ep, max_per_language=1)
                if res:
                    p = os.path.join(temp_dir, f"{base_name}.fallback_subdl_{primary}.srt")
                    with open(p, "wb") as f: f.write(res[0]["content"])
                    return "subdl", "✓ Found — downloaded", p
                return "subdl", "✗ Not found", None
            except Exception as e:
                return "subdl", f"✗ Error: {str(e)[:20]}", None

        pipeline_info["stage2"]["searching"] = True
        pipeline_info["stage3"]["searching"] = True
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [executor.submit(try_os), executor.submit(try_subdl)]
            for fut in as_completed(tasks):
                provider, result, path = fut.result()
                if provider == "os": pipeline_info["stage2"]["result"] = result
                else: pipeline_info["stage3"]["result"] = result
                
                if path and primary not in found_langs:
                    sub_paths.insert(0, path)
                    found_langs.add(primary)
        
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


def _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=False, quality=None):
    """Build mpv command-line arguments."""
    mpv_exe = find_executable("mpv") or "mpv"
    primary = normalize_lang(preferred_sub_lang or "ar")
    
    args = [
        mpv_exe,
        url,
        f"--title={title}",
        "--fs",
        "--force-window=immediate",
        "--keep-open=yes",
        "--network-timeout=60",
        "--tls-verify=no",
        "--hwdec=no",
        "--framedrop=no",
        "--hls-bitrate=max",
        "--hr-seek=yes",
        "--hr-seek-framedrop=yes",
        "--audio-wait-open=0.5",
        "--audio-stream-silence=yes",
        "--audio-pitch-correction=yes",
        "--sub-fix-timing=yes",
        "--sub-use-margins=yes",
        "--sub-ass-override=strip",
        "--sub-auto=fuzzy",
        f"--slang={primary},ar,ara,arabic,en,eng,fr,fra,es,spa",
        "--cache=yes",
        "--demuxer-max-bytes=150M",
        "--demuxer-max-back-bytes=50M",
        "--demuxer-readahead-secs=30",
        "--cache-pause=yes",
        "--cache-pause-initial=yes",
        "--cache-pause-wait=5",
        "--term-status-msg=STATUS: ${=time-pos} / ${=duration} | FPS=${estimated-vf-fps} | DROP=${drop-frame-count}",
    ]

    if start_time > 0:
        args.append(f"--start={start_time}")

    if use_ytdl and is_tool_available("yt-dlp"):
        args.insert(1, "--ytdl")
        fmt = _quality_to_ytdl_format(quality)
        if fmt:
            args.append(f"--ytdl-format={fmt}")
        else:
            args.append("--ytdl-format=bestvideo+bestaudio/best")
            args.append("--ytdl-raw-options=format-sort=res,fps")

        is_proxied = "localhost:3010" in url.lower() or "127.0.0.1:3010" in url.lower()
        if headers and not is_proxied:
            header_list = [f"{k}: {v}" for k, v in headers.items() if "," not in str(v)]
            if header_list:
                args.append(f"--ytdl-raw-options-append=http-header-fields={','.join(header_list)}")
                
    if headers and not ("localhost:3010" in url.lower() or "127.0.0.1:3010" in url.lower()):
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua: args.append(f"--user-agent={ua}")
        ref = headers.get("Referer") or headers.get("referer")
        if ref: args.append(f"--referrer={ref}")
        header_fields = [f"{k}: {v}" for k, v in headers.items() if "," not in str(v)]
        if header_fields:
            args.append(f"--http-header-fields={','.join(header_fields)}")

    for sp in sub_paths:
        args.append(f"--sub-file={sp}")

    return args


def _run_mpv(args):
    """Run mpv and parse playback stats."""
    app_logger.debug(f"Launching mpv with args: {args}")
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
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
            except: pass
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

        # Run Pipeline (wrapped in try/except to never block launch)
        sub_paths = []
        info = {}
        try:
            sub_paths, info = _prepare_subtitles(title, subtitles, headers, meta, preferred_sub_lang, include_all_subs, fallback_langs, preferred_langs)
        except Exception as sub_err:
            _stream_log.exception(f"EXCEPTION in _prepare_subtitles: {sub_err}")
            app_logger.error(f"Subtitle pipeline crashed: {sub_err}", exc_info=True)
            info = {"stage1": {"count": 0, "details": []}, "stage2": {"searching": False, "result": f"Error: {sub_err}"}, "stage3": {"searching": False, "result": "Skipped"}, "final_count": 0}

        _stream_log.info(f"sub_paths after pipeline: {sub_paths}")
        _stream_log.info(f"pipeline_info: {info}")

        # ── Stream Launch Info Panel ──
        try:
            domain = urlparse(url).netloc if url else "unknown"
            primary = normalize_lang(preferred_sub_lang or "ar")
            
            info_table = Table(box=None, show_header=False, padding=(0, 1))
            info_table.add_row("Title", ": " + title)
            info_table.add_row("Quality", ": " + str(quality or "best/auto"))
            info_table.add_row("Source", ": " + domain)
            
            url_l = (url or "").lower()
            is_proxied = "localhost:3010" in url_l or "127.0.0.1:3010" in url_l
            
            # Determine mode
            known_ytdlp_sites = [
                "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
                "twitch.tv", "soundcloud.com"
            ]
            is_known_platform = any(s in url_l for s in known_ytdlp_sites)
            prefer_ytdl_val = is_tool_available("yt-dlp") and not is_proxied and is_known_platform
            
            info_table.add_row("Mode", ": " + ("yt-dlp" if prefer_ytdl_val else "direct"))
            
            info_table.add_section()
            info_table.add_row(f"[bold]Stage 1 — Source subtitles[/]", f": {info.get('stage1', {}).get('count', 0)} found")
            for l_code, label, success in info.get("stage1", {}).get("details", []):
                status = "[bold green]✓[/]" if success else "[red]✗ missing[/]"
                info_table.add_row("", f"  → {l_code} ({label})  {status}")

            info_table.add_section()
            info_table.add_row("[bold]Stage 2 — OpenSubtitles fallback[/]", "")
            if info.get("stage2", {}).get("searching"):
                info_table.add_row("", f"  Searching for: {primary.upper()}")
            info_table.add_row("", f"  Result: {info.get('stage2', {}).get('result', 'Skipped')}")

            info_table.add_section()
            info_table.add_row("[bold]Stage 3 — SUBDL fallback[/]", "")
            if info.get("stage3", {}).get("searching"):
                info_table.add_row("", f"  Searching for: {primary.upper()}")
            info_table.add_row("", f"  Result: {info.get('stage3', {}).get('result', 'Skipped')}")

            info_table.add_section()
            info_table.add_row(f"[bold]Final subtitles passed to {player_name}[/]", f": {len(sub_paths)} files")
            for i, p in enumerate(sub_paths, 1):
                fname = os.path.basename(p)
                tag = " [bold green]← preferred[/]" if primary in fname.lower() else ""
                info_table.add_row("", f"  {i}. {fname}{tag}")

            console.print(Panel(info_table, title="🎬 Stream Launch Info", border_style="cyan", padding=(1, 2)))
        except Exception as e:
            _stream_log.exception(f"EXCEPTION in printing launch panel: {e}")
            app_logger.debug(f"Info panel error: {e}")

        # TASK 1: PAUSE EXECUTION
        input("Press Enter to launch the player...")

        # ── Player Launch ──
        if not url:
            _stream_log.error("Stream URL is empty!")
            console.print("[red]Error: Stream URL is empty![/red]")
            time.sleep(2)
            return False

        mpv_args = _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=prefer_ytdl_val, quality=quality if prefer_ytdl_val else None)
        
        _stream_log.info(f"mpv_args: {mpv_args}")
        app_logger.debug(f"Launching {player_name} with {len(mpv_args)} args. URL present: {bool(url)}")
        
        if player_name == "mpv":
            stats = _run_mpv(mpv_args)
            _stream_log.info(f"mpv stats: {stats}")
            app_logger.debug(f"mpv returned stats: {stats}")
            
            # Only return stats if it actually played something or exited cleanly (exit_code 0)
            # If it exited with error and played 0 frames, return False to trigger retry
            result = stats
            if stats.get("duration") == 0 and stats.get("position") == 0 and stats.get("exit_code", 0) != 0:
                result = False
            
            _stream_log.info(f"play_stream RESULT: {result}")
            return result
        elif player_name == "vlc":
            vlc_args = _build_vlc_args(player_exe, url, title, headers, sub_paths, start_time)
            stats = _run_vlc(vlc_args)
            _stream_log.info(f"VLC stats: {stats}")
            return stats
        return None

    except Exception as fatal_err:
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
