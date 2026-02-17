import atexit
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def start_local_backend(backend_url: str, timeout: int = 30):
    """Start local backend if backend_url points at localhost and wait until it's healthy.

    Returns subprocess.Process or None.
    """

    def _is_running(url: str) -> bool:
        try:
            req = Request(
                url.rstrip("/") + "/", headers={"User-Agent": "cinema-cli/1.0"}
            )
            with urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except Exception:
            return False

    try:
        host = backend_url.split("://")[-1].split(":")[0]
    except Exception:
        host = ""

    if host not in ("localhost", "127.0.0.1", ""):
        return None

    # If already running, nothing to do
    if _is_running(backend_url):
        return None

    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "backend")
    )
    log_path = os.path.join(backend_dir, "backend.log")

    show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"

    # Ensure log directory exists and open log file for append
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logfile = open(log_path, "a+", encoding="utf-8")

    stdout = logfile
    stderr = logfile

    proc = None
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
            logfile.close()
            return None

    # Optionally tail live logs to console while waiting
    stop_tailer = None
    tail_thread = None
    if show_logs:
        import threading

        stop_tailer = threading.Event()

        def _tail_file(path, stop_event):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    # Seek to near end
                    f.seek(0, os.SEEK_END)
                    while not stop_event.is_set():
                        line = f.readline()
                        if line:
                            try:
                                console.print(line.rstrip())
                            except Exception:
                                pass
                        else:
                            time.sleep(0.2)
            except Exception:
                return

        tail_thread = threading.Thread(
            target=_tail_file, args=(log_path, stop_tailer), daemon=True
        )
        tail_thread.start()

    # Wait until healthy or timeout while showing a friendly status
    from src.config import console

    with console.status("Starting backend, please wait...", spinner="dots"):
        waited = 0.0
        interval = 0.5
        while waited < timeout:
            if _is_running(backend_url):
                if stop_tailer:
                    stop_tailer.set()
                logfile.flush()
                logfile.close()
                return proc
            time.sleep(interval)
            waited += interval

    # Timeout reached; stop tailer if running and return proc (logs available in backend.log)
    if stop_tailer:
        stop_tailer.set()
    logfile.flush()
    logfile.close()
    return proc


from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout as PTLayout
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.table import Table

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
)
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
from src.utils.player import play_stream, play_video, detect_available_players
from src.utils.storage import load_json_data, save_json_data
from src.utils.utils import generate_filename
from src.utils.validator import select_working_source, select_multiple_working_sources, verify_source


class CinemaCLI:
    def __init__(self):
        # Attempt to ensure a local backend is running (only for localhost URLs)
        self._backend_proc = None
        self._maybe_start_backend(BACKEND_URL)

        self.settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}

        # Defaults for settings
        if "filename_template" not in self.settings:
            self.settings["filename_template"] = "{title}.{year}"
        if "filename_template_tv" not in self.settings:
            self.settings["filename_template_tv"] = "{title}.S{season}E{episode}"
        if "library_dir" not in self.settings:
            self.settings["library_dir"] = os.path.expanduser("~/Downloads/CinemaCLI")
        if "preferred_subtitle" not in self.settings:
            self.settings["preferred_subtitle"] = "ar"
        if "preferred_player" not in self.settings:
            self.settings["preferred_player"] = "mpv"

        self.api = APIClient(self.settings)

        # IMPORTANT: ensure lists/dicts, not None
        self.history = load_json_data(HISTORY_FILE) or []
        self.favorites = load_json_data(FAVORITES_FILE) or []
        self.playback = load_json_data(PLAYBACK_FILE) or {}

        os.makedirs(self.settings["library_dir"], exist_ok=True)
        self.download_manager = DownloadManager(downloads_dir=self.settings["library_dir"], api_client=self.api)
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
        # Only auto-start when pointing to localhost and not already running
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
        # Try to start via npm start; fallback to node index.js if npm not available
        try:
            # Allow showing backend logs when requested via env var
            show_logs = os.getenv("AUTO_START_BACKEND_SHOW_LOGS") == "1"
            stdout = None if show_logs else subprocess.DEVNULL
            stderr = None if show_logs else subprocess.DEVNULL

            # Use shell=True for cross-platform command resolution (npm on PATH)
            self._backend_proc = subprocess.Popen(
                "npm start", cwd=backend_dir, shell=True, stdout=stdout, stderr=stderr
            )
            # Wait briefly for server to come up
            for _ in range(10):
                if self._is_backend_running(backend_url):
                    return
                time.sleep(0.5)
        except Exception:
            try:
                self._backend_proc = subprocess.Popen(
                    ["node", "index.js"], cwd=backend_dir, stdout=stdout, stderr=stderr
                )
                for _ in range(10):
                    if self._is_backend_running(backend_url):
                        return
                    time.sleep(0.5)
            except Exception:
                # If starting fails, leave user to start backend manually
                return

    def _cleanup_backend(self):
        if self._backend_proc and self._backend_proc.poll() is None:
            try:
                self._backend_proc.terminate()
                time.sleep(0.2)
                if self._backend_proc.poll() is None:
                    self._backend_proc.kill()
            except Exception:
                pass

    def main_menu(self):
        while True:
            print_header("Main Menu")
            options = [
                {"name": "🔍 Search Movies & TV", "action": self.handle_search},
                {"name": "🌍 Discovery", "action": self.handle_discovery},
                {"name": "📈 Trending This Week", "action": self.handle_trending},
                {"name": "🔥 Popular Content", "action": self.handle_popular},
                {"name": "🎭 Browse by Genre", "action": self.handle_genres},
                {"name": "⭐ My Favorites", "action": self.handle_favorites},
                {"name": "🕒 Watch History", "action": self.handle_history},
                {"name": "📁 Local Library", "action": self.handle_local_library},
                {"name": "📥 Download Manager", "action": self.handle_download_manager},
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
                    "selected": f"bg:{PRIMARY} fg:#ffffff bold",
                    "item": f"{TEXT}",
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

            # Navigation controls (use consistent "title")
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

            # Navigation controls (FIX: use "title" consistently)
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
            # You need an API method like: GET /trending/tv/day?page=page
            data = self.api.get_trending_tv_today(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "tv"

            # Navigation controls
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
            # TMDB: /trending/movie/day
            data = self.api.get_trending_movies_today(page=page)
            if not data:
                return

            results = data.get("results", [])
            for r in results:
                r["media_type"] = "movie"

            # Navigation controls
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

    def handle_search(self):
        print_header("Search")
        query = console.input(
            f"[bold {ACCENT}]Search for a movie or TV show: [/bold {ACCENT}]"
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
        print_header("Popular")
        types = [
            {"name": "🎬 Movies", "val": "movie"},
            {"name": "📺 TV Shows", "val": "tv"},
        ]
        console.print(f"1. {types[0]['name']}\n2. {types[1]['name']}")
        choice = console.input(f"\n[bold {ACCENT}]Select type (1-2): [/bold {ACCENT}]")
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

    def handle_genres(self):
        print_header("Genres")
        types = [
            {"name": "🎬 Movies", "val": "movie"},
            {"name": "📺 TV Shows", "val": "tv"},
        ]
        console.print(f"1. {types[0]['name']}\n2. {types[1]['name']}")
        choice = console.input(f"\n[bold {ACCENT}]Select type (1-2): [/bold {ACCENT}]")
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
                {"selected": f"bg:{ACCENT} fg:#ffffff bold", "item": f"{TEXT}"}
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

    # ✅ FIXED: this method was broken / mis-indented in your file
    def update_history(self, media, stats, episode=None):
        if not self.history:
            self.history = []

        # Find entry in history; if missing, insert it
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

    def handle_settings(self):
        # Language code to display name mapping
        _sub_names = {
            "ar": "Arabic", "en": "English", "fr": "French",
            "es": "Spanish", "de": "German", "tr": "Turkish",
            "pt": "Portuguese", "it": "Italian",
        }

        print_header("Settings")
        console.print(
            f"[bold {TEXT}]1. Backend URL:[/bold {TEXT}] {self.settings.get('backend', BACKEND_URL)}"
        )
        console.print(
            f"[bold {TEXT}]2. TMDB API Key:[/bold {TEXT}] {self.settings.get('tmdb_key', 'Using Default')}"
        )
        console.print(
            f"[bold {TEXT}]3. Movie Filename Template:[/bold {TEXT}] {self.settings.get('filename_template')}"
        )
        console.print(
            f"[bold {TEXT}]4. TV Filename Template:[/bold {TEXT}] {self.settings.get('filename_template_tv')}"
        )
        console.print(
            f"[bold {TEXT}]5. Library Directory:[/bold {TEXT}] {self.settings.get('library_dir')}"
        )

        pref_sub = self.settings.get('preferred_subtitle', 'ar')
        pref_sub_name = _sub_names.get(pref_sub, pref_sub)
        console.print(
            f"[bold {TEXT}]6. Preferred Subtitle Language:[/bold {TEXT}] {pref_sub_name} ({pref_sub})"
        )

        pref_player = self.settings.get('preferred_player', 'mpv')
        available_players = detect_available_players()
        avail_str = ', '.join(available_players) if available_players else 'none found'
        console.print(
            f"[bold {TEXT}]7. Preferred Player:[/bold {TEXT}] {pref_player.upper()} (available: {avail_str})"
        )

        choice = console.input(
            f"\n[bold {ACCENT}]Select setting to change (1-7) or Enter to back: [/bold {ACCENT}]"
        )

        if choice == "1":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new backend URL: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["backend"] = new_val.strip()
        elif choice == "2":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new TMDB API Key: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["tmdb_key"] = new_val.strip()
        elif choice == "3":
            console.print("[dim]Tokens: {title}, {year}, {quality}, {provider}[/dim]")
            new_val = console.input(
                f"[bold {ACCENT}]Enter new Movie Template: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["filename_template"] = new_val.strip()
        elif choice == "4":
            console.print(
                "[dim]Tokens: {title}, {year}, {season}, {episode}, {quality}, {provider}[/dim]"
            )
            new_val = console.input(
                f"[bold {ACCENT}]Enter new TV Template: [/bold {ACCENT}]"
            )
            if new_val.strip():
                self.settings["filename_template_tv"] = new_val.strip()
        elif choice == "5":
            new_val = console.input(
                f"[bold {ACCENT}]Enter new Library Directory: [/bold {ACCENT}]"
            )
            if new_val.strip():
                p = os.path.expanduser(new_val.strip())
                os.makedirs(p, exist_ok=True)
                self.settings["library_dir"] = p
                self.download_manager.downloads_dir = p
        elif choice == "6":
            lang_options = [
                {"name": f"📝 {name}", "value": code}
                for code, name in _sub_names.items()
            ]
            sel = selection_menu(
                lang_options,
                "Preferred Subtitle Language",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if sel and sel["action"] == "select":
                self.settings["preferred_subtitle"] = sel["value"]["value"]
                console.print(
                    f"[green]Preferred subtitle set to: {_sub_names.get(sel['value']['value'], sel['value']['value'])}[/green]"
                )
        elif choice == "7":
            player_options = [
                {"name": "🎬 MPV", "value": "mpv"},
                {"name": "📺 VLC", "value": "vlc"},
                {"name": "🍎 IINA (macOS)", "value": "iina"},
            ]
            sel = selection_menu(
                player_options,
                "Preferred Player",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if sel and sel["action"] == "select":
                chosen = sel["value"]["value"]
                self.settings["preferred_player"] = chosen
                console.print(
                    f"[green]Preferred player set to: {chosen.upper()}[/green]"
                )
        else:
            return

        save_json_data(SETTINGS_FILE, self.settings)
        console.print("[green]Settings saved![/green]")
        time.sleep(1)

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

    def handle_media(self, media):
        self.history = [h for h in self.history if h.get("id") != media.get("id")]
        self.history.insert(0, media)
        self.history = self.history[:50]
        save_json_data(HISTORY_FILE, self.history)

        m_type = media.get("media_type", "movie")
        if m_type == "movie":
            self.play_movie(media)
        else:
            self.show_seasons(media)

    def play_movie(self, media):
        title = media.get("title")
        tmdb_id = media.get("id")
        
        # Use enhanced source fetching with retry
        console.print(f"[bold {ACCENT}]Fetching sources for: {title}...[/bold {ACCENT}]")
        data = self.api.get_sources_enhanced(tmdb_id, "movie", min_sources=3)

        rel = media.get("release_date") or ""
        year = rel[:4] if isinstance(rel, str) and len(rel) >= 4 else None

        # ✅ FIX: include tmdb_id + type so playback resume works
        meta = {"year": year, "tmdb_id": tmdb_id, "type": "movie"}

        stats = self.handle_sources(title, data, meta)
        if isinstance(stats, dict):
            self.update_history(media, stats, episode=None)

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

    def autoplay_countdown(self, next_ep_name, timeout=10):
        """Displays a countdown panel. Returns True if finished, False if interrupted."""
        from rich.live import Live
        from rich.progress import Progress, BarColumn, TextColumn
        import select
        
        clear()
        progress = Progress(
            TextColumn("[bold yellow]Next episode starts in {task.fields[secs]} seconds...[/bold yellow]"),
            BarColumn(bar_width=40, complete_style=PRIMARY, finished_style=SUCCESS),
            TextColumn("[dim](Press any key to cancel)[/dim]")
        )
        
        task_id = progress.add_task("autoplay", total=timeout, secs=timeout)
        
        with Live(progress, console=console, refresh_per_second=10):
            start_time = time.time()
            while not progress.finished:
                elapsed = time.time() - start_time
                remaining = max(0, timeout - elapsed)
                progress.update(task_id, completed=elapsed, secs=int(remaining))
                
                # Cross-platform key check
                if sys.platform == "win32":
                    import msvcrt
                    if msvcrt.kbhit():
                        msvcrt.getch()
                        return False
                else:
                    if select.select([sys.stdin], [], [], 0)[0]:
                        sys.stdin.read(1)
                        return False
                
                if remaining <= 0:
                    break
                time.sleep(0.05)
                
        return True

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
                next_step_auto = False

                while True:
                    title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"
                    
                    # Use enhanced source fetching for TV episodes
                    console.print(f"[bold {ACCENT}]Fetching sources for: {title}...[/bold {ACCENT}]")
                    data = self.api.get_sources_enhanced(
                        media["id"], "tv", s_num, ep["episode_number"], min_sources=3
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

                    if next_step_auto:
                        stats = self.handle_sources(title, data, meta, autoplay=True)
                        next_step_auto = False
                    else:
                        stats = self.handle_sources(title, data, meta)

                    if isinstance(stats, dict):
                        self.update_history(media, stats, episode=ep)
                        
                        # --- Smart Autoplay Logic ---
                        if stats.get("finished") and selected_idx + 1 < len(episodes):
                            next_ep = episodes[selected_idx + 1]
                            next_title = f"E{next_ep['episode_number']} - {next_ep['name']}"
                            
                            if self.autoplay_countdown(next_title):
                                selected_idx += 1
                                ep = next_ep
                                next_step_auto = True
                                continue # Start next episode
                    
                    if not stats:
                        break

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

    def handle_batch_download(self, media, season, episodes):
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
            f"\n[bold {PRIMARY}]Preparing batch download for {len(selected_episodes)} episodes...[/bold {PRIMARY}]"
        )


        # Batch preferences (applied to all episodes in this batch)
        selected_quality = "auto"
        preferred_sub_lang = self.settings.get("preferred_subtitle", "ar")
        include_all_subs = True

        try:
            first_ep = selected_episodes[0]
            first_title = f"{media.get('name')} S{s_num}E{first_ep['episode_number']} - {first_ep.get('name')}"
            first_data = self.api.get_sources_enhanced(media["id"], "tv", s_num, first_ep["episode_number"], min_sources=2)
            first_files = first_data.get("files", []) if isinstance(first_data, dict) else []
            first_subs = first_data.get("subtitles", []) if isinstance(first_data, dict) else []

            # Quality selection
            qualities = []
            for f in first_files:
                q = f.get("quality", "Unknown")
                if q not in qualities:
                    qualities.append(q)

            def quality_sort_key(q):
                q = (q or "").lower()
                if "4k" in q or "2160" in q: return 0
                if "1080" in q: return 1
                if "720" in q: return 2
                if "480" in q: return 3
                if "360" in q: return 4
                return 5

            qualities.sort(key=quality_sort_key)

            if len(qualities) > 1:
                q_options = [{"name": "✨ Auto (Best Available)", "value": "auto"}]
                for q in qualities:
                    q_options.append({"name": f"📺 {q}", "value": q})
                q_sel = selection_menu(
                    q_options,
                    f"Batch Download - Select Quality (S{s_num})",
                    show_details=False,
                    formatter=lambda x: x["name"],
                )
                if q_sel:
                    selected_quality = q_sel["value"]["value"]

            # Subtitle language selection
            if first_subs:
                def _norm_lang(l):
                    l = (l or "").strip().lower()
                    if l in ["arabic", "ara", "ar"]:
                        return "ar"
                    if l in ["english", "eng", "en"]:
                        return "en"
                    if l in ["french", "fra", "fre", "fr"]:
                        return "fr"
                    if l in ["spanish", "spa", "es"]:
                        return "es"
                    return l or "und"

                langs = []
                for s in first_subs:
                    if isinstance(s, dict):
                        code = _norm_lang(s.get("lang") or s.get("language"))
                        if code not in langs:
                            langs.append(code)

                def _lang_label(code):
                    names = {"ar": "Arabic (Default)", "en": "English", "fr": "French", "es": "Spanish", "und": "Unknown"}
                    return names.get(code, code)

                lang_opts = [{"name": f"📝 {_lang_label('ar')}", "value": "ar"}]
                for code in langs:
                    if code != "ar":
                        lang_opts.append({"name": f"📝 {_lang_label(code)}", "value": code})
                lang_opts.append({"name": "🚫 No subtitles", "value": "none"})

                lang_sel = selection_menu(
                    lang_opts,
                    "Batch Download - Select Subtitle Language",
                    show_details=False,
                    formatter=lambda x: x["name"],
                )
                if lang_sel:
                    chosen = lang_sel["value"]["value"]
                    if chosen == "none":
                        preferred_sub_lang = "none"
                    else:
                        preferred_sub_lang = chosen
        except Exception:
            pass

        for ep in selected_episodes:
            title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"
            
            # Use enhanced source fetching for each episode
            data = self.api.get_sources_enhanced(
                media["id"], "tv", s_num, ep["episode_number"], min_sources=2
            )
            files = data.get("files", [])
            subtitles = data.get("subtitles", [])

            if not files:
                console.print(f"[yellow]No sources found for {title}. Skipping...[/yellow]")
                continue

            # Apply batch-selected quality (fallback gracefully if unavailable)
            if selected_quality != "auto":
                q_files = [f for f in files if f.get("quality") == selected_quality]
                if q_files:
                    files = q_files

            # Apply batch-selected subtitles
            if preferred_sub_lang == "none":
                subtitles = []

            air = ep.get("air_date") or ""
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else None
            meta = {
                "year": year,
                "season": s_num,
                "episode": ep.get("episode_number"),
                "type": "tv",
            }

            # Automated source selection for batch download
            console.print(f"[dim]  Finding working source for {title}...[/dim]")
            selected_source = select_working_source(files)
            
            if not selected_source:
                console.print(f"[red]No working source found for {title}. Skipping...[/red]")
                continue

            template = self.settings.get(
                "filename_template_tv", "{title}.S{season}E{episode}"
            )
            filename = generate_filename(template, title, meta, selected_source)

            console.print(
                f"[green]Queuing download: {selected_source.get('provider')} ({selected_source.get('quality')})...[/green]"
            )
            
            # Use top working source + other files as fallback
            fallback_sources = [f for f in files if f != selected_source]
            
            self.download_manager.add_task(
                selected_source.get("file"),
                filename,
                title,
                subtitles,
                selected_source.get("headers"),
                meta=meta,
                fallback_sources=fallback_sources,
                api_params={
                    "tmdb_id": media.get("id"),
                    "media_type": "tv",
                    "season": s_num,
                    "episode": ep.get("episode_number")
                },
                preferred_sub_lang=preferred_sub_lang,
                include_all_subs=include_all_subs
            )

        console.print(f"\n[bold {SUCCESS}]Batch download queued![/bold {SUCCESS}]")
        time.sleep(2)

    def handle_sources(self, title, data, meta=None, autoplay=False):
        files = data.get("files", [])
        subtitles = data.get("subtitles", [])
        if not files:
            console.print("[red]No streams found.[/red]")
            time.sleep(1.5)
            return False

        # --- Helper: normalize language codes ---
        def _norm_lang(l):
            l = (l or "").strip().lower()
            if l in ["arabic", "ara", "ar"]:
                return "ar"
            if l in ["english", "eng", "en"]:
                return "en"
            if l in ["french", "fra", "fre", "fr"]:
                return "fr"
            if l in ["spanish", "spa", "es"]:
                return "es"
            if l in ["german", "deu", "ger", "de"]:
                return "de"
            if l in ["turkish", "tur", "tr"]:
                return "tr"
            if l in ["portuguese", "por", "pt"]:
                return "pt"
            if l in ["italian", "ita", "it"]:
                return "it"
            return l or "und"

        def _lang_label(code):
            names = {
                "ar": "Arabic", "en": "English", "fr": "French",
                "es": "Spanish", "de": "German", "tr": "Turkish",
                "pt": "Portuguese", "it": "Italian", "und": "Unknown",
            }
            return names.get(code, code)

        def quality_sort_key(q):
            q = (q or "").lower()
            if "4k" in q or "2160" in q: return 0
            if "1080" in q: return 1
            if "720" in q: return 2
            if "480" in q: return 3
            if "360" in q: return 4
            return 5

        # --- Resume playback support ---
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
                if res and res.get("action") == "select" and isinstance(res.get("value"), str) and res["value"].startswith("Resume"):
                    start_time = pos

        # --- Smart Local Playback ---
        local_file = None
        lib_path = self.settings.get("library_dir")
        if lib_path and os.path.exists(lib_path):
            from src.utils.library import scan_library
            lib_data = scan_library(lib_path)
            if meta and meta.get("type") == "movie":
                for m in lib_data.get("movies", []):
                    if m["title"].lower() == title.lower() or m["title"].lower() in title.lower():
                        local_file = m["path"]
                        break
            elif meta and meta.get("type") == "tv":
                show_name = title.split(" S")[0]
                if show_name in lib_data.get("tv", {}):
                    season_num = meta.get("season")
                    episode_num = meta.get("episode")
                    for ep in lib_data["tv"][show_name].get(season_num, []):
                        if ep["episode"] == episode_num:
                            local_file = ep["path"]
                            break

        # ── QUALITY SELECTION (once, before action) ──
        qualities = []
        for f in files:
            q = f.get("quality", "Unknown")
            if q not in qualities:
                qualities.append(q)
        qualities.sort(key=quality_sort_key)

        selected_quality = "auto"
        if not autoplay and len(qualities) > 1:
            q_options = [
                {"name": "✨ Auto (Best Available)", "value": "auto"},
                {"name": "🔄 Auto (Adaptive - match connection speed)", "value": "adaptive"},
            ]
            for q in qualities:
                q_options.append({"name": f"📺 {q}", "value": q})
            q_sel = selection_menu(
                q_options,
                f"{title} - Select Quality",
                show_details=False,
                formatter=lambda x: x["name"],
            )
            if q_sel and q_sel["action"] == "select":
                selected_quality = q_sel["value"]["value"]

        # ── SUBTITLE SELECTION ──
        preferred_sub_lang = self.settings.get("preferred_subtitle", "ar")
        include_all_subs = False

        if subtitles:
            lang_codes = []
            for s in subtitles:
                if isinstance(s, dict) and s.get("url"):
                    code = _norm_lang(s.get("lang") or s.get("language"))
                    if code not in lang_codes:
                        lang_codes.append(code)

            if lang_codes:
                sub_options = [
                    {"name": f"📝 Default ({_lang_label(preferred_sub_lang)})", "value": "auto"},
                    {"name": "🗂 All Available", "value": "all"},
                ]
                for code in lang_codes:
                    sub_options.append({"name": f"📝 {_lang_label(code)}", "value": code})
                sub_options.append({"name": "🚫 No subtitles", "value": "none"})

                sub_sel = selection_menu(
                    sub_options,
                    f"{title} - Select Subtitles",
                    show_details=False,
                    formatter=lambda x: x["name"],
                )
                if sub_sel and sub_sel.get("action") == "select":
                    choice = sub_sel["value"]["value"]
                    if choice == "all":
                        include_all_subs = True
                    elif choice == "none":
                        subtitles = []
                        preferred_sub_lang = "none"
                        include_all_subs = False
                    elif choice != "auto":
                        preferred_sub_lang = choice

        # ── Handle adaptive quality (speed test) ──
        if selected_quality == "adaptive":
            console.print(f"[bold {ACCENT}]Testing connection speed...[/bold {ACCENT}]")
            _adaptive_target = None
            try:
                _test_url = files[0].get("file") if files else None
                if _test_url:
                    import time as _t
                    _req = Request(_test_url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-262143"})
                    _start = _t.time()
                    with urlopen(_req, timeout=10) as _resp:
                        _chunk = _resp.read(262144)
                    _elapsed = _t.time() - _start
                    if _elapsed > 0 and len(_chunk) > 0:
                        _speed_mbps = (len(_chunk) * 8) / (_elapsed * 1_000_000)
                        if _speed_mbps > 15:
                            _adaptive_target = "1080"
                        elif _speed_mbps > 8:
                            _adaptive_target = "720"
                        elif _speed_mbps > 3:
                            _adaptive_target = "480"
                        else:
                            _adaptive_target = "360"
                        # Find matching quality from available options
                        for q in qualities:
                            if _adaptive_target in q:
                                selected_quality = q
                                break
                        console.print(f"[green]Connection: {_speed_mbps:.1f} Mbps → Quality: {selected_quality}[/green]")
            except Exception:
                pass
            if selected_quality == "adaptive":
                selected_quality = "auto"
                console.print(f"[yellow]Speed test failed, using best available[/yellow]")
            time.sleep(1)

        # ── Filter files by selected quality ──
        filtered_files = files
        if selected_quality not in ("auto", "adaptive"):
            filtered_files = [f for f in files if f.get("quality") == selected_quality]
            if not filtered_files:
                filtered_files = files  # fallback

        while True:
            # ── ACTION SELECTION ──
            options = ["▶ Play", "⬇ Download"]
            if local_file:
                options.insert(0, "✨ Play Local (High Quality)")

            if autoplay and not local_file:
                act = {"action": "select", "value": "▶ Play"}
            elif autoplay and local_file:
                act = {"action": "select", "value": "✨ Play Local (High Quality)"}
            else:
                act = selection_menu(
                    options,
                    f"{title} - Choose Action",
                    show_details=False,
                    formatter=lambda x: x,
                )

            if not act or act["action"] in ["back", "quit"]:
                return False

            if act["value"] == "✨ Play Local (High Quality)":
                from src.utils.player import play_video
                psl = self.settings.get("preferred_subtitle", "ar")
                ppl = self.settings.get("preferred_player", "mpv")
                play_video(local_file, title, preferred_sub_lang=psl, player=ppl)
                return {"position": 0, "duration": 0, "finished": True}

            # ── Find working source with enhanced selection ──
            console.print(f"[bold {ACCENT}]Selecting source for: {title} ({selected_quality})...[/bold {ACCENT}]")
            
            # Get multiple working sources for fallback
            working_sources = select_multiple_working_sources(filtered_files, count=3)
            
            if not working_sources:
                # Try fetching fresh sources
                console.print(f"[yellow]No working sources, trying fresh fetch...[/yellow]")
                if meta and meta.get("tmdb_id"):
                    fresh_data = self.api.get_sources_api(
                        meta["tmdb_id"], 
                        meta.get("type", "movie"),
                        meta.get("season"),
                        meta.get("episode"),
                        force_refresh=True
                    )
                    if fresh_data and fresh_data.get("files"):
                        working_sources = select_multiple_working_sources(fresh_data["files"], count=3)
            
            if not working_sources:
                console.print(f"[bold red]No working source found for: {title}[/bold red]")
                time.sleep(2)
                return False
            
            selected = working_sources[0]
            fallback_sources = working_sources[1:] if len(working_sources) > 1 else []
            
            # Report success to API client for adaptive scoring
            self.api.report_source_result(selected.get("provider"), True)

            if act["value"] == "▶ Play":
                stats = play_stream(
                    selected.get("file"),
                    title,
                    subtitles,
                    selected.get("headers"),
                    meta,
                    start_time=start_time,
                    preferred_sub_lang=preferred_sub_lang,
                    include_all_subs=include_all_subs,
                    player=self.settings.get("preferred_player", "mpv"),
                )
                if isinstance(stats, dict) and playback_key:
                    self.playback[playback_key] = stats
                    save_json_data(PLAYBACK_FILE, self.playback)
                return stats or False

            elif act["value"] == "⬇ Download":
                template = self.settings.get("filename_template", "{title}.{year}")
                if meta and meta.get("type") == "tv":
                    template = self.settings.get(
                        "filename_template_tv", "{title}.S{season}E{episode}"
                    )

                filename = generate_filename(template, title, meta, selected)
                # Add quality tag to filename if specified
                if selected_quality != "auto":
                    base, ext = os.path.splitext(filename)
                    filename = f"{base}.{selected_quality}{ext}"

                # Include api_params for source refresh during retries
                api_params = None
                if meta and meta.get("tmdb_id"):
                    api_params = {
                        "tmdb_id": meta.get("tmdb_id"),
                        "media_type": meta.get("type"),
                        "season": meta.get("season"),
                        "episode": meta.get("episode"),
                    }

                # Use pre-validated fallback sources, plus remaining unvalidated ones
                all_fallbacks = list(fallback_sources)  # Pre-validated working sources
                for f in filtered_files:
                    if f != selected and f not in all_fallbacks:
                        all_fallbacks.append(f)

                self.download_manager.add_task(
                    selected.get("file"),
                    filename,
                    title,
                    subtitles,
                    selected.get("headers"),
                    meta=meta,
                    fallback_sources=all_fallbacks,
                    api_params=api_params,
                    preferred_sub_lang=preferred_sub_lang,
                    include_all_subs=include_all_subs,
                )
                return False

    def start_player(self, url, title):
        ppl = self.settings.get("preferred_player", "mpv").upper()
        print_header(title)
        console.print(
            f"1. ▶ Play with {ppl}\n2. ⬇ Download Video\n3. 🔗 Copy URL\n4. ⬅ Back"
        )
        choice = console.input(
            f"\n[bold {ACCENT}]Select action (1-4): [/bold {ACCENT}]"
        )

        if choice == "1":
            play_video(url, title, player=self.settings.get("preferred_player", "mpv"))
        elif choice == "2":
            template = self.settings.get("filename_template", "{title}.{year}")
            # generate_filename expects (template, title, meta, selected)
            filename = generate_filename(
                template,
                title,
                meta=None,
                selected={"provider": "direct", "quality": "auto"},
            )
            self.download_manager.add_task(url, filename, title, None, None, api_params=None)
        elif choice == "3":
            console.print(f"\n[bold]URL:[/bold] {url}")
            console.input("\nPress Enter to return...")
        else:
            return


    def handle_local_library(self):
        from src.utils.library import scan_library, format_size
        
        lib_path = self.settings.get("library_dir")
        if not os.path.exists(lib_path):
            console.print(f"[red]Library directory dose not exist: {lib_path}[/red]")
            time.sleep(2)
            return

        while True:
            clear()
            print_header("Local Library")
            console.print(f"[dim]Library Path: {lib_path}[/dim]\n")
            
            data = scan_library(lib_path)
            
            options = []
            if data["movies"]:
                options.append({"name": f"🎬 Movies ({len(data['movies'])})", "type": "movies_root"})
            if data["tv"]:
                options.append({"name": f"📺 TV Shows ({len(data['tv'])})", "type": "tv_root"})
            
            if not options:
                console.print("[yellow]Library is empty.[/yellow]")
                time.sleep(2)
                break
            
            sel = selection_menu(options, "Browse Offline Media", show_details=False, 
                                 formatter=lambda x: x["name"])
            if not sel or sel["action"] == "back":
                break
                
            v = sel["value"]
            if v["type"] == "movies_root":
                self.handle_library_movies(data["movies"])
            elif v["type"] == "tv_root":
                self.handle_library_tv(data["tv"])

    def handle_library_movies(self, movies):
        from src.utils.library import format_size
        while True:
            sel = selection_menu(movies, "Local Movies", show_details=True, 
                                 formatter=lambda x: f"{x['title']} ({x.get('year', 'N/A')})")
            if not sel or sel["action"] == "back":
                break
            
            movie = sel["value"]
            if sel["action"] == "favorite":
                self.toggle_favorite(movie)
                continue
                
            while True:
                clear()
                print_header(movie["title"])
                console.print(f"[bold]Path:[/bold] {movie['path']}")
                console.print(f"[bold]Size:[/bold] {format_size(movie['size'])}")
                console.print(f"[bold]Resolution:[/bold] {movie.get('resolution', 'Unknown')}")
                
                subs = movie.get("subtitles", [])
                if subs:
                    console.print(f"\n[bold]Embedded Subtitles ({len(subs)}):[/bold]")
                    for s in subs:
                        console.print(f"  - {s}")
                
                console.print("\n1. ▶ Play\n2. ⬅ Back")
                
                choice = console.input(f"\n[bold {ACCENT}]Select action (1-2): [/bold {ACCENT}]")
                if choice == "1":
                    play_video(movie["path"], movie["title"], player=self.settings.get("preferred_player", "mpv"))
                else:
                    break

    def handle_library_tv(self, tv_data):
        from src.utils.library import format_size
        while True:
            shows = [{"title": s, "seasons": d} for s, d in tv_data.items()]
            sel = selection_menu(shows, "Local TV Shows", show_details=False, formatter=lambda x: x["title"])
            if not sel or sel["action"] == "back":
                break
                
            show = sel["value"]
            while True:
                seasons = [{"num": sn, "eps": eps} for sn, eps in show["seasons"].items()]
                seasons.sort(key=lambda x: x["num"])
                s_sel = selection_menu(seasons, f"{show['title']} - Seasons", show_details=False, 
                                       formatter=lambda x: f"Season {x['num']} ({len(x['eps'])} Episodes)")
                if not s_sel or s_sel["action"] == "back":
                    break
                    
                season = s_sel["value"]
                while True:
                    e_sel = selection_menu(season["eps"], f"{show['title']} S{season['num']} Episodes", show_details=True,
                                           formatter=lambda x: f"E{x['episode']} - {x['filename']}")
                    if not e_sel or e_sel["action"] == "back":
                        break
                        
                    ep = e_sel["value"]
                    # If user chose 'favorite', we handle it (even though it's offline)
                    if e_sel["action"] == "favorite":
                        self.toggle_favorite(ep)
                        continue

                    # For selecting, we go to details or play?
                    # Let's show details AND option to play
                    while True:
                        clear()
                        print_header(f"{show['title']} S{season['num']}E{ep['episode']}")
                        console.print(f"[bold]File:[/bold] {ep['filename']}")
                        console.print(f"[bold]Path:[/bold] {ep['path']}")
                        console.print(f"[bold]Size:[/bold] {format_size(ep['size'])}")
                        console.print(f"[bold]Resolution:[/bold] {ep.get('resolution', 'Unknown')}")
                        
                        subs = ep.get("subtitles", [])
                        if subs:
                            console.print(f"\n[bold]Embedded Subtitles ({len(subs)}):[/bold]")
                            for s in subs:
                                console.print(f"  - {s}")
    
                        console.print("\n1. ▶ Play\n2. ⬅ Back")
                        
                        choice = console.input(f"\n[bold {ACCENT}]Select action (1-2): [/bold {ACCENT}]")
                        if choice == "1":
                            play_video(ep["path"], f"{show['title']} S{season['num']}E{ep['episode']}", player=self.settings.get("preferred_player", "mpv"))
                        else:
                            break

    def handle_download_manager(self):
        import select
        from rich.table import Table
        from rich.panel import Panel
        from rich.live import Live

        selected_indices = set()  # Track multi-selected items
        show_action_menu = False

        def _status_icon(s):
            return {"downloading": "⬇", "pending": "⏳", "muxing": "🔄", "completed": "✅", "error": "❌"}.get(s, "?")

        def generate_queue_table():
            q = self.download_manager.get_queue()
            table = Table(title="Download Queue", expand=True, border_style=ACCENT, box=None)
            table.add_column("", width=3, justify="center")   # checkbox
            table.add_column("#", style="dim", width=4, justify="center")
            table.add_column("Title", style=f"bold {TEXT}", no_wrap=True, max_width=35)
            table.add_column("Progress", width=28)
            table.add_column("Downloaded", justify="center", width=18)
            table.add_column("Speed", style="cyan", justify="right", width=12)
            # table.add_column("ETA", style="yellow", justify="right", width=9)
            table.add_column("Status", justify="center", width=12)

            for i, task in enumerate(q):
                s = task["status"]
                is_active = s == "downloading"
                is_muxing = s == "muxing"
                is_done = s == "completed"
                is_error = s == "error"
                is_pending = s == "pending"

                # ── Checkbox ──
                chk = "[bold green]✓[/]" if i in selected_indices else "[dim]○[/]"

                # ── Status text ──
                status_color = (
                    "green" if is_done else
                    "bold blue" if is_muxing else
                    "bold cyan" if is_active else
                    "red" if is_error else
                    "dim white"
                )
                display_status = (task.get("status_message") or s).upper()
                if is_error and "Validation" in task.get("error_log", ""):
                    display_status = "INVALID"
                if is_pending:
                    # Show queue position
                    pending_q = [t for t in q if t["status"] == "pending"]
                    pos = next((j for j, t in enumerate(pending_q) if t["id"] == task["id"]), 0)
                    display_status = f"QUEUE #{pos + 1}"
                status_text = f"[{status_color}]{_status_icon(s)} {display_status}[/{status_color}]"

                # ── Progress bar ──
                p = task.get("progress", 0)
                bar_width = 20
                filled = int((p / 100) * bar_width)
                bar_color = (
                    "green" if is_done else
                    "bold blue" if is_muxing else
                    "bold cyan" if is_active else
                    "dim white"
                )
                bar = f"[{bar_color}]" + "━" * filled + "[/][dim]" + "─" * (bar_width - filled) + "[/]"
                progress_display = f"{bar} [bold]{p:5.1f}%[/]"

                # ── Downloaded / Total ──
                dl_bytes = task.get("_bytes_downloaded", 0)
                total_bytes = task.get("_bytes_total", 0)
                dl_human = task.get("downloaded", "")
                total_human = task.get("total_size", "")

                if is_active or is_muxing:
                    if total_bytes > 0 and dl_bytes > 0:
                        downloaded_display = f"[cyan]{self.download_manager._bytes_to_human(dl_bytes)}[/] / [white]{self.download_manager._bytes_to_human(total_bytes)}[/]"
                    elif dl_human and dl_human not in ("0 B", "---", "0B"):
                        if total_human and total_human not in ("Unknown", "---", "0B"):
                            downloaded_display = f"[cyan]{dl_human}[/] / [white]{total_human}[/]"
                        else:
                            downloaded_display = f"[cyan]{dl_human}[/]"
                    else:
                        downloaded_display = "[dim]starting...[/]" if is_active else "[blue]processing[/]"
                elif is_done:
                    if total_bytes > 0:
                        downloaded_display = f"[green]{self.download_manager._bytes_to_human(total_bytes)}[/]"
                    elif total_human and total_human not in ("Unknown", "---"):
                        downloaded_display = f"[green]{total_human}[/]"
                    else:
                        downloaded_display = "[green]Complete[/]"
                elif is_error:
                    downloaded_display = "[red]Failed[/]"
                else:
                    downloaded_display = "[dim]waiting...[/]"

                # ── Speed ──
                if is_active:
                    speed_val = task.get("speed", "---")
                    if speed_val and speed_val not in ("---", "0 B/s", "0B/s"):
                        if speed_val in ("finishing", "finalizing"):
                            speed = f"[yellow]{speed_val}[/]"
                        else:
                            speed = f"[cyan]{speed_val}[/]"
                    else:
                        speed = "[dim]connecting[/]"
                elif is_muxing:
                    speed = "[bold blue]muxing[/]"
                elif is_done:
                    speed = "[green]done[/]"
                elif is_error:
                    speed = "[red]---[/]"
                else:
                    speed = "[dim]---[/]"

                # ── ETA ──
                if is_active:
                    eta_val = task.get("eta", "---")
                    if eta_val and eta_val not in ("---", "00:00"):
                        if eta_val in ("soon", "done"):
                            eta = f"[yellow]{eta_val}[/]"
                        else:
                            eta = f"[green]{eta_val}[/]"
                    else:
                        eta = "[dim]---[/]"
                elif is_muxing:
                    eta = "[blue]muxing[/]"
                elif is_done:
                    eta = "[green]✓[/]"
                elif is_error:
                    eta = "[red]✗[/]"
                else:
                    eta = "[dim]---[/]"

                table.add_row(chk, f"#{i + 1}", task["title"], progress_display, downloaded_display, speed, eta, status_text)

            # Build subtitle with available actions
            actions = []
            if selected_indices:
                n = len(selected_indices)
                actions.append(f"[bold green](Enter)[/] Actions on {n} selected")
            actions.extend([
                "[bold white](1-9)[/] Toggle select",
                "[bold yellow](A)[/] Select All",
                "[bold dim](N)[/] Select None",
                "[bold blue](R)[/] Retry errors",
                "[bold green](C)[/] Clear done",
                "[bold white](Q)[/] Back",
            ])
            subtitle = " │ ".join(actions)
            return Panel(table, subtitle=subtitle, border_style=ACCENT)

        def show_actions_for_selected():
            """Show action menu for selected items. Returns True to continue, False to exit."""
            nonlocal selected_indices
            q = self.download_manager.get_queue()
            items = [q[i] for i in sorted(selected_indices) if i < len(q)]
            if not items:
                return True

            clear()
            n = len(items)
            console.print(f"\n[bold {ACCENT}]Actions for {n} selected item(s):[/bold {ACCENT}]\n")
            for idx, item in enumerate(items):
                console.print(f"  {idx+1}. {_status_icon(item['status'])} {item['title']} [{item['status'].upper()}]")
            console.print()
            console.print(f"  [bold red]1. 🗑  Remove selected[/bold red]")
            console.print(f"  [bold yellow]2. 🔄 Retry selected[/bold yellow]")
            console.print(f"  [bold white]3. ⬅  Cancel[/bold white]")
            console.print()

            choice = console.input(f"[bold {ACCENT}]Choose action (1-3): [/bold {ACCENT}]").strip()

            if choice == "1":
                for item in items:
                    self.download_manager.remove_task(item["id"])
                console.print(f"[red]Removed {n} item(s).[/red]")
                selected_indices.clear()
                time.sleep(1)
            elif choice == "2":
                retried = 0
                for item in items:
                    if item["status"] in ("error", "completed", "pending"):
                        self.download_manager.retry_task(item["id"])
                        retried += 1
                console.print(f"[yellow]Retried {retried} item(s).[/yellow]")
                selected_indices.clear()
                time.sleep(1)
            # choice 3 or anything else = cancel
            return True

        clear()
        with Live(generate_queue_table(), refresh_per_second=2, console=console, screen=True) as live:
            while True:
                live.update(generate_queue_table())

                key = None
                if sys.platform == "win32":
                    import msvcrt
                    if msvcrt.kbhit():
                        raw_key = msvcrt.getch()
                        if raw_key in [b'\x00', b'\xe0']:
                            msvcrt.getch()
                        elif raw_key == b'\r':
                            key = 'enter'
                        else:
                            try:
                                key = raw_key.decode('utf-8').lower()
                            except Exception:
                                pass
                else:
                    if select.select([sys.stdin], [], [], 0)[0]:
                        key = sys.stdin.read(1).lower()
                        if key == '\n':
                            key = 'enter'

                if key:
                    q = self.download_manager.get_queue()
                    if key in ('q', '\x1b'):
                        break
                    elif key == 'enter' and selected_indices:
                        live.stop()
                        show_actions_for_selected()
                        clear()
                        live.start()
                    elif key == 'c':
                        self.download_manager.clear_completed()
                        selected_indices.clear()
                    elif key == 'r':
                        for t in q:
                            if t["status"] == "error":
                                self.download_manager.retry_task(t["id"])
                    elif key == 'a':
                        # Select all
                        selected_indices = set(range(len(q)))
                    elif key == 'n':
                        # Select none
                        selected_indices.clear()
                    elif key.isdigit() and key != '0':
                        idx = int(key) - 1
                        if 0 <= idx < len(q):
                            if idx in selected_indices:
                                selected_indices.discard(idx)
                            else:
                                selected_indices.add(idx)

                time.sleep(0.1)
        clear()

if __name__ == "__main__":
    # Ensure backend is started and healthy before instantiating the CLI
    start_local_backend(os.getenv("BACKEND_URL"), timeout=30)

    cli = CinemaCLI()
    try:
        show_splash()
        cli.main_menu()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
