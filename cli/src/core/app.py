import sys
import os
import time
import atexit
import subprocess
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.columns import Columns
from rich.layout import Layout
from rich.align import Align
from rich import box

from src.core.settings import SettingsManager
from src.core.data import DataManager
from src.utils.api import APIClient
from src.utils.download_manager import DownloadManager
from src.ui.ui import print_header, selection_menu, show_splash, clear
from src.utils.utils import sanitize_filename
from src.config import console

# Import UI handlers (we will move these to a separate UI module later, 
# but for now we might need to keep some logic here or import from a UI controller)
# Actually, to avoid circular imports, let's keep the main menu loop here 
# and delegate specific screens to methods.

from src.services.sources import SourceManager
from src.services.subtitles import SubtitleManager

class CinemaApp:
    def __init__(self):
        self.console = console
        self.settings = SettingsManager()
        self.data = DataManager()
        
        # Initialize Theme
        from src.ui.theme import ThemeManager
        self.theme_manager = ThemeManager(self.settings)
        self.theme_manager.load_theme() # Load saved theme
        
        # Ensure backend is running
        self._backend_proc = None
        self._maybe_start_backend(self.settings.backend_url)
        atexit.register(self._cleanup_backend)

        # Initialize API
        self.api = APIClient(self.settings)
        
        # Initialize Managers
        self.source_manager = SourceManager(self.api)
        self.subtitle_manager = SubtitleManager()
        
        from src.services.library import LibraryManager
        self.library_manager = LibraryManager()

        # Initialize Download Manager
        self.download_manager = DownloadManager(self.source_manager)
        self.download_manager.start()

    def run(self):
        show_splash()
        time.sleep(2)
        self.main_menu()

    def main_menu(self):
        while True:
            options = [
                {"name": "🔍 Search Movies & TV", "action": self.handle_search},
                {"name": "🌍 Discovery & New Releases", "action": self.handle_discovery},
                {"name": "📈 Trending This Week", "action": self.handle_trending},
                {"name": "🔥 Popular Content", "action": self.handle_popular},
                {"name": "🎭 Browse by Genre", "action": self.handle_genres},
                {"name": "⭐ My Favorites", "action": self.handle_favorites},
                {"name": "🕒 Watch History", "action": self.handle_history},
                {"name": "📁 Local Library", "action": self.handle_library}, 
                {"name": "📥 Download Manager", "action": self.handle_downloads},
                {"name": "⚙️ Settings & Theme", "action": self.handle_settings},
                {"name": "❌ Exit Application", "action": sys.exit},
            ]

            sel = selection_menu(
                options, 
                "Main Menu", 
                show_details=False, 
                formatter=lambda x: x["name"]
            )
            
            if not sel or sel["action"] == "quit":
                sys.exit()
                
            if sel["action"] == "select":
                action = sel["value"]["action"]
                try:
                    action()
                except SystemExit:
                    raise
                except Exception as e:
                    self.console.print(f"[red]Error: {e}[/red]")
                    time.sleep(2)

    # --- Handlers ---
    # These methods will largely be similar to the original main.py but using self.data and self.settings

    def handle_search(self):
        # Premium split menu for search
        search_options = [
            {"name": "🎬 Movies & TV Shows", "val": "media"},
            {"name": "👤 People (Actors/Directors)", "val": "person"},
        ]
        
        sel = selection_menu(search_options, "Search", show_details=False, formatter=lambda x: x["name"])
        
        if not sel or sel["action"] != "select":
            return
            
        search_type = sel["value"]["val"]
        
        if search_type == "person":
            self.handle_person_search()
            return

        query = self.console.input(f"\n[bold {self.theme_manager.accent}]🔍 Search for a movie or TV show: [/bold {self.theme_manager.accent}]")
        if not query.strip():
            return

        with self.console.status(f"[{self.theme_manager.accent}]Searching...[/{self.theme_manager.accent}]", spinner="dots"):
            data = self.api.get_tmdb_data("search/multi", {"query": query})
            
        if not data or not data.get("results"):
            self.console.print(f"[{self.theme_manager.warning}]No results found.[/{self.theme_manager.warning}]")
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

    def handle_person_search(self):
        query = self.console.input(f"\n[bold {self.theme_manager.accent}]🔍 Search for a person: [/bold {self.theme_manager.accent}]")
        if not query.strip(): return

        with self.console.status(f"[{self.theme_manager.accent}]Searching people...[/{self.theme_manager.accent}]", spinner="dots"):
            data = self.api.search_person(query)
            
        if not data or not data.get("results"):
            self.console.print(f"[{self.theme_manager.warning}]No people found.[/{self.theme_manager.warning}]")
            time.sleep(1.5)
            return

        results = data["results"]
        
        while True:
             sel = selection_menu(results, f"People matching '{query}'", show_details=False, formatter=lambda x: x["name"])
             if not sel or sel["action"] == "back": break
             
             if sel["action"] == "select":
                 self.show_person_details(sel["value"])

    def show_person_details(self, person):
        pid = person.get("id")
        
        with self.console.status(f"[{self.theme_manager.accent}]Fetching details...[/{self.theme_manager.accent}]", spinner="dots"):
            details = self.api.get_person_details(pid)
            credits = self.api.get_person_credits(pid)
        
        if not details or not credits: return
        
        cast = credits.get("cast", [])
        cast.sort(key=lambda x: x.get("popularity", 0), reverse=True)
        known_for = [c for c in cast if c.get("media_type") in ["movie", "tv"]][:50]
        
        while True:
            print_header(details.get("name"))
            
            # Info Table
            info_table = Table(box=box.SIMPLE, show_header=False, border_style=self.theme_manager.primary)
            info_table.add_column("Key", style=f"bold {self.theme_manager.accent}")
            info_table.add_column("Value")
            
            info_table.add_row("Birthday", details.get("birthday") or "Unknown")
            info_table.add_row("Place of Birth", details.get("place_of_birth") or "Unknown")
            info_table.add_row("Role", details.get("known_for_department") or "Unknown")
            
            bio = details.get('biography', 'No biography available.')
            if len(bio) > 500: bio = bio[:500] + "..."
            
            self.console.print(Panel(
                bio,
                title=f"[bold]Biography[/bold]",
                border_style=self.theme_manager.secondary,
                box=box.ROUNDED
            ))
            self.console.print(info_table)
            self.console.print(f"\n[bold {self.theme_manager.primary}]Filmography / Known For:[/bold {self.theme_manager.primary}]")

            sel = selection_menu(
                known_for, 
                f"{details.get('name')} - Works", 
                show_details=False, 
                formatter=lambda x: f"{x.get('title') or x.get('name')} ({x.get('release_date', '')[:4] or 'N/A'}) as {x.get('character', 'Unknown')}"
            )
            
            if not sel or sel["action"] == "back": break
            if sel["action"] == "select":
                self.handle_media(sel["value"])
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])

    def handle_discovery(self):
        print_header("Discovery")
        options = [
            {"name": "🆕 New Movies (In Theaters/Digital)", "val": "movies"},
            {"name": "📺 New Episodes (Airing Today)", "val": "episodes"},
            {"name": "🔥 Trending TV (Today)", "val": "trending_tv_today"},
            {"name": "🔥 Movie of the Day", "val": "movie_of_the_day"},
        ]
        
        while True:
            sel = selection_menu(options, "Discovery Options", show_details=False, formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back":
                break
            
            if sel["action"] == "select":
                choice = sel["value"]["val"]
                if choice == "movies": self.browse_new_movies()
                elif choice == "episodes": self.browse_new_episodes()
                elif choice == "trending_tv_today": self.browse_trending_tv_today()
                elif choice == "movie_of_the_day": self.browse_movie_of_the_day()

    # Stub methods for browsing - adapting from main.py
    def browse_new_movies(self):
        self._browse_paginated(self.api.get_new_movies, "New Movies", "movie")

    def browse_new_episodes(self):
        self._browse_paginated(self.api.get_new_episodes, "New Episodes", "tv")

    def browse_trending_tv_today(self):
        self._browse_paginated(self.api.get_trending_tv_today, "Trending TV Today", "tv")

    def browse_movie_of_the_day(self):
        self._browse_paginated(self.api.get_trending_movies_today, "Movie of the Day", "movie")
    
    def _browse_paginated(self, api_func, title, media_type):
        page = 1
        while True:
            with self.console.status(f"[{self.theme_manager.accent}]Loading {title}...[/{self.theme_manager.accent}]", spinner="dots"):
                data = api_func(page=page)
            if not data: return
            
            results = data.get("results", [])
            for r in results: r["media_type"] = media_type
            
            # Nav
            if data.get("total_pages", 1) > page:
                results.append({"id": "next_page", "title": "➡️ Next Page", "special": True})
            if page > 1:
                results.insert(0, {"id": "prev_page", "title": "⬅️ Previous Page", "special": True})
            
            sel = selection_menu(results, f"{title} (Page {page})")
            if not sel or sel["action"] == "back": break
            
            val = sel["value"]
            if val.get("special"):
                if val["id"] == "next_page": page += 1
                elif val["id"] == "prev_page": page -= 1
                continue
            
            if sel["action"] == "favorite":
                self.toggle_favorite(val)
                continue
            
            if sel["action"] == "select":
                self.handle_media(val)


    def handle_trending(self):
        with self.console.status(f"[{self.theme_manager.accent}]Fetching trending...[/{self.theme_manager.accent}]", spinner="dots"):
            data = self.api.get_tmdb_data("trending/all/week")
        if not data: return
        results = [r for r in data["results"] if r.get("media_type") in ["movie", "tv"]]
        self._browse_list(results, "Trending This Week")

    def handle_popular(self):
        print_header("Popular")
        # Simple prompt for simplicity
        self.console.print("1. Movies\n2. TV Shows")
        choice = self.console.input(f"\n[bold {self.theme_manager.accent}]Select (1-2): [/bold {self.theme_manager.accent}]")
        m_type = "movie" if choice == "1" else "tv"
        
        with self.console.status(f"[{self.theme_manager.accent}]Fetching popular...[/{self.theme_manager.accent}]", spinner="dots"):
            data = self.api.get_tmdb_data(f"{m_type}/popular")
        if not data: return
        results = data["results"]
        for r in results: r["media_type"] = m_type
        self._browse_list(results, f"Popular {m_type.title()}s")

    def _browse_list(self, results, title):
        while True:
            sel = selection_menu(results, title)
            if not sel or sel["action"] == "back": break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_genres(self):
        # ... (Similar to main.py implementation)
        print_header("Genres")
        self.console.print("1. Movies\n2. TV Shows")
        choice = self.console.input(f"\n[bold {self.theme_manager.accent}]Select (1-2): [/bold {self.theme_manager.accent}]")
        m_type = "movie" if choice == "1" else "tv" # Default to TV if not 1

        with self.console.status(f"[{self.theme_manager.accent}]Fetching genres...[/{self.theme_manager.accent}]", spinner="dots"):
            data = self.api.get_tmdb_data(f"genre/{m_type}/list")
        if not data: return
        genres = data["genres"]
        
        # Simple selection for genres
        # We can use selection_menu here too if we adapt formatting
        sel = selection_menu(genres, "Genres", show_details=False, formatter=lambda x: x["name"])
        if sel and sel["action"] == "select":
             genre = sel["value"]
             with self.console.status(f"[{self.theme_manager.accent}]Fetching {genre['name']}...[/{self.theme_manager.accent}]", spinner="dots"):
                 data = self.api.get_tmdb_data(f"discover/{m_type}", {"with_genres": genre["id"]})
             if data:
                 results = data["results"]
                 for r in results: r["media_type"] = m_type
                 self._browse_list(results, f"{genre['name']} {m_type.title()}s")

    def handle_favorites(self):
        if not self.data.favorites:
            print_header("Favorites")
            self.console.print("[yellow]No favorites yet.[/yellow]")
            time.sleep(1)
            return
        
        while True:
            sel = selection_menu(self.data.favorites, "My Favorites")
            if not sel or sel["action"] == "back": break
            if sel["action"] == "favorite":
                self.toggle_favorite(sel["value"])
                continue
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_history(self):
        if not self.data.history:
            print_header("History")
            self.console.print("[yellow]History empty.[/yellow]")
            time.sleep(1)
            return

        while True:
            sel = selection_menu(self.data.history, "Watch History")
            if not sel or sel["action"] == "back": break
            if sel["action"] == "select":
                self.handle_media(sel["value"])

    def handle_downloads(self):
        # Enhanced view with download_menu
        from src.ui.ui import download_menu
        
        while True:
            selected_task = download_menu(self.download_manager)
            if not selected_task: # Back/Quit
                break
            
            # Show management options for selected task
            opts = []
            status = selected_task.get("status")
            
            if status in ["downloading", "pending"]:
                opts.append({"name": "❌ Cancel Download", "val": "cancel"})
            elif status in ["completed", "error"]:
                 opts.append({"name": "🗑️ Remove from List", "val": "remove"})
            
            if status == "error":
                opts.append({"name": "🔄 Retry Download", "val": "retry"})
            
            opts.append({"name": "🔙 Back", "val": "back"})

            sel = selection_menu(opts, f"Manage: {selected_task.get('filename')}", show_details=False, formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back" or sel["value"]["val"] == "back": continue
            
            act = sel["value"]["val"]
            if act == "cancel":
                self.download_manager.remove_task(selected_task["id"])
                self.console.print("[yellow]Download cancelled.[/yellow]")
                time.sleep(1)
            elif act == "remove":
                self.download_manager.remove_task(selected_task["id"])
                self.console.print("[yellow]Removed from list.[/yellow]")
                time.sleep(1)
            elif act == "retry":
                self.download_manager.retry_task(selected_task["id"])
                self.console.print("[green]Retrying...[/green]")
                time.sleep(1)

    def handle_settings(self):
        while True:
            current_theme = self.settings.get("theme", "default")
            subs = self.settings.subtitle_languages
            paths = self.settings.local_library_paths
            
            options = [
                {"name": f"🌐 Backend URL: {self.settings.backend_url}", "val": "backend"},
                {"name": f"🔑 TMDB Key: {self.settings.tmdb_key or 'Default'}", "val": "tmdb"},
                {"name": f"🎬 Movie Template: {self.settings.filename_template_movie}", "val": "movie_tmp"},
                {"name": f"📺 TV Template: {self.settings.filename_template_tv}", "val": "tv_tmp"},
                {"name": f"📁 Local Library Paths: ({len(paths)})", "val": "paths"},
                {"name": f"🎨 Theme: {current_theme.title()}", "val": "theme"},
                {"name": f"💬 Subtitle Languages: {', '.join(subs).upper()}", "val": "subs"},
                {"name": "🔙 Back to Main Menu", "val": "back"},
            ]
            
            sel = selection_menu(options, "Settings", show_details=False, formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back" or (sel["action"] == "select" and sel["value"]["val"] == "back"):
                break
                
            choice = sel["value"]["val"]
            print_header("Settings")
            
            if choice == "backend":
                val = self.console.input(f"[bold {self.theme_manager.accent}]New Backend URL: [/bold {self.theme_manager.accent}]")
                if val: self.settings.backend_url = val
            elif choice == "tmdb":
                val = self.console.input(f"[bold {self.theme_manager.accent}]New TMDB Key: [/bold {self.theme_manager.accent}]")
                if val: self.settings.tmdb_key = val
            elif choice == "movie_tmp":
                val = self.console.input(f"[bold {self.theme_manager.accent}]New Movie Template: [/bold {self.theme_manager.accent}]")
                if val: self.settings.filename_template_movie = val
            elif choice == "tv_tmp":
                val = self.console.input(f"[bold {self.theme_manager.accent}]New TV Template: [/bold {self.theme_manager.accent}]")
                if val: self.settings.filename_template_tv = val
            elif choice == "paths":
                self.handle_path_settings()
            elif choice == "theme":
                themes = list(self.theme_manager.settings.get_all_themes() if hasattr(self.theme_manager.settings, 'get_all_themes') else ["default", "cyberpunk", "nord", "high_contrast", "oceanic", "midnight", "aura"])
                from src.ui.theme import THEMES
                themes = list(THEMES.keys())
                
                theme_opts = [{"name": t.title(), "val": t} for t in themes]
                t_sel = selection_menu(theme_opts, "Select Theme", show_details=False, formatter=lambda x: x["name"])
                if t_sel and t_sel["action"] == "select":
                    next_theme = t_sel["value"]["val"]
                    self.settings.set("theme", next_theme)
                    self.theme_manager.load_theme()
                    self.console.print(f"[green]Theme applied: {next_theme.title()}. Re-entering settings menu...[/green]")
                    time.sleep(1)
                    continue # Refresh the settings loop
            elif choice == "subs":
                self.handle_subtitle_settings()

    def handle_path_settings(self):
        while True:
            paths = self.settings.local_library_paths
            print_header("Local Library Paths")
            for i, p in enumerate(paths):
                self.console.print(f"  {i+1}. {p}")
            
            opts = [
                {"name": "✚ Add New Path", "val": "add"},
                {"name": "🗑 Remove Path", "val": "remove"},
                {"name": "🧹 Clear All", "val": "clear"},
                {"name": "🔙 Back", "val": "back"}
            ]
            sel = selection_menu(opts, "Path Settings", show_details=False, formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back" or sel["value"]["val"] == "back": break
            
            act = sel["value"]["val"]
            if act == "add":
                 new_path = self.console.input(f"[bold {self.theme_manager.accent}]Enter absolute path: [/bold {self.theme_manager.accent}]")
                 if new_path:
                     paths.append(new_path)
                     self.settings.local_library_paths = paths
            elif act == "remove":
                idx = self.console.input(f"[bold {self.theme_manager.accent}]Index to remove: [/bold {self.theme_manager.accent}]")
                try:
                    paths.pop(int(idx)-1)
                    self.settings.local_library_paths = paths
                except: pass
            elif act == "clear":
                self.settings.local_library_paths = []

    def handle_subtitle_settings(self):
        available = [("Arabic", "ar"), ("English", "en"), ("French", "fr"), ("Spanish", "es")]
        while True:
            cur_subs = self.settings.subtitle_languages
            menu_opts = []
            for name, code in available:
                status = "●" if code in cur_subs else "○"
                menu_opts.append({"name": f"{status} {name}", "code": code})
            
            sel = selection_menu(menu_opts, "Subtitle Languages", show_details=False, formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back": break
            
            code = sel["value"]["code"]
            if code in cur_subs:
                if len(cur_subs) > 1: cur_subs.remove(code)
            else:
                cur_subs.append(code)
            self.settings.subtitle_languages = cur_subs

    def handle_library(self):
        print_header("Local Library")
        paths = self.settings.local_library_paths
        if not paths:
            self.console.print("[yellow]No local library paths configured.[/yellow]")
            self.console.print("Go to Settings > 5 to add paths.")
            time.sleep(2)
            return

        self.console.print("[cyan]Scanning library...[/cyan]")
        items = self.library_manager.scan(paths)
        if not items:
            self.console.print("[yellow]No video files found.[/yellow]")
            time.sleep(1.5)
            return
            
        while True:
            sel = selection_menu(items, f"Local Library ({len(items)})", show_details=False, formatter=lambda x: f"{x['title']} ({x.get('year') or 'TV'}) | {x['filename']}")
            if not sel or sel["action"] == "back": break
            
            if sel["action"] == "select":
                self.play_local_media(sel["value"])

    def play_local_media(self, media):
        from src.utils.player import play_stream
        path = media["path"]
        title = media["title"]
        # Basic play
        play_stream(path, title) 
        # TODO: Add history tracking for local files if feasible

    def toggle_favorite(self, media):
        added = self.data.toggle_favorite(media)
        msg = "[green]Added to favorites![/green]" if added else "[yellow]Removed from favorites.[/yellow]"
        self.console.print(msg)
        time.sleep(0.5)

    def handle_media(self, media):
        # Update history (move to top)
        self.data.add_to_history(media)
        
        m_type = media.get("media_type", "movie")
        if m_type == "tv" or "first_air_date" in media:
            self.show_seasons(media)
            return

        while True:
            options = [
                {"name": "▶ Play Now", "val": "play"},
                {"name": "📥 Download", "val": "download"},
                {"name": "ℹ️ View Details & Cast", "val": "details"},
                {"name": "⭐ Toggle Favorite", "val": "favorite"},
            ]
            
            sel = selection_menu(options, f"{media.get('title') or media.get('name')}", show_details=False, formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back": break
            
            if sel["action"] == "select":
                act = sel["value"]["val"]
                if act == "play":
                    self.play_movie(media)
                    break 
                elif act == "details":
                    self.show_media_details(media)
                elif act == "favorite":
                    self.toggle_favorite(media)
                elif act == "download":
                    self.handle_movie_download(media)

    def show_media_details(self, media):
        tmdb_id = media.get("id")
        m_type = media.get("media_type", "movie")
        
        with self.console.status(f"[{self.theme_manager.accent}]Fetching premium details...[/{self.theme_manager.accent}]", spinner="dots"):
            details = self.api.get_tmdb_data(f"{m_type}/{tmdb_id}") or media
            credits = self.api.get_media_credits(m_type, tmdb_id)
        
        print_header(details.get('title') or details.get('name'))
        
        # Info Table
        info_table = Table(box=box.SIMPLE, show_header=False, border_style=self.theme_manager.primary)
        info_table.add_column("Key", style=f"bold {self.theme_manager.accent}")
        info_table.add_column("Value")
        
        vote = details.get("vote_average", 0)
        date = details.get("release_date") or details.get("first_air_date") or "N/A"
        runtime = details.get("runtime") or details.get("episode_run_time", [0])[0]
        genres = ", ".join([g["name"] for g in details.get("genres", [])])
        
        info_table.add_row("Rating", f"⭐ {vote:.1f}/10")
        info_table.add_row("Release Date", date)
        if runtime: info_table.add_row("Runtime", f"⏱ {runtime} min")
        if genres: info_table.add_row("Genres", genres)
        if details.get("status"): info_table.add_row("Status", details.get("status"))

        self.console.print(Panel(
            details.get("overview", "No overview available."),
            title=f"[bold]Overview[/bold]",
            border_style=self.theme_manager.secondary,
            box=box.ROUNDED
        ))
        
        self.console.print(info_table)
        
        if credits:
            cast = credits.get("cast", [])[:20]
            if cast:
                self.console.print(f"\n[bold {self.theme_manager.primary}]Top Cast:[/bold {self.theme_manager.primary}]")
                sel = selection_menu(
                    cast, 
                    "Cast Details", 
                    show_details=False, 
                    formatter=lambda x: f"{x.get('name'):<25} as {x.get('character')}"
                )
                if sel and sel["action"] == "select":
                    self.show_person_details(sel["value"])
            else:
                self.console.print("[yellow]No cast info available.[/yellow]")
                self.console.input("\nPress Enter to return...")
        else:
             self.console.input("\nPress Enter to return...")

    def play_movie(self, media):
        from src.utils.player import play_stream
        
        title = media.get("title")
        tmdb_id = media.get("id")
        year = media.get("release_date", "")[:4]

        self.console.print(f"[cyan]Finding best source for: {title}...[/cyan]")
        
        # Use SourceManager for auto-selection
        source, subtitles = self.source_manager.get_best_source(tmdb_id, "movie", preferred_quality=self.settings.get("quality", "auto"))
        
        if not source:
            self.console.print("[red]No working sources found.[/red]")
            time.sleep(2)
            return

        url = source.get("url")
        self.console.print(f"[green]Selected: {source.get('quality')} - {source.get('provider')}[/green]")
        
        # Subtitles
        self.console.print(f"[cyan]Searching for subtitles in: {', '.join(self.settings.subtitle_languages)}...[/cyan]")
        sub_paths = self.subtitle_manager.get_subtitles(
            title, 
            subtitles, 
            match_data={"year": year, "series_name": title.split(" (")[0]},
            preferred_langs=self.settings.subtitle_languages
        )
        if sub_paths:
            self.console.print(f"[green]Found {len(sub_paths)} subtitle(s).[/green]")
        else:
            self.console.print("[yellow]No subtitles found for preferred languages.[/yellow]")
        
        # Check for history position
        start_time = 0
        hist = next((h for h in self.data.history if h["id"] == tmdb_id), None)
        if hist and not hist.get("finished", False):
            pos = hist.get("position", 0)
            if pos > 0:
                self.console.print(f"[{self.theme_manager.accent}]Resume playback from {int(pos // 60)}:{int(pos % 60):02d}?[/{self.theme_manager.accent}]")
                choice = self.console.input("[Y/n]: ").lower()
                if choice != "n":
                    start_time = pos

        stats = play_stream(url, title, subtitles=subtitles, headers=source.get("headers"), start_time=start_time, meta={"year": year}, sub_files=sub_paths)
        
        if stats:
            self.data.update_history_progress(tmdb_id, stats)
            if stats.get("finished", False):
                from src.ui.ui import post_playback_menu
                post_playback_menu(10, f"Finished: {title}")

    def handle_movie_download(self, media):
        title = media.get("title")
        tmdb_id = media.get("id")
        year = media.get("release_date", "")[:4]

        # Use delayed fetch for downloads to avoid URL expiry
        filename = f"{sanitize_filename(title)} ({year}).mp4"
        
        # Add task to download manager
        self.download_manager.add_task(
            None, # URL will be fetched by worker
            filename, 
            title, 
            subtitles=None, 
            headers=None,
            meta={"year": year, "type": "movie"},
            api_params={"tmdb_id": tmdb_id, "media_type": "movie"}
        )

    def show_seasons(self, media):
        tmdb_id = media.get("id")
        
        with self.console.status(f"[{self.theme_manager.accent}]Fetching seasons...[/{self.theme_manager.accent}]", spinner="dots"):
             details = self.api.get_tmdb_data(f"tv/{tmdb_id}")
        
        if not details: return

        seasons = details.get("seasons", [])
        # Filter out specials/season 0 if desired, or keep them. keeping for now.
        seasons = [s for s in seasons if s["season_number"] > 0] # usually skip 0
        
        while True:
            sel = selection_menu(seasons, f"Seasons - {media.get('name')}", show_details=False, formatter=lambda x: f"Season {x['season_number']} ({x['episode_count']} Episodes)")
            if not sel or sel["action"] == "back": break
            
            if sel["action"] == "favorite":
                self.toggle_favorite(media)
                continue
                
            if sel["action"] == "select":
                self.show_episodes(media, sel["value"])

    def show_episodes(self, media, season):
        tmdb_id = media.get("id")
        s_num = season["season_number"]
        data = self.api.get_tmdb_data(f"tv/{tmdb_id}/season/{s_num}")
        if not data: return

        episodes = data.get("episodes", [])
        
        while True:
            sel = selection_menu(episodes, f"Season {s_num} - {media.get('name')}", show_details=False, formatter=lambda x: f"E{x['episode_number']} - {x['name']}")
            if not sel or sel["action"] == "back": break
            
            if sel["action"] == "favorite":
                self.toggle_favorite(media)
                continue
                
            if sel["action"] == "batch":
                self.handle_batch_download(media, season, episodes)
                continue

            if sel["action"] == "select":
                # Episode Options Menu
                ep = sel["value"]
                while True:
                    opts = [
                        {"name": "▶ Play Now", "val": "play"},
                        {"name": "📥 Download", "val": "download"},
                        {"name": "🔙 Back", "val": "back"},
                    ]
                    opt_sel = selection_menu(opts, f"E{ep['episode_number']} - {ep['name']}", show_details=False, formatter=lambda x: x["name"])
                    if not opt_sel or opt_sel["action"] == "back":
                        break
                    
                    if opt_sel["action"] == "favorite":
                        self.toggle_favorite(media)
                        continue
                        
                    if opt_sel["action"] == "batch":
                        # Too many menus deep, but we can trigger it.
                        self.handle_batch_download(media, season, episodes)
                        continue

                    if not opt_sel["value"] or opt_sel["action"] == "quit":
                        break

                    val = opt_sel["value"].get("val")
                    if val == "back": break
                    if val == "play":
                        # Start playback loop from selected episode
                        current_ep_idx = next((i for i, e in enumerate(episodes) if e["id"] == ep["id"]), 0)
                        while 0 <= current_ep_idx < len(episodes):
                            current_ep = episodes[current_ep_idx]
                            finished = self.play_episode(media, season, current_ep)
                            if not finished: break
                            
                            from src.ui.ui import post_playback_menu
                            title_str = f"Finished: E{current_ep['episode_number']} - {current_ep['name']}"
                            action = post_playback_menu(10, title_str)
                            if action == "next": current_ep_idx += 1
                            elif action == "prev": current_ep_idx = max(0, current_ep_idx - 1)
                            elif action == "replay": pass
                            else: break
                        break
                    elif val == "download":
                        self.handle_episode_download(media, season, ep)
                        break
                    elif action == "replay":
                        pass # Index stays same
                    else: # back
                        break

    def play_episode(self, media, season, episode):
        from src.utils.player import play_stream
        title = f"{media.get('name')} S{season['season_number']}E{episode['episode_number']}"
        tmdb_id = media.get("id")
        
        self.console.print(f"[cyan]Finding best source for: {title}...[/cyan]")

        source, subtitles = self.source_manager.get_best_source(
            tmdb_id, 
            "tv", 
            season=season["season_number"], 
            episode=episode["episode_number"],
            preferred_quality=self.settings.get("quality", "auto")
        )

        if not source:
            self.console.print("[red]No working sources found.[/red]")
            time.sleep(2)
            return False

        url = source.get("url")
        self.console.print(f"[green]Selected: {source.get('quality')} - {source.get('provider')}[/green]")
        
        # Subtitles
        self.console.print(f"[cyan]Searching for subtitles in: {', '.join(self.settings.subtitle_languages)}...[/cyan]")
        sub_paths = self.subtitle_manager.get_subtitles(
            title, 
            subtitles, 
            match_data={
                "series_name": media.get("name"),
                "season": season["season_number"], 
                "episode": episode["episode_number"]
            },
            preferred_langs=self.settings.subtitle_languages
        )
        if sub_paths:
            self.console.print(f"[green]Found {len(sub_paths)} subtitle(s).[/green]")
        else:
            self.console.print("[yellow]No subtitles found for preferred languages.[/yellow]")

        # Check history
        start_time = 0
        hist = next((h for h in self.data.history if h["id"] == tmdb_id), None)
        if hist and hist.get("last_episode"):
            le = hist["last_episode"]
            if le["season"] == season["season_number"] and le["episode"] == episode["episode_number"]:
                 pos = le.get("position", 0)
                 if pos > 0:
                     self.console.print(f"[{self.theme_manager.accent}]Resume playback from {int(pos // 60)}:{int(pos % 60):02d}?[/{self.theme_manager.accent}]")
                     choice = self.console.input("[Y/n]: ").lower()
                     if choice != "n":
                         start_time = pos

        stats = play_stream(
            url, 
            title, 
            subtitles=subtitles, 
            headers=source.get("headers"), 
            start_time=start_time, 
            meta={"season": season["season_number"], "episode": episode["episode_number"]},
            sub_files=sub_paths
        )
        
        if stats:
            self.data.update_history_progress(tmdb_id, stats, episode=episode)
            return stats.get("finished", False)
            
        return False

    def handle_episode_download(self, media, season, episode, batch_info=None):
        tmdb_id = media.get("id")
        s_num = season["season_number"]
        e_num = episode["episode_number"]
        title = f"{media.get('name')} S{s_num:02d}E{e_num:02d} - {episode.get('name')}"

        filename = f"{sanitize_filename(media.get('name'))} S{s_num:02d}E{e_num:02d}.mp4"

        # Base metadata for a single episode task
        meta = {
            "series_name": media.get("name"),
            "season": s_num,
            "episode": e_num,
            "type": "tv",
        }

        # When invoked from a batch download, enrich meta with batch context so
        # the download manager can distinguish and group episodes intelligently.
        if batch_info:
            meta.update(
                {
                    "batch_id": batch_info.get("id"),
                    "batch_label": batch_info.get("label"),
                    "batch_index": batch_info.get("index"),
                    "batch_total": batch_info.get("total"),
                }
            )

        self.download_manager.add_task(
            None,  # URL will be fetched by worker
            filename,
            title,
            subtitles=None,
            headers=None,
            meta=meta,
            api_params={
                "tmdb_id": tmdb_id,
                "media_type": "tv",
                "season": s_num,
                "episode": e_num,
            },
        )

    def handle_batch_download(self, media, season, episodes):
        from src.ui.ui import multi_selection_menu
        title = f"Batch Download: {media.get('name')} Season {season['season_number']}"
        selected_eps = multi_selection_menu(
            episodes, 
            title, 
            formatter=lambda x: f"E{x['episode_number']} - {x['name']}"
        )
        
        if not selected_eps:
            return

        total = len(selected_eps)
        self.console.print(f"[cyan]Adding {total} episodes to queue...[/cyan]")

        # Ensure a stable ordering when queued (ascending by episode number)
        ordered_eps = sorted(selected_eps, key=lambda e: e.get("episode_number", 0))

        batch_id = f"tv:{media.get('id')}:{season['season_number']}"
        for idx, ep in enumerate(ordered_eps, start=1):
            batch_info = {
                "id": batch_id,
                "label": title,
                "index": idx,
                "total": total,
            }
            self.handle_episode_download(media, season, ep, batch_info=batch_info)
        
        self.console.print(f"[green]Successfully added {len(selected_eps)} episodes to download manager![/green]")
        time.sleep(1.5)

    # --- Backend Management ---
    def _maybe_start_backend(self, backend_url: str) -> None:
        try:
            host = backend_url.split("://")[-1].split(":")[0]
        except Exception:
            host = ""

        if host not in ("localhost", "127.0.0.1", ""):
            return

        if self._is_backend_running(backend_url):
            return

        # Start backend logic (simplified from main.py)
        # Assuming we are in cli/src/core/
        # Project root is ../../../
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
        
        try:
            show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"
            stdout = None if show_logs else subprocess.DEVNULL
            stderr = None if show_logs else subprocess.DEVNULL
            
            self._backend_proc = subprocess.Popen(
                "npm start", cwd=backend_dir, shell=True, stdout=stdout, stderr=stderr
            )
            # Wait loop
            for _ in range(10):
                if self._is_backend_running(backend_url): return
                time.sleep(0.5)
        except Exception:
            pass

    def _is_backend_running(self, url: str) -> bool:
        from urllib.request import Request, urlopen
        from urllib.error import URLError, HTTPError
        try:
            req = Request(url.rstrip("/") + "/", headers={"User-Agent": "cinema-cli/1.0"})
            with urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except (URLError, HTTPError, ValueError):
            return False

    def _cleanup_backend(self):
        if self._backend_proc and self._backend_proc.poll() is None:
            try:
                self._backend_proc.terminate()
                time.sleep(0.2)
                if self._backend_proc.poll() is None:
                    self._backend_proc.kill()
            except Exception:
                pass

if __name__ == "__main__":
    app = CinemaApp()
    app.run()
