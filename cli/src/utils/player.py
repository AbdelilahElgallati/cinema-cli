import os
import shutil
import subprocess
import time
import requests
import urllib3

from rich.align import Align
from rich.panel import Panel
from src.config import SUCCESS, ACCENT, WARNING, console
from src.ui.ui import clear
from src.utils.subtitles import fetch_arabic_subtitle

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
    return l or "und"


def _prepare_subtitles(title, subtitles, headers, meta, preferred_sub_lang, include_all_subs):
    """Download / collect subtitle paths. Returns list of local file paths or URLs."""
    sub_paths = []
    preferred = _norm_lang(preferred_sub_lang or "ar")

    if subtitles:
        items = []
        for s in subtitles:
            if isinstance(s, dict) and s.get("url"):
                items.append({"lang": _norm_lang(s.get("lang") or s.get("language")), "url": s.get("url")})

        preferred_items = [x for x in items if x["lang"] == preferred]
        if not preferred_items and preferred != "ar":
            preferred_items = [x for x in items if x["lang"] == "ar"]
        ordered = []
        if preferred_items:
            ordered.append(preferred_items[0])

        if include_all_subs:
            seen = {x["url"] for x in ordered}
            seen_lang = {x["lang"] for x in ordered}
            for x in items:
                if x["url"] in seen or x["lang"] in seen_lang:
                    continue
                ordered.append(x)
                seen.add(x["url"])
                seen_lang.add(x["lang"])

        try:
            temp_dir = os.path.join(os.getcwd(), ".download_temp")
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

    # Fallback: fetch Arabic from OpenSubtitles
    if not sub_paths:
        try:
            temp_dir = os.path.join(os.getcwd(), ".download_temp")
            os.makedirs(temp_dir, exist_ok=True)
            yr = sn = epn = None
            if isinstance(meta, dict):
                yr = meta.get("year")
                sn = meta.get("season")
                epn = meta.get("episode")
            res = fetch_arabic_subtitle(title, year=yr, season=sn, episode=epn)
            if res:
                content, sub_ext = res
                base = "".join(c for c in title if c.isalnum() or c in " _-").strip().replace(" ", "_")
                sub_path = os.path.join(temp_dir, f"{base}.ar.{sub_ext}")
                with open(sub_path, "wb") as f:
                    f.write(content)
                sub_paths.append(sub_path)
        except Exception:
            pass

    return sub_paths


# ─── MPV argument builders ───────────────────────────────────────────

def _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=False):
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
        "--sub-fix-timing=yes",
        "--sub-use-margins=yes",
        "--sub-ass-override=force",
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
    if sub_paths:
        args.extend([
            "--sub-delay=0",
            "--audio-delay=0",
        ])

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
                preferred_sub_lang='ar', include_all_subs=True, player='mpv'):
    """
    Plays a stream using the chosen player (mpv, vlc, or iina).
    Attempts yt-dlp via mpv first, then falls back to direct mpv, then VLC.
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
    sub_paths = _prepare_subtitles(title, subtitles, headers, meta, preferred_sub_lang, include_all_subs)

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
        # Attempt 1: direct mpv without yt-dlp (most reliable for direct stream URLs)
        mpv_args = _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=False)
        console.print(f"[dim]Launching mpv (direct mode)...[/dim]")
        stats = _run_mpv(mpv_args)

        # If mpv closed instantly (played < 3 sec, no duration detected), try with yt-dlp
        if stats["duration"] == 0 and stats["position"] == 0 and shutil.which("yt-dlp"):
            console.print(f"[{WARNING}]Direct playback failed, retrying with yt-dlp...[/{WARNING}]")
            mpv_args = _build_mpv_args(url, title, headers, sub_paths, preferred_sub_lang, start_time, use_ytdl=True)
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
