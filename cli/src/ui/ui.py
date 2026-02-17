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
from prompt_toolkit.layout import Layout as PTLayout
from prompt_toolkit.layout import VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from rich import box
from rich.align import Align
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from src.config import (
    ACCENT,
    BACKEND_URL,
    BG,
    PRIMARY,
    SECONDARY,
    SUCCESS,
    TEXT,
    WARNING,
    console,
)


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_header(subtitle=""):
    clear()
    title = Text("🎬 CINEMA CLI", style=f"bold {PRIMARY}")
    if subtitle:
        title.append(f" | {subtitle}", style=f"italic {ACCENT}")

    console.print(Panel(Align.center(title), border_style=PRIMARY, box=box.DOUBLE))
    console.print()


def show_splash():
    clear()
    art = f"""
[bold {PRIMARY}]
 ██████╗██╗███╗   ██╗███████╗███████╗███╗   ███╗ █████╗      ██████╗██╗     ██╗
██╔════╝██║████╗  ██║██╔════╝██╔════╝████╗ ████║██╔══██╗    ██╔════╝██║     ██║
██║     ██║██╔██╗ ██║█████╗  ███████╗██╔████╔██║███████║    ██║     ██║     ██║
██║     ██║██║╚██╗██║██╔══╝  ██╔════╝██║╚██╔╝██║██╔══██║    ██║     ██║     ██║
╚██████╗██║██║ ╚████║███████╗███████╗██║ ╚═╝ ██║██║  ██║    ╚██████╗███████╗██║
 ╚═════╝╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝
[/bold {PRIMARY}]
[italic {ACCENT}]      Elevate Your Movie Experience - v2.0.0[/italic {ACCENT}]
[dim]   Enhanced CLI with smart search, batch downloads, and more[/dim]
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
        SpinnerColumn(spinner_name="dots", style=ACCENT),
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
        f"[bold {TEXT}]{title}[/bold {TEXT}] ({year}) | ⭐ {rating:.1f} | {media_type}"
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

    def get_formatted_text():
        res = []
        res.append(("class:title", f" {title} \n"))
        res.append(("class:border", "─" * 60 + "\n"))

        for i in range(len(items)):
            item = items[i]
            display = formatter(item) if formatter else format_item(item)
            # Strip rich tags for prompt_toolkit display or use HTML
            clean_display = display.replace(f"[bold {TEXT}]", "").replace(
                f"[/bold {TEXT}]", ""
            )

            if i == selected_index:
                res.append(("class:selected", f" ▶ {clean_display} \n"))
            else:
                res.append(("class:item", f"   {clean_display} \n"))

        res.append(("class:border", "─" * 60 + "\n"))
        res.append(
            (
                "class:help",
                " [↑/↓] Navigate  [Enter] Select  [D] Batch Download  [F] Favorite  [B] Back  [Q] Quit ",
            )
        )
        return res

    def get_details_text():
        if not show_details or not items:
            return ""

        item = items[selected_index]
        overview = item.get("overview", "No description available.")

        # Wrap overview text at 50 characters for better readability
        def wrap_text(text, width=50):
            import textwrap

            return "\n".join(textwrap.wrap(text, width=width))

        overview = wrap_text(overview)

        rating = item.get("vote_average", 0)
        votes = item.get("vote_count", 0)
        popularity = item.get("popularity", 0)

        title_text = html.escape(str(item.get("title") or item.get("name")))
        overview_text = html.escape(overview)

        details = f"\n<title> {title_text} </title>\n"
        details += f"<border>{'━' * 50}</border>\n"
        details += f"<rating>⭐ Rating: {rating:.1f}/10 ({votes} votes)</rating>\n"
        details += f"<pop>🔥 Popularity: {popularity:.0f}</pop>\n\n"
        details += f"<overview>{overview_text}</overview>\n"

        return HTML(details)

    style = Style.from_dict(
        {
            "title": f"bold {PRIMARY}",
            "border": f"{PRIMARY}",
            "selected": f"bg:{PRIMARY} fg:#ffffff bold",
            "item": f"{TEXT}",
            "help": f"italic {ACCENT}",
            "title": f"bold {ACCENT}",
            "rating": f"{WARNING}",
            "pop": f"{SUCCESS}",
            "overview": f"{TEXT}",
        }
    )

    # Layout with details on the right
    body = VSplit(
        [
            Window(content=FormattedTextControl(get_formatted_text), width=60),
            Window(content=FormattedTextControl(get_details_text)),
        ],
        padding=2,
    )

    app = Application(
        layout=PTLayout(body), key_bindings=kb, style=style, full_screen=False
    )
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

    def get_formatted_text():
        res = []
        res.append(("class:title", f" {title} \n"))
        res.append(("class:border", "─" * 60 + "\n"))

        for i in range(len(items)):
            item = items[i]
            display = formatter(item) if formatter else format_item(item)
            clean_display = display.replace(f"[bold {TEXT}]", "").replace(
                f"[/bold {TEXT}]", ""
            )

            checkbox = " [x]" if i in checked_indices else " [ ]"
            prefix = " ▶" if i == selected_index else "  "

            if i == selected_index:
                res.append(("class:selected", f"{prefix}{checkbox} {clean_display} \n"))
            else:
                res.append(("class:item", f"{prefix}{checkbox} {clean_display} \n"))

        res.append(("class:border", "─" * 60 + "\n"))
        res.append(
            (
                "class:help",
                " [↑/↓] Navigate  [Space] Toggle  [A] Select All  [Enter] Confirm  [B/Q] Back ",
            )
        )
        return res

    style = Style.from_dict(
        {
            "title": f"bold {PRIMARY}",
            "border": f"{PRIMARY}",
            "selected": f"bg:{PRIMARY} fg:#ffffff bold",
            "item": f"{TEXT}",
            "help": f"italic {ACCENT}",
        }
    )

    app = Application(
        layout=PTLayout(Window(FormattedTextControl(get_formatted_text))),
        key_bindings=kb,
        style=style,
        full_screen=False,
    )
    return app.run()
