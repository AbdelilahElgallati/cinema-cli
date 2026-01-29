import sys
import os
import time
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit import Application
from prompt_toolkit.layout import Layout as PTLayout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# from src.config import console, BACKEND_URL, TMDB_API_KEY, HISTORY_FILE, FAVORITES_FILE, SETTINGS_FILE, PRIMARY, SECONDARY, ACCENT, TEXT, SUCCESS, WARNING
from src.config import console, BACKEND_URL, TMDB_API_KEY, HISTORY_FILE, FAVORITES_FILE, SETTINGS_FILE, PRIMARY, SECONDARY, ACCENT, TEXT, SUCCESS, WARNING
from src.utils.storage import load_json_data, save_json_data
from src.utils.api import APIClient
from src.ui.ui import print_header, show_splash, selection_menu, format_item, clear
from src.utils.player import play_stream, play_video
from src.utils.downloads import download_stream

class CinemaCLI:
    def __init__(self):
        self.settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
        self.api = APIClient(self.settings)
        self.history = load_json_data(HISTORY_FILE)
        self.favorites = load_json_data(FAVORITES_FILE)
        
    def main_menu(self):
        while True:
            print_header("Main Menu")
            options = [
                {"name": "🔍 Search Movies & TV", "action": self.handle_search},
                {"name": "📈 Trending This Week", "action": self.handle_trending},
                {"name": "🔥 Popular Content", "action": self.handle_popular},
                {"name": "🎭 Browse by Genre", "action": self.handle_genres},
                {"name": "⭐ My Favorites", "action": self.handle_favorites},
                {"name": "🕒 Watch History", "action": self.handle_history},
                {"name": "⚙️ Settings", "action": self.handle_settings},
                {"name": "❌ Exit", "action": sys.exit}
            ]
            
            selected_index = 0
            kb = KeyBindings()

            @kb.add('up')
            def _(event):
                nonlocal selected_index
                selected_index = (selected_index - 1) % len(options)

            @kb.add('down')
            def _(event):
                nonlocal selected_index
                selected_index = (selected_index + 1) % len(options)

            @kb.add('enter')
            def _(event):
                event.app.exit(result=options[selected_index]['action'])

            @kb.add('q')
            def _(event):
                event.app.exit(result=sys.exit)
            @kb.add('b')
            def _(event):
                event.app.exit(result=None)

            def get_menu_text():
                res = []
                for i, opt in enumerate(options):
                    if i == selected_index:
                        res.append(('class:selected', f"  ▶ {opt['name']}  \n"))
                    else:
                        res.append(('class:item', f"    {opt['name']}  \n"))
                return res

            style = Style.from_dict({
                'selected': f'bg:{PRIMARY} fg:#ffffff bold',
                'item': f'{TEXT}',
            })

            app = Application(layout=PTLayout(Window(FormattedTextControl(get_menu_text))), key_bindings=kb, style=style)
            action = app.run()
            if action:
                action()

    def handle_search(self):
        print_header("Search")
        query = console.input(f"[bold {ACCENT}]Search for a movie or TV show: [/bold {ACCENT}]")
        if not query.strip(): return

        data = self.api.get_tmdb_data("search/multi", {"query": query})
        if not data or not data.get('results'):
            console.print("[yellow]No results found.[/yellow]")
            time.sleep(1.5)
            return

        results = [r for r in data['results'] if r.get('media_type') in ['movie', 'tv']]
        while True:
            sel = selection_menu(results, f"Search Results for '{query}'")
            if not sel or sel['action'] == 'back': break
            if sel['action'] == 'favorite':
                self.toggle_favorite(sel['value'])
                continue
            if sel['action'] == 'select':
                self.handle_media(sel['value'])

    def handle_trending(self):
        data = self.api.get_tmdb_data("trending/all/week")
        if not data: return
        results = [r for r in data['results'] if r.get('media_type') in ['movie', 'tv']]
        while True:
            sel = selection_menu(results, "Trending This Week")
            if not sel or sel['action'] == 'back': break
            if sel['action'] == 'favorite':
                self.toggle_favorite(sel['value'])
                continue
            if sel['action'] == 'select':
                self.handle_media(sel['value'])

    def handle_popular(self):
        print_header("Popular")
        types = [{"name": "🎬 Movies", "val": "movie"}, {"name": "📺 TV Shows", "val": "tv"}]
        console.print(f"1. {types[0]['name']}\n2. {types[1]['name']}")
        choice = console.input(f"\n[bold {ACCENT}]Select type (1-2): [/bold {ACCENT}]")
        m_type = types[0]['val'] if choice == '1' else types[1]['val']
        
        data = self.api.get_tmdb_data(f"{m_type}/popular")
        if not data: return
        results = data['results']
        for r in results: r['media_type'] = m_type
        
        while True:
            sel = selection_menu(results, f"Popular {m_type.title()}s")
            if not sel or sel['action'] == 'back': break
            if sel['action'] == 'favorite':
                self.toggle_favorite(sel['value'])
                continue
            if sel['action'] == 'select':
                self.handle_media(sel['value'])

    def handle_genres(self):
        print_header("Genres")
        types = [{"name": "🎬 Movies", "val": "movie"}, {"name": "📺 TV Shows", "val": "tv"}]
        console.print(f"1. {types[0]['name']}\n2. {types[1]['name']}")
        choice = console.input(f"\n[bold {ACCENT}]Select type (1-2): [/bold {ACCENT}]")
        m_type = types[0]['val'] if choice == '1' else types[1]['val']
        
        data = self.api.get_tmdb_data(f"genre/{m_type}/list")
        if not data: return
        genres = data['genres']
        
        selected_index = 0
        kb = KeyBindings()
        @kb.add('up')
        def _(event):
            nonlocal selected_index
            selected_index = (selected_index - 1) % len(genres)
        @kb.add('down')
        def _(event):
            nonlocal selected_index
            selected_index = (selected_index + 1) % len(genres)
        @kb.add('enter')
        def _(event):
            event.app.exit(result=genres[selected_index])
        @kb.add('q')
        def _(event):
            event.app.exit()
        @kb.add('b')
        def _(event):
            event.app.exit()

        def get_genre_text():
            res = []
            for i, g in enumerate(genres):
                if i == selected_index:
                    res.append(('class:selected', f"  ▶ {g['name']}  \n"))
                else:
                    res.append(('class:item', f"    {g['name']}  \n"))
            return res

        app = Application(layout=PTLayout(Window(FormattedTextControl(get_genre_text))), key_bindings=kb, style=Style.from_dict({'selected': f'bg:{ACCENT} fg:#ffffff bold', 'item': f'{TEXT}'}))
        genre = app.run()
        
        if genre:
            data = self.api.get_tmdb_data(f"discover/{m_type}", {"with_genres": genre['id']})
            if not data: return
            results = data['results']
            for r in results: r['media_type'] = m_type
            while True:
                sel = selection_menu(results, f"{genre['name']} {m_type.title()}s")
                if not sel or sel['action'] == 'back': break
                if sel['action'] == 'select': self.handle_media(sel['value'])

    def handle_favorites(self):
        if not self.favorites:
            print_header("Favorites")
            console.print("[yellow]No favorites yet. Press 'F' on any item to add it![/yellow]")
            time.sleep(2)
            return
        while True:
            sel = selection_menu(self.favorites, "My Favorites")
            if not sel or sel['action'] == 'back': break
            if sel['action'] == 'favorite':
                self.toggle_favorite(sel['value'])
                self.favorites = load_json_data(FAVORITES_FILE)
                if not self.favorites: break
                continue
            if sel['action'] == 'select': self.handle_media(sel['value'])

    def handle_history(self):
        if not self.history:
            print_header("History")
            console.print("[yellow]Your watch history is empty.[/yellow]")
            time.sleep(2)
            return
        while True:
            sel = selection_menu(self.history, "Watch History")
            if not sel or sel['action'] == 'back': break
            if sel['action'] == 'select': self.handle_media(sel['value'])

    def handle_settings(self):
        print_header("Settings")
        console.print(f"[bold {TEXT}]1. Backend URL:[/bold {TEXT}] {self.settings.get('backend', BACKEND_URL)}")
        console.print(f"[bold {TEXT}]2. TMDB API Key:[/bold {TEXT}] {self.settings.get('tmdb_key', 'Using Default')}")
        
        choice = console.input(f"\n[bold {ACCENT}]Select setting to change (1-2) or Enter to back: [/bold {ACCENT}]")
        
        if choice == '1':
            new_val = console.input(f"[bold {ACCENT}]Enter new backend URL: [/bold {ACCENT}]")
            if new_val.strip():
                self.settings['backend'] = new_val.strip()
        elif choice == '2':
            new_val = console.input(f"[bold {ACCENT}]Enter new TMDB API Key: [/bold {ACCENT}]")
            if new_val.strip():
                self.settings['tmdb_key'] = new_val.strip()
        else:
            return

        save_json_data(SETTINGS_FILE, self.settings)
        console.print("[green]Settings saved![/green]")
        time.sleep(1)

    def toggle_favorite(self, item):
        item_id = item.get('id')
        exists = any(f.get('id') == item_id for f in self.favorites)
        if exists:
            self.favorites = [f for f in self.favorites if f.get('id') != item_id]
            console.print(f"[yellow]Removed from favorites.[/yellow]")
        else:
            self.favorites.insert(0, item)
            console.print(f"[green]Added to favorites![/green]")
        save_json_data(FAVORITES_FILE, self.favorites)
        time.sleep(0.5)

    def handle_media(self, media):
        self.history = [h for h in self.history if h.get('id') != media.get('id')]
        self.history.insert(0, media)
        self.history = self.history[:50]
        save_json_data(HISTORY_FILE, self.history)

        m_type = media.get('media_type', 'movie')
        if m_type == 'movie':
            self.play_movie(media)
        else:
            self.show_seasons(media)

    def play_movie(self, media):
        title = media.get('title')
        tmdb_id = media.get('id')
        data = self.api.get_sources_api(tmdb_id, 'movie')
        self.handle_sources(title, data)

    def show_seasons(self, media):
        print_header(f"{media.get('name')} - Seasons")
        data = self.api.get_tmdb_data(f"tv/{media['id']}")
        if not data: return
        seasons = [s for s in data.get('seasons', []) if s.get('season_number') > 0]
        
        def fmt_season(x):
            name = x.get('name', '')
            air = x.get('air_date') or '????-??-??'
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else '????'
            rating = x.get('vote_average', 0)
            return f"{name} ({year}) | ⭐ {rating:.1f} | TV"

        while True:
            sel = selection_menu(seasons, f"{media.get('name')} Seasons", show_details=False, formatter=fmt_season)
            if not sel or sel['action'] == 'back': break
            if sel['action'] == 'select':
                self.show_episodes(media, sel['value'])

    def show_episodes(self, media, season):
        s_num = season['season_number']
        print_header(f"{media.get('name')} - Season {s_num}")
        data = self.api.get_tmdb_data(f"tv/{media['id']}/season/{s_num}")
        if not data: return
        episodes = data.get('episodes', [])
        
        def fmt_ep(x):
            name = x.get('name', 'Unknown')
            air = x.get('air_date') or 'N/A'
            year = air[:4] if isinstance(air, str) and len(air) >= 4 else 'N/A'
            rating = x.get('vote_average', 0)
            return f"{name} ({year}) | ⭐ {rating:.1f} | TV"
        
        selected_idx = 0
        while True:
            sel = selection_menu(episodes, f"Season {s_num} Episodes", show_details=True, formatter=fmt_ep, default_index=selected_idx)
            if not sel or sel['action'] == 'back': break
            if sel['action'] == 'select':
                ep = sel['value']
                selected_idx = episodes.index(ep)
                
                while True:
                    title = f"{media.get('name')} S{s_num}E{ep['episode_number']} - {ep.get('name')}"
                    data = self.api.get_sources_api(media['id'], 'tv', s_num, ep['episode_number'])
                    played = self.handle_sources(title, data)
                    
                    if not played:
                        break
                        
                    # Finished Watching Menu
                    opts = ["Next Episode", "Previous Episode", "Replay", "Back to List"]
                    fin_sel = selection_menu(opts, "Finished Watching", show_details=False, formatter=lambda x: x)
                    
                    if not fin_sel or fin_sel['action'] in ['back', 'quit']:
                        break
                        
                    choice = fin_sel['value']
                    if choice == "Next Episode":
                        if selected_idx + 1 < len(episodes):
                            selected_idx += 1
                            ep = episodes[selected_idx]
                        else:
                            console.print("[yellow]No next episode in this season.[/yellow]")
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

    def handle_sources(self, title, data):
        files = data.get('files', [])
        subtitles = data.get('subtitles', [])
        if not files:
            console.print("[red]No streams found.[/red]")
            time.sleep(1.5)
            return False
        while True:
            def fmt_src(x):
                q = x.get('quality', 'auto')
                p = x.get('provider', 'src')
                t = x.get('type', 'std')
                return f"{p.upper():<12} [{q}] {t}"
            sel = selection_menu(files, f"Select Source - {title}", show_details=False, formatter=fmt_src)
            if not sel or sel['action'] in ['back', 'quit']:
                return False
            selected = sel['value']
            act = selection_menu(['▶ Play', '⬇ Download'], f"{title} - Choose Action", show_details=False, formatter=lambda x: x)
            if not act or act['action'] in ['back', 'quit']:
                continue
            if act['value'] == '▶ Play':
                play_stream(selected.get('file'), title, subtitles, selected.get('headers'))
                return True
            elif act['value'] == '⬇ Download':
                safe_title = "".join(c for c in title if c.isalnum() or c in ' _-').strip().replace(' ', '_')
                ext = 'mp4'
                if '.m3u8' in selected.get('file', '') or '.m3u' in selected.get('file', ''):
                    ext = 'ts'
                filename = f"{safe_title}.{ext}"
                download_stream(selected.get('file'), filename, subtitles, selected.get('headers'), session=self.api.session)
                return False

    def start_player(self, url, title):
        print_header(title)
        console.print(f"1. ▶ Play with MPV\n2. ⬇ Download Video\n3. 🔗 Copy URL\n4. ⬅ Back")
        choice = console.input(f"\n[bold {ACCENT}]Select action (1-4): [/bold {ACCENT}]")
        
        if choice == '1':
            play_video(url, title)
        elif choice == '2':
            safe_title = "".join(c for c in title if c.isalnum() or c in ' _-').strip().replace(' ', '_')
            filename = f"{safe_title}.mp4"
            download_stream(url, filename, session=self.api.session)
        elif choice == '3':
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
