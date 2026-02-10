import html
import os
import textwrap
import time

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


def _colors():
    """Get live theme colors from config."""
    import src.config as cfg
    return cfg.PRIMARY, cfg.SECONDARY, cfg.ACCENT, cfg.SUCCESS, cfg.TEXT, cfg.WARNING, cfg.BG


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_header(subtitle=""):
    clear()
    P, S, A, Su, T, W, B = _colors()
    title = Text("🎬 CINEMA CLI", style=f"bold {P}")
    if subtitle:
        title.append(f" | {subtitle}", style=f"italic {A}")

    from src.config import console
    console.print(Panel(Align.center(title), border_style=P, box=box.DOUBLE))
    console.print()


def show_splash():
    clear()
    P, S, A, Su, T, W, B = _colors()
    from src.config import console

    art = f"""
[bold {P}]
 ██████╗██╗███╗   ██╗███████╗███████╗  ██╗    ██╗
██╔════╝██║████╗  ██║██╔════╝██╔════╝  ██║    ██║
██║     ██║██╔██╗ ██║█████╗  ███████╗  ██║ █╗ ██║
██║     ██║██║╚██╗██║██╔══╝  ╚════██║  ██║███╗██║
╚██████╗██║██║ ╚████║███████╗███████║  ╚███╔███╔╝
 ╚═════╝╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝   ╚══╝╚══╝ 
[/bold {P}]
[italic {A}]      Elevate Your Movie Experience - v2.0.0[/italic {A}]
    """
    console.print(Align.center(art))

    with Progress(
        SpinnerColumn(spinner_name="dots", style=A),
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
    P, S, A, Su, T, W, B = _colors()
    title = item.get("title") or item.get("name", "Unknown")
    date = item.get("release_date") or item.get("first_air_date", "????-??-??")
    year = date[:4]
    media_type = (
        "Movie" if "title" in item or item.get("media_type") == "movie" else "TV"
    )
    rating = item.get("vote_average", 0)
    return (
        f"[bold {T}]{title}[/bold {T}] ({year}) | ⭐ {rating:.1f} | {media_type}"
    )


def selection_menu(items, title, show_details=True, formatter=None, default_index=0):
    if not items:
        return None

    P, S, A, Su, T, W, B = _colors()

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

        start = max(0, selected_index - 5)
        end = min(len(items), start + 12)
        if end - start < 12:
            start = max(0, end - 12)

        for i in range(start, end):
            item = items[i]
            display = formatter(item) if formatter else format_item(item)
            clean_display = display.replace(f"[bold {T}]", "").replace(
                f"[/bold {T}]", ""
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
            "title": f"bold {A}",
            "border": f"{P}",
            "selected": f"bg:{P} fg:#ffffff bold",
            "item": f"{T}",
            "help": f"italic {A}",
            "rating": f"{W}",
            "pop": f"{Su}",
            "overview": f"{T}",
        }
    )

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

    P, S, A, Su, T, W, B = _colors()

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

        start = max(0, selected_index - 5)
        end = min(len(items), start + 12)
        if end - start < 12:
            start = max(0, end - 12)

        for i in range(start, end):
            item = items[i]
            display = formatter(item) if formatter else format_item(item)
            clean_display = display.replace(f"[bold {T}]", "").replace(
                f"[/bold {T}]", ""
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
            "title": f"bold {P}",
            "border": f"{P}",
            "selected": f"bg:{P} fg:#ffffff bold",
            "item": f"{T}",
            "help": f"italic {A}",
        }
    )

    app = Application(
        layout=PTLayout(Window(FormattedTextControl(get_formatted_text))),
        key_bindings=kb,
        style=style,
        full_screen=False,
    )
    return app.run()
