import shutil
import subprocess
import time
import os

from rich.align import Align
from rich.panel import Panel
from src.config import SUCCESS, console
from src.ui.ui import clear
from src.utils.subtitles import fetch_subtitle


def play_stream(url, title, subtitles=None, headers=None, meta=None, start_time=0, sub_files=None, sub_file=None):
    """
    Plays a stream using mpv.
    Returns a dict with playback stats: {position, duration, finished}
    """
    if sub_files is None:
        sub_files = []
    if sub_file:
        sub_files.append(sub_file)

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
        "--fs",
        "--force-window=immediate",
        "--network-timeout=60",
        "--slang=ar,ara,arabic,en,eng,fr,fre,french",
        "--sub-auto=exact",
        "--hwdec=auto",
    ]

    # Add multiple subtitle files
    for sf in sub_files:
        if sf and os.path.exists(sf):
            mpv_args.append(f"--sub-file={sf}")

    if start_time > 0:
        mpv_args.append(f"--start={start_time}")
        
    # Enable status message for progress tracking
    mpv_args.extend(["--term-status-msg=STATUS: ${=time-pos} / ${=duration}"])

    # Handle headers and yt-dlp
    if shutil.which("yt-dlp"):
        mpv_args.insert(1, "--ytdl")
        
        if headers:
            header_list = []
            for key, value in headers.items():
                if "," not in str(value):
                    header_list.append(f"{key}: {value}")
            if header_list:
                mpv_args.append(f"--ytdl-raw-options=http-header-fields={','.join(header_list)}")
    elif headers:
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            mpv_args.append(f"--user-agent={ua}")
        ref = headers.get("Referer") or headers.get("referer")
        if ref:
            mpv_args.append(f"--referrer={ref}")

    # Fallback to internal download/fetch logic if NO sub_files passed
    if not sub_files and subtitles:
        from src.services.subtitles import SubtitleManager
        sub_manager = SubtitleManager()
        
        # Get all preferred subtitles
        sub_paths = sub_manager.get_subtitles(title, subtitles, match_data=meta or {})
        if sub_paths:
            sub_files.extend(sub_paths)

    # Add multiple subtitle files
    for sf in sub_files:
        if sf and os.path.exists(sf):
            mpv_args.append(f"--sub-file={sf}")
                
    # Select default language
    from src.core.settings import SettingsManager
    settings = SettingsManager()
    default_lang = settings.default_subtitle_language
    if default_lang:
        mpv_args.append(f"--slang={default_lang},ar,en,fr")

    # Launch Process
    try:
        process = subprocess.Popen(
            mpv_args,
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
                except:
                    pass

        process.wait()

        return {
            "position": position,
            "duration": duration,
            "finished": (duration > 0 and position > duration * 0.98),
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
