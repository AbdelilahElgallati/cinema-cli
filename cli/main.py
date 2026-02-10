import atexit
import os
import subprocess
import sys
import time
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout as PTLayout
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.config import (
    ACCENT,
    BACKEND_URL,
    FAVORITES_FILE,
    HISTORY_FILE,
    PLAYBACK_FILE,
    PRIMARY,
    SECONDARY,
    SETTINGS_FILE,
    SUCCESS,
    TEXT,
    TMDB_API_KEY,
    WARNING,
    console,
    reload_theme,
)
from src.themes import apply_theme, get_theme, get_theme_label, list_themes
from src.ui.ui import (
    clear,
    format_item,
    multi_selection_menu,
    print_header,
    selection_menu,
    show_splash,
)
from src.utils.api import APIClient
from src.utils.download_manager import DownloadManager
from src.utils.library import scan_library
from src.utils.player import play_stream, play_video
from src.utils.source_checker import find_working_source
from src.utils.storage import load_json_data, save_json_data
from src.utils.utils import generate_filename


def _get_colors():
    """Get current theme colors (re-import from config to get live values)."""
    import src.config as cfg
    return cfg.PRIMARY, cfg.SECONDARY, cfg.ACCENT, cfg.SUCCESS, cfg.TEXT, cfg.WARNING


class CinemaCLI:
    def __init__(self):
        # Attempt to ensure a local backend is running (only for localhost URLs)
        self._backend_proc = None
        self._maybe_start_backend(BACKEND_URL)

        self.settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}

        # Defaults for filename templates
        if "filename_template" not in self.settings:
            self.settings["filename_template"] = "{title}.{year}"
        if "filename_template_tv" not in self.settings:
            self.settings["filename_template_tv"] = "{title}.S{season}E{episode}"
        if "preferred_quality" not in self.settings:
            self.settings["preferred_quality"] = "auto"
        if "theme" not in self.settings:
            self.settings["theme"] = "default"

        # Apply saved theme
        theme_name = self.settings.get("theme", "default")
        apply_theme(theme_name)

        self.api = APIClient(self.settings)

        # IMPORTANT: ensure lists/dicts, not None
        self.history = load_json_data(HISTORY_FILE) or []
        self.favorites = load_json_data(FAVORITES_FILE) or []
        self.playback = load_json_data(PLAYBACK_FILE) or {}

        self.download_manager = DownloadManager(self.settings)
        self.download_manager.start()

        # Ensure backend process is terminated on exit if we started it
        atexit.register(self._cleanup_backend)

    def _is_backend_running(self, url: str) -> bool:
        try:
            req = Request(
                url.rstrip("/") + "/", headers={"User-Agent": "cinema-cli/1.0"}
            )
            with urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except (URLError, HTTPError, ValueError):
            return False

    def _maybe_start_backend(self, backend_url: str) -> None:
        try:
            host = backend_url.split("://")[-1].split(":")[0]
        except Exception:
            host = ""

        if host not in ("localhost", "127.0.0.1", ""):
            return

        if self._is_backend_running(backend_url):
            return

        backend_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend")
        )
        if not os.path.isdir(backend_dir):
            console.print("[yellow]Backend directory not found.[/yellow]")
            return

        # Auto-install npm deps if node_modules is missing
        node_modules = os.path.join(backend_dir, "node_modules")
        if not os.path.isdir(node_modules):
            console.print("[dim]Installing backend dependencies (first run)...[/dim]")
            try:
                subprocess.run(
                    "npm install",
                    cwd=backend_dir,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                )
                console.print("[green]Dependencies installed.[/green]")
            except Exception as e:
                console.print(f"[red]Failed to install backend deps: {e}[/red]")
                return

        # Clear old log to avoid confusion
        log_path = os.path.join(backend_dir, "backend.log")
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

        try:
            logfile = open(log_path, "a", encoding="utf-8")
        except Exception:
            logfile = None

        stdout_dest = logfile if logfile else subprocess.DEVNULL
        stderr_dest = logfile if logfile else subprocess.DEVNULL

        # Use 'node index.js' directly to avoid 'npm start' which may use
        # --watch mode (hangs waiting for file changes on crash)
        try:
            self._backend_proc = subprocess.Popen(
                ["node", "index.js"], cwd=backend_dir,
                stdout=stdout_dest, stderr=stderr_dest
            )
        except Exception:
            try:
                self._backend_proc = subprocess.Popen(
                    "npm start", cwd=backend_dir, shell=True,
                    stdout=stdout_dest, stderr=stderr_dest
                )
            except Exception:
                if logfile:
                    logfile.close()
                return

        # Wait up to 10 seconds for backend to become healthy
        console.print("[dim]Starting backend...[/dim]")
        for i in range(20):
            if self._is_backend_running(backend_url):
                console.print("[green]Backend ready![/green]")
                if logfile:
                    logfile.flush()
                return
            time.sleep(0.5)

        console.print("[yellow]Backend may still be starting...[/yellow]")
        if logfile:
            logfile.flush()

    def _cleanup_backend(self):
        if self._backend_proc and self._backend_proc.poll() is None:
            try:
                self._backend_proc.terminate()
                time.sleep(0.2)
                if self._backend_proc.poll() is None:
                    self._backend_proc.kill()
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════
    # Main Menu
    # ═══════════════════════════════════════════════════════════
    def main_menu(self):
        while True:
            P, S, A, Su, T, W = _get_colors()
            print_header("Main Menu")
            options = [
                {"name": "🔍 Search Movies & TV", "action": self.handle_search},
                {"name": "🌍 Discovery", "action": self.handle_discovery},
                {"name": "📈 Trending This Week", "action": self.handle_trending},
                {"name": "🔥 Popular Content", "action": self.handle_popular},
                {"name": "🎭 Browse by Genre", "action": self.handle_genres},
                {"name": "🎬 Search by Actor", "action": self.handle_actor_search},
                {"name": "📁 Local Library", "action": self.handle_library},
                {"name": "📥 Download Manager", "action": self.handle_downloads},
                {"name": "⭐ My Favorites", "action": self.handle_favorites},
                {"name": "🕒 Watch History", "action": self.handle_history},
                {"name": "⚙️ Settings", "action": self.handle_settings},
                {"name": "❌ Exit", "action": sys.exit},
            ]

            selected_index = 0
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
                event.app.exit(result=options[selected_index]["action"])

            @kb.add("q")
            def _(event):
                event.app.exit(result=sys.exit)

            @kb.add("b")
            def _(event):
                event.app.exit(result=None)

            def get_menu_text():
                res = []
                for i, opt in enumerate(options):
                    if i == selected_index:
                        res.append(("class:selected", f"  ▶ {opt['name']}  \n"))
                    else:
                        res.append(("class:item", f"    {opt['name']}  \n"))
                return res

            style = Style.from_dict(
                {
                    "selected": f"bg:{P} fg:#ffffff bold",
                    "item": f"{T}",
                }
            )

            app = Application(
                layout=PTLayout(Window(FormattedTextControl(get_menu_text))),
                key_bindings=kb,
                style=style,
            )
            action = app.run()
            if action:
                action()

    # ═══════════════════════════════════════════════════════════
    # Discovery
    # ═══════════════════════════════════════════════════════════
    def handle_discovery(self):
        print_header("Discovery")
        options = [
            {"name": "🆕 New Movies (In Theaters/Digital)", "val": "movies"},
            {"name": "📺 New Episodes (Airing Today)", "val": "episodes"},
            {"name": "🔥 Trending TV (Today)", "val": "trending_tv_today"},
            {"name": "🔥 Movie of the Day", "val": "movie_of_the_day"},
        ]

        while True:
            sel = selection_menu(
                options,
                "Discovery Options",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if not sel or sel["action"] == "back":
                break

            if sel["action"] == "select":
                choice = sel["value"]["val"]
                if choice == "movies":
                    self.browse_new_movies()
                elif choice == "episodes":
                    self.browse_new_episodes()
                elif choice == "trending_tv_today":
                    self.browse_trending_tv_today()
                elif choice == "movie_of_the_day":
                    self.browse_movie_of_the_day()

    def browse_new_movies(self):
        page = 1
        while True:
            data = self.api.get_new_movies(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "movie"

            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"New Movies (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue

            if sel["action"] == "select":
                self.handle_media(val)

    def browse_new_episodes(self):
        page = 1
        while True:
            data = self.api.get_new_episodes(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "tv"

            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"New Episodes (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue

            if sel["action"] == "select":
                self.handle_media(val)

    def browse_trending_tv_today(self):
        page = 1
        while True:
            data = self.api.get_trending_tv_today(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "tv"

            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"Trending TV Today (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue

            if sel["action"] == "select":
                self.handle_media(val)

    def browse_movie_of_the_day(self):
        page = 1
        while True:
            data = self.api.get_trending_movies_today(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "movie"

            if data.get("total_pages", 1) > page:
                results.append(
                    {"id": "next_page", "title": "➡️ Next Page", "special": True}
                )
            if page > 1:
                results.insert(
                    0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True}
                )

            sel = selection_menu(results, f"🔥 Movie of the Day (Page {page})")
            if not sel or sel["action"] == "back":
                break

            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page":
                    page += 1
                elif val["id"] == "prev_page":
                    page -= 1
                continue

            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue

            if sel["action"] == "select":
                self.handle_media(val)

    # ═══════════════════════════════════════════════════════════
    # Search
    # ═══════════════════════════════════════════════════════════
    def handle_search(self):
        P, S, A, Su, T, W = _get_colors()
        print_header("Search")
        query = console.input(
            f"[bold {A}]Search for a movie or TV show: [/bold {A}]"
        )
        if not query.strip():
            return

        data = self.api.get_tmdb_data("search/multi", {"query": query})
        if not data or not data.get("results"):
            console.print("[yellow]No results found.[/yellow]")
            time.sleep(1.5)
            return

        results = [r for r in data["results"] if r.get("media_type") in ["movie", "tv"]]
        while True:
            sel = selection_menu(results, f"Search Results for '{query}'")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_trending(self):
        data = self.api.get_tmdb_data("trending/all/week")
        if not data:
            return
        results = [r for r in data["results"] if r.get("media_type") in ["movie", "tv"]]
        while True:
            sel = selection_menu(results, "Trending This Week")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_popular(self):
        P, S, A, Su, T, W = _get_colors()
        print_header("Popular")
        types = [
            {"name": "🎬 Movies", "val": "movie"},
            {"name": "📺 TV Shows", "val": "tv"},
        ]
        console.print(f"1. {types[0]['name']}\n2. {types[1]['name']}")
        choice = console.input(f"\n[bold {A}]Select type (1-2): [/bold {A}]")
        m_type = types[0]["val"] if choice == "1" else types[1]["val"]

        data = self.api.get_tmdb_data(f"{m_type}/popular")
        if not data:
            return
        results = data["results"]
        for r in results:
            r["media_type"] = m_type

        while True:
            sel = selection_menu(results, f"Popular {m_type.title()}s")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    # ═══════════════════════════════════════════════════════════
    # Genres
    # ═══════════════════════════════════════════════════════════
    def handle_genres(self):
        P, S, A, Su, T, W = _get_colors()
        print_header("Genres")
        types = [
            {"name": "🎬 Movies", "val": "movie"},
            {"name": "📺 TV Shows", "val": "tv"},
        ]
        console.print(f"1. {types[0]['name']}\n2. {types[1]['name']}")
        choice = console.input(f"\n[bold {A}]Select type (1-2): [/bold {A}]")
        m_type = types[0]["val"] if choice == "1" else types[1]["val"]

        data = self.api.get_tmdb_data(f"genre/{m_type}/list")
        if not data:
            return
        genres = data["genres"]

        selected_index = 0
        kb = KeyBindings()

        @kb.add("up")
        def _(event):
            nonlocal selected_index
            selected_index = (selected_index - 1) % len(genres)

        @kb.add("down")
        def _(event):
            nonlocal selected_index
            selected_index = (selected_index + 1) % len(genres)

        @kb.add("enter")
        def _(event):
            event.app.exit(result=genres[selected_index])

        @kb.add("q")
        def _(event):
            event.app.exit()

        @kb.add("b")
        def _(event):
            event.app.exit()

        def get_genre_text():
            res = []
            for i, g in enumerate(genres):
                if i == selected_index:
                    res.append(("class:selected", f"  ▶ {g['name']}  \n"))
                else:
                    res.append(("class:item", f"    {g['name']}  \n"))
            return res

        app = Application(
            layout=PTLayout(Window(FormattedTextControl(get_genre_text))),
            key_bindings=kb,
            style=Style.from_dict(
                {"selected": f"bg:{A} fg:#ffffff bold", "item": f"{T}"}
            ),
        )
        genre = app.run()

        if genre:
            data = self.api.get_tmdb_data(
                f"discover/{m_type}", {"with_genres": genre["id"]}
            )
            if not data:
                return
            results = data["results"]
            for r in results:
                r["media_type"] = m_type
            while True:
                sel = selection_menu(results, f"{genre['name']} {m_type.title()}s")
                if not sel or sel["action"] == "back":
                    break
                if sel["action"] == "select":
                    self.handle_media(sel["value"])

    # ═══════════════════════════════════════════════════════════
    # Actor Search
    # ═══════════════════════════════════════════════════════════
    def handle_actor_search(self):
        P, S, A, Su, T, W = _get_colors()
        print_header("Search by Actor")
        query = console.input(
            f"[bold {A}]Enter actor name: [/bold {A}]"
        )
        if not query.strip():
            return

        with console.status("Searching...", spinner="dots"):
            data = self.api.search_person(query)

        if not data or not data.get("results"):
            console.print("[yellow]No actors found.[/yellow]")
            time.sleep(1.5)
            return

        # Filter to people with known acting credits
        people = [
            p for p in data["results"]
            if p.get("known_for_department") == "Acting"
        ]
        if not people:
            people = data["results"]

        # Format for selection
        def fmt_person(p):
            name = p.get("name", "Unknown")
            dept = p.get("known_for_department", "")
            pop = p.get("popularity", 0)
            return f"{name} | {dept} | 🔥 {pop:.0f}"

        sel = selection_menu(
            people,
            f"Actors matching '{query}'",
            show_details=False,
            formatter=fmt_person,
        )

        if not sel or sel["action"] != "select":
            return

        person = sel["value"]
        person_id = person["id"]

        with console.status(f"Loading filmography for {person.get('name')}...", spinner="dots"):
            credits_data = self.api.get_person_credits(person_id)

        if not credits_data:
            console.print("[yellow]Could not fetch filmography.[/yellow]")
            time.sleep(1.5)
            return

        # Combine cast appearances
        cast_credits = credits_data.get("cast", [])
        # Sort by popularity descending
        cast_credits.sort(key=lambda x: x.get("popularity", 0), reverse=True)
        # Filter to movies and TV
        cast_credits = [
            c for c in cast_credits
            if c.get("media_type") in ["movie", "tv"]
        ]

        if not cast_credits:
            console.print("[yellow]No movie/TV credits found.[/yellow]")
            time.sleep(1.5)
            return

        while True:
            sel = selection_menu(
                cast_credits,
                f"🎬 {person.get('name')} — Filmography ({len(cast_credits)} titles)",
            )
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    # ═══════════════════════════════════════════════════════════
    # Local Library
    # ═══════════════════════════════════════════════════════════
    def handle_library(self):
        print_header("Local Library")
        default_dl = os.path.join(os.path.expanduser("~"), "Downloads", "Cinema-CLI")
        downloads_root = self.settings.get("download_path") or default_dl
        library = scan_library(downloads_root)

        movies = library.get("movies", [])
        tv_shows = library.get("tv", [])
        other = library.get("other", [])

        total = len(movies) + sum(
            sum(len(s["episodes"]) for s in show["seasons"])
            for show in tv_shows
        ) + len(other)

        if total == 0:
            console.print("[yellow]No downloaded content found.[/yellow]")
            console.print(f"[dim]Downloads folder: {downloads_root}[/dim]")
            time.sleep(2)
            return

        # Build flat list for browsing
        items = []
        for m in movies:
            items.append({
                "display": f"🎬 {m['title']}  ({m['size_human']})",
                "path": m["path"],
                "type": "movie",
            })
        for show in tv_shows:
            for season in show["seasons"]:
                for ep in season["episodes"]:
                    items.append({
                        "display": f"📺 {show['title']} S{ep['season_number']:02d}E{ep['episode_number']:02d}  ({ep['size_human']})",
                        "path": ep["path"],
                        "type": "tv",
                    })
        for o in other:
            items.append({
                "display": f"📄 {o['filename']}  ({o['size_human']})",
                "path": o["path"],
                "type": "other",
            })

        while True:
            sel = selection_menu(
                items,
                f"📁 Local Library ({total} files)",
                show_details=False,
                formatter=lambda x: x["display"],
            )
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "select":
                chosen = sel["value"]
                play_video(chosen["path"], chosen["display"])

    # ═══════════════════════════════════════════════════════════
    # Download Manager (Centralized)
    # ═══════════════════════════════════════════════════════════
    def handle_downloads(self):
        from rich.table import Table
        from rich.live import Live
        from rich.panel import Panel
        from rich.progress import BarColumn

        P, S, A, Su, T, W = _get_colors()

    def handle_downloads(self):
        from rich.table import Table
        from rich.live import Live
        from rich.panel import Panel
        from rich.progress import BarColumn
        import msvcrt

        P, S, A, Su, T, W = _get_colors()
        clear()  # Ensure we start with a clean screen

        def generate_table():
            queue = self.download_manager.get_queue()
            stats = self.download_manager.get_stats()

            if not queue:
                return Panel(
                    "[yellow]No downloads in queue.[/yellow]\n\n[dim]Start a download by selecting '⬇ Download' when viewing a movie or episode.[/dim]\n\n[dim]Press 'b' to go back...[/dim]",
                    title="📥 Download Manager",
                    border_style=P,
                )

            # Summary line
            summary = (
                f"  [bold {A}]Active: {stats['active']}[/bold {A}]  "
                f"[bold {W}]Pending: {stats['pending']}[/bold {W}]  "
                f"[bold {Su}]Done: {stats['completed']}[/bold {Su}]  "
                f"[bold red]Failed: {stats['failed']}[/bold red]  "
                f"[dim]Total: {stats['total']}[/dim]\n"
            )

            # Build table
            table = Table(
                show_header=True,
                header_style=f"bold {A}",
                border_style=P,
                expand=True,
                title="📥 Download Manager",
                caption=f"\n{summary}\n[bold {A}]Actions:[/bold {A}] [bold]R[/bold] Retry failed | [bold]X[/bold] Remove item | [bold]C[/bold] Clear completed | [bold]O[/bold] Open folder | [bold]B[/bold] Back",
            )
            table.add_column("#", width=3, style="dim")
            table.add_column("Title", min_width=20, max_width=40)
            table.add_column("Status", width=12)
            table.add_column("Progress", min_width=20)
            table.add_column("Speed", width=12)
            table.add_column("ETA", width=8)

            for i, task in enumerate(queue):
                title = task.get("title", "Unknown")
                if len(title) > 38:
                    title = title[:35] + "..."
                status = task.get("status", "?")
                progress = task.get("progress", 0)
                speed = task.get("speed", "")
                eta = task.get("eta", "")
                dl_size = task.get("downloaded_size", "")
                total_size = task.get("total_size", "")

                # Status with color
                if status == "downloading":
                    st = f"[bold cyan]⬇ Downloading[/bold cyan]"
                elif status == "completed":
                    st = f"[bold {Su}]✓ Done[/bold {Su}]"
                elif status == "error":
                    st = f"[bold red]✗ Failed[/bold red]"
                    # Append error message to title or status if possible, 
                    # but title column is better for long messages
                    err = task.get("error_msg", "")
                    if err:
                        title += f"\n[red size=11]└ {err}[/red size=11]"
                elif status == "pending":
                    st = f"[bold {W}]⏳ Queued[/bold {W}]"
                else:
                    st = status

                # Progress bar
                filled = int(progress / 100 * 20)
                bar = "█" * filled + "░" * (20 - filled)
                size_info = ""
                if dl_size and total_size:
                    size_info = f" {dl_size}/{total_size}"
                elif total_size:
                    size_info = f" ?/{total_size}"
                prog_text = f"[{A}]{bar}[/{A}] {progress:.0f}%{size_info}"

                table.add_row(
                    str(i + 1),
                    f"[bold {T}]{title}[/bold {T}]",
                    st,
                    prog_text,
                    speed or "—",
                    eta or "—",
                )
            return table

        with Live(generate_table(), refresh_per_second=4) as live:
            while True:
                live.update(generate_table())
                
                # Non-blocking input handling check
                if msvcrt.kbhit():
                    key = msvcrt.getch().decode("utf-8").lower()
                    
                    if key == "b" or key == "q":
                        break
                    
                    elif key == "r":
                        # Retry all failed
                        queue = self.download_manager.get_queue()
                        failed = [t for t in queue if t["status"] == "error"]
                        for t in failed:
                            self.download_manager.retry_task(t["id"])
                    
                    elif key == "c":
                        self.download_manager.clear_completed()
                    
                    elif key == "o":
                        default_dl = os.path.join(os.path.expanduser("~"), "Downloads", "Cinema-CLI")
                        downloads_root = self.settings.get("download_path") or default_dl
                        os.makedirs(downloads_root, exist_ok=True)
                        try:
                            if sys.platform == "win32":
                                os.startfile(downloads_root)
                            elif sys.platform == "darwin":
                                subprocess.Popen(["open", downloads_root])
                            else:
                                subprocess.Popen(["xdg-open", downloads_root])
                        except Exception:
                            pass
                    
                    elif key == "x":
                        # Interactive removal needs to pause Live temporarily or use input()
                        # But input() inside Live can break layout. 
                        # Ideally, we stop Live, ask, then resume.
                        live.stop()
                        idx_str = console.input(f"[{A}]Enter item # to remove (or Enter to cancel): [/{A}]").strip()
                        try:
                            if idx_str:
                                idx = int(idx_str) - 1
                                queue = self.download_manager.get_queue()
                                if 0 <= idx < len(queue):
                                    self.download_manager.remove_task(queue[idx]["id"])
                        except ValueError:
                            pass
                        live.start()

                time.sleep(0.1)

    # ═══════════════════════════════════════════════════════════
    # Favorites & History
    # ═══════════════════════════════════════════════════════════
    def handle_favorites(self):
        if not self.favorites:
            print_header("Favorites")
            console.print(
                "[yellow]No favorites yet. Press 'F' on any item to add it![/yellow]"
            )
            time.sleep(2)
            return
        while True:
            sel = selection_menu(self.favorites, "My Favorites")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                self.favorites = load_json_data(FAVORITES_FILE) or []
                if not self.favorites:
                    break
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_history(self):
        if not self.history:
            print_header("History")
            console.print("[yellow]Your watch history is empty.[/yellow]")
            time.sleep(2)
            return
        while True:
            sel = selection_menu(self.history, "Watch History")
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def update_history(self, media, stats, episode=None):
        if not self.history:
            self.history = []

        existing = None
        for item in self.history:
            if item.get("id") == media.get("id"):
                existing = item
                break

        if not existing:
            self.history.insert(0, media)
            existing = self.history[0]

        existing["last_watched"] = time.time()

        if episode:
            existing["last_episode"] = {
                "season": episode.get("season_number"),
                "episode": episode.get("episode_number"),
                "name": episode.get("name"),
                "position": stats.get("position"),
                "duration": stats.get("duration"),
            }
        else:
            existing["position"] = stats.get("position")
            existing["duration"] = stats.get("duration")
            existing["finished"] = stats.get("finished")

        save_json_data(HISTORY_FILE, self.history)

    # ═══════════════════════════════════════════════════════════
    # Settings
    # ═══════════════════════════════════════════════════════════
    def handle_settings(self):
        P, S, A, Su, T, W = _get_colors()
        print_header("Settings")
        console.print(
            f"[bold {T}]1. Backend URL:[/bold {T}] {self.settings.get('backend', BACKEND_URL)}"
        )
        console.print(
            f"[bold {T}]2. TMDB API Key:[/bold {T}] {self.settings.get('tmdb_key', 'Using Default')}"
        )
        console.print(
            f"[bold {T}]3. Movie Filename Template:[/bold {T}] {self.settings.get('filename_template')}"
        )
        console.print(
            f"[bold {T}]4. TV Filename Template:[/bold {T}] {self.settings.get('filename_template_tv')}"
        )
        console.print(
            f"[bold {T}]5. Preferred Quality:[/bold {T}] {self.settings.get('preferred_quality', 'auto')}"
        )
        console.print(
            f"[bold {T}]6. Theme:[/bold {T}] {get_theme_label(self.settings.get('theme', 'default'))}"
        )
        console.print(
            f"[bold {T}]7. Subtitle Language:[/bold {T}] {self.settings.get('subtitle_language', 'ar')}"
        )
        console.print(
            f"[bold {T}]8. Download Directory:[/bold {T}] {self.settings.get('download_path', 'downloads')}"
        )

        choice = console.input(
            f"\n[bold {A}]Select setting to change (1-8) or Enter to back: [/bold {A}]"
        )

        if choice == "1":
            new_val = console.input(
                f"[bold {A}]Enter new backend URL: [/bold {A}]"
            )
            if new_val.strip():
                self.settings["backend"] = new_val.strip()
        elif choice == "2":
            new_val = console.input(
                f"[bold {A}]Enter new TMDB API Key: [/bold {A}]"
            )
            if new_val.strip():
                self.settings["tmdb_key"] = new_val.strip()
        elif choice == "3":
            console.print("[dim]Tokens: {title}, {year}, {quality}, {provider}[/dim]")
            new_val = console.input(
                f"[bold {A}]Enter new Movie Template: [/bold {A}]"
            )
            if new_val.strip():
                self.settings["filename_template"] = new_val.strip()
        elif choice == "4":
            console.print(
                "[dim]Tokens: {title}, {year}, {season}, {episode}, {quality}, {provider}[/dim]"
            )
            new_val = console.input(
                f"[bold {A}]Enter new TV Template: [/bold {A}]"
            )
            if new_val.strip():
                self.settings["filename_template_tv"] = new_val.strip()
        elif choice == "5":
            quality_opts = ["auto", "4K", "1080p", "720p", "480p"]
            console.print("[dim]Options: " + ", ".join(quality_opts) + "[/dim]")
            new_val = console.input(
                f"[bold {A}]Enter preferred quality: [/bold {A}]"
            )
            if new_val.strip() in quality_opts:
                self.settings["preferred_quality"] = new_val.strip()
            else:
                console.print("[yellow]Invalid quality. Keeping current setting.[/yellow]")
                time.sleep(1)
                return
        elif choice == "6":
            self._select_theme()
            return
        elif choice == "7":
            new_val = console.input(
                f"[bold {A}]Enter subtitle language code (e.g. ar, en): [/bold {A}]"
            )
            if new_val.strip():
                self.settings["subtitle_language"] = new_val.strip()
        elif choice == "8":
            new_val = console.input(
                f"[bold {A}]Enter download directory path: [/bold {A}]"
            )
            if new_val.strip():
                self.settings["download_path"] = new_val.strip()
        else:
            return

        save_json_data(SETTINGS_FILE, self.settings)
        console.print("[green]Settings saved![/green]")
        time.sleep(1)

    def _select_theme(self):
        P, S, A, Su, T, W = _get_colors()
        themes = list_themes()
        console.print(f"\n[bold {A}]Available Themes:[/bold {A}]")
        for i, name in enumerate(themes, 1):
            label = get_theme_label(name)
            marker = " ✓" if name == self.settings.get("theme", "default") else ""
            console.print(f"  {i}. {label}{marker}")

        choice = console.input(
            f"\n[bold {A}]Select theme (1-{len(themes)}): [/bold {A}]"
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(themes):
                theme_name = themes[idx]
                self.settings["theme"] = theme_name
                apply_theme(theme_name)
                save_json_data(SETTINGS_FILE, self.settings)
                console.print(f"[green]Theme changed to {get_theme_label(theme_name)}![/green]")
                time.sleep(1)
            else:
                console.print("[yellow]Invalid selection.[/yellow]")
                time.sleep(1)
        except ValueError:
            return

    # ═══════════════════════════════════════════════════════════
    # Favorites toggle
    # ═══════════════════════════════════════════════════════════
    def toggle_favorite(self, item):
        item_id = item.get("id")
        exists = any(f.get("id") == item_id for f in self.favorites)
        if exists:
            self.favorites = [f for f in self.favorites if f.get("id") != item_id]
            console.print("[yellow]Removed from favorites.[/yellow]")
        else:
            self.favorites.insert(0, item)
            console.print("[green]Added to favorites![/green]")
        save_json_data(FAVORITES_FILE, self.favorites)
        time.sleep(0.5)

    # ═══════════════════════════════════════════════════════════
    # Media Handling (with Cast & Crew display)
    # ═══════════════════════════════════════════════════════════
    def handle_media(self, media):
        self.history = [h for h in self.history if h.get("id") != media.get("id")]
        self.history.insert(0, media)
        self.history = self.history[:50]
        save_json_data(HISTORY_FILE, self.history)

        m_type = media.get("media_type", "movie")

        # Show cast & crew details
        self._show_cast(media)

        if m_type == "movie":
            self.play_movie(media)
        else:
            self.show_seasons(media)

    def _show_cast(self, media):
        """Display top cast members for a media item."""
        from rich.panel import Panel
        from rich.table import Table

        P, S, A, Su, T, W = _get_colors()
        tmdb_id = media.get("id")
        m_type = media.get("media_type", "movie")

        try:
            credits = self.api.get_credits(tmdb_id, m_type)
            if not credits or not credits.get("cast"):
                return

            cast = credits["cast"][:8]  # Top 8 cast members

            table = Table(
                title="🎭 Top Cast",
                show_header=True,
                header_style=f"bold {A}",
                border_style=P,
                title_style=f"bold {P}",
            )
            table.add_column("Actor", style=f"bold {T}")
            table.add_column("Character", style=f"{S}")

            for member in cast:
                name = member.get("name", "Unknown")
                character = member.get("character", "—")
                table.add_row(name, character)

            console.print(table)
            console.print()
        except Exception:
            pass  # Silently skip if credits fail

    # ═══════════════════════════════════════════════════════════
    # Movie Playback (Auto Source Selection)
    # ═══════════════════════════════════════════════════════════
    def play_movie(self, media):
        title = media.get("title")
        tmdb_id = media.get("id")
        data = self.api.get_sources_api(tmdb_id, "movie")

        rel = media.get("release_date") or ""
        year = rel[:4] if isinstance(rel, str) and len(rel) >= 4 else None

        meta = {"year": year, "tmdb_id": tmdb_id, "type": "movie"}

        stats = self.handle_sources(title, data, meta)
        if isinstance(stats, dict):
            self.update_history(media, stats, episode=None)

    # ═══════════════════════════════════════════════════════════
    # TV Show: Seasons → Episodes → Playback
    # ═══════════════════════════════════════════════════════════
    def show_seasons(self, media):
        print_header(f"{media.get('name')} - Seasons")
        data = self.api.get_tmdb_data(f"tv/{media['id']}")
        if not data:
            return
        seasons = [s for s in data.get("seasons", []) if s.get("season_number") > 0]

        def fmt_season(x):
            name = x.get("name", "")
            air = x.get("air_date") or "????-??-??"
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else "????"
            rating = x.get("vote_average", 0)
            return f"{name} ({year}) | ⭐ {rating:.1f} | TV"

        while True:
            sel = selection_menu(
                seasons,
                f"{media.get('name')} Seasons",
                show_details=False,
                formatter=fmt_season,
            )
            if not sel or sel["action"] == "back":
                break
            if sel["action"] == "select":
                self.show_episodes(media, sel["value"])

    def show_episodes(self, media, season):
        s_num = season["season_number"]
        print_header(f"{media.get('name')} - Season {s_num}")
        data = self.api.get_tmdb_data(f"tv/{media['id']}/season/{s_num}")
        if not data:
            return
        episodes = data.get("episodes", [])

        def fmt_ep(x):
            name = x.get("name", "Unknown")
            air = x.get("air_date") or "N/A"
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else "N/A"
            rating = x.get("vote_average", 0)
            return f"{name} ({year}) | ⭐ {rating:.1f} | TV"

        selected_idx = 0
        while True:
            sel = selection_menu(
                episodes,
                f"Season {s_num} Episodes",
                show_details=True,
                formatter=fmt_ep,
                default_index=selected_idx,
            )
            if not sel or sel["action"] == "back":
                break

            if sel["action"] == "batch":
                self.handle_batch_download(media, season, episodes)
                continue

            if sel["action"] == "select":
                ep = sel["value"]
                selected_idx = episodes.index(ep)

                while True:
                    title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"
                    data = self.api.get_sources_api(
                        media["id"], "tv", s_num, ep["episode_number"]
                    )

                    air = ep.get("air_date") or ""
                    year = air[:4] if isinstance(air, str) and len(air) >= 4 else None

                    meta = {
                        "year": year,
                        "season": s_num,
                        "episode": ep.get("episode_number"),
                        "tmdb_id": media["id"],
                        "type": "tv",
                    }

                    stats = self.handle_sources(title, data, meta)

                    if not stats:
                        break

                    if isinstance(stats, dict):
                        self.update_history(media, stats, episode=ep)

                    # ── Smart Autoplay ──────────────────────
                    if selected_idx + 1 < len(episodes):
                        next_ep = episodes[selected_idx + 1]
                        autoplay_result = self._autoplay_countdown(
                            media, season, next_ep, episodes, selected_idx
                        )
                        if autoplay_result == "autoplay":
                            selected_idx += 1
                            ep = episodes[selected_idx]
                            continue
                        elif autoplay_result == "cancel":
                            # Show manual options
                            pass
                        else:
                            break
                    else:
                        console.print(
                            "[yellow]No next episode in this season.[/yellow]"
                        )
                        time.sleep(1)
                        break

                    # Manual post-playback options
                    opts = [
                        "Next Episode",
                        "Previous Episode",
                        "Replay",
                        "Back to List",
                    ]
                    fin_sel = selection_menu(
                        opts,
                        "Finished Watching",
                        show_details=False,
                        formatter=lambda x: x,
                    )

                    if not fin_sel or fin_sel["action"] in ["back", "quit"]:
                        break

                    choice = fin_sel["value"]
                    if choice == "Next Episode":
                        if selected_idx + 1 < len(episodes):
                            selected_idx += 1
                            ep = episodes[selected_idx]
                        else:
                            console.print(
                                "[yellow]No next episode in this season.[/yellow]"
                            )
                            time.sleep(1)
                            break
                    elif choice == "Previous Episode":
                        if selected_idx > 0:
                            selected_idx -= 1
                            ep = episodes[selected_idx]
                        else:
                            console.print("[yellow]No previous episode.[/yellow]")
                            time.sleep(1)
                            break
                    elif choice == "Replay":
                        pass
                    elif choice == "Back to List":
                        break

    # ═══════════════════════════════════════════════════════════
    # Smart Autoplay Countdown
    # ═══════════════════════════════════════════════════════════
    def _autoplay_countdown(self, media, season, next_ep, episodes, current_idx):
        """
        Show a 10-second countdown before auto-playing the next episode.
        Returns: 'autoplay' if countdown completes, 'cancel' if user interrupts.
        """
        P, S, A, Su, T, W = _get_colors()
        next_title = f"S{season['season_number']}E{next_ep['episode_number']} - {next_ep.get('name', '')}"

        clear()
        console.print(
            f"\n[bold {P}]⏭️  Next: {next_title}[/bold {P}]"
        )
        console.print(
            f"\n[dim]Press Enter to cancel autoplay...[/dim]\n"
        )

        cancelled = threading.Event()

        def _wait_for_input():
            try:
                input()
                cancelled.set()
            except EOFError:
                pass

        input_thread = threading.Thread(target=_wait_for_input, daemon=True)
        input_thread.start()

        for remaining in range(10, 0, -1):
            if cancelled.is_set():
                return "cancel"
            console.print(f"  [bold {A}]Starting in {remaining}...[/bold {A}]", end="\r")
            time.sleep(1)

        if cancelled.is_set():
            return "cancel"

        console.print(f"\n[bold {Su}]▶ Auto-playing next episode...[/bold {Su}]")
        time.sleep(0.3)
        return "autoplay"

    # ═══════════════════════════════════════════════════════════
    # Batch Download (Auto Source Selection)
    # ═══════════════════════════════════════════════════════════
    def handle_batch_download(self, media, season, episodes):
        P, S, A, Su, T, W = _get_colors()
        s_num = season["season_number"]

        def fmt_ep(x):
            name = x.get("name", "Unknown")
            ep_num = x.get("episode_number", "?")
            return f"E{ep_num} - {name}"

        selected_episodes = multi_selection_menu(
            episodes, f"Select Episodes to Download (S{s_num})", formatter=fmt_ep
        )
        if not selected_episodes:
            return

        console.print(
            f"\n[bold {P}]Preparing batch download for {len(selected_episodes)} episodes...[/bold {P}]"
        )

        preferred_quality = self.settings.get("preferred_quality", "auto")
        if preferred_quality == "auto":
            preferred_quality = None

        for ep in selected_episodes:
            title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"

            data = self.api.get_sources_api(
                media["id"], "tv", s_num, ep["episode_number"]
            )
            files = data.get("files", [])
            subtitles = data.get("subtitles", [])

            # Fix malformed URLs (e.g., https:/// with triple slash)
            valid_files = []
            for f in files:
                url = f.get("file", "")
                if url.startswith("https:///") or url.startswith("http:///"):
                    # Attempt to fix it
                    fixed_url = url.replace("https:///", "https://").replace("http:///", "http://")
                    f["file"] = fixed_url
                valid_files.append(f)
            
            files = valid_files

            if not files:
                console.print(
                    f"[yellow]No sources found for {title}. Skipping...[/yellow]"
                )
                continue

            # Auto-select best working source
            console.print(
                f"\n[bold {A}]🔍 Finding source for: {title}[/bold {A}]"
            )

            def on_progress(i, total, src):
                q = src.get("quality", "auto")
                p = src.get("provider", "src")
                console.print(
                    f"  [{A}]Testing {i+1}/{total}: {p} [{q}]...[/{A}]"
                )

            selected_source = find_working_source(
                files,
                preferred_quality=preferred_quality,
                on_progress=on_progress,
            )

            if not selected_source:
                console.print(
                    f"[red]No working source found for: {title}. Skipping...[/red]"
                )
                continue

            console.print(
                f"[green]✓ Using {selected_source.get('provider')} [{selected_source.get('quality')}][/green]"
            )

            air = ep.get("air_date") or ""
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else None
            meta = {
                "year": year,
                "season": s_num,
                "episode": ep.get("episode_number"),
                "type": "tv",
            }

            template = self.settings.get(
                "filename_template_tv", "{title}.S{season}E{episode}"
            )
            filename = generate_filename(template, title, meta, selected_source)

            self.download_manager.add_task(
                selected_source.get("file"),
                filename,
                title,
                subtitles,
                selected_source.get("headers"),
                meta,
            )

        console.print(f"\n[bold {Su}]Batch download queued![/bold {Su}]")
        time.sleep(2)

    # ═══════════════════════════════════════════════════════════
    # Source Handling (Auto Selection + Play/Download)
    # ═══════════════════════════════════════════════════════════
    def handle_sources(self, title, data, meta=None):
        P, S, A, Su, T, W = _get_colors()
        files = data.get("files", [])
        subtitles = data.get("subtitles", [])
        if not files:
            console.print("[red]No streams found.[/red]")
            time.sleep(1.5)
            return False

        # Fix malformed URLs (e.g., https:/// -> https://)
        for f in files:
            url = f.get("file", "")
            if url.startswith("https:///") or url.startswith("http:///"):
                f["file"] = url.replace("https:///", "https://").replace("http:///", "http://")

        # Resume playback support
        start_time = 0
        playback_key = None
        if meta and meta.get("tmdb_id"):
            if meta.get("type") == "movie":
                playback_key = f"movie_{meta['tmdb_id']}"
            elif meta.get("type") == "tv":
                playback_key = (
                    f"tv_{meta['tmdb_id']}_s{meta['season']}_e{meta['episode']}"
                )

        if playback_key and playback_key in self.playback:
            info = self.playback[playback_key]
            pos = info.get("position", 0)
            dur = info.get("duration", 0)
            if not info.get("finished") and pos > 10 and (dur == 0 or pos < dur * 0.95):
                import math

                mins = math.floor(pos / 60)
                secs = int(pos % 60)
                res = selection_menu(
                    [f"Resume from {mins}:{secs:02d}", "Start from Beginning"],
                    "Resume Playback?",
                    show_details=False,
                    formatter=lambda x: x,
                )
                if res and res["value"].startswith("Resume"):
                    start_time = pos

        # ── Auto Source Selection ───────────────────────────
        preferred_quality = self.settings.get("preferred_quality", "auto")
        if preferred_quality == "auto":
            preferred_quality = None

        console.print(f"\n[bold {A}]🔍 Finding best source for: {title}[/bold {A}]")

        def on_progress(i, total, src):
            q = src.get("quality", "auto")
            p = src.get("provider", "src")
            console.print(f"  [{A}]Testing {i+1}/{total}: {p.upper()} [{q}]...[/{A}]")

        selected = find_working_source(
            files,
            preferred_quality=preferred_quality,
            on_progress=on_progress,
        )

        if not selected:
            # Build descriptive label
            if meta and meta.get("type") == "tv":
                label = f"{title} S{meta.get('season', '?'):02}E{meta.get('episode', '?'):02}"
            else:
                label = title
            console.print(f"\n[bold red]No working source found for: {label}[/bold red]")
            time.sleep(2)
            return False

        q = selected.get("quality", "auto")
        p = selected.get("provider", "src")
        console.print(f"\n[bold {Su}]✓ Source found: {p.upper()} [{q}][/bold {Su}]")

        # Ask Play or Download
        act_items = ["▶ Play", "⬇ Download"]
        act = selection_menu(
            act_items,
            f"{title} — {p.upper()} [{q}]",
            show_details=False,
            formatter=lambda x: x,
        )
        if not act or act.get("action") in ["back", "quit", None]:
            return False

        chosen = act.get("value", "")
        if chosen == "▶ Play":
            stats = play_stream(
                selected.get("file"),
                title,
                subtitles,
                selected.get("headers"),
                meta,
                start_time=start_time,
                settings=self.settings,
            )
            if isinstance(stats, dict) and playback_key:
                self.playback[playback_key] = stats
                save_json_data(PLAYBACK_FILE, self.playback)
            return stats or False

        elif chosen == "⬇ Download":
            template = self.settings.get("filename_template", "{title}.{year}")
            if meta and meta.get("type") == "tv":
                template = self.settings.get(
                    "filename_template_tv", "{title}.S{season}E{episode}"
                )

            filename = generate_filename(template, title, meta, selected)
            self.download_manager.add_task(
                selected.get("file"),
                filename,
                title,
                subtitles,
                selected.get("headers"),
                meta=meta,
            )
            return False

    def start_player(self, url, title):
        P, S, A, Su, T, W = _get_colors()
        print_header(title)
        console.print(
            "1. ▶ Play with MPV\n2. ⬇ Download Video\n3. 🔗 Copy URL\n4. ⬅ Back"
        )
        choice = console.input(
            f"\n[bold {A}]Select action (1-4): [/bold {A}]"
        )

        if choice == "1":
            play_video(url, title)
        elif choice == "2":
            template = self.settings.get("filename_template", "{title}.{year}")
            filename = generate_filename(
                template,
                title,
                meta=None,
                selected={"provider": "direct", "quality": "auto"},
            )
            self.download_manager.add_task(url, filename, title, None, None)
        elif choice == "3":
            console.print(f"\n[bold]URL:[/bold] {url}")
            console.input("\nPress Enter to return...")
        else:
            return


if __name__ == "__main__":
    cli = CinemaCLI()
    try:
        show_splash()
        cli.main_menu()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
