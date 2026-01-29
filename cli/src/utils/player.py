import shutil
import subprocess
import time
from rich.panel import Panel
from rich.align import Align
from src.config import console, SUCCESS
from src.ui.ui import clear

def play_stream(url, title, subtitles=None, headers=None):
    clear()
    console.print(Panel(Align.center(f"[bold {SUCCESS}]Starting Player: {title}[/bold {SUCCESS}]\n\n[dim]{url}[/dim]"), title="MPV Player", border_style=SUCCESS))
    if shutil.which("mpv") is None:
        console.print(f"\n[bold red]MPV not found![/bold red]")
        console.input("\nPress Enter to return...")
        return
    mpv_args = ["mpv", url, f"--title={title}", "--fs", "--force-window=immediate", "--network-timeout=60", "--slang=ar,ara,arabic,en,eng"]
    if headers:
        ua = headers.get('User-Agent') or headers.get('user-agent')
        if ua: mpv_args.append(f"--user-agent={ua}")
        ref = headers.get('Referer') or headers.get('referer')
        if ref: mpv_args.append(f"--referrer={ref}")
    if subtitles:
        ar = [s for s in subtitles if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
        if ar:
            mpv_args.append(f"--sub-file={ar[0]['url']}")
    try:
        subprocess.run(mpv_args, check=False)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        time.sleep(2)

def play_video(url, title):
    clear()
    console.print(Panel(
        Align.center(f"[bold {SUCCESS}]Starting Player: {title}[/bold {SUCCESS}]\n\n[dim]{url}[/dim]"),
        title="MPV Player",
        border_style=SUCCESS
    ))
    try:
        if shutil.which("mpv"):
            subprocess.run(["mpv", url, f"--title={title}", "--fs"], check=False)
        else:
            console.print(f"\n[bold red]MPV not found![/bold red]")
            console.input("\nPress Enter to return...")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        time.sleep(2)
