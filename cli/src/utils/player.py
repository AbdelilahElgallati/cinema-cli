import os
import shutil
import subprocess
import tempfile
import time
import requests
import urllib3
import atexit

from rich.align import Align
from rich.panel import Panel
from src.config import SUCCESS, ACCENT, WARNING, console
from src.ui.ui import clear
from src.utils import app_logger
from src.utils.subtitles import fetch_arabic_subtitle, fetch_subtitles
from src.utils.system_tools import find_executable, is_tool_available

# Suppress SSL warnings for subtitle providers with expired certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── Supported Players ───────────────────────────────────────────────
SUPPORTED_PLAYERS = ["mpv", "vlc", "iina"]  # iina for macOS users

# In-memory probe cache to avoid probing the same URL repeatedly.
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
            # VLC common install paths on Windows
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
    # Fallback: try any available player
    for p in detect_available_players():
        if p == "vlc":
            return "vlc", _get_vlc_executable()
        return p, p
    return None, None


# ─── Subtitle helpers (shared) ───────────────────────────────────────

def _norm_lang(lang: str) -> str:
    l = (lang or "").strip().lower()
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
    if l in ["chinese", "zho", "chi", "zh"]:
        return "zh"
    if l in ["japanese", "jpn", "ja"]:
        return "ja"
    if l in ["korean", "kor", "ko"]:
        return "ko"
    if l in ["hindi", "hin", "hi"]:
        return "hi"
    return l or "und"


def _vtt_to_srt(vtt_text: str) -> str:  # NOSONAR
    """Convert WebVTT subtitle text to SRT format for better player compatibility."""
    import re as _re
    lines = vtt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    srt_blocks = []
    cue_idx = 0
    i = 0

    # Skip BOM and WEBVTT header
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("WEBVTT"):
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            break
        if not stripped:
            i += 1
            continue
        break

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("NOTE") or line.startswith("STYLE"):
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            continue
        if "-->" in line:
            ts_line = _re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", line)
            ts_line = _re.sub(r"(\d{2}:\d{2})\.(\d{3})", r"\1,\2", ts_line)
            ts_line = _re.sub(r"([\d:,]+\s*-->\s*[\d:,]+)\s+.*", r"\1", ts_line)
            m = _re.match(r"\s*([\d:,]+)\s*-->\s*([\d:,]+)\s*$", ts_line)
            if m:
                start_ts, end_ts = m.group(1), m.group(2)
                if start_ts.count(":") == 1:
                    start_ts = f"00:{start_ts}"
                if end_ts.count(":") == 1:
                    end_ts = f"00:{end_ts}"
                ts_line = f"{start_ts} --> {end_ts}"
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].rstrip())
                i += 1
            if text_lines:
                cue_idx += 1
                srt_blocks.append(f"{cue_idx}\n{ts_line}\n" + "\n".join(text_lines))
        else:
            i += 1

    return "\n\n".join(srt_blocks) + "\n" if srt_blocks else vtt_text


def _looks_like_subtitle(data: bytes) -> bool:
    """Validate that bytes look like actual subtitle content, not HTML/error."""
    if not data or len(data) < 20:
        return False
    head = data[:2048].lower()
    if b"<html" in head or b"<!doctype" in head:
        return False
    return (b"webvtt" in head) or (b" --> " in head) or (b"{\\an" in head)


def _prepare_subtitles(title, subtitles, headers, meta, preferred_sub_lang, include_all_subs, fallback_langs=None, preferred_langs=None):  # NOSONAR
    """Download / collect subtitle paths. Returns list of local file paths or URLs.

    preferred_langs: ordered list of language codes (primary first).
        When provided, tracks in this order are fetched and passed to the player.
        include_all_subs=True means all langs in the list; False means just the first.
    """
    sub_paths = []

    # If subtitles are explicitly disabled, return empty immediately.
    if preferred_sub_lang in ("none", ""):
        return sub_paths

    # Build effective ordered language list
    primary = _norm_lang(preferred_sub_lang or "ar")
    if preferred_langs and isinstance(preferred_langs, (list, tuple)) and preferred_langs:
        wanted = [_norm_lang(l) for l in preferred_langs if l and _norm_lang(l) != "none"]
        if not wanted:
            wanted = [primary]
        elif wanted[0] != primary:
            wanted = [primary] + [l for l in wanted if l != primary]
    else:
        wanted = [primary]

    if subtitles:
        items = []
        for s in subtitles:
            if isinstance(s, dict) and s.get("url"):
                items.append({"lang": _norm_lang(s.get("lang") or s.get("language")), "url": s.get("url")})

        # Build ordered list: wanted languages first (in priority order), then others if include_all
        ordered = []
        seen_url = set()
        seen_lang = set()

        for lang in (wanted if include_all_subs else wanted[:1]):
            for x in items:
                if x["lang"] == lang and x["url"] not in seen_url and x["lang"] not in seen_lang:
                    ordered.append(x)
                    seen_url.add(x["url"])
                    seen_lang.add(x["lang"])
                    break  # one per language

        if include_all_subs:
            # Append remaining languages not explicitly in wanted list
            for x in items:
                if x["url"] not in seen_url and x["lang"] not in seen_lang:
                    ordered.append(x)
                    seen_url.add(x["url"])
                    seen_lang.add(x["lang"])

        # Fallback: nothing matched wanted langs — use first available
        if not ordered and items:
            ordered.append(items[0])

        try:
            temp_dir = os.path.join(tempfile.gettempdir(), "cinema-cli-subs")
            os.makedirs(temp_dir, exist_ok=True)
            base = "".join(c for c in title if c.isalnum() or c in " _-").strip().replace(" ", "_")
            for s in ordered[:5]:
                sub_url = s["url"]
                # Always save as .srt for maximum player compatibility
                local_sub = os.path.join(temp_dir, f"{base}.{s['lang']}.srt")
                try:
                    r = requests.get(sub_url, timeout=15, headers=headers or {}, verify=False)  # NOSONAR
                    if r.status_code == 200 and r.content and _looks_like_subtitle(r.content):
                        # Decode with robust encoding detection
                        decoded = None
                        for enc in ["utf-8", "utf-8-sig", "cp1256", "windows-1256",
                                    "iso-8859-6", "iso-8859-1", "cp1252",
                                    "shift_jis", "euc-kr", "gb18030", "latin-1"]:
                            try:
                                decoded = r.content.decode(enc)
                                break
                            except (UnicodeDecodeError, LookupError):
                                continue
                        if decoded is None:
                            decoded = r.content.decode("utf-8", errors="ignore")

                        # Convert VTT to SRT if needed for better sync
                        if decoded.lstrip().startswith("WEBVTT") or ".vtt" in sub_url.lower():
                            decoded = _vtt_to_srt(decoded)

                        with open(local_sub, "w", encoding="utf-8-sig") as f:
                            f.write(decoded)
                        sub_paths.append(local_sub)
                    elif r.status_code == 200:
                        # Content didn't validate — skip this subtitle, don't pass raw URL
                        pass
                    else:
                        # HTTP error — skip silently
                        pass
                except Exception as e:
                    app_logger.debug(f"Suppressed error in _prepare_subtitles (individual sub download): {e}", exc_info=True)
        except Exception as e:
            app_logger.debug(f"Suppressed error in _prepare_subtitles (main block): {e}", exc_info=True)

    # Fallback: fetch from OpenSubtitles (multi-language)
    if not sub_paths:
        try:
            temp_dir = os.path.join(tempfile.gettempdir(), "cinema-cli-subs")
            os.makedirs(temp_dir, exist_ok=True)
            yr = sn = epn = None
            if isinstance(meta, dict):
                yr = meta.get("year")
                sn = meta.get("season")
                epn = meta.get("episode")

            # Build language request list: wanted langs first, then fallback_langs, then ar+en
            langs = list(wanted) if include_all_subs else [primary]
            if fallback_langs and isinstance(fallback_langs, (list, tuple)):
                for x in fallback_langs:
                    c = str(x).strip().lower()
                    if c and c not in langs and c != "none":
                        langs.append(c)
            for last in ("ar", "en"):
                if last not in langs:
                    langs.append(last)

            subs_found = fetch_subtitles(title, langs, year=yr, season=sn, episode=epn, max_per_language=1)
            if not subs_found:
                # keep old behavior as final fallback
                res = fetch_arabic_subtitle(title, year=yr, season=sn, episode=epn)
                if res:
                    content, sub_ext = res
                    subs_found = [{"lang": "ar", "content": content, "ext": sub_ext}]

            if subs_found:
                base = "".join(c for c in title if c.isalnum() or c in " _-").strip().replace(" ", "_")
                # Sort by wanted-list priority
                def _sort_key(s):
                    lang = _norm_lang(str(s.get("lang") or "und"))
                    try:
                        return langs.index(lang)
                    except ValueError:
                        return len(langs)
                subs_found = sorted(subs_found, key=_sort_key)
                saved = []
                for s in subs_found:
                    lang = _norm_lang(str(s.get("lang") or "und"))
                    content = s.get("content") or b""
                    # Validate content
                    if not _looks_like_subtitle(content):
                        continue
                    # Decode and convert VTT→SRT if needed
                    try:
                        decoded = content.decode("utf-8", errors="ignore")
                        if decoded.lstrip().startswith("WEBVTT"):
                            decoded = _vtt_to_srt(decoded)
                            sub_ext = "srt"
                        else:
                            sub_ext = str(s.get("ext") or "srt")
                    except Exception:
                        sub_ext = str(s.get("ext") or "srt")
                        decoded = None

                    sub_path = os.path.join(temp_dir, f"{base}.{lang}.{sub_ext}")
                    if decoded:
                        with open(sub_path, "w", encoding="utf-8-sig") as f:
                            f.write(decoded)
                    else:
                        with open(sub_path, "wb") as f:
                            f.write(content)
                    saved.append(sub_path)
                    if not include_all_subs:
                        break
                sub_paths.extend(saved)
        except Exception as e:
            app_logger.debug(f"Suppressed error in _prepare_subtitles (OS fallback): {e}", exc_info=True)

    return sub_paths


# ─── MPV argument builders ───────────────────────────────────────────

def _quality_to_ytdl_format(quality):
    """Convert a quality label ('1080p', '720p', '480p', '360p', '4k') to a
    yt-dlp / mpv --ytdl-format selector string.  Returns None for 'best'/'auto'."""
    if not quality or quality in ("auto", "best"):
        return None
    q = quality.lower().replace("p", "").strip()
    height_map = {"4k": 2160, "2160": 2160, "1080": 1080, "720": 720, "480": 480, "360": 360, "240": 240}
    height = height_map.get(q)
    if height is None:
        # Fallback: try parsing raw number
        try:
            height = int(q)
        except ValueError:
            return None
    # Strict quality lock: do NOT fall back to global "best" when user selected
    # a specific resolution. If unavailable, yt-dlp should fail and caller can
    # switch source/quality explicitly.
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"


def _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=False, quality=None):  # NOSONAR
    """Build mpv command-line arguments."""
    mpv_exe = find_executable("mpv") or "mpv"
    args = [
        mpv_exe,
        url,
        f"--title={title}",
        "--fs",
        "--force-window=immediate",
        "--keep-open=yes",
        "--network-timeout=60",
        # Hardware decoding and performance tweaks to prevent frame drops
        "--hwdec=auto-safe",
        "--profile=fast",
        "--vd-lavc-fast=yes",
        "--framedrop=vo",
        # Robust streaming and reconnection
        "--demuxer-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_delay_max=5",
        # HLS quality: always pick the highest-bitrate variant when mpv handles
        # the manifest directly (without yt-dlp). This is the key fix for the
        # 360p default issue — mpv otherwise picks the first (lowest) variant.
        "--hls-bitrate=max",
        # Synchronization and timing fixes
        "--hr-seek=yes",
        "--hr-seek-framedrop=yes",
        "--audio-wait-open=0.5",
        "--audio-stream-silence=yes",
        "--audio-pitch-correction=yes",
        # Subtitle synchronization and auto-correction
        # sub-fix-timing: smooths tiny gaps between consecutive events (display only)
        "--sub-fix-timing=yes",
        "--sub-use-margins=yes",
        # strip: apply mpv's default styling for external SRT/VTT but don't
        # override ASS timing overrides — prevents desync when switching tracks
        "--sub-ass-override=strip",
        "--sub-auto=fuzzy",
        f"--slang={preferred_sub_lang},ar,ara,arabic,en,eng,fr,fra,es,spa",
        # Cache and buffering for stability (massively increased for HLS)
        "--cache=yes",
        "--demuxer-max-bytes=1024M",
        "--demuxer-max-back-bytes=256M",
        "--demuxer-readahead-secs=120",
        "--cache-pause=yes",
        "--term-status-msg=STATUS: ${=time-pos} / ${=duration} | FPS=${estimated-vf-fps} | DROP=${drop-frame-count}",
    ]

    if start_time > 0:
        args.append(f"--start={start_time}")

    if use_ytdl and is_tool_available("yt-dlp"):
        args.insert(1, "--ytdl")
        # Always enforce a video+audio selector when using yt-dlp to avoid
        # accidental audio-only HLS variant selection on some manifests.
        fmt = _quality_to_ytdl_format(quality)
        if fmt:
            # User selected a specific resolution — enforce it strictly.
            args.append(f"--ytdl-format={fmt}")
        else:
            # Auto/best mode: ask yt-dlp to sort by resolution and fps so the
            # highest available resolution is always preferred over the default
            # first-variant behaviour.
            args.append("--ytdl-format=bestvideo+bestaudio/best")
            args.append("--ytdl-raw-options=format-sort=res,fps")

        # Pass custom headers into yt-dlp when present
        if headers:
            header_list = []
            for k, v in headers.items():
                if "," not in str(v):
                    header_list.append(f"{k}: {v}")
            if header_list:
                # format-sort was already appended above; append additional
                # raw options as a separate flag (mpv allows multiple).
                args.append(f"--ytdl-raw-options-append=http-header-fields={','.join(header_list)}")
                
    if headers:
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            args.append(f"--user-agent={ua}")
        ref = headers.get("Referer") or headers.get("referer")
        if ref:
            args.append(f"--referrer={ref}")
        # Pass all headers via http-header-fields for better compatibility
        header_fields = [f"{k}: {v}" for k, v in headers.items() if "," not in str(v)]
        if header_fields:
            args.append(f"--http-header-fields={','.join(header_fields)}")

    for sp in sub_paths:
        args.append(f"--sub-file={sp}")
    # Do NOT force sub-delay=0 or audio-delay=0 here.
    # Pinning both to 0 prevents mpv from doing per-track compensation,
    # causing desync whenever the user switches subtitle tracks.
    # mpv's automatic sync is reliable; expose z/x to the user for manual tuning.

    return args


def _run_mpv(args):  # NOSONAR
    """Run mpv and parse position/duration from status messages.
    Returns dict with playback stats."""
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
        if not line:
            break
        if "STATUS:" in line:
            try:
                status = line.split("STATUS:", 1)[1].strip()
                parts = [p.strip() for p in status.split("|") if p.strip()]
                if parts:
                    pos_dur = parts[0]
                    p_str, d_str = [x.strip() for x in pos_dur.split("/", 1)]
                    p = float(p_str)
                    d = float(d_str)
                    if d > 0:
                        position = p
                        duration = d
                for token in parts[1:]:
                    if token.upper().startswith("FPS="):
                        fps_values.append(float(token.split("=", 1)[1].strip()))
                    elif token.upper().startswith("DROP="):
                        dropped_frames = max(dropped_frames, int(token.split("=", 1)[1].strip()))
            except Exception as e:
                app_logger.debug(f"Suppressed error in _run_mpv (status parsing): {e}", exc_info=True)
        low = line.lower()
        if "video:" in low and "no video" not in low:
            had_video = True
        if "video: no video" in low or "no video streams selected" in low:
            no_video_explicit = True

    process.wait()
    return {
        "position": position,
        "duration": duration,
        "finished": (duration > 0 and position > duration * 0.9),
        "had_video": had_video,
        "fps_avg": (sum(fps_values) / len(fps_values)) if fps_values else 0,
        "dropped_frames": dropped_frames,
        # Only treat as no-video when mpv explicitly reports it.
        # Do NOT infer from position/time alone; that caused false positives
        # and endless fallback loops when users closed playback manually.
        "no_video": no_video_explicit,
    }


# ─── VLC argument builders ───────────────────────────────────────────

def _build_vlc_args(vlc_exe, url, title, headers, sub_paths, start_time):
    """Build VLC command-line arguments."""
    args = [
        vlc_exe,
        url,
        f"--meta-title={title}",
        "--fullscreen",
        "--play-and-exit",
    ]

    if start_time > 0:
        args.append(f"--start-time={start_time}")

    if headers:
        # VLC uses --http-user-agent and --http-referrer
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            args.append(f"--http-user-agent={ua}")
        ref = headers.get("Referer") or headers.get("referer")
        if ref:
            args.append(f"--http-referrer={ref}")

    # VLC subtitle: only the first one via --sub-file, rest via --input-slave
    if sub_paths:
        args.append(f"--sub-file={sub_paths[0]}")
        for sp in sub_paths[1:]:
            args.append(f"--input-slave={sp}")

    return args


def _run_vlc(args):
    """Run VLC and wait for it to finish. Returns basic stats."""
    start = time.time()
    subprocess.run(args, capture_output=True, text=True)
    elapsed = time.time() - start
    return {
        "position": elapsed,
        "duration": elapsed,
        "finished": elapsed > 30,  # VLC doesn't expose easy position tracking
    }


def _ffprobe_has_video(url, headers=None, timeout_sec=12):  # NOSONAR
    """Return (ok, reason) for whether ffprobe sees a video stream on URL.

    This runs before opening MPV to reject broken/audio-only HLS URLs early.
    """
    ffprobe_exe = find_executable("ffprobe")
    if not ffprobe_exe:
        return True, "ffprobe_unavailable"

    cache_key = f"{url}|{(headers or {}).get('Referer','')}|{(headers or {}).get('Origin','')}"
    now = time.time()
    cached = _PROBE_CACHE.get(cache_key)
    if cached and (now - cached["ts"] < _PROBE_TTL_SECONDS):
        return cached["ok"], cached["reason"]

    cmd = [
        ffprobe_exe,
        "-v", "error",
        "-show_entries", "stream=codec_type",
        "-select_streams", "v:0",
        "-of", "default=noprint_wrappers=1:nokey=1",
    ]

    if headers and isinstance(headers, dict):
        # ffmpeg/ffprobe expects CRLF-separated header lines.
        header_lines = []
        for k, v in headers.items():
            if v is None:
                continue
            header_lines.append(f"{k}: {v}")
        if header_lines:
            cmd.extend(["-headers", "\r\n".join(header_lines) + "\r\n"])
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            cmd.extend(["-user_agent", str(ua)])

    cmd.append(url)

    run_kw = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "ignore",
        "timeout": timeout_sec,
    }
    if os.name == "nt":
        run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        res = subprocess.run(cmd, **run_kw)
        out = (res.stdout or "").strip().lower()
        # Only hard-reject when ffprobe completed successfully and found no
        # video stream. Any transport/auth/network failure is treated as
        # inconclusive so playback can still proceed and be judged by mpv.
        if res.returncode == 0:
            ok = "video" in out
            reason = "ok" if ok else "no_video_stream_detected"
        else:
            ok = True
            reason = "probe_inconclusive"
    except subprocess.TimeoutExpired:
        ok = True
        reason = "probe_timeout_inconclusive"
    except Exception as exc:
        ok = True
        reason = f"probe_inconclusive:{exc}"[:160]

    _PROBE_CACHE[cache_key] = {"ok": ok, "reason": reason, "ts": now}
    return ok, reason


# ─── Main play functions ─────────────────────────────────────────────

def play_stream(url, title, subtitles=None, headers=None, meta=None, start_time=0, preferred_sub_lang='ar', include_all_subs=True, preferred_langs=None, player='mpv', fallback_langs=None, quality=None):  # NOSONAR
    """
    Plays a stream using the chosen player (mpv, vlc, or iina).
    preferred_langs: ordered list of language codes (primary first) from settings.
    quality: desired resolution, e.g. '1080p', '720p', '480p', '360p', '4k'.
             Passed to yt-dlp via --ytdl-format when mpv falls back to yt-dlp mode.
    Returns a dict with playback stats: {position, duration, finished}
    """
    player_name, player_exe = _resolve_player(player)

    if player_exe is None:
        clear()
        console.print("\n[bold red]No supported player found![/bold red]")
        console.print("[yellow]Install one of: mpv, VLC, or iina[/yellow]")
        console.print("[dim]mpv: https://mpv.io/installation/[/dim]")
        console.print("[dim]VLC: https://www.videolan.org/vlc/[/dim]")
        console.input(_PRESS_ENTER_PROMPT)
        return None

    # Show player info
    clear()
    player_label = player_name.upper()
    controls = "q=Quit, Space=Pause"
    if player_name == "mpv":
        controls = "q=Quit, Space=Pause, z/x=Sub Sync (-/+), j=Audio, v=Sub Visibility"
    elif player_name == "vlc":
        controls = "Space=Pause, g/h=Sub Sync (-/+), j=Audio Track, v=Sub Track"

    console.print(
        Panel(
            Align.center(
                f"[bold {SUCCESS}]Starting {player_label}: {title}[/bold {SUCCESS}]\n\n"
                f"[dim]{url}[/dim]\n\n"
                f"[white]Controls: {controls}[/white]"
            ),
            title=f"{player_label} Player",
            border_style=SUCCESS,
        )
    )

    # Prepare subtitles
    sub_paths = _prepare_subtitles(
        title, subtitles, headers, meta,
        preferred_sub_lang, include_all_subs,
        fallback_langs=fallback_langs,
        preferred_langs=preferred_langs,
    )

    # ── VLC path ──
    if player_name == "vlc":
        try:
            vlc_args = _build_vlc_args(player_exe, url, title, headers, sub_paths, start_time)
            return _run_vlc(vlc_args)
        except Exception as e:
            console.print(f"[red]VLC Error: {e}[/red]")
            time.sleep(2)
            return None

    # ── Pre-play probe: reject URLs that have no video stream before opening MPV ──
    probe_ok, probe_reason = _ffprobe_has_video(url, headers=headers)
    if not probe_ok:
        console.print(f"[{WARNING}]Source rejected before playback (no video): {probe_reason}[/{WARNING}]")
        return {
            "position": 0,
            "duration": 0,
            "finished": False,
            "had_video": False,
            "no_video": True,
            "probe_failed": True,
        }

    # ── MPV path (prefer yt-dlp for HLS/quality-enforced streams) ──
    try:
        url_l = (url or "").lower()
        looks_like_manifest = any(sig in url_l for sig in [
            ".m3u8", "m3u8", "master", "playlist", "/hls/", "index.m3u8"
        ])
        quality_enforced = bool(quality and quality not in ("auto", "adaptive", "best"))
        # Always use yt-dlp for HLS/manifest URLs so that format-sort and
        # quality selectors are applied — regardless of whether custom headers
        # are present. This is the primary fix for the 360p-default issue when
        # quality=auto (no specific quality was user-selected).
        prefer_ytdl = is_tool_available("yt-dlp") and (
            looks_like_manifest or quality_enforced
        )

        mpv_args = _build_mpv_args(
            url,
            title,
            headers,
            sub_paths,
            preferred_sub_lang,
            start_time,
            use_ytdl=prefer_ytdl,
            quality=quality if prefer_ytdl else None,
        )
        console.print(f"[dim]Launching mpv ({'yt-dlp' if prefer_ytdl else 'direct'} mode)...[/dim]")
        stats = _run_mpv(mpv_args)

        # If first mode failed instantly, retry with an alternate mode.
        if stats["duration"] == 0 and stats["position"] == 0:
            if prefer_ytdl and quality_enforced:
                console.print(
                    f"[{WARNING}]Quality-locked playback failed for this source; retrying with best available format...[/{WARNING}]"
                )
                mpv_args_fallback = _build_mpv_args(
                    url, title, headers, sub_paths, preferred_sub_lang, start_time,
                    use_ytdl=True, quality=None
                )
                stats = _run_mpv(mpv_args_fallback)
                if stats["duration"] > 0 or stats["position"] > 0:
                    return stats
                # If it still fails, fall through to the general fallback

            alt_use_ytdl = (not prefer_ytdl) and is_tool_available("yt-dlp")
            if alt_use_ytdl or prefer_ytdl:
                console.print(
                    f"[{WARNING}]Primary playback mode failed, retrying with {'yt-dlp' if alt_use_ytdl else 'direct'}...[/{WARNING}]"
                )
                mpv_args = _build_mpv_args(
                    url,
                    title,
                    headers,
                    sub_paths,
                    preferred_sub_lang,
                    start_time,
                    use_ytdl=alt_use_ytdl,
                    quality=quality if alt_use_ytdl else None,
                )
                stats = _run_mpv(mpv_args)

        # If still nothing and VLC is available, offer VLC fallback
        if stats["duration"] == 0 and stats["position"] == 0:
            vlc_exe = _get_vlc_executable()
            if vlc_exe:
                console.print(f"[{WARNING}]mpv could not play this stream. Trying VLC as fallback...[/{WARNING}]")
                time.sleep(1)
                vlc_args = _build_vlc_args(vlc_exe, url, title, headers, sub_paths, start_time)
                return _run_vlc(vlc_args)

        return stats

    except Exception as e:
        console.print(f"[red]Player Error: {e}[/red]")
        time.sleep(2)
        return None


def play_video(url, title, preferred_sub_lang="ar", player="mpv"):
    """Play a direct video link or local file using the chosen player."""
    player_name, player_exe = _resolve_player(player)

    if player_exe is None:
        clear()
        console.print("\n[bold red]No supported player found![/bold red]")
        console.print("[yellow]Install one of: mpv, VLC, or iina[/yellow]")
        console.input(_PRESS_ENTER_PROMPT)
        return

    # Check if local file exists
    if not url.startswith("http") and not os.path.exists(url):
        console.print(f"\n[bold red]Error: File not found at {url}[/bold red]")
        console.input(_PRESS_ENTER_PROMPT)
        return

    player_label = player_name.upper()
    controls = "q=Quit, Space=Pause"
    if player_name == "mpv":
        controls = "q=Quit, Space=Pause, z/x=Sub Sync, j=Audio, v=Sub Visibility"
    elif player_name == "vlc":
        controls = "Space=Pause, g/h=Sub Sync, j=Audio Track, v=Sub Track"

    clear()
    console.print(
        Panel(
            Align.center(
                f"[bold {SUCCESS}]Starting {player_label}: {title}[/bold {SUCCESS}]\n\n"
                f"[dim]{url}[/dim]\n\n"
                f"[white]Controls: {controls}[/white]"
            ),
            title=f"{player_label} Player",
            border_style=SUCCESS,
        )
    )

    try:
        if player_name == "vlc":
            vlc_args = [
                player_exe, url,
                f"--meta-title={title}",
                "--fullscreen",
                "--play-and-exit",
            ]
            subprocess.run(vlc_args, check=False)
        elif player_name == "iina":
            subprocess.run([
                "iina", "--mpv-fs", url,
            ], check=False)
        else:
            # mpv with --keep-open to prevent instant close
            mpv_exe = find_executable("mpv") or "mpv"
            subprocess.run([
                mpv_exe, url,
                f"--title={title}",
                "--fs",
                "--keep-open=yes",
                f"--slang={preferred_sub_lang},ar,ara,arabic,en,eng,fr,fra,es,spa",
                "--sub-auto=exact",
            ], check=False)
    except Exception as e:
        app_logger.debug(f"Suppressed error in play_video: {e}", exc_info=True)
        console.print(f"\n[bold red]Failed to launch player:[/bold red] {e}")
        time.sleep(3)
