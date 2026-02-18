import os
import shutil
import subprocess
import tempfile
import time
import requests
import urllib3

from rich.align import Align
from rich.panel import Panel
from src.config import SUCCESS, ACCENT, WARNING, console
from src.ui.ui import clear
from src.utils.subtitles import fetch_arabic_subtitle, fetch_subtitles

# Suppress SSL warnings for subtitle providers with expired certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── Supported Players ───────────────────────────────────────────────
SUPPORTED_PLAYERS = ["mpv", "vlc", "iina"]  # iina for macOS users


def detect_available_players():
    """Return list of players found on the system."""
    found = []
    for p in SUPPORTED_PLAYERS:
        if shutil.which(p):
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
    if shutil.which("vlc"):
        return "vlc"
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
    if player in ("mpv", "iina") and shutil.which(player):
        return player, player
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


def _prepare_subtitles(title, subtitles, headers, meta, preferred_sub_lang,
                       include_all_subs, fallback_langs=None, preferred_langs=None):
    """Download / collect subtitle paths. Returns list of local file paths or URLs.

    preferred_langs: ordered list of language codes (primary first).
        When provided, tracks in this order are fetched and passed to the player.
        include_all_subs=True means all langs in the list; False means just the first.
    """
    sub_paths = []

    # Build effective ordered language list
    primary = _norm_lang(preferred_sub_lang or "ar")
    if preferred_langs and isinstance(preferred_langs, (list, tuple)) and preferred_langs:
        wanted = [_norm_lang(l) for l in preferred_langs if l]
        if wanted[0] != primary:
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
                sub_ext = "vtt" if ".vtt" in sub_url.lower() else ("srt" if ".srt" in sub_url.lower() else "srt")
                local_sub = os.path.join(temp_dir, f"{base}.{s['lang']}.{sub_ext}")
                try:
                    r = requests.get(sub_url, timeout=15, headers=headers or {}, verify=False)
                    if r.status_code == 200 and r.content and len(r.content) > 20:
                        with open(local_sub, "wb") as f:
                            f.write(r.content)
                        sub_paths.append(local_sub)
                    else:
                        sub_paths.append(sub_url)
                except Exception:
                    sub_paths.append(sub_url)
        except Exception:
            pass

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
                    if c and c not in langs:
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
                    sub_ext = str(s.get("ext") or "srt")
                    sub_path = os.path.join(temp_dir, f"{base}.{lang}.{sub_ext}")
                    with open(sub_path, "wb") as f:
                        f.write(s.get("content") or b"")
                    saved.append(sub_path)
                    if not include_all_subs:
                        break
                sub_paths.extend(saved)
        except Exception:
            pass

    return sub_paths


# ─── MPV argument builders ───────────────────────────────────────────

def _quality_to_ytdl_format(quality):
    """Convert a quality label ('1080p', '720p', '480p', '360p', '4k') to a
    yt-dlp / mpv --ytdl-format selector string.  Returns None for 'best'/'auto'."""
    if not quality or quality in ("auto", "best"):
        return None
    q = quality.lower().replace("p", "").strip()
    height_map = {"4k": 2160, "2160": 2160, "1080": 1080, "720": 720, "480": 480, "360": 360}
    height = height_map.get(q)
    if height is None:
        # Fallback: try parsing raw number
        try:
            height = int(q)
        except ValueError:
            return None
    # Prefer video stream with height <= desired; accept any audio; fall back to best
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"


def _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=False, quality=None):
    """Build mpv command-line arguments."""
    args = [
        "mpv",
        url,
        f"--title={title}",
        "--fs",
        "--force-window=immediate",
        "--keep-open=yes",
        "--network-timeout=60",
        # Robust streaming and reconnection
        "--demuxer-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_delay_max=5",
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
        # Cache and buffering for stability
        "--cache=yes",
        "--demuxer-max-bytes=256M",
        "--demuxer-max-back-bytes=128M",
        "--demuxer-readahead-secs=30",
        "--hls-bitrate=max",
        "--cache-pause=yes",
        "--term-status-msg=STATUS: ${=time-pos} / ${=duration}",
    ]

    if start_time > 0:
        args.append(f"--start={start_time}")

    if use_ytdl and shutil.which("yt-dlp"):
        args.insert(1, "--ytdl")
        # Quality selection via yt-dlp: request a specific resolution when the user
        # chose something other than "best".  mpv passes this to yt-dlp as a format
        # selector so the HLS manifest variant is chosen before playback starts.
        fmt = _quality_to_ytdl_format(quality)
        if fmt:
            args.append(f"--ytdl-format={fmt}")
        if headers:
            header_list = []
            for k, v in headers.items():
                if "," not in str(v):
                    header_list.append(f"{k}: {v}")
            if header_list:
                args.append(f"--ytdl-raw-options=http-header-fields=\"{','.join(header_list)}\"")
    elif headers:
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


def _run_mpv(args):
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

    while True:
        line = process.stdout.readline()
        if not line:
            break
        if "STATUS:" in line:
            try:
                parts = line.split("STATUS:")[1].strip().split("/")
                if len(parts) >= 2:
                    p = float(parts[0].strip())
                    d = float(parts[1].strip())
                    if d > 0:
                        position = p
                        duration = d
            except Exception:
                pass

    process.wait()
    return {
        "position": position,
        "duration": duration,
        "finished": (duration > 0 and position > duration * 0.9),
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
    result = subprocess.run(args, capture_output=True, text=True)
    elapsed = time.time() - start
    return {
        "position": elapsed,
        "duration": elapsed,
        "finished": elapsed > 30,  # VLC doesn't expose easy position tracking
    }


# ─── Main play functions ─────────────────────────────────────────────

def play_stream(url, title, subtitles=None, headers=None, meta=None, start_time=0,
                preferred_sub_lang='ar', include_all_subs=True, preferred_langs=None,
                player='mpv', fallback_langs=None, quality=None):
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
        console.print(f"\n[bold red]No supported player found![/bold red]")
        console.print(f"[yellow]Install one of: mpv, VLC, or iina[/yellow]")
        console.print(f"[dim]mpv: https://mpv.io/installation/[/dim]")
        console.print(f"[dim]VLC: https://www.videolan.org/vlc/[/dim]")
        console.input("\nPress Enter to return...")
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

    # ── MPV path (with retry logic to fix instant-close) ──
    try:
        # Attempt 1: direct mpv without yt-dlp (most reliable for direct stream URLs).
        # Quality selection via --ytdl-format only applies when yt-dlp is active,
        # so pass quality=None for the direct attempt; it is applied in the retry.
        mpv_args = _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=False)
        console.print(f"[dim]Launching mpv (direct mode)...[/dim]")
        stats = _run_mpv(mpv_args)

        # If mpv closed instantly (played < 3 sec, no duration detected), try with yt-dlp.
        # Pass the user's quality preference so the correct HLS variant is selected.
        if stats["duration"] == 0 and stats["position"] == 0 and shutil.which("yt-dlp"):
            console.print(f"[{WARNING}]Direct playback failed, retrying with yt-dlp...[/{WARNING}]")
            mpv_args = _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=True, quality=quality)
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
        console.print(f"\n[bold red]No supported player found![/bold red]")
        console.print(f"[yellow]Install one of: mpv, VLC, or iina[/yellow]")
        console.input("\nPress Enter to return...")
        return

    # Check if local file exists
    if not url.startswith("http") and not os.path.exists(url):
        console.print(f"\n[bold red]Error: File not found at {url}[/bold red]")
        console.input("\nPress Enter to return...")
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
            subprocess.run([
                "mpv", url,
                f"--title={title}",
                "--fs",
                "--keep-open=yes",
                f"--slang={preferred_sub_lang},ar,ara,arabic,en,eng,fr,fra,es,spa",
                "--sub-auto=exact",
            ], check=False)
    except Exception as e:
        console.print(f"\n[bold red]Failed to launch player:[/bold red] {e}")
        time.sleep(3)
