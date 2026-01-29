# import shutil
# import subprocess
# import time
# from rich.panel import Panel
# from rich.align import Align
# from src.config import console, SUCCESS
# from src.ui.ui import clear

# def play_stream(url, title, subtitles=None, headers=None):
#     clear()
#     console.print(Panel(Align.center(f"[bold {SUCCESS}]Starting Player: {title}[/bold {SUCCESS}]\n\n[dim]{url}[/dim]"), title="MPV Player", border_style=SUCCESS))
#     if shutil.which("mpv") is None:
#         console.print(f"\n[bold red]MPV not found![/bold red]")
#         console.input("\nPress Enter to return...")
#         return
#     mpv_args = ["mpv", url, f"--title={title}", "--fs", "--force-window=immediate", "--network-timeout=60", "--slang=ar,ara,arabic,en,eng"]
#     if headers:
#         ua = headers.get('User-Agent') or headers.get('user-agent')
#         if ua: mpv_args.append(f"--user-agent={ua}")
#         ref = headers.get('Referer') or headers.get('referer')
#         if ref: mpv_args.append(f"--referrer={ref}")
#     if subtitles:
#         ar = [s for s in subtitles if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
#         if ar:
#             mpv_args.append(f"--sub-file={ar[0]['url']}")
#     try:
#         subprocess.run(mpv_args, check=False)
#     except Exception as e:
#         console.print(f"[red]Error: {e}[/red]")
#         time.sleep(2)

# def play_video(url, title):
#     clear()
#     console.print(Panel(
#         Align.center(f"[bold {SUCCESS}]Starting Player: {title}[/bold {SUCCESS}]\n\n[dim]{url}[/dim]"),
#         title="MPV Player",
#         border_style=SUCCESS
#     ))
#     try:
#         if shutil.which("mpv"):
#             subprocess.run(["mpv", url, f"--title={title}", "--fs"], check=False)
#         else:
#             console.print(f"\n[bold red]MPV not found![/bold red]")
#             console.input("\nPress Enter to return...")
#     except Exception as e:
#         console.print(f"[red]Error: {e}[/red]")
#         time.sleep(2)


import shutil
import subprocess
import time
from rich.panel import Panel
from rich.align import Align
from src.config import console, SUCCESS
from src.ui.ui import clear

def play_stream(url, title, subtitles=None, headers=None):
    """
    Plays a stream using mpv. It attempts to use yt-dlp as a stream source
    if available, as it handles complex stream URLs and headers better.
    """
    clear()
    console.print(Panel(Align.center(f"[bold {SUCCESS}]Starting Player: {title}[/bold {SUCCESS}]\n\n[dim]{url}[/dim]"), title="MPV Player", border_style=SUCCESS))
    
    if shutil.which("mpv") is None:
        console.print(f"\n[bold red]MPV not found![/bold red]")
        console.input("\nPress Enter to return...")
        return

    mpv_args = ["mpv", url, f"--title={title}", "--fs", "--force-window=immediate", "--network-timeout=60", "--slang=ar,ara,arabic,en,eng"]
    
    # 1. Try to use yt-dlp as a stream source for better compatibility
    if shutil.which("yt-dlp"):
        mpv_args = ["mpv", "--ytdl", url, f"--title={title}", "--fs", "--force-window=immediate", "--network-timeout=60", "--slang=ar,ara,arabic,en,eng"]
        
        # yt-dlp handles headers better when passed via the --http-header-fields option
        if headers:
            header_list = []
            for key, value in headers.items():
                # mpv/yt-dlp can't handle headers with commas in values, so we skip them
                if ',' not in str(value):
                    header_list.append(f"{key}: {value}")
            
            if header_list:
                mpv_args.append(f"--ytdl-raw-options=http-header-fields={','.join(header_list)}")
    
    # 2. Fallback to direct mpv with headers if yt-dlp is not available or if the stream is not compatible with ytdl
    elif headers:
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
    # This function is for direct video links, no change needed here
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
