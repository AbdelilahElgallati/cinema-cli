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
from src.utils.player import play_stream, play_video
from src.utils.storage import load_json_data, save_json_data
from src.utils.utils import generate_filename


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

        self.api = APIClient(self.settings)

        # IMPORTANT: ensure lists/dicts, not None
        self.history = load_json_data(HISTORY_FILE) or []
        self.favorites = load_json_data(FAVORITES_FILE) or []
        self.playback = load_json_data(PLAYBACK_FILE) or {}

        self.download_manager = DownloadManager()
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

        choice = console.input(
            f"\n[bold {ACCENT}]Select setting to change (1-4) or Enter to back: [/bold {ACCENT}]"
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
        data = self.api.get_sources_api(tmdb_id, "movie")

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

        # Ask for source for EACH episode individually
        for ep in selected_episodes:
            title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"
            console.print(
                f"\n[bold {ACCENT}]Select source for: {title}[/bold {ACCENT}]"
            )

            data = self.api.get_sources_api(
                media["id"], "tv", s_num, ep["episode_number"]
            )
            files = data.get("files", [])
            subtitles = data.get("subtitles", [])

            if not files:
                console.print(
                    f"[yellow]No sources found for {title}. Skipping...[/yellow]"
                )
                continue

            def fmt_src(x):
                q = x.get("quality", "auto")
                p = x.get("provider", "src")
                t = x.get("type", "std")
                return f"{p.upper():<12} [{q}] {t}"

            sel = selection_menu(
                files, f"Sources - {title}", show_details=False, formatter=fmt_src
            )
            if not sel or sel["action"] != "select":
                console.print(f"[yellow]Skipping {title}...[/yellow]")
                continue

            selected_source = sel["value"]

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

            console.print(
                f"[green]Queuing download using {selected_source.get('provider')} ({selected_source.get('quality')})...[/green]"
            )
            self.download_manager.add_task(
                selected_source.get("file"),
                filename,
                title,
                subtitles,
                selected_source.get("headers"),
                meta,
            )

        console.print(f"\n[bold {SUCCESS}]Batch download queued![/bold {SUCCESS}]")
        time.sleep(2)

    def handle_sources(self, title, data, meta=None):
        files = data.get("files", [])
        subtitles = data.get("subtitles", [])
        if not files:
            console.print("[red]No streams found.[/red]")
            time.sleep(1.5)
            return False

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

        while True:

            def fmt_src(x):
                q = x.get("quality", "auto")
                p = x.get("provider", "src")
                t = x.get("type", "std")
                return f"{p.upper():<12} [{q}] {t}"

            sel = selection_menu(
                files, f"Select Source - {title}", show_details=False, formatter=fmt_src
            )
            if not sel or sel["action"] in ["back", "quit"]:
                return False

            selected = sel["value"]
            act = selection_menu(
                ["▶ Play", "⬇ Download"],
                f"{title} - Choose Action",
                show_details=False,
                formatter=lambda x: x,
            )
            if not act or act["action"] in ["back", "quit"]:
                continue

            if act["value"] == "▶ Play":
                # ✅ FIX: return stats dict, not True
                stats = play_stream(
                    selected.get("file"),
                    title,
                    subtitles,
                    selected.get("headers"),
                    meta,
                    start_time=start_time,
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
        print_header(title)
        console.print(
            "1. ▶ Play with MPV\n2. ⬇ Download Video\n3. 🔗 Copy URL\n4. ⬅ Back"
        )
        choice = console.input(
            f"\n[bold {ACCENT}]Select action (1-4): [/bold {ACCENT}]"
        )

        if choice == "1":
            play_video(url, title)
        elif choice == "2":
            template = self.settings.get("filename_template", "{title}.{year}")
            # generate_filename expects (template, title, meta, selected)
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
    # Ensure backend is started and healthy before instantiating the CLI
    start_local_backend(os.getenv("BACKEND_URL"), timeout=30)

    cli = CinemaCLI()
    try:
        show_splash()
        cli.main_menu()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
