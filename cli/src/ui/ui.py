import atexit
import html
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout as PTLayout, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from rich import box
from rich.align import Align
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from src.config import console, BACKEND_URL # Keep backend url and console
from src.ui.theme import theme # Import the singleton theme manager


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_header(subtitle=""):
    clear()
    title = Text("🎬 CINEMA CLI", style=f"bold {theme.primary}")
    if subtitle:
        title.append(f" | {subtitle}", style=f"italic {theme.accent}")

    # Use a wider panel for a more "premium" look
    panel = Panel(
        Align.center(title), 
        border_style=theme.primary, 
        box=box.HEAVY_EDGE, 
        expand=False, 
        padding=(1, 10),
        subtitle=f"[dim]Premium Edition[/dim]",
        subtitle_align="right"
    )
    console.print(Align.center(panel))
    console.print()


def show_splash():
    clear()
    art = f"""
    [bold {theme.primary}]
    ██████╗██╗███╗   ██╗███████╗███████╗███╗   ███╗ █████╗      ██████╗██╗     ██╗
    ██╔════╝██║████╗  ██║██╔════╝██╔════╝████╗ ████║██╔══██╗    ██╔════╝██║     ██║
    ██║     ██║██╔██╗ ██║█████╗  ███████╗██╔████╔██║███████║    ██║     ██║     ██║
    ██║     ██║██║╚██╗██║██╔══╝  ██╔════╝██║╚██╔╝██║██╔══██║    ██║     ██║     ██║
    ╚██████╗██║██║ ╚████║███████╗███████╗██║ ╚═╝ ██║██║  ██║    ╚██████╗███████╗██║
    ╚═════╝╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝
    [/bold {theme.primary}]
    [bold {theme.accent}]              PREMIUM EDITION - v1.1.0[/bold {theme.accent}]
    [italic {theme.secondary}]          Elevate Your Movie Experience[/italic {theme.secondary}]
    """
    console.print(Align.center(art))

    # Ensure local backend is running (for localhost BACKEND_URL)
    def _is_backend_running(url: str) -> bool:
        try:
            req = Request(
                url.rstrip("/") + "/", headers={"User-Agent": "cinema-cli/1.0"}
            )
            with urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except (URLError, HTTPError, ValueError):
            return False

    def _maybe_start_backend(url: str):
        try:
            host = url.split("://")[-1].split(":")[0]
        except Exception:
            host = ""

        if host not in ("localhost", "127.0.0.1", ""):
            return None

        if _is_backend_running(url):
            return None

        backend_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "backend")
        )
        show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"
        stdout = None if show_logs else subprocess.DEVNULL
        stderr = None if show_logs else subprocess.DEVNULL

        try:
            proc = subprocess.Popen(
                "npm start", cwd=backend_dir, shell=True, stdout=stdout, stderr=stderr
            )
        except Exception:
            try:
                proc = subprocess.Popen(
                    ["node", "index.js"], cwd=backend_dir, stdout=stdout, stderr=stderr
                )
            except Exception:
                return None

        # wait briefly for server to come up
        for _ in range(10):
            if _is_backend_running(url):
                return proc
            time.sleep(0.5)

        return proc

    # Attempt auto-start; keep process reference to cleanup later
    _backend_proc = _maybe_start_backend(BACKEND_URL)
    if _backend_proc:
        atexit.register(
            lambda: (
                _backend_proc.terminate()
                if _backend_proc and _backend_proc.poll() is None
                else None
            )
        )

    with Progress(
        SpinnerColumn(spinner_name="dots", style=theme.accent),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(description="Initializing engine...", total=None)
        time.sleep(1.5)
        progress.add_task(description="Loading favorites...", total=None)
        time.sleep(0.5)
        progress.add_task(description="Ready!", total=None)
        time.sleep(0.5)


def format_item(item):
    title = item.get("title") or item.get("name", "Unknown")
    date = item.get("release_date") or item.get("first_air_date", "????-??-??")
    year = date[:4]
    media_type = (
        "Movie" if "title" in item or item.get("media_type") == "movie" else "TV"
    )
    rating = item.get("vote_average", 0)
    return (
        f"[bold {theme.text}]{title}[/bold {theme.text}] ({year}) | ⭐ {rating:.1f} | {media_type}"
    )


def selection_menu(items, title, show_details=True, formatter=None, default_index=0):
    if not items:
        return None

    clear()
    selected_index = default_index
    if selected_index < 0 or selected_index >= len(items):
        selected_index = 0

    result = {"action": None, "value": None}
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(items)

    @kb.add("down")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(items)

    @kb.add("enter")
    def _(event):
        result["action"] = "select"
        result["value"] = items[selected_index]
        event.app.exit()

    @kb.add("b")
    def _(event):
        result["action"] = "back"
        event.app.exit()

    @kb.add("q")
    def _(event):
        result["action"] = "quit"
        event.app.exit()

    @kb.add("f")
    def _(event):
        result["action"] = "favorite"
        result["value"] = items[selected_index]
        event.app.exit()

    @kb.add("d")
    def _(event):
        result["action"] = "batch"
        event.app.exit()

    def get_header_text():
        # Premium-style header bar
        return [
            (
                "class:header_title",
                f"  🎬  CINEMA CLI  •  {title}  •  Premium Browse  ",
            )
        ]

    def get_list_text():
        res = []
        start = max(0, selected_index - 10)
        end = min(len(items), start + 20)
        if end - start < 20:
            start = max(0, end - 20)

        for i in range(start, end):
            item = items[i]
            display = formatter(item) if formatter else format_item(item)
            clean_display = display.replace(f"[bold {theme.text}]", "").replace(
                f"[/bold {theme.text}]", ""
            )

            if i == selected_index:
                res.append(("class:selected", f" ▶ {clean_display} \n"))
            else:
                res.append(("class:item", f"   {clean_display} \n"))
        return res

    def get_details_text():
        if not show_details or not items:
            return ""

        item = items[selected_index]
        overview = item.get("overview", "No description available.")

        def wrap_text(text, width=60):
            import textwrap
            return "\n".join(textwrap.wrap(text, width=width))

        overview = wrap_text(overview)
        rating = item.get("vote_average", 0)
        votes = item.get("vote_count", 0)
        popularity = item.get("popularity", 0)

        title_text = html.escape(str(item.get("title") or item.get("name")))
        overview_text = html.escape(overview)

        details = f"\n<title> {title_text} </title>\n"
        details += f"<border>{'━' * 60}</border>\n"
        details += f"<rating>⭐ Rating: {rating:.1f}/10 ({votes} votes)</rating>\n"
        details += f"<pop>🔥 Popularity: {popularity:.0f}</pop>\n\n"
        details += f"<overview>{overview_text}</overview>\n"

        return HTML(details)

    def get_footer_text():
        # Premium key-hint ribbon
        return [
            ("class:footer_key", " [↑/↓] "),
            ("class:footer_text", "Navigate "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [Enter] "),
            ("class:footer_text", "Play / Open "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [D] "),
            ("class:footer_text", "Batch Queue "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [F] "),
            ("class:footer_text", "Favorite "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [B] "),
            ("class:footer_text", "Back "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [Q] "),
            ("class:footer_text", "Quit "),
        ]

    style = Style.from_dict(
        {
            "header_title": f"bold {theme.text} bg:{theme.primary}",
            "footer": f"bg:{theme.bg}",
            "footer_key": f"bold {theme.accent}",
            "footer_text": f"{theme.text}",
            "footer_sep": "fg:#555555",
            "selected": f"bg:{theme.accent} fg:#000000 bold",
            "item": f"{theme.text}",
            "rating": f"bold {theme.warning}",
            "pop": f"bold {theme.success}",
            "overview": f"{theme.text}",
            "title": f"bold {theme.secondary} underline",
            "border": f"dim {theme.primary}",
        }
    )

    from prompt_toolkit.layout.containers import HSplit, VSplit, Window, FloatContainer
    from prompt_toolkit.layout.controls import FormattedTextControl

    layout = PTLayout(
        HSplit([
            Window(content=FormattedTextControl(get_header_text), height=1, style="class:header_title"),
            VSplit([
                Window(content=FormattedTextControl(get_list_text), width=70, ignore_content_width=True),
                Window(content=FormattedTextControl(get_details_text), ignore_content_width=True),
            ], padding=2),
            Window(content=FormattedTextControl(get_footer_text), height=1, style="class:footer"),
        ])
    )

    app = Application(layout=layout, key_bindings=kb, style=style, full_screen=True)
    app.run()
    return result


def multi_selection_menu(items, title, formatter=None):
    if not items:
        return []

    clear()
    selected_index = 0
    checked_indices = set()
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(items)

    @kb.add("down")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(items)

    @kb.add("space")
    def _(event):
        if selected_index in checked_indices:
            checked_indices.remove(selected_index)
        else:
            checked_indices.add(selected_index)

    @kb.add("a")
    def _(event):
        if len(checked_indices) == len(items):
            checked_indices.clear()
        else:
            for i in range(len(items)):
                checked_indices.add(i)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=[items[i] for i in sorted(list(checked_indices))])

    @kb.add("b")
    @kb.add("q")
    def _(event):
        event.app.exit(result=[])

    def get_header_text():
        return [
            (
                "class:header_title",
                f"  🎬  CINEMA CLI  •  {title}  •  Batch Select  ",
            )
        ]

    def get_list_text():
        res = []
        start = max(0, selected_index - 10)
        end = min(len(items), start + 20)
        if end - start < 20:
            start = max(0, end - 20)

        for i in range(start, end):
            item = items[i]
            display = formatter(item) if formatter else format_item(item)
            clean_display = display.replace(f"[bold {theme.text}]", "").replace(
                f"[/bold {theme.text}]", ""
            )

            checkbox = " [x]" if i in checked_indices else " [ ]"
            prefix = " ▶" if i == selected_index else "  "

            if i == selected_index:
                res.append(("class:selected", f"{prefix}{checkbox} {clean_display} \n"))
            else:
                res.append(("class:item", f"{prefix}{checkbox} {clean_display} \n"))
        return res

    def get_footer_text():
        return [
            ("class:footer_key", " [↑/↓] "),
            ("class:footer_text", "Navigate "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [Space] "),
            ("class:footer_text", "Toggle Episode "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [A] "),
            ("class:footer_text", "Select All "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [Enter] "),
            ("class:footer_text", "Confirm Batch "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [B/Q] "),
            ("class:footer_text", "Back / Quit "),
        ]

    style = Style.from_dict(
        {
            "header_title": f"bold {theme.text} bg:{theme.primary}",
            "footer": f"bg:{theme.bg}",
            "footer_key": f"bold {theme.accent}",
            "footer_text": f"{theme.text}",
            "footer_sep": "fg:#555555",
            "selected": f"bg:{theme.accent} fg:#000000 bold",
            "item": f"{theme.text}",
            "border": f"dim {theme.primary}",
        }
    )

    layout = PTLayout(
        HSplit([
            Window(content=FormattedTextControl(get_header_text), height=1, style="class:header_title"),
            Window(content=FormattedTextControl(get_list_text)),
            Window(content=FormattedTextControl(get_footer_text), height=1, style="class:footer"),
        ])
    )

    app = Application(layout=layout, key_bindings=kb, style=style, full_screen=True)
    return app.run()


def post_playback_menu(seconds=10, title="Finished Watching"):
    """
    Shows a 'Finished Watching' menu with a countdown for the next episode.
    Returns: 'next', 'prev', 'replay', or 'back'
    """
    import threading

    result = {"action": "next"} # Default action if timeout
    start_time = time.time()
    selected_index = 0
    options = [
        {"name": "Next Episode", "val": "next"},
        {"name": "Previous Episode", "val": "prev"},
        {"name": "Replay", "val": "replay"},
        {"name": "Back to List", "val": "back"}
    ]

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(options)

    @kb.add("down")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(options)

    @kb.add("enter")
    def _(event):
        result["action"] = options[selected_index]["val"]
        event.app.exit()

    @kb.add("escape")
    @kb.add("q")
    @kb.add("b")
    def _(event):
        result["action"] = "back"
        event.app.exit()

    def get_text():
        elapsed = time.time() - start_time
        remaining = int(seconds - elapsed)
        
        res = []
        res.append(("", "\n")) # Top padding
        
        for i, opt in enumerate(options):
            prefix = " ▶ " if i == selected_index else "   "
            line = f"{prefix}{opt['name']}"
            if opt["val"] == "next" and remaining > 0:
                line += f" ({remaining}s)"
            
            if i == selected_index:
                res.append(("class:selected", f"{line}\n"))
            else:
                res.append(("class:item", f"{line}\n"))

        res.append(("", "\n"))
        res.append(
            (
                "class:help",
                "   Enter: confirm • B / Q / Esc: back • Auto-advances when the timer hits 0\n",
            )
        )
        res.append(("", "\n"))  # Bottom padding
        return res

    style = Style.from_dict(
        {
            "frame.border": f"{theme.primary}",
            "frame.label": f"bold {theme.accent}",
            "selected": f"bg:{theme.accent} fg:#000000 bold",
            "item": f"{theme.text}",
            "help": f"italic {theme.text} fg:#888888",
        }
    )

    from prompt_toolkit.layout.containers import Window, Float, FloatContainer
    from prompt_toolkit.widgets import Frame

    menu_content = Window(FormattedTextControl(get_text), height=len(options) + 2, width=40)
    
    frame = Frame(
        menu_content,
        title=f"{title} - Select Next Action"
    )

    # Use PTLayout for consistency with the rest of the UI
    layout = PTLayout(
        FloatContainer(
            content=Window(),
            floats=[
                Float(content=frame)
            ]
        )
    )

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
        refresh_interval=0.5
    )

    def auto_next():
        while app.is_running:
            if (time.time() - start_time) >= seconds:
                if app.is_running:
                     app.exit()
                break
            time.sleep(0.5)

    t = threading.Thread(target=auto_next, daemon=True)
    t.start()

    app.run()
    return result["action"]




def download_menu(manager):
    """
    Specific menu for downloads with auto-refresh.
    """
    selected_index = 0
    items = []
    
    def get_items():
        return manager.get_queue()

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        nonlocal selected_index
        if items:
            selected_index = (selected_index - 1) % len(items)

    @kb.add("down")
    def _(event):
        nonlocal selected_index
        if items:
            selected_index = (selected_index + 1) % len(items)

    @kb.add("q")
    @kb.add("escape")  
    def _(event):
        event.app.exit(result=None)

    @kb.add("enter")
    def _(event):
        if items and 0 <= selected_index < len(items):
             event.app.exit(result=items[selected_index])

    @kb.add("c") # Clear completed
    def _(event):
        manager.clear_completed()
        
    @kb.add("r") # Refresh UI manually
    def _(event):
        event.app.invalidate()
        
    def get_header_text():
        # Aggregated stats
        nonlocal items
        items = get_items()
        active = sum(1 for x in items if x["status"] == "downloading")
        completed = sum(1 for x in items if x["status"] == "completed")
        return [
            (
                "class:header_title",
                f"  🎬  CINEMA CLI  •  Premium Download Manager  •  🚀 {active} Active  •  ✅ {completed} Done  ",
            )
        ]

    def get_list_text():
        nonlocal items
        items = get_items() 
        
        nonlocal selected_index
        if not items:
            selected_index = 0
            empty_message = (
                " \n"
                "   🚫 No active downloads right now.\n"
                "   Tip: Queue single episodes or whole seasons from the browse screens.\n"
                "   Press [Q] to return to the main menu.\n"
            )
            return [("class:item", empty_message)]
            
        if selected_index >= len(items):
            selected_index = len(items) - 1
            
        res = []
        for i, item in enumerate(items):
            style = "class:selected" if i == selected_index else ""
            prefix = " ▶ " if i == selected_index else "   "
            
            status = item.get("status", "unknown")
            # Map status to icon
            icon = "⏳" # default
            if status == "pending": icon = "⏳"
            elif status == "downloading": icon = "🚀"
            elif status == "processing": icon = "⚙️ "
            elif status == "completed": icon = "✅"
            elif status == "error": icon = "❌"
            
            progress = item.get("progress", 0)
            bar_len = 20
            filled = int(progress / 100 * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            
            # Title handling
            title = item.get("title") or item.get("filename", "Unknown")
            if len(title) > 35: title = title[:32] + "..."
            
            # Column definitions (fixed widths)
            # Prefix+Icon (5) | Title (35) | Progress (8) | Bar (22) | Speed (12) | ETA (10)
            col_title = f"{title:<35}"
            col_progress = f"{progress:>5.1f}%"
            col_speed = f"{item.get('speed', '0 B/s'):>12}"
            col_eta = f"ETA: {item.get('eta', '00:00'):>5}"
            
            line = f"{prefix}{icon} {col_title} | {col_progress} | {bar} | {col_speed} | {col_eta}\n"
            
            s_style = style
            if i != selected_index:
                if status == "completed": s_style = "class:completed"
                elif status == "downloading": s_style = "class:downloading"
                elif status == "processing": s_style = "class:processing"
                elif status == "error": s_style = "class:error"
                else: s_style = "class:item"

            res.append((s_style, line))
        return res

    def get_footer_text():
        return [
            ("class:footer_key", " [↑/↓] "),
            ("class:footer_text", "Navigate "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [Enter] "),
            ("class:footer_text", "Inspect Task "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [C] "),
            ("class:footer_text", "Clear Completed "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [R] "),
            ("class:footer_text", "Refresh "),
            ("class:footer_sep", " │ "),
            ("class:footer_key", " [Q] "),
            ("class:footer_text", "Back "),
        ]

    layout = HSplit([
        Window(content=FormattedTextControl(get_header_text), height=1, style="class:header_title"),
        Window(height=1),
        Window(content=FormattedTextControl(get_list_text, focusable=True), wrap_lines=False),
        Window(height=1),
        Window(content=FormattedTextControl(get_footer_text), height=1, style="class:footer")
    ])

    style_dict = {
        "header_title": f"bold {theme.text} bg:{theme.primary}",
        "footer": f"bg:{theme.bg}",
        "footer_key": f"bold {theme.accent}",
        "footer_text": f"{theme.text}",
        "footer_sep": "fg:#555555",
        "selected": f"bg:{theme.accent} fg:#000000 bold",
        "item": f"{theme.text}",
        "completed": f"fg:{theme.success}",
        "downloading": f"fg:{theme.accent} bold",
        "processing": f"fg:{theme.warning} italic",
        "error": f"fg:{theme.warning} bold",
    }
    
    app = Application(
        layout=PTLayout(layout, focused_element=layout.children[2]),
        key_bindings=kb,
        style=Style.from_dict(style_dict),
        full_screen=True,
        refresh_interval=0.5
    )
    return app.run()
