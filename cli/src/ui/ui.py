import atexit
import html
import os
import re
import subprocess
import sys
import textwrap
import time
from urllib.parse import urlparse
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
from rich.rule import Rule
from rich.text import Text
from src.config import (
    ACCENT,
    APP_VERSION,
    BACKEND_URL,
    BG,
    PRIMARY,
    SECONDARY,
    SETTINGS_FILE,
    SUCCESS,
    TEXT,
    WARNING,
    console,
)
from src.utils.storage import load_json_data



# ─── ASCII art ─────────────────────────────────────────────────────────────────

_CINEMA_ART = (
    "  ██████╗██╗███╗   ██╗███████╗███╗   ███╗  █████╗      ██████╗██╗     ██╗ \n"
    " ██╔════╝██║████╗  ██║██╔════╝████╗ ████║ ██╔══██╗    ██╔════╝██║     ██║ \n"
    " ██║     ██║██╔██╗ ██║█████╗  ██╔████╔██║ ███████║    ██║     ██║     ██║ \n"
    " ██║     ██║██║╚██╗██║██╔══╝  ██║╚██╔╝██║ ██╔══██║    ██║     ██║     ██║ \n"
    " ╚██████╗██║██║ ╚████║███████╗██║ ╚═╝ ██║ ██║  ██║    ╚██████╗███████╗██║ \n"
    "  ╚═════╝╚═╝╚═╝  ╚═══╝╚══════╝╚═╝     ╚═╝ ╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝ "
)

_GOODBYE_ART = (
    "  ___            _  _             _  \n"
    " / __| ___  ___ | || |__ _  _  __| | \n"
    " \\__ \\/ -_)/ -_)| || / _` || |/ _` | \n"
    " |___/\\___|\\___|_||_\\__,_||_|\\__,_| "
)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _strip_rich(text: str) -> str:
    """Remove rich markup tags to get a plain display string."""
    return re.sub(r"\[/?[^\[\]]*\]", "", text)


def _get_highlight_fg() -> str:
    """Return the per-theme highlight foreground colour."""
    try:
        from src.config import THEMES, _active_theme_name
        return THEMES[_active_theme_name].get("highlight_fg", "#FFFFFF")
    except Exception:
        return "#FFFFFF"


# ─── Terminal clear ────────────────────────────────────────────────────────────

def clear():
    os.system("cls" if os.name == "nt" else "clear")


# ─── Splash screen ─────────────────────────────────────────────────────────────

def show_splash():
    clear()

    art_text = Text(justify="center")
    art_text.append(_CINEMA_ART, style=f"bold {PRIMARY}")

    tagline = Text(justify="center")
    tagline.append("  Your Cinema. Your Way.  ", style=f"bold {ACCENT}")
    tagline.append(f" v{APP_VERSION} ", style=f"dim {TEXT}")

    console.print()
    console.print(Align.center(art_text))
    console.print(Align.center(tagline))
    console.print()
    console.print(
        Rule(
            f"[dim {TEXT}]  Streaming · Downloads · Subtitles · Smart Search  [/dim {TEXT}]",
            style=PRIMARY,
        )
    )
    console.print()

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

        def _backend_launch_env(target_url: str):
            env = os.environ.copy()
            try:
                parsed = urlparse(target_url or "")
                host = (parsed.hostname or "").lower()
                if host not in ("localhost", "127.0.0.1", ""):
                    return env
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if port:
                    env["PORT"] = str(port)
            except Exception:
                pass
            return env

        backend_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
        )
        show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"
        stdout = None if show_logs else subprocess.DEVNULL
        stderr = None if show_logs else subprocess.DEVNULL
        launch_env = _backend_launch_env(url)

        try:
            proc = subprocess.Popen(
                "npm start",
                cwd=backend_dir,
                shell=True,
                stdout=stdout,
                stderr=stderr,
                env=launch_env,
            )
        except Exception:
            try:
                proc = subprocess.Popen(
                    ["node", "index.js"],
                    cwd=backend_dir,
                    stdout=stdout,
                    stderr=stderr,
                    env=launch_env,
                )
            except Exception:
                return None

        for _ in range(10):
            if _is_backend_running(url):
                return proc
            time.sleep(0.5)

        return proc

    settings = load_json_data(SETTINGS_FILE) or {}
    backend_url = settings.get("backend") or BACKEND_URL
    _backend_proc = _maybe_start_backend(backend_url)
    if _backend_proc:
        atexit.register(
            lambda: (
                _backend_proc.terminate()
                if _backend_proc and _backend_proc.poll() is None
                else None
            )
        )

    _steps = [
        "Initialising engine...",
        "Loading library data...",
        "Connecting to backend...",
        "Ready!",
    ]
    with Progress(
        SpinnerColumn(spinner_name="dots2", style=f"bold {PRIMARY}"),
        TextColumn(f"[{ACCENT}]{{task.description}}[/{ACCENT}]"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(_steps[0], total=None)
        time.sleep(0.7)
        for step in _steps[1:]:
            progress.update(task, description=step)
            time.sleep(0.4)


def show_goodbye():
    """Display farewell art and pause briefly before exit."""
    clear()
    art_text = Text(justify="center")
    art_text.append(_GOODBYE_ART, style=f"bold {ACCENT}")
    console.print()
    console.print(Align.center(art_text))
    console.print()
    console.print(
        Align.center(
            Text(f"Thanks for using Cinema CLI v{APP_VERSION}  ♥", style=f"dim {TEXT}")
        )
    )
    console.print()
    time.sleep(1.2)


# ─── Header ────────────────────────────────────────────────────────────────────

def print_header(subtitle=""):
    clear()
    header = Text()
    header.append("🎬  CINEMA CLI", style=f"bold {PRIMARY}")
    if subtitle:
        header.append("  │  ", style=f"dim {PRIMARY}")
        header.append(subtitle, style=f"bold {ACCENT}")
    header.append("  │  ", style=f"dim {PRIMARY}")
    header.append(f"v{APP_VERSION}", style=f"dim {TEXT}")

    console.print(
        Panel(
            Align.center(header),
            border_style=PRIMARY,
            box=box.HEAVY,
            padding=(0, 2),
        )
    )
    console.print()


# ─── Item formatter ────────────────────────────────────────────────────────────


def format_item(item):
    title = item.get("title") or item.get("name", "Unknown")
    date  = item.get("release_date") or item.get("first_air_date", "????-??-??")
    year  = date[:4] if isinstance(date, str) and len(date) >= 4 else "????"
    media_type = (
        "Movie" if "title" in item or item.get("media_type") == "movie" else "TV"
    )
    rating = item.get("vote_average", 0)
    return (
        f"[bold {TEXT}]{title}[/bold {TEXT}] "
        f"[{WARNING}]({year})[/{WARNING}] "
        f"[dim]⭐ {rating:.1f}[/dim] "
        f"[dim {SECONDARY}]·[/dim {SECONDARY}] "
        f"[dim {ACCENT}]{media_type}[/dim {ACCENT}]"
    )


# ─── Help bar strings ──────────────────────────────────────────────────────────

_HELP_BROWSE = "  ↑↓ Navigate   Enter Select   F Favourite   W Watch Later   D Batch DL   B Back   Q Quit  "
_HELP_SELECT = "  ↑↓ Navigate   Enter Confirm   B/Q Cancel  "
_HELP_MULTI  = "  ↑↓ Navigate   Space Toggle   A All/None   Enter Confirm   B/Q Cancel  "


# ─── Selection menu ────────────────────────────────────────────────────────────

def selection_menu(items, title, show_details=True, formatter=None, default_index=0):
    """Interactive arrow-key selection menu.

    Returns {"action": "select"|"back"|"quit"|"favorite"|"batch", "value": item}.
    """
    if not items:
        return None

    clear()
    selected_index = default_index
    if selected_index < 0 or selected_index >= len(items):
        selected_index = 0

    result = {"action": None, "value": None}

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(items)

    @kb.add("down")
    def _down(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(items)

    @kb.add("enter")
    def _enter(event):
        result["action"] = "select"
        result["value"]  = items[selected_index]
        event.app.exit()

    @kb.add("b")
    def _back(event):
        result["action"] = "back"
        event.app.exit()

    @kb.add("q")
    def _quit(event):
        result["action"] = "quit"
        event.app.exit()

    @kb.add("f")
    def _fav(event):
        result["action"] = "favorite"
        result["value"]  = items[selected_index]
        event.app.exit()

    @kb.add("w")
    def _watch_later(event):
        result["action"] = "watch_later"
        result["value"]  = items[selected_index]
        event.app.exit()

    @kb.add("d")
    def _batch(event):
        result["action"] = "batch"
        event.app.exit()

    @kb.add("g")
    def _top(event):
        nonlocal selected_index
        selected_index = 0

    @kb.add("G")
    def _bottom(event):
        nonlocal selected_index
        selected_index = len(items) - 1

    def get_formatted_text():
        res = []
        res.append(("class:header", f"  ══  {title}  ══\n"))
        res.append(("class:border", "─" * 66 + "\n"))

        visible_start = max(0, selected_index - 12)
        visible_end   = min(len(items), visible_start + 25)

        if visible_start > 0:
            res.append(("class:dim", f"  ↑ {visible_start} more above\n"))

        for i in range(visible_start, visible_end):
            item = items[i]
            raw  = formatter(item) if formatter else format_item(item)
            disp = _strip_rich(raw)

            if i == selected_index:
                res.append(("class:selected", f"  ▶  {disp}\n"))
            else:
                res.append(("class:item", f"     {disp}\n"))

        remaining = len(items) - visible_end
        if remaining > 0:
            res.append(("class:dim", f"  ↓ {remaining} more below\n"))

        res.append(("class:border", "\n" + "─" * 66 + "\n"))
        res.append(("class:help", _HELP_BROWSE))
        return res

    def get_details_text():
        if not show_details or not items:
            return ""

        item     = items[selected_index]
        overview = item.get("overview", "No description available.")
        overview = "\n".join(textwrap.wrap(overview, width=46))

        rating     = item.get("vote_average", 0)
        votes      = item.get("vote_count", 0)
        popularity = item.get("popularity", 0)

        title_text    = html.escape(str(item.get("title") or item.get("name", "")))
        overview_text = html.escape(overview)

        details  = f"\n<header> {title_text} </header>\n"
        details += f"<border>{'━' * 50}</border>\n"
        details += f"<rating>⭐  {rating:.1f}/10  ({votes:,} votes)</rating>\n"
        details += f"<pop>🔥  Popularity: {popularity:.0f}</pop>\n\n"
        details += f"<overview>{overview_text}</overview>\n"
        return HTML(details)

    hl_fg = _get_highlight_fg()
    style = Style.from_dict(
        {
            "header":   f"bold {PRIMARY}",
            "border":   f"dim {PRIMARY}",
            "selected": f"bg:{PRIMARY} fg:{hl_fg} bold",
            "item":     f"{TEXT}",
            "help":     f"italic dim {TEXT}",
            "dim":      f"dim {TEXT}",
            "rating":   f"{WARNING}",
            "pop":      f"{SUCCESS}",
            "overview": f"{TEXT}",
        }
    )

    body = VSplit(
        [
            Window(content=FormattedTextControl(get_formatted_text), width=68),
            Window(content=FormattedTextControl(get_details_text)),
        ],
        padding=2,
    )

    app = Application(
        layout=PTLayout(body), key_bindings=kb, style=style, full_screen=False
    )
    app.run()
    return result


# ─── Multi-selection menu ───────────────────────────────────────────────────────

def multi_selection_menu(items, title, formatter=None):
    """Arrow-key multi-select with Space to toggle.

    Returns list of selected items, or [] when cancelled.
    """
    if not items:
        return []

    clear()
    selected_index  = 0
    checked_indices: set = set()

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(items)

    @kb.add("down")
    def _down(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(items)

    @kb.add("space")
    def _toggle(event):
        if selected_index in checked_indices:
            checked_indices.remove(selected_index)
        else:
            checked_indices.add(selected_index)

    @kb.add("a")
    def _all(event):
        if len(checked_indices) == len(items):
            checked_indices.clear()
        else:
            for i in range(len(items)):
                checked_indices.add(i)

    @kb.add("enter")
    def _confirm(event):
        event.app.exit(result=[items[i] for i in sorted(checked_indices)])

    @kb.add("b")
    @kb.add("q")
    def _cancel(event):
        event.app.exit(result=[])

    def get_formatted_text():
        res = []
        count = len(checked_indices)
        res.append(("class:header", f"  ══  {title}  ({count} selected)  ══\n"))
        res.append(("class:border", "─" * 66 + "\n"))

        visible_start = max(0, selected_index - 12)
        visible_end   = min(len(items), visible_start + 25)

        if visible_start > 0:
            res.append(("class:dim", f"  ↑ {visible_start} more above\n"))

        for i in range(visible_start, visible_end):
            item     = items[i]
            raw      = formatter(item) if formatter else format_item(item)
            disp     = _strip_rich(raw)
            checkbox = " [✓]" if i in checked_indices else " [ ]"
            cursor   = "▶ " if i == selected_index else "  "

            if i == selected_index:
                res.append(("class:selected", f"  {cursor}{checkbox}  {disp}\n"))
            else:
                res.append(("class:item", f"  {cursor}{checkbox}  {disp}\n"))

        remaining = len(items) - visible_end
        if remaining > 0:
            res.append(("class:dim", f"  ↓ {remaining} more below\n"))

        res.append(("class:border", "\n" + "─" * 66 + "\n"))
        res.append(("class:help", _HELP_MULTI))
        return res

    hl_fg = _get_highlight_fg()
    style = Style.from_dict(
        {
            "header":   f"bold {PRIMARY}",
            "border":   f"dim {PRIMARY}",
            "selected": f"bg:{PRIMARY} fg:{hl_fg} bold",
            "item":     f"{TEXT}",
            "help":     f"italic dim {TEXT}",
            "dim":      f"dim {TEXT}",
        }
    )

    app = Application(
        layout=PTLayout(Window(FormattedTextControl(get_formatted_text))),
        key_bindings=kb,
        style=style,
        full_screen=False,
    )
    return app.run() or []

