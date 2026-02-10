import shutil
import subprocess
import time
import os
import requests
import random

from rich.align import Align
from rich.panel import Panel
from src.config import SUCCESS, console
from src.ui.ui import clear
from src.utils.subtitles import fetch_subtitle


def play_stream(url, title, subtitles=None, headers=None, meta=None, start_time=0, settings=None):
    """
    Plays a stream using mpv. It attempts to use yt-dlp as a stream source
    if available, as it handles complex stream URLs and headers better.
    Returns a dict with playback stats: {position, duration, finished}
    """
    clear()
    console.print(
        Panel(
            Align.center(
                f"[bold {SUCCESS}]Starting Player: {title}[/bold {SUCCESS}]\n\n[dim]{url}[/dim]\n\n[white]Controls: q=Quit, Space=Pause, z/x=Sub Sync (-/+), j=Audio, v=Sub Visibility[/white]"
            ),
            title="MPV Player",
            border_style=SUCCESS,
        )
    )

    if shutil.which("mpv") is None:
        console.print(f"\n[bold red]MPV not found![/bold red]")
        console.input("\nPress Enter to return...")
        return None

    mpv_args = [
        "mpv",
        url,
        f"--title={title}",
        "--slang=ar,ara,arabic,en,eng,fr,fre,fra",
        "--sub-auto=exact",
        "--osc",
        "--input-default-bindings",
        "--input-vo-keyboard=yes",
    ]

    if start_time > 0:
        mpv_args.append(f"--start={start_time}")

    # Enable status message for progress tracking
    mpv_args.extend(["--term-status-msg=STATUS: ${=time-pos} / ${=duration}"])

    # 1. Try to use yt-dlp as a stream source for better compatibility
    if shutil.which("yt-dlp"):
        mpv_args = [
            "mpv",
            "--ytdl",
            url,
            f"--title={title}",
            "--force-window=immediate",
            "--network-timeout=60",
            "--slang=ar,ara,arabic,en,eng,fr,fre,fra",
            "--osc",
            "--input-default-bindings",
            "--input-vo-keyboard=yes",
        ]
        mpv_args.extend(["--term-status-msg=STATUS: ${=time-pos} / ${=duration}"])

        # yt-dlp handles headers better when passed via the --http-header-fields option
        # BUT passing them via ytdl-raw-options is tricky due to comma splitting.
        # We'll split them into specific options where possible.
        ytdl_opts = []
        
        if headers:
            # 1. Handle User-Agent
            ua = headers.get("User-Agent") or headers.get("user-agent")
            if ua:
                # Pass to mpv directly
                mpv_args.append(f"--user-agent={ua}")
                # Pass to yt-dlp
                # Note: commas in UA string might break ytdl-raw-options parsing
                if "," not in ua:
                    ytdl_opts.append(f"user-agent={ua}")

            # 2. Handle Referer
            ref = headers.get("Referer") or headers.get("referer")
            if ref:
                # Pass to mpv directly
                mpv_args.append(f"--referrer={ref}")
                # Pass to yt-dlp
                if "," not in ref:
                    ytdl_opts.append(f"referer={ref}")
            
            # 3. Handle other headers and pass them to mpv's --http-header-fields
            # mpv expects --http-header-fields="Key1: Val1,Key2: Val2"
            other_headers = []
            for k, v in headers.items():
                if k.lower() not in ["user-agent", "referer"]:
                    # Escape commas if any (simple approach: remove them or skip)
                    val_str = str(v)
                    if "," in val_str:
                        continue 
                    other_headers.append(f"{k}: {val_str}")

            if other_headers:
                mpv_args.append(f"--http-header-fields={','.join(other_headers)}")

        if ytdl_opts:
            mpv_args.append(f"--ytdl-raw-options={','.join(ytdl_opts)}")

    # 2. Fallback to direct mpv with headers if yt-dlp is not available or if the stream is not compatible with ytdl
    elif headers:
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            mpv_args.append(f"--user-agent={ua}")
        ref = headers.get("Referer") or headers.get("referer")
        if ref:
            mpv_args.append(f"--referrer={ref}")

    # Subtitle handling: Hybrid Sourcing (Provider + OpenSubtitles Fallback)
    
    # Define languages we want
    wanted_langs = {"ar": False, "en": False, "fr": False}
    lang_map = {
        "ar": "ar", "ara": "ar", "arabic": "ar",
        "en": "en", "eng": "en", "english": "en",
        "fr": "fr", "fre": "fr", "fra": "fr", "french": "fr"
    }

    temp_dir = os.path.join(os.path.expanduser("~"), ".cinema-cli", "temp")
    os.makedirs(temp_dir, exist_ok=True)

    # 1. Process Provider Subtitles
    if subtitles:
        unique_subs = []
        for s in subtitles:
            raw_lang = s.get("lang", "").lower()
            mapped_lang = lang_map.get(raw_lang)
            
            if mapped_lang and not wanted_langs[mapped_lang]:
                unique_subs.append(s)
                wanted_langs[mapped_lang] = True
            
            if all(wanted_langs.values()):
                break
        
        console.print(f"[dim]Processing {len(unique_subs)} provider subtitles...[/dim]")

        for i, sub in enumerate(unique_subs):
            try:
                sub_url = sub["url"]
                raw_lang = sub.get("lang", "unk").lower()
                lang_code = lang_map.get(raw_lang, raw_lang)
                
                sub_ext = "srt"
                if ".vtt" in sub_url:
                    sub_ext = "vtt"
                
                clean_title = (
                    "".join(c for c in title if c.isalnum() or c in " _-")
                    .strip()
                    .replace(" ", "_")
                )
                rand_id = random.randint(1000, 9999)
                local_filename = f"{clean_title}_{lang_code}_{rand_id}_{i}.{sub_ext}"
                local_path = os.path.join(temp_dir, local_filename)
                
                r = requests.get(sub_url, timeout=5)
                if r.status_code == 200 and r.content:
                    with open(local_path, "wb") as f:
                        f.write(r.content)
                    mpv_args.append(f"--sub-file={local_path}")
            except Exception as e:
                pass

    # 2. Fetch Missing Languages from OpenSubtitles
    yr = None
    sn = None
    epn = None
    if isinstance(meta, dict):
        yr = meta.get("year")
        sn = meta.get("season")
        epn = meta.get("episode")

    for lang_code, found in wanted_langs.items():
        if not found:
            # console.print(f"[dim]Fetching missing subtitle for {lang_code}...[/dim]")
            try:
                res = fetch_subtitle(title, year=yr, season=sn, episode=epn, language=lang_code)
                if res:
                    content, sub_ext = res
                    base = (
                        "".join(c for c in title if c.isalnum() or c in " _-")
                        .strip()
                        .replace(" ", "_")
                    )
                    rand_id = random.randint(1000, 9999)
                    sub_path = os.path.join(temp_dir, f"{base}_{lang_code}_{rand_id}_os.{sub_ext}")
                    with open(sub_path, "wb") as f:
                        f.write(content)
                    mpv_args.append(f"--sub-file={sub_path}")
                    # console.print(f"[dim green]Found external subtitle for {lang_code}[/dim green]")
            except:
                pass

    try:
        console.print(f"[dim]Running command: {' '.join(mpv_args)}[/dim]")
        
        process = subprocess.Popen(
            mpv_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout to avoid deadlock
            universal_newlines=True,
            encoding="utf-8",
            errors="ignore",
        )

        position = 0
        duration = 0

        # Read output in real-time
        while True:
            # Check if process is still running
            output = process.stdout.readline()

            if output == '' and process.poll() is not None:
                break
            
            if output:
                # Check for errors or status
                lower_out = output.lower()
                if "error" in lower_out or "failed" in lower_out:
                    console.print(f"[red]{output.strip()}[/red]")
                
                if "STATUS:" in output:
                    try:
                        parts = output.split("STATUS:")[1].strip().split("/")
                        if len(parts) >= 2:
                            p = float(parts[0].strip())
                            d = float(parts[1].strip())
                            if d > 0:
                                position = p
                                duration = d
                    except:
                        pass
                # Optional: print other mpv output for debug
                # else:
                #    console.print(f"[dim]{output.strip()}[/dim]")

        process.wait()

        if duration == 0:
            console.print("[bold red]Playback failed or finished immediately.[/bold red]")
            console.print(f"[dim]Stats: Position={position}, Duration={duration}[/dim]")
            console.input("Press Enter to continue...")

        return {
            "position": position,
            "duration": duration,
            "finished": (duration > 0 and position > duration * 0.9),
        }

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        time.sleep(2)
        return None


def play_video(url, title):
    # This function is for direct video links, no change needed here
    clear()
    console.print(
        Panel(
            Align.center(
                f"[bold {SUCCESS}]Starting Player: {title}[/bold {SUCCESS}]\n\n[dim]{url}[/dim]"
            ),
            title="MPV Player",
            border_style=SUCCESS,
        )
    )
    try:
        if shutil.which("mpv"):
            subprocess.run(["mpv", url, f"--title={title}", "--fs"], check=False)
        else:
            console.print(f"\n[bold red]MPV not found![/bold red]")
            console.input("\nPress Enter to return...")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        time.sleep(2)
