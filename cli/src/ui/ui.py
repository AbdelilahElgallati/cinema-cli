import os
import time
import sys
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout as PTLayout, Window, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML

from src.config import console, PRIMARY, SECONDARY, ACCENT, SUCCESS, WARNING, BG, TEXT

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

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
 ██████╗██╗███╗   ██╗███████╗███████╗  ██╗    ██╗
██╔════╝██║████╗  ██║██╔════╝██╔════╝  ██║    ██║
██║     ██║██╔██╗ ██║█████╗  ███████╗  ██║ █╗ ██║
██║     ██║██║╚██╗██║██╔══╝  ╚════██║  ██║███╗██║
╚██████╗██║██║ ╚████║███████╗███████║  ╚███╔███╔╝
 ╚═════╝╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝   ╚══╝╚══╝ 
[/bold {PRIMARY}]
[italic {ACCENT}]      Elevate Your Movie Experience - v2.0.0[/italic {ACCENT}]
    """
    console.print(Align.center(art))
    
    with Progress(
        SpinnerColumn(spinner_name="dots", style=ACCENT),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        progress.add_task(description="Initializing engine...", total=None)
        time.sleep(1.5)
        progress.add_task(description="Loading favorites...", total=None)
        time.sleep(0.5)
        progress.add_task(description="Ready!", total=None)
        time.sleep(0.5)

def format_item(item):
    title = item.get('title') or item.get('name', 'Unknown')
    date = item.get('release_date') or item.get('first_air_date', '????-??-??')
    year = date[:4]
    media_type = "Movie" if 'title' in item or item.get('media_type') == 'movie' else "TV"
    rating = item.get('vote_average', 0)
    return f"[bold {TEXT}]{title}[/bold {TEXT}] ({year}) | ⭐ {rating:.1f} | {media_type}"

def selection_menu(items, title, show_details=True, formatter=None, default_index=0):
    if not items:
        return None

    clear()
    selected_index = default_index
    if selected_index < 0 or selected_index >= len(items):
        selected_index = 0
        
    result = {'action': None, 'value': None}
    
    kb = KeyBindings()

    @kb.add('up')
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(items)

    @kb.add('down')
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(items)

    @kb.add('enter')
    def _(event):
        result['action'] = 'select'
        result['value'] = items[selected_index]
        event.app.exit()

    @kb.add('b')
    def _(event):
        result['action'] = 'back'
        event.app.exit()
    
    @kb.add('q')
    def _(event):
        result['action'] = 'quit'
        event.app.exit()

    @kb.add('f')
    def _(event):
        result['action'] = 'favorite'
        result['value'] = items[selected_index]
        event.app.exit()

    def get_formatted_text():
        res = []
        res.append(('class:title', f" {title} \n"))
        res.append(('class:border', "─" * 60 + "\n"))
        
        # Show a window of items
        start = max(0, selected_index - 5)
        end = min(len(items), start + 12)
        if end - start < 12:
            start = max(0, end - 12)

        for i in range(start, end):
            item = items[i]
            display = formatter(item) if formatter else format_item(item)
            # Strip rich tags for prompt_toolkit display or use HTML
            clean_display = display.replace(f"[bold {TEXT}]", "").replace(f"[/bold {TEXT}]", "")
            
            if i == selected_index:
                res.append(('class:selected', f" ▶ {clean_display} \n"))
            else:
                res.append(('class:item', f"   {clean_display} \n"))
        
        res.append(('class:border', "─" * 60 + "\n"))
        res.append(('class:help', " [↑/↓] Navigate  [Enter] Select  [F] Favorite  [B] Back  [Q] Quit "))
        return res

    def get_details_text():
        if not show_details or not items:
            return ""
        
        item = items[selected_index]
        overview = item.get('overview', 'No description available.')
        if len(overview) > 300:
            overview = overview[:297] + "..."
        
        rating = item.get('vote_average', 0)
        votes = item.get('vote_count', 0)
        popularity = item.get('popularity', 0)
        
        details = f"\n<title> {item.get('title') or item.get('name')} </title>\n"
        details += f"<border>{'━' * 40}</border>\n"
        details += f"<rating>⭐ Rating: {rating:.1f}/10 ({votes} votes)</rating>\n"
        details += f"<pop>🔥 Popularity: {popularity:.0f}</pop>\n\n"
        details += f"<overview>{overview}</overview>\n"
        
        return HTML(details)

    style = Style.from_dict({
        'title': f'bold {PRIMARY}',
        'border': f'{PRIMARY}',
        'selected': f'bg:{PRIMARY} fg:#ffffff bold',
        'item': f'{TEXT}',
        'help': f'italic {ACCENT}',
        'title': f'bold {ACCENT}',
        'rating': f'{WARNING}',
        'pop': f'{SUCCESS}',
        'overview': f'{TEXT}',
    })

    # Layout with details on the right
    body = VSplit(
        [
            Window(content=FormattedTextControl(get_formatted_text), width=62),
            Window(content=FormattedTextControl(get_details_text))
        ],
        padding=2
    )

    app = Application(layout=PTLayout(body), key_bindings=kb, style=style, full_screen=False)
    app.run()
    return result
