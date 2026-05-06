import atexit
import html
import os
import re
import socket
import subprocess
import textwrap
import time
from urllib.error import URLError
from urllib.parse import urlparse
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
    PRIMARY,
    SETTINGS_FILE,
    SUCCESS,
    TEXT,
    THEMES,
    WARNING,
    _active_theme_name,
    console,
)
from src.utils.storage import load_json_data, save_json_data

# ─── ASCII art ─────────────────────────────────────────────────────────────────

_CINEMA_ART_LINES = [
    "  ██████╗██╗███╗   ██╗███████╗███╗   ███╗  █████╗      ██████╗██╗     ██╗ ",
    " ██╔════╝██║████╗  ██║██╔════╝████╗ ████║ ██╔══██╗    ██╔════╝██║     ██║ ",
    " ██║     ██║██╔██╗ ██║█████╗  ██╔████╔██║ ███████║    ██║     ██║     ██║ ",
    " ██║     ██║██║╚██╗██║██╔══╝  ██║╚██╔╝██║ ██╔══██║    ██║     ██║     ██║ ",
    " ╚██████╗██║██║ ╚████║███████╗██║ ╚═╝ ██║ ██║  ██║    ╚██████╗███████╗██║ ",
    "  ╚═════╝╚═╝╚═╝  ╚═══╝╚══════╝╚═╝     ╚═╝ ╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝ ",
]

_GOODBYE_ART = (
    "  ___            _  _             _  \n"
    " / __| ___  ___ | || |__ _  _  __| | \n"
    " \\__ \\/ -_)/ -_)| || / _` || |/ _` | \n"
    " |___/\\___|\\___|_||_\\__,_||_|\\__,_| "
)

_CLASS_BORDER = "class:border"
_CLASS_DIM = "class:dim"


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _strip_rich(text: str) -> str:
    """Remove rich markup tags to get a plain display string."""
    return re.sub(r"\[/?[^\[\]]*\]", "", text)


def _get_highlight_fg() -> str:
    """Return the per-theme highlight foreground colour."""
    try:
        return THEMES[_active_theme_name].get("highlight_fg", "#FFFFFF")
    except Exception:
        return "#FFFFFF"


# ─── Terminal clear ────────────────────────────────────────────────────────────


def clear():
    os.system("cls" if os.name == "nt" else "clear")


# ─── Splash screen ─────────────────────────────────────────────────────────────


def _hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return (255, 255, 255)


def _interpolate_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def show_splash():  # NOSONAR
    clear()

    # Create a stunning vertical gradient for the ASCII art
    c1 = _hex_to_rgb(PRIMARY)
    c2 = _hex_to_rgb(ACCENT)

    art_text = Text(justify="center")
    num_lines = len(_CINEMA_ART_LINES)
    for i, line in enumerate(_CINEMA_ART_LINES):
        t = i / max(1, (num_lines - 1))
        r, g, b = _interpolate_color(c1, c2, t)
        color_hex = f"#{r:02x}{g:02x}{b:02x}"
        art_text.append(line + "\n", style=f"bold {color_hex}")

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
    def _probe_urls(base_url: str):
        base = str(base_url or "").rstrip("/")
        return [f"{base}/", f"{base}/health", f"{base}/proxy/status"]

    def _is_backend_running(url: str) -> bool:
        for probe_url in _probe_urls(url):
            try:
                parsed = urlparse(probe_url)
                if parsed.scheme not in ("http", "https"):
                    continue
                req = Request(
                    probe_url,
                    headers={
                        "User-Agent": "cinema-cli/1.0",
                        "Connection": "close",
                        "Accept": "*/*",
                    },
                )
                # Reduced timeout for non-blocking health check
                with urlopen(req, timeout=1) as resp:
                    if 200 <= int(getattr(resp, "status", 0)) < 400:
                        return True
            except (URLError, ValueError):
                continue
        return False

    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    def _maybe_start_backend(url: str):
        try:
            host = url.split("://")[-1].split(":")[0]
        except Exception:
            host = ""

        if host not in ("localhost", "127.0.0.1", ""):
            return None

        if _is_backend_running(url):
            return None

        def _backend_launch_env(port: int):
            env = os.environ.copy()
            env["PORT"] = str(port)
            return env

        backend_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
        )
        show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"
        stdout = None if show_logs else subprocess.DEVNULL
        stderr = None if show_logs else subprocess.DEVNULL
        parsed_backend_url = urlparse(url or "http://localhost:3010")
        launch_host = parsed_backend_url.hostname or "localhost"
        launch_scheme = parsed_backend_url.scheme or "http"
        launch_port = _find_free_port()
        launch_url = f"{launch_scheme}://{launch_host}:{launch_port}"
        launch_env = _backend_launch_env(launch_port)

        npm_command = ["npm.cmd", "start"] if os.name == "nt" else ["npm", "start"]
        try:
            proc = subprocess.Popen(
                npm_command,
                cwd=backend_dir,
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
            if _is_backend_running(launch_url):
                os.environ["BACKEND_URL"] = launch_url
                settings["backend"] = launch_url
                try:
                    save_json_data(SETTINGS_FILE, settings)
                except Exception:
                    pass
                return proc
            time.sleep(0.5)

        return proc

    settings = load_json_data(SETTINGS_FILE, default={}, expected_type=dict) or {}
    backend_url = settings.get("backend") or BACKEND_URL

    _steps = [
        "Initialising engine...",
        "Loading library data...",
        "Connecting to backend...",
        "Ready!",
    ]

    # Premium loading spinner - backend start moved inside to keep spinner animated
    with Progress(
        SpinnerColumn(spinner_name="point", style=f"bold {ACCENT}"),
        TextColumn(f"[{TEXT}]{{task.description}}[/{TEXT}]"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(_steps[0], total=None)
        time.sleep(0.4)

        progress.update(task, description=_steps[1])
        time.sleep(0.4)

        progress.update(task, description=_steps[2])
        _backend_proc = _maybe_start_backend(backend_url)
        if _backend_proc:
            atexit.register(
                lambda: (
                    _backend_proc.terminate()
                    if _backend_proc and _backend_proc.poll() is None
                    else None
                )
            )

        progress.update(task, description=_steps[3])
        time.sleep(0.4)


def show_goodbye():
    """Display farewell art and pause briefly before exit."""
    clear()

    # Gradient goodbye
    c1 = _hex_to_rgb(ACCENT)
    c2 = _hex_to_rgb(PRIMARY)

    art_text = Text(justify="center")
    lines = _GOODBYE_ART.split("\n")
    num_lines = len(lines)
    for i, line in enumerate(lines):
        t = i / max(1, (num_lines - 1))
        r, g, b = _interpolate_color(c1, c2, t)
        color_hex = f"#{r:02x}{g:02x}{b:02x}"
        art_text.append(line + "\n", style=f"bold {color_hex}")

    console.print()
    console.print(Align.center(art_text))
    console.print()
    console.print(
        Align.center(Text(f"Thanks for using Cinema CLI v{APP_VERSION}  ♥", style=f"dim {TEXT}"))
    )
    console.print()
    time.sleep(1.2)


# ─── Header ────────────────────────────────────────────────────────────────────


def print_header(subtitle=""):
    """Print a stunning, professional app bar header."""
    clear()
    header = Text()
    header.append("🎬 CINEMA CLI", style=f"bold {PRIMARY}")
    if subtitle:
        header.append(" ┃ ", style=f"dim {PRIMARY}")
        header.append(str(subtitle).upper(), style=f"bold {ACCENT}")
    header.append(" ┃ ", style=f"dim {PRIMARY}")
    header.append(f"v{APP_VERSION}", style=f"dim {TEXT}")

    console.print(
        Panel(
            Align.center(header),
            border_style=PRIMARY,
            box=box.HORIZONTALS,
            padding=(0, 2),
        )
    )
    console.print()


# ─── Item formatter ────────────────────────────────────────────────────────────


def format_item(item):
    title = item.get("title") or item.get("name", "Unknown")
    date = item.get("release_date") or item.get("first_air_date", "????-??-??")
    year = date[:4] if isinstance(date, str) and len(date) >= 4 else "????"
    media_type = "Movie" if "title" in item or item.get("media_type") == "movie" else "TV"
    rating = item.get("vote_average", 0)

    # Enhance the item rendering with cleaner spacing and icons
    type_icon = "🎬" if media_type == "Movie" else "📺"

    short_title = textwrap.shorten(title, width=40, placeholder="...")
    return f"{type_icon} {short_title:<40} {year}  ⭐ {rating:.1f}"


# ─── Help bar strings ──────────────────────────────────────────────────────────

_HELP_BROWSE = (
    "  ↑↓ Navigate   Enter Select   F Favourite   W Watch Later   D Batch DL   B Back   Q Quit  "
)
_HELP_SELECT = "  ↑↓ Navigate   Enter Confirm   B/Q Cancel  "
_HELP_MULTI = "  ↑↓ Navigate   Space Toggle   A All/None   Enter Confirm   B/Q Cancel  "
_HELP_BROWSE_JUMP = "  ↑↓ Navigate   Enter Select   J Jump   F Favourite   W Watch Later   D Batch DL   B Back   Q Quit  "


# ─── Selection menu ────────────────────────────────────────────────────────────


def selection_menu(  # NOSONAR
    items,
    title,
    show_details=True,
    formatter=None,
    default_index=0,
    allow_jump=False,
):
    """Interactive arrow-key selection menu.

    Returns {"action": "select"|"back"|"quit"|"favorite"|"batch"|"jump", "value": item}.
    """
    if not items:
        return None

    clear()
    selected_index = default_index
    if selected_index < 0 or selected_index >= len(items):
        selected_index = 0

    result = {"action": None, "value": None}

    kb = KeyBindings()

    @kb.add("k")
    @kb.add("up")
    def _up(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(items)

    @kb.add("j")
    @kb.add("down")
    def _down(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(items)

    @kb.add("pageup")
    def _pageup(event):
        nonlocal selected_index
        selected_index = max(0, selected_index - 10)

    @kb.add("pagedown")
    def _pagedown(event):
        nonlocal selected_index
        selected_index = min(len(items) - 1, selected_index + 10)

    @kb.add("home")
    @kb.add("g")
    def _top(event):
        nonlocal selected_index
        selected_index = 0

    @kb.add("end")
    @kb.add("G")
    def _bottom(event):
        nonlocal selected_index
        selected_index = len(items) - 1

    @kb.add("enter")
    def _enter(event):
        result["action"] = "select"
        result["value"] = items[selected_index]
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
        result["value"] = items[selected_index]
        event.app.exit()

    @kb.add("w")
    def _watch_later(event):
        result["action"] = "watch_later"
        result["value"] = items[selected_index]
        event.app.exit()

    @kb.add("d")
    def _batch(event):
        result["action"] = "batch"
        event.app.exit()

    @kb.add("J")
    def _jump(event):
        if allow_jump:
            result["action"] = "jump"
            result["value"] = items[selected_index]
            event.app.exit()

    def get_formatted_text():
        res = []
        res.append(("class:header", f"  ╭── {title} ──╮\n"))
        res.append((_CLASS_BORDER, "  " + "─" * 64 + "\n"))

        visible_start = max(0, selected_index - 12)
        visible_end = min(len(items), visible_start + 25)

        if visible_start > 0:
            res.append((_CLASS_DIM, f"  ↑ {visible_start} more above\n"))

        for i in range(visible_start, visible_end):
            item = items[i]
            raw = formatter(item) if formatter else format_item(item)
            disp = _strip_rich(raw)

            if i == selected_index:
                res.append(("class:selected", f"  ▶  {disp}\n"))
            else:
                res.append(("class:item", f"     {disp}\n"))

        remaining = len(items) - visible_end
        if remaining > 0:
            res.append((_CLASS_DIM, f"  ↓ {remaining} more below\n"))

        res.append((_CLASS_BORDER, "\n  " + "─" * 64 + "\n"))
        res.append(("class:help", _HELP_BROWSE_JUMP if allow_jump else _HELP_BROWSE))
        return res

    def get_details_text():
        if not show_details or not items:
            return ""

        item = items[selected_index]
        overview = item.get("overview", "No description available.")
        overview = "\n".join(textwrap.wrap(overview, width=46))

        rating = item.get("vote_average", 0)
        votes = item.get("vote_count", 0)
        popularity = item.get("popularity", 0)
        release_date = item.get("release_date") or item.get("first_air_date", "Unknown")
        lang = str(item.get("original_language", "en")).upper()
        media_type = (
            "🎬 Movie" if ("title" in item or item.get("media_type") == "movie") else "📺 TV Show"
        )
        adult = "🔞 18+" if item.get("adult") else "✅ General"

        title_text = html.escape(str(item.get("title") or item.get("name", "")))
        overview_text = html.escape(overview)

        # Build a stunning detail card
        details = f"\n<header> 📌 {title_text} </header>\n"
        details += f"<border>{'━' * 50}</border>\n"
        details += f"<meta> {media_type}  │  📅 {release_date}  │  🌍 {lang}  │  {adult} </meta>\n"
        details += f"<border>{'─' * 50}</border>\n"
        details += f"<rating>⭐  {rating:.1f}/10  ({votes:,} votes)</rating>\n"
        details += f"<pop>🔥  Popularity: {popularity:.0f}</pop>\n"
        details += f"<border>{'━' * 50}</border>\n\n"
        details += f"<overview>{overview_text}</overview>\n"
        return HTML(details)

    hl_fg = _get_highlight_fg()
    style = Style.from_dict(
        {
            "header": f"bold {PRIMARY}",
            "border": f"dim {PRIMARY}",
            "selected": f"bg:{PRIMARY} fg:{hl_fg} bold",
            "item": f"{TEXT}",
            "help": f"italic dim {TEXT}",
            "dim": f"dim {TEXT}",
            "rating": f"{WARNING} bold",
            "pop": f"{SUCCESS} bold",
            "meta": "cyan",
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

    app = Application(layout=PTLayout(body), key_bindings=kb, style=style, full_screen=False)
    app.run()
    return result


# ─── Multi-selection menu ───────────────────────────────────────────────────────


def multi_selection_menu(items, title, formatter=None):  # NOSONAR
    """Arrow-key multi-select with Space to toggle.

    Returns list of selected items, or [] when cancelled.
    """
    if not items:
        return []

    clear()
    selected_index = 0
    checked_indices: set = set()

    kb = KeyBindings()

    @kb.add("k")
    @kb.add("up")
    def _up(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(items)

    @kb.add("j")
    @kb.add("down")
    def _down(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(items)

    @kb.add("pageup")
    def _pageup(event):
        nonlocal selected_index
        selected_index = max(0, selected_index - 10)

    @kb.add("pagedown")
    def _pagedown(event):
        nonlocal selected_index
        selected_index = min(len(items) - 1, selected_index + 10)

    @kb.add("home")
    @kb.add("g")
    def _top(event):
        nonlocal selected_index
        selected_index = 0

    @kb.add("end")
    @kb.add("G")
    def _bottom(event):
        nonlocal selected_index
        selected_index = len(items) - 1

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
        res.append(("class:header", f"  ╭── {title}  ({count} selected) ──╮\n"))
        res.append((_CLASS_BORDER, "  " + "─" * 64 + "\n"))

        visible_start = max(0, selected_index - 12)
        visible_end = min(len(items), visible_start + 25)

        if visible_start > 0:
            res.append((_CLASS_DIM, f"  ↑ {visible_start} more above\n"))

        for i in range(visible_start, visible_end):
            item = items[i]
            raw = formatter(item) if formatter else format_item(item)
            disp = _strip_rich(raw)
            checkbox = " [✓]" if i in checked_indices else " [ ]"
            cursor = "▶ " if i == selected_index else "  "

            if i == selected_index:
                res.append(("class:selected", f"  {cursor}{checkbox}  {disp}\n"))
            else:
                res.append(("class:item", f"  {cursor}{checkbox}  {disp}\n"))

        remaining = len(items) - visible_end
        if remaining > 0:
            res.append((_CLASS_DIM, f"  ↓ {remaining} more below\n"))

        res.append((_CLASS_BORDER, "\n  " + "─" * 64 + "\n"))
        res.append(("class:help", _HELP_MULTI))
        return res

    hl_fg = _get_highlight_fg()
    style = Style.from_dict(
        {
            "header": f"bold {PRIMARY}",
            "border": f"dim {PRIMARY}",
            "selected": f"bg:{PRIMARY} fg:{hl_fg} bold",
            "item": f"{TEXT}",
            "help": f"italic dim {TEXT}",
            "dim": f"dim {TEXT}",
        }
    )

    app = Application(
        layout=PTLayout(Window(FormattedTextControl(get_formatted_text))),
        key_bindings=kb,
        style=style,
        full_screen=False,
    )
    return app.run() or []
