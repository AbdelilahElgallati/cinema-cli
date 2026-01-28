# import sys
# import os
# import requests
# import subprocess
# import shutil
# from dotenv import load_dotenv
# from rich.console import Console
# from rich.table import Table
# from rich.panel import Panel
# from rich.text import Text
# from rich.progress import Progress
# from prompt_toolkit import Application
# from prompt_toolkit.key_binding import KeyBindings
# from prompt_toolkit.layout import Layout, Window, HSplit
# from prompt_toolkit.layout.controls import FormattedTextControl
# from prompt_toolkit.styles import Style

# # Load environment variables
# load_dotenv()

# # Configuration
# BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:3000")
# TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# console = Console()

# class CineProTUI:
#     def __init__(self):
#         self.session = requests.Session()
#         if not TMDB_API_KEY:
#             console.print("[red]Error: TMDB_API_KEY not found in environment variables.[/red]")
#             sys.exit(1)

#     def search_tmdb(self, query, media_type="multi"):
#         url = "https://api.themoviedb.org/3/search/multi"
#         params = {
#             "api_key": TMDB_API_KEY,
#             "query": query,
#             "language": "en-US"
#         }
#         try:
#             response = self.session.get(url, params=params)
#             response.raise_for_status()
#             results = response.json().get("results", [])
#             # Filter only movie and tv
#             return [r for r in results if r.get("media_type") in ["movie", "tv"]]
#         except Exception as e:
#             console.print(f"[red]Error searching TMDB: {e}[/red]")
#             return []

#     def get_sources(self, tmdb_id, media_type, season=None, episode=None):
#         if media_type == "movie":
#             url = f"{BACKEND_URL}/movie/{tmdb_id}"
#         else:
#             url = f"{BACKEND_URL}/tv/{tmdb_id}?s={season}&e={episode}"
        
#         try:
#             response = self.session.get(url)
#             # Backend might return error object
#             if response.status_code != 200:
#                 return {}
#             return response.json()
#         except Exception as e:
#             return {}

#     def play_video(self, url, title, subtitles=None):
#         mpv_args = ["mpv", url, f"--title={title}", "--fs"] # --fs for fullscreen
        
#         # Add shortcuts help to title or print it before
#         console.print(Panel("[green]Starting Player...[/green]\n[yellow]Space: Pause | Arrows: Seek | F: Fullscreen | Q: Quit[/yellow]", title="MPV Player"))

#         if subtitles:
#             # Check for Arabic or user preferred subs
#             # Prioritize Arabic
#             arabic_subs = [s for s in subtitles if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
#             if arabic_subs:
#                 sub_url = arabic_subs[0]["url"]
#                 mpv_args.append(f"--sub-file={sub_url}")
#                 console.print(f"[cyan]Subtitle loaded: Arabic[/cyan]")
#             else:
#                 # Load first available or let mpv handle? 
#                 # Mpv might need sub-file argument for external subs
#                 if len(subtitles) > 0:
#                      sub_url = subtitles[0]["url"]
#                      mpv_args.append(f"--sub-file={sub_url}")
#                      console.print(f"[cyan]Subtitle loaded: {subtitles[0].get('lang', 'Unknown')}[/cyan]")

#         try:
#             # Check if mpv is available
#             if shutil.which("mpv") is None:
#                 console.print("[red]Error: 'mpv' executable not found in PATH. Please install mpv.[/red]")
#                 input("Press Enter to continue...")
#                 return

#             subprocess.run(mpv_args)
#         except Exception as e:
#             console.print(f"[red]Error running mpv: {e}[/red]")
#             input("Press Enter to continue...")

#     def selection_menu(self, items, title, formatter=lambda x: str(x), can_jump=False):
#         """
#         Interactive menu using prompt_toolkit.
#         items: list of objects
#         formatter: function to concert string to display text
#         Returns: selected item or None (if back/quit)
#         """
#         if not items:
#             console.print("[yellow]No items to display.[/yellow]")
#             return None

#         bindings = KeyBindings()
#         selected_index = 0
        
#         # We need a container to update the UI
#         # But prompt_toolkit Application takes over the screen.
#         # We want to mimic a loop.
        
#         result = {'value': None, 'action': None}

#         @bindings.add('up')
#         def _(event):
#             nonlocal selected_index
#             selected_index = (selected_index - 1) % len(items)

#         @bindings.add('down')
#         def _(event):
#             nonlocal selected_index
#             selected_index = (selected_index + 1) % len(items)

#         @bindings.add('enter')
#         def _(event):
#             result['value'] = items[selected_index]
#             result['action'] = 'select'
#             event.app.exit()

#         @bindings.add('q')
#         @bindings.add('escape')
#         def _(event):
#             result['action'] = 'quit'
#             event.app.exit()
            
#         @bindings.add('b')
#         def _(event):
#             result['action'] = 'back'
#             event.app.exit()

#         if can_jump:
#             @bindings.add('g')
#             def _(event):
#                  # We can't easily capture input inside this binding without complex logic.
#                  # We'll set a flag and handle it outside or return a special action
#                  result['action'] = 'jump'
#                  event.app.exit()

#         def get_formatted_text():
#             lines = []
#             # Header
#             lines.append(('class:header', f" {title} \n"))
#             lines.append(('class:header', f" {'=' * len(title)} \n\n"))
            
#             # Helper text
#             helper = "↑/↓: Navigate | Enter: Select | B: Back | Q: Quit"
#             if can_jump:
#                 helper += " | G: Jump to Episode"
#             lines.append(('class:helper', f"{helper}\n\n"))

#             # Calculate visible window to avoid scrolling issues if list is huge
#             # For simplicity, we render all, prompt_toolkit handles scrolling if we use a proper layout, 
#             # but simpler TextControl just dumps.
#             # Let's limit display ref roughly to terminal height if needed, but simple list is fine.
            
#             start_idx = 0
#             end_idx = len(items)
            
#             # Simple pagination logic
#             max_rows = 20
#             if len(items) > max_rows:
#                 start_idx = max(0, selected_index - (max_rows // 2))
#                 end_idx = min(len(items), start_idx + max_rows)
            
#             for i in range(start_idx, end_idx):
#                 item = items[i]
#                 if i == selected_index:
#                     lines.append(('class:selected', f" > {formatter(item)}\n"))
#                 else:
#                     lines.append(('', f"   {formatter(item)}\n"))
            
#             if start_idx > 0:
#                 lines.insert(3, ('', "   ... (scroll up)\n"))
#             if end_idx < len(items):
#                 lines.append(('', "   ... (scroll down)\n"))
                
#             return lines

#         # Define Layout
#         layout = Layout(
#             HSplit([
#                 Window(content=FormattedTextControl(get_formatted_text))
#             ])
#         )

#         # Style
#         style = Style.from_dict({
#             'header': 'bold cyan',
#             'selected': 'reverse green',
#             'helper': 'gray'
#         })

#         app = Application(layout=layout, key_bindings=bindings, style=style, full_screen=True)
#         app.run()
        
#         return result

#     def input_loop(self):
#         console.clear()
#         console.print(Panel.fit("🎬 CinePro CLI", style="bold magenta"))

#         while True:
#             query = console.input("\n[bold cyan]🔍 Search movie/tv (or 'q' to quit): [/bold cyan]")
#             if query.lower() in ['q', 'quit', 'exit']:
#                 break
            
#             with console.status("[bold green]Searching TMDB...[/bold green]"):
#                 results = self.search_tmdb(query)
            
#             if not results:
#                 console.print("[red]No results found.[/red]")
#                 continue

#             # Selection Loop
#             while True:
#                 def result_formatter(x):
#                     date = x.get('release_date') or x.get('first_air_date') or 'N/A'
#                     year = date.split('-')[0]
#                     return f"{x.get('title') or x.get('name')} ({year}) [{x.get('media_type').upper()}]"

#                 selection = self.selection_menu(results, "Search Results", result_formatter)
                
#                 if selection['action'] in ['quit', 'back', None]:
#                     break
                
#                 media = selection['value']
#                 # Determine next step
#                 if media['media_type'] == 'tv':
#                     self.handle_tv_show(media)
#                 else:
#                     self.handle_movie(media)
#                 # After handling, we loop back to results list (or break if handle returns 'back'?)
#                 # For now, looping back to results makes sense so user can pick another.
    
#     def handle_movie(self, media):
#         # Fetch sources
#         with console.status("[bold yellow]Fetching sources...[/bold yellow]"):
#             data = self.get_sources(media['id'], 'movie')
        
#         self.handle_sources_selection(data, media['title'], media)

#     def handle_tv_show(self, media):
#         # We need to pick season and episode
#         # TMDB API doesn't give full episode list nicely in search results.
#         # Usually we need details endpoint.
#         # For simplicity, we can ask user or fetch stats.
#         # But 'G' jump implies we can pick. 
#         # Let's start with a Season numeric input -> Episode numeric list?
#         # Or just "Enter Season" -> "Enter Episode" prompts, but user wants TUI lists.
#         # Creating TUI lists for Seasons/Episodes requires fetching metadata.
        
#         # Getting season count:
#         details_url = f"https://api.themoviedb.org/3/tv/{media['id']}?api_key={TMDB_API_KEY}"
#         resp = self.session.get(details_url)
#         if resp.status_code != 200:
#             console.print("[red]Could not fetch TV details.[/red]")
#             return
        
#         tv_details = resp.json()
#         seasons = tv_details.get('seasons', [])
#         seasons = [s for s in seasons if s['season_number'] > 0] # Filter specials usually

#         while True:
#             # Select Season
#             season_sel = self.selection_menu(
#                 seasons, 
#                 f"{media['name']} - Select Season", 
#                 lambda x: f"Season {x['season_number']} ({x['episode_count']} episodes)"
#             )
            
#             if season_sel['action'] in ['back', 'quit', None]:
#                 break
                
#             current_season = season_sel['value']
            
#             # Generate episodes list (simulated based on count)
#             # Fetching episode names would require another API call per season.
#             # Let's do that for better UX
#             ep_count = current_season['episode_count']
            
#             # Fetch Season Details for names
#             s_num = current_season['season_number']
#             s_url = f"https://api.themoviedb.org/3/tv/{media['id']}/season/{s_num}?api_key={TMDB_API_KEY}"
#             s_resp = self.session.get(s_url)
#             episodes = []
#             if s_resp.status_code == 200:
#                 episodes = s_resp.json().get('episodes', [])
#             else:
#                  # Fallback
#                  episodes = [{'episode_number': i+1, 'name': f'Episode {i+1}'} for i in range(ep_count)]

#             while True:
#                 ep_sel = self.selection_menu(
#                     episodes,
#                     f"{media['name']} - S{s_num} - Select Episode",
#                     lambda x: f"E{x['episode_number']} - {x['name']}",
#                     can_jump=True
#                 )
                
#                 if ep_sel['action'] == 'back':
#                     break
#                 if ep_sel['action'] == 'quit':
#                     return
                
#                 if ep_sel['action'] == 'jump':
#                     # Ask for number
#                     # We need to temporarily close full screen app? 
#                     # Actually app.run() finished.
#                     try:
#                         ep_num = int(console.input("[bold yellow]Go to Episode Number: [/bold yellow]"))
#                         # Find episode
#                         found = next((e for e in episodes if e['episode_number'] == ep_num), None)
#                         if found:
#                             self.play_tv_episode(media, s_num, found)
#                         else:
#                             console.print("[red]Episode not found.[/red]")
#                             input("Enter to continue...")
#                     except ValueError:
#                         pass
#                     continue
                
#                 if ep_sel['action'] == 'select':
#                      self.play_tv_episode(media, s_num, ep_sel['value'])

#     def play_tv_episode(self, media, season_num, episode):
#         with console.status(f"[bold yellow]Fetching sources for S{season_num}E{episode['episode_number']}...[/bold yellow]"):
#             data = self.get_sources(media['id'], 'tv', season_num, episode['episode_number'])
        
#         self.handle_sources_selection(data, f"{media['name']} S{season_num}E{episode['episode_number']}", media)

#     def handle_sources_selection(self, data, title, media_obj):
#         files = data.get("files", [])
#         subtitles = data.get("subtitles", [])
        
#         if not files:
#             console.print("[red]No streaming sources found.[/red]")
#             input("Press Enter to go back...")
#             return

#         while True:
#             # Create list for menu
#             # Provider - Type - Quality (if available)?
#             def source_fmt(x):
#                 # Now that we inject provider in api.js, we can use it.
#                 prov = x.get('provider', 'Unknown')
#                 quality = x.get('quality', 'Auto') 
#                 # Some might have 'type' like 'hls' or 'mp4'
#                 f_type = x.get('type', 'Standard')
#                 return f"{prov} | {f_type} | {quality}"

#             sel = self.selection_menu(
#                 files,
#                 f"Sources for {title}",
#                 source_fmt
#             )
            
#             if sel['action'] in ['back', 'quit', None]:
#                 break
                
#             selected_file = sel['value']
#             # Prompt Play or Download
#             # Simple choice menu
#             action_menu = [
#                 {'name': 'Play', 'id': 'play'},
#                 {'name': 'Download', 'id': 'download'}
#             ]
            
#             act = self.selection_menu(
#                action_menu,
#                "Choose Action",
#                lambda x: x['name']
#             )
            
#             if act['action'] in ['back', 'quit', None]:
#                 continue
                
#             if act['value']['id'] == 'play':
#                 self.play_video(selected_file['file'], title, subtitles)
#             elif act['value']['id'] == 'download':
#                 fname = f"{title.replace(' ', '_')}_{selected_file.get('provider', 'src')}.mp4"
#                 self.download_video(selected_file['file'], fname)

#     def download_video(self, url, filename):
#         try:
#             with console.status(f"[yellow]Starting download: {filename}[/yellow]"):
#                 response = self.session.get(url, stream=True)
#                 total_size = int(response.headers.get('content-length', 0))
            
#             with open(filename, 'wb') as f, Progress() as progress:
#                 task = progress.add_task("[cyan]Downloading...", total=total_size)
#                 for data in response.iter_content(chunk_size=4096):
#                     f.write(data)
#                     progress.update(task, advance=len(data))
#             console.print("[green]Download complete![/green]")
#             input("Press Enter to continue...")
#         except Exception as e:
#              console.print(f"[red]Download failed: {e}[/red]")
#              input("Enter to continue...")

# if __name__ == "__main__":
#     try:
#         app = CineProTUI()
#         app.input_loop()
#     except KeyboardInterrupt:
#         print("\nGoodbye!")
#         sys.exit(0)


# ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::



# import sys
# import os
# import requests
# import subprocess
# import shutil
# from dotenv import load_dotenv
# from rich.console import Console
# from rich.text import Text
# from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
# from prompt_toolkit import Application
# from prompt_toolkit.key_binding import KeyBindings
# from prompt_toolkit.layout import Layout, Window, HSplit
# from prompt_toolkit.layout.controls import FormattedTextControl
# from prompt_toolkit.styles import Style

# # Load environment variables
# load_dotenv()

# # Configuration
# BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:3000")
# TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# console = Console()

# # ASCII Art Logo
# LOGO = r"""
#    ____ _            ____           
#   / ___(_)_ __   ___|  _ \ _ __ ___ 
#  | |   | | '_ \ / _ \ |_) | '__/ _ \
#  | |___| | | | |  __/  __/| | | (_) |
#   \____|_|_| |_|\___|_|   |_|  \___/ 
#       Movie & TV CLI Player
# """

# class CineProTUI:
#     def __init__(self):
#         self.session = requests.Session()
#         if not TMDB_API_KEY:
#             console.print("[red]Error: TMDB_API_KEY not found in environment variables.[/red]")
#             sys.exit(1)

#     def clear_screen(self):
#         os.system('cls' if os.name == 'nt' else 'clear')

#     def print_header(self):
#         self.clear_screen()
#         console.print(Text(LOGO, style="bold magenta"))
#         console.print(Text("──────────────────────────────────────────────", style="dim"))

#     def search_tmdb(self, query, media_type="multi"):
#         url = "https://api.themoviedb.org/3/search/multi"
#         params = {
#             "api_key": TMDB_API_KEY,
#             "query": query,
#             "language": "en-US"
#         }
#         try:
#             response = self.session.get(url, params=params)
#             response.raise_for_status()
#             results = response.json().get("results", [])
#             return [r for r in results if r.get("media_type") in ["movie", "tv"]]
#         except Exception as e:
#             return []

#     def get_sources(self, tmdb_id, media_type, season=None, episode=None):
#         if media_type == "movie":
#             url = f"{BACKEND_URL}/movie/{tmdb_id}"
#         else:
#             url = f"{BACKEND_URL}/tv/{tmdb_id}?s={season}&e={episode}"
        
#         try:
#             response = self.session.get(url)
#             if response.status_code != 200:
#                 return {}
#             return response.json()
#         except Exception:
#             return {}

#     def play_video(self, url, title, subtitles=None, headers=None):
#         # ani-cli style playing message
#         console.print(f"\n[bold green]▶ Playing:[/bold green] {title}")
#         console.print("[dim]Space: Pause | Arrows: Seek | F: Fullscreen | Q: Quit[/dim]")

#         # Remove quiet flags to see errors, force window to see if it tries to open
#         mpv_args = ["mpv", url, f"--title={title}", "--fs", "--force-window=immediate", "--tls-verify=no"]

#         # Default User-Agent if not provided
#         default_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        
#         if headers:
#             # Handle headers carefully
#             ua = headers.get('User-Agent') or headers.get('user-agent') or default_ua
#             referrer = headers.get('Referer') or headers.get('referer')
            
#             mpv_args.append(f"--user-agent={ua}")
            
#             if referrer:
#                 mpv_args.append(f"--referrer={referrer}")
            
#             # Other headers
#             other_headers = []
#             for k, v in headers.items():
#                 k_lower = k.lower()
#                 if k_lower in ['user-agent', 'referer']:
#                     continue
#                 if ',' in v:
#                    continue
#                 other_headers.append(f"{k}: {v}")
            
#             if other_headers:
#                 header_str = ",".join(other_headers)
#                 mpv_args.append(f"--http-header-fields={header_str}")
#         else:
#             # No headers provided, use default UA
#             mpv_args.append(f"--user-agent={default_ua}")

#         if subtitles:
#             # Prioritize Arabic, then English, then others
#             arabic_subs = [s for s in subtitles if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
#             if arabic_subs:
#                 sub_url = arabic_subs[0]["url"]
#                 mpv_args.append(f"--sub-file={sub_url}")
#                 console.print(f"[cyan]ℹ Subtitles:[/cyan] Arabic loaded")
#             elif len(subtitles) > 0:
#                  sub_url = subtitles[0]["url"]
#                  mpv_args.append(f"--sub-file={sub_url}")
#                  console.print(f"[cyan]ℹ Subtitles:[/cyan] {subtitles[0].get('lang', 'Default')} loaded")

#         try:
#             if shutil.which("mpv") is None:
#                 console.print("[red]Error: 'mpv' not found. Install it to play videos.[/red]")
#                 input("Press Enter...")
#                 return

#             # Print command for debug
#             debug_cmd = " ".join([f'"{arg}"' if " " in arg else arg for arg in mpv_args])
#             console.print(f"\n[dim]Running: {debug_cmd}[/dim]\n")

#             subprocess.run(mpv_args)
#         except Exception as e:
#             console.print(f"[red]Error running mpv: {e}[/red]")
#             input("Press Enter...")

#     def selection_menu(self, items, title, formatter=lambda x: str(x), can_jump=False):
#         if not items:
#             console.print("[yellow]No items found.[/yellow]")
#             return {'value': None, 'action': 'back'}

#         bindings = KeyBindings()
#         selected_index = 0
#         result = {'value': None, 'action': None}

#         @bindings.add('up')
#         def _(event):
#             nonlocal selected_index
#             selected_index = (selected_index - 1) % len(items)

#         @bindings.add('down')
#         def _(event):
#             nonlocal selected_index
#             selected_index = (selected_index + 1) % len(items)

#         @bindings.add('enter')
#         def _(event):
#             result['value'] = items[selected_index]
#             result['action'] = 'select'
#             event.app.exit()

#         @bindings.add('q')
#         @bindings.add('escape')
#         def _(event):
#             result['action'] = 'quit'
#             event.app.exit()
            
#         @bindings.add('b')
#         def _(event):
#             result['action'] = 'back'
#             event.app.exit()

#         if can_jump:
#             @bindings.add('g')
#             def _(event):
#                  result['action'] = 'jump'
#                  event.app.exit()

#         def get_formatted_text():
#             lines = []
            
#             # Re-draw Logo/Header to keep context
#             lines.append(('class:logo', LOGO + "\n"))
#             lines.append(('class:separator', "──────────────────────────────────────────────\n"))
#             lines.append(('class:title', f" {title}\n\n"))

#             # Calculate pagination
#             max_rows = os.get_terminal_size().lines - 12 # Reserve space for header
#             if max_rows < 5: max_rows = 5
            
#             start_idx = 0
#             end_idx = len(items)
            
#             if len(items) > max_rows:
#                 start_idx = max(0, selected_index - (max_rows // 2))
#                 end_idx = min(len(items), start_idx + max_rows)
#                 # Ensure we fill the screen
#                 if end_idx - start_idx < max_rows and start_idx > 0:
#                     start_idx = max(0, end_idx - max_rows)

#             for i in range(start_idx, end_idx):
#                 item = items[i]
#                 display_text = formatter(item)
                
#                 if i == selected_index:
#                     # Ani-cli style: Cyan arrow, bold text
#                     lines.append(('class:pointer', " > "))
#                     lines.append(('class:selected', f"{display_text}\n"))
#                 else:
#                     lines.append(('', "   "))
#                     lines.append(('class:unselected', f"{display_text}\n"))
            
#             lines.append(('\n', ''))
            
#             # Footer / Shortcuts
#             shortcuts = " [Enter] Select  [B] Back  [Q] Quit"
#             if can_jump:
#                 shortcuts += "  [G] Jump to Ep"
            
#             lines.append(('class:footer', f"{shortcuts}"))

#             return lines

#         layout = Layout(HSplit([Window(content=FormattedTextControl(get_formatted_text))]))

#         # Ani-cli inspired color scheme
#         style = Style.from_dict({
#             'logo': 'bold magenta',
#             'separator': 'dim white',
#             'title': 'bold white underline',
#             'pointer': 'bold cyan',
#             'selected': 'bold cyan',
#             'unselected': 'white',
#             'footer': 'dim gray',
#         })

#         app = Application(layout=layout, key_bindings=bindings, style=style, full_screen=True)
#         app.run()
        
#         return result

#     def input_loop(self):
#         while True:
#             self.print_header()
            
#             # Simple input prompt
#             console.print("[bold cyan]?[/bold cyan] Search Query [dim](or 'q' to quit)[/dim]: ", end="")
#             query = input().strip()
            
#             if query.lower() in ['q', 'quit', 'exit']:
#                 break
#             if not query:
#                 continue
            
#             # Searching spinner
#             with Progress(
#                 SpinnerColumn(style="bold cyan"),
#                 TextColumn("[cyan]Searching TMDB...[/cyan]"),
#                 transient=True
#             ) as progress:
#                 progress.add_task("search", total=None)
#                 results = self.search_tmdb(query)
            
#             if not results:
#                 console.print("\n[red]No results found.[/red]")
#                 import time; time.sleep(1)
#                 continue

#             # --- SELECTION LOOP ---
#             while True:
#                 def result_formatter(x):
#                     date = x.get('release_date') or x.get('first_air_date') or 'N/A'
#                     year = date.split('-')[0]
#                     m_type = "TV" if x.get('media_type') == 'tv' else "MOVIE"
#                     return f"[{m_type}] {x.get('title') or x.get('name')} ({year})"

#                 selection = self.selection_menu(results, "Search Results", result_formatter)
                
#                 if selection['action'] in ['quit', 'back', None]:
#                     break # Back to search input
                
#                 media = selection['value']
                
#                 if media['media_type'] == 'tv':
#                     action = self.handle_tv_show(media)
#                     if action == 'quit': return
#                 else:
#                     action = self.handle_movie(media)
#                     if action == 'quit': return

#     def handle_movie(self, media):
#         with Progress(SpinnerColumn(style="yellow"), TextColumn("[yellow]Fetching movie sources...[/yellow]"), transient=True) as p:
#             p.add_task("fetch", total=None)
#             data = self.get_sources(media['id'], 'movie')
        
#         return self.handle_sources_selection(data, media['title'])

#     def handle_tv_show(self, media):
#         # Fetch seasons
#         with Progress(SpinnerColumn(style="magenta"), TextColumn("[magenta]Fetching TV details...[/magenta]"), transient=True) as p:
#             p.add_task("fetch", total=None)
#             resp = self.session.get(f"https://api.themoviedb.org/3/tv/{media['id']}?api_key={TMDB_API_KEY}")
        
#         if resp.status_code != 200:
#             console.print("[red]Error fetching seasons.[/red]")
#             return 'back'
        
#         seasons = [s for s in resp.json().get('seasons', []) if s['season_number'] > 0]

#         while True:
#             season_sel = self.selection_menu(
#                 seasons, 
#                 f"{media['name']} > Select Season", 
#                 lambda x: f"Season {x['season_number']} ({x['episode_count']} Eps)"
#             )
            
#             if season_sel['action'] == 'back': break
#             if season_sel['action'] == 'quit': return 'quit'
                
#             current_season = season_sel['value']
#             s_num = current_season['season_number']
            
#             # Fetch Episodes
#             with Progress(SpinnerColumn(style="magenta"), TextColumn(f"[magenta]Fetching Season {s_num}...[/magenta]"), transient=True) as p:
#                 p.add_task("fetch")
#                 s_url = f"https://api.themoviedb.org/3/tv/{media['id']}/season/{s_num}?api_key={TMDB_API_KEY}"
#                 s_resp = self.session.get(s_url)
            
#             episodes = []
#             if s_resp.status_code == 200:
#                 episodes = s_resp.json().get('episodes', [])
#             else:
#                 episodes = [{'episode_number': i+1, 'name': f'Episode {i+1}'} for i in range(current_season['episode_count'])]

#             while True:
#                 ep_sel = self.selection_menu(
#                     episodes,
#                     f"{media['name']} > S{s_num} > Select Episode",
#                     lambda x: f"Ep {x['episode_number']} - {x['name']}",
#                     can_jump=True
#                 )
                
#                 if ep_sel['action'] == 'back': break
#                 if ep_sel['action'] == 'quit': return 'quit'
                
#                 if ep_sel['action'] == 'jump':
#                     # Interactive Jump Prompt (clears menu temporarily)
#                     self.print_header()
#                     console.print(f"[bold cyan]Jump to Episode in Season {s_num}[/bold cyan]")
#                     try:
#                         ep_input = console.input("[bold yellow]Enter Episode Number: [/bold yellow]")
#                         ep_num = int(ep_input)
#                         found_ep = next((e for e in episodes if e['episode_number'] == ep_num), None)
#                         if found_ep:
#                             self.play_tv_episode(media, s_num, found_ep)
#                         else:
#                             console.print("[red]Episode not found![/red]")
#                             import time; time.sleep(1)
#                     except ValueError:
#                         console.print("[red]Invalid number.[/red]")
#                         import time; time.sleep(1)
#                     continue

#                 if ep_sel['action'] == 'select':
#                      self.play_tv_episode(media, s_num, ep_sel['value'])

#     def play_tv_episode(self, media, s_num, episode):
#         title_disp = f"{media['name']} S{s_num}E{episode['episode_number']}"
#         with Progress(SpinnerColumn(style="yellow"), TextColumn(f"[yellow]Fetching sources for {title_disp}...[/yellow]"), transient=True) as p:
#             p.add_task("fetch")
#             data = self.get_sources(media['id'], 'tv', s_num, episode['episode_number'])
        
#         self.handle_sources_selection(data, title_disp)

#     def handle_sources_selection(self, data, title):
#         files = data.get("files", [])
#         subtitles = data.get("subtitles", [])
        
#         if not files:
#             console.print("[red]No streams found.[/red]")
#             input("Press Enter...")
#             return 'back'

#         while True:
#             # Simple Source Menu
#             def src_fmt(x):
#                 # Make it look techy: Provider [Quality]
#                 q = x.get('quality', 'unk').upper()
#                 p = x.get('provider', 'SRC').upper()
#                 return f"{p:<10} [{q}]"

#             sel = self.selection_menu(files, f"Select Source for {title}", src_fmt)
            
#             if sel['action'] in ['back', 'quit', None]:
#                 return sel['action'] # Propagate back or quit

#             selected_file = sel['value']

#             # Action Menu: Play or Download
#             action_menu = ['Play', 'Download']
#             if not action_menu:
#                  return 'back'

#             def act_fmt(x): return f"▶ {x}" if x == 'Play' else f"⬇ {x}"
            
#             act_sel = self.selection_menu(action_menu, f"{title} - Choose Action", act_fmt)
            
#             if act_sel['action'] in ['back', 'quit', None]:
#                  continue # Back to source selection

#             if act_sel['value'] == 'Play':
#                 self.play_video(selected_file['file'], title, subtitles, selected_file.get('headers'))
#             elif act_sel['value'] == 'Download':
#                 # Sanitize filename
#                 safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ' or c=='_']).rstrip().replace(' ', '_')
#                 ext = 'mp4'
#                 if '.m3u8' in selected_file['file']:
#                      ext = 'ts' # naive, but usually m3u8 downloads as stream dump. Actually requests download won't work well for m3u8.
#                      # But for now, let's just download what url points to.
#                      # If it's m3u8, user might just get the playlist text unless we use ffmpeg.
#                      # Let's warn if m3u8?
#                      pass
                
#                 filename = f"{safe_title}_{selected_file.get('provider', 'src')}.{ext}"
#                 self.download_video(selected_file['file'], filename, selected_file.get('headers'))

#             # After action, stay in loop? or go back?
#             # Usually stay in loop to try other sources if failed.
#             pass

#     def download_video(self, url, filename, headers=None):
#         # Check if it's an HLS stream
#         if '.m3u8' in url or '.m3u' in url:
#             console.print(f"\n[bold yellow]⚠ Stream is HLS (m3u8). Using MPV to record...[/bold yellow]")
#             console.print(f"[dim]Output: {filename}[/dim]")
            
#             # Construct MPV record command
#             # --vo=null --ao=null : disable playback output (headless-like)
#             # --stream-record process
#             mpv_cmd = [
#                 "mpv", url,
#                 f"--stream-record={filename}",
#                 "--vo=null", "--ao=null",
#                 "--msg-level=all=status" # Show progress
#             ]

#             if headers:
#                  # Reconstruct headers for MPV
#                  # Reuse logic from play_video roughly
#                  ua = headers.get('User-Agent') or headers.get('user-agent')
#                  if ua: mpv_cmd.append(f"--user-agent={ua}")
#                  ref = headers.get('Referer') or headers.get('referer')
#                  if ref: mpv_cmd.append(f"--referrer={ref}")
#                  # Other headers... simplified for now
            
#             try:
#                 subprocess.run(mpv_cmd)
#                 console.print(f"\n[bold green]✔ Recording Complete:[/bold green] {filename}")
#             except Exception as e:
#                 console.print(f"[red]MPV Transfer failed:[/red] {e}")
            
#             input("Press Enter to continue...")
#             return

#         # Fallback to standard HTTP download for .mp4 etc
#         try:
#             req_headers = {}
#             if headers:
#                 for k, v in headers.items():
#                     if ',' not in v:
#                         req_headers[k] = v
            
#             if 'User-Agent' not in req_headers:
#                  req_headers['User-Agent'] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

#             console.print(f"\n[yellow]Initialize HTTP download for {filename}...[/yellow]")
            
#             with self.session.get(url, headers=req_headers, stream=True) as r:
#                 r.raise_for_status()
#                 total_length = int(r.headers.get('content-length', 0))
                
#                 with open(filename, 'wb') as f, Progress(
#                     SpinnerColumn(),
#                     TextColumn("[progress.description]{task.description}"),
#                     BarColumn(),
#                     TextColumn("{task.percentage:>3.0f}%"),
#                     TextColumn("{task.completed}/{task.total}"),
#                     transient=True
#                 ) as progress:
#                     task = progress.add_task(f"[cyan]Downloading {filename}...", total=total_length if total_length > 0 else None)
#                     for chunk in r.iter_content(chunk_size=8192):
#                         f.write(chunk)
#                         progress.update(task, advance=len(chunk))
            
#             console.print(f"[bold green]✔ Download Complete:[/bold green] {filename}")
#             input("Press Enter to continue...")

#         except Exception as e:
#             console.print(f"[red]Download Failed:[/red] {e}")
#             input("Press Enter...") 

# if __name__ == "__main__":
#     try:
#         app = CineProTUI()
#         app.input_loop()
#     except KeyboardInterrupt:
#         sys.exit(0)



import sys
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import subprocess
import shutil
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

# Load environment variables
load_dotenv()

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:3000")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

console = Console()

def create_session_with_retries():
    """Create a requests session with retry logic and proper timeout"""
    session = requests.Session()
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=3,  # Total number of retries
        backoff_factor=1,  # Wait 1, 2, 4 seconds between retries
        status_forcelist=[429, 500, 502, 503, 504],  # HTTP status codes to retry
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]  # Methods to retry
    )
    
    # Mount retry adapter
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=10,
        pool_block=False
    )
    
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Set default headers
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Connection': 'keep-alive'
    })
    
    return session

class CineProTUI:
    def __init__(self):
        self.session = create_session_with_retries()
        self.timeout = (10, 30)  # (connect timeout, read timeout)
        
        if not TMDB_API_KEY:
            console.print("[red]Error: TMDB_API_KEY not found in environment variables.[/red]")
            sys.exit(1)

    def search_tmdb(self, query, media_type="multi"):
        url = "https://api.themoviedb.org/3/search/multi"
        params = {
            "api_key": TMDB_API_KEY,
            "query": query,
            "language": "en-US"
        }
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            results = response.json().get("results", [])
            # Filter only movie and tv
            return [r for r in results if r.get("media_type") in ["movie", "tv"]]
        except requests.exceptions.Timeout:
            console.print("[red]Request timed out. Please check your internet connection.[/red]")
            return []
        except requests.exceptions.ConnectionError:
            console.print("[red]Connection error. Please check your internet connection.[/red]")
            return []
        except Exception as e:
            console.print(f"[red]Error searching TMDB: {e}[/red]")
            return []

    def get_sources(self, tmdb_id, media_type, season=None, episode=None):
        if media_type == "movie":
            url = f"{BACKEND_URL}/movie/{tmdb_id}"
        else:
            url = f"{BACKEND_URL}/tv/{tmdb_id}?s={season}&e={episode}"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            # Backend might return error object
            if response.status_code != 200:
                return {}
            return response.json()
        except requests.exceptions.Timeout:
            console.print("[red]Request timed out while fetching sources.[/red]")
            return {}
        except requests.exceptions.ConnectionError:
            console.print("[red]Connection error while fetching sources.[/red]")
            return {}
        except Exception as e:
            console.print(f"[yellow]Could not fetch sources: {e}[/yellow]")
            return {}

    def play_video(self, url, title, subtitles=None, headers=None):
        mpv_args = ["mpv", url, f"--title={title}", "--fs"] # --fs for fullscreen
        
        # Add shortcuts help to title or print it before
        console.print(Panel("[green]Starting Player...[/green]\n[yellow]Space: Pause | Arrows: Seek | F: Fullscreen | Q: Quit[/yellow]", title="MPV Player"))

        # Add headers if provided
        if headers:
            ua = headers.get('User-Agent') or headers.get('user-agent')
            if ua:
                mpv_args.append(f"--user-agent={ua}")
            ref = headers.get('Referer') or headers.get('referer')
            if ref:
                mpv_args.append(f"--referrer={ref}")

        if subtitles:
            # Check for Arabic or user preferred subs
            # Prioritize Arabic
            arabic_subs = [s for s in subtitles if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
            if arabic_subs:
                sub_url = arabic_subs[0]["url"]
                mpv_args.append(f"--sub-file={sub_url}")
                console.print(f"[cyan]Subtitle loaded: Arabic[/cyan]")
            else:
                # Load first available or let mpv handle? 
                # Mpv might need sub-file argument for external subs
                if len(subtitles) > 0:
                     sub_url = subtitles[0]["url"]
                     mpv_args.append(f"--sub-file={sub_url}")
                     console.print(f"[cyan]Subtitle loaded: {subtitles[0].get('lang', 'Unknown')}[/cyan]")

        try:
            # Check if mpv is available
            if shutil.which("mpv") is None:
                console.print("[red]Error: 'mpv' executable not found in PATH. Please install mpv.[/red]")
                input("Press Enter to continue...")
                return

            subprocess.run(mpv_args)
        except Exception as e:
            console.print(f"[red]Error running mpv: {e}[/red]")
            input("Press Enter to continue...")

    def selection_menu(self, items, title, formatter=lambda x: str(x), can_jump=False):
        """
        Interactive menu using prompt_toolkit.
        items: list of objects
        formatter: function to convert object to display text
        Returns: selected item or None (if back/quit)
        """
        if not items:
            console.print("[yellow]No items to display.[/yellow]")
            return None

        bindings = KeyBindings()
        selected_index = 0
        
        # We need a container to update the UI
        # But prompt_toolkit Application takes over the screen.
        # We want to mimic a loop.
        
        result = {'value': None, 'action': None}

        @bindings.add('up')
        def _(event):
            nonlocal selected_index
            selected_index = (selected_index - 1) % len(items)

        @bindings.add('down')
        def _(event):
            nonlocal selected_index
            selected_index = (selected_index + 1) % len(items)

        @bindings.add('enter')
        def _(event):
            result['value'] = items[selected_index]
            result['action'] = 'select'
            event.app.exit()

        @bindings.add('q')
        @bindings.add('escape')
        def _(event):
            result['action'] = 'quit'
            event.app.exit()
            
        @bindings.add('b')
        def _(event):
            result['action'] = 'back'
            event.app.exit()

        if can_jump:
            @bindings.add('g')
            def _(event):
                 # We can't easily capture input inside this binding without complex logic.
                 # We'll set a flag and handle it outside or return a special action
                 result['action'] = 'jump'
                 event.app.exit()

        def get_formatted_text():
            lines = []
            # Header
            lines.append(('class:header', f" {title} \n"))
            lines.append(('class:header', f" {'=' * len(title)} \n\n"))
            
            # Helper text
            helper = "↑/↓: Navigate | Enter: Select | B: Back | Q: Quit"
            if can_jump:
                helper += " | G: Jump to Episode"
            lines.append(('class:helper', f"{helper}\n\n"))

            # Calculate visible window to avoid scrolling issues if list is huge
            # For simplicity, we render all, prompt_toolkit handles scrolling if we use a proper layout, 
            # but simpler TextControl just dumps.
            # Let's limit display to roughly terminal height if needed, but simple list is fine.
            
            start_idx = 0
            end_idx = len(items)
            
            # Simple pagination logic
            max_rows = 20
            if len(items) > max_rows:
                start_idx = max(0, selected_index - (max_rows // 2))
                end_idx = min(len(items), start_idx + max_rows)
            
            for i in range(start_idx, end_idx):
                item = items[i]
                if i == selected_index:
                    lines.append(('class:selected', f" > {formatter(item)}\n"))
                else:
                    lines.append(('', f"   {formatter(item)}\n"))
            
            if start_idx > 0:
                lines.insert(3, ('', "   ... (scroll up)\n"))
            if end_idx < len(items):
                lines.append(('', "   ... (scroll down)\n"))
            
            return lines

        style = Style.from_dict({
            'header': 'bold cyan',
            'helper': 'italic yellow',
            'selected': 'reverse bold',
        })

        # Use FormattedTextControl which updates based on our function
        text_control = FormattedTextControl(get_formatted_text)
        window = Window(content=text_control, wrap_lines=True)
        layout = Layout(window)
        
        app = Application(layout=layout, key_bindings=bindings, full_screen=False, style=style, mouse_support=False)
        app.run()
        
        return result

    def print_header(self):
        os.system('cls' if os.name == 'nt' else 'clear')
        header = Text()
        header.append(" ██████╗██╗███╗   ██╗███████╗██████╗ ██████╗  ██████╗ \n", style="bold cyan")
        header.append("██╔════╝██║████╗  ██║██╔════╝██╔══██╗██╔══██╗██╔═══██╗\n", style="bold cyan")
        header.append("██║     ██║██╔██╗ ██║█████╗  ██████╔╝██████╔╝██║   ██║\n", style="bold cyan")
        header.append("██║     ██║██║╚██╗██║██╔══╝  ██╔═══╝ ██╔══██╗██║   ██║\n", style="bold cyan")
        header.append("╚██████╗██║██║ ╚████║███████╗██║     ██║  ██║╚██████╔╝\n", style="bold cyan")
        header.append(" ╚═════╝╚═╝╚═╝  ╚═══╝╚══════╝╚═╝     ╚═╝  ╚═╝ ╚═════╝ \n", style="bold cyan")
        header.append("\n              Your Ultimate CLI Streaming Tool\n", style="italic yellow")
        console.print(header)

    def main_menu(self):
        menu_items = ['Search Movie/TV Show', 'Quit']
        
        def fmt(x):
            if x == 'Search Movie/TV Show':
                return "🔍 Search Movie/TV Show"
            elif x == 'Quit':
                return "❌ Quit"
            return x

        sel = self.selection_menu(menu_items, "Main Menu", fmt)
        
        if sel is None or sel['action'] == 'quit' or (sel['action'] == 'select' and sel['value'] == 'Quit'):
            return 'quit'
        
        if sel['action'] == 'select' and sel['value'] == 'Search Movie/TV Show':
            return 'search'
        
        return None

    def input_loop(self):
        while True:
            self.print_header()
            action = self.main_menu()
            
            if action == 'quit':
                console.print("\n[bold cyan]Thanks for using CinePro! Goodbye! 👋[/bold cyan]\n")
                break
            
            if action == 'search':
                self.print_header()
                console.print("[bold cyan]Enter Search Query[/bold cyan]")
                query = console.input("[bold yellow]Search: [/bold yellow]")
                
                if not query.strip():
                    continue
                
                with Progress(SpinnerColumn(style="magenta"), TextColumn("[magenta]Searching TMDB...[/magenta]"), transient=True) as p:
                    p.add_task("search")
                    results = self.search_tmdb(query)
                
                if not results:
                    console.print("[red]No results found or error occurred.[/red]")
                    input("Press Enter to continue...")
                    continue
                
                # Show results
                while True:
                    def res_fmt(x):
                        t = x.get('title') or x.get('name')
                        y = x.get('release_date', x.get('first_air_date', 'N/A'))[:4] if x.get('release_date') or x.get('first_air_date') else 'N/A'
                        mtype = 'Movie' if x['media_type'] == 'movie' else 'TV Show'
                        return f"{t} ({y}) - {mtype}"
                    
                    res_sel = self.selection_menu(results, "Search Results", res_fmt)
                    
                    if res_sel['action'] in ['back', 'quit', None]:
                        break
                    
                    if res_sel['action'] == 'select':
                        media = res_sel['value']
                        
                        if media['media_type'] == 'movie':
                            action = self.handle_movie(media)
                        else:
                            action = self.handle_tv_show(media)
                        
                        if action == 'quit':
                            console.print("\n[bold cyan]Thanks for using CinePro! Goodbye! 👋[/bold cyan]\n")
                            return

    def handle_movie(self, media):
        title = media.get('title', 'Unknown')
        with Progress(SpinnerColumn(style="yellow"), TextColumn(f"[yellow]Fetching sources for {title}...[/yellow]"), transient=True) as p:
            p.add_task("fetch")
            data = self.get_sources(media['id'], 'movie')
        
        return self.handle_sources_selection(data, title)

    def handle_tv_show(self, media):
        # Fetch Seasons
        with Progress(SpinnerColumn(style="magenta"), TextColumn(f"[magenta]Fetching Seasons for {media['name']}...[/magenta]"), transient=True) as p:
            p.add_task("fetch")
            s_url = f"https://api.themoviedb.org/3/tv/{media['id']}?api_key={TMDB_API_KEY}"
            
            try:
                s_resp = self.session.get(s_url, timeout=self.timeout)
                s_resp.raise_for_status()
            except requests.exceptions.Timeout:
                console.print("[red]Request timed out while fetching TV show details. Please try again.[/red]")
                input("Press Enter to continue...")
                return 'back'
            except requests.exceptions.ConnectionError:
                console.print("[red]Connection error while fetching TV show details. Please check your internet connection.[/red]")
                input("Press Enter to continue...")
                return 'back'
            except Exception as e:
                console.print(f"[red]Error fetching TV show: {e}[/red]")
                input("Press Enter to continue...")
                return 'back'
        
        seasons = []
        if s_resp.status_code == 200:
            seasons = s_resp.json().get('seasons', [])
        
        if not seasons:
            console.print("[red]No seasons found.[/red]")
            input("Press Enter...")
            return 'back'
        
        while True:
            season_sel = self.selection_menu(
                seasons,
                f"{media['name']} > Select Season",
                lambda x: f"Season {x['season_number']} - {x.get('name', '')} ({x.get('episode_count', '?')} episodes)"
            )
            
            if season_sel['action'] == 'back': return 'back'
            if season_sel['action'] == 'quit': return 'quit'
            if season_sel['action'] != 'select': continue
            
            current_season = season_sel['value']
            s_num = current_season['season_number']
            
            # Fetch Episodes
            with Progress(SpinnerColumn(style="magenta"), TextColumn(f"[magenta]Fetching Season {s_num}...[/magenta]"), transient=True) as p:
                p.add_task("fetch")
                s_url = f"https://api.themoviedb.org/3/tv/{media['id']}/season/{s_num}?api_key={TMDB_API_KEY}"
                
                try:
                    s_resp = self.session.get(s_url, timeout=self.timeout)
                    s_resp.raise_for_status()
                except requests.exceptions.Timeout:
                    console.print("[red]Request timed out while fetching episodes. Please try again.[/red]")
                    input("Press Enter to continue...")
                    continue
                except requests.exceptions.ConnectionError:
                    console.print("[red]Connection error while fetching episodes. Please check your internet connection.[/red]")
                    input("Press Enter to continue...")
                    continue
                except Exception as e:
                    console.print(f"[red]Error fetching episodes: {e}[/red]")
                    input("Press Enter to continue...")
                    continue
            
            episodes = []
            if s_resp.status_code == 200:
                episodes = s_resp.json().get('episodes', [])
            else:
                episodes = [{'episode_number': i+1, 'name': f'Episode {i+1}'} for i in range(current_season['episode_count'])]

            while True:
                ep_sel = self.selection_menu(
                    episodes,
                    f"{media['name']} > S{s_num} > Select Episode",
                    lambda x: f"Ep {x['episode_number']} - {x['name']}",
                    can_jump=True
                )
                
                if ep_sel['action'] == 'back': break
                if ep_sel['action'] == 'quit': return 'quit'
                
                if ep_sel['action'] == 'jump':
                    # Interactive Jump Prompt (clears menu temporarily)
                    self.print_header()
                    console.print(f"[bold cyan]Jump to Episode in Season {s_num}[/bold cyan]")
                    try:
                        ep_input = console.input("[bold yellow]Enter Episode Number: [/bold yellow]")
                        ep_num = int(ep_input)
                        found_ep = next((e for e in episodes if e['episode_number'] == ep_num), None)
                        if found_ep:
                            self.play_tv_episode(media, s_num, found_ep)
                        else:
                            console.print("[red]Episode not found![/red]")
                            import time; time.sleep(1)
                    except ValueError:
                        console.print("[red]Invalid number.[/red]")
                        import time; time.sleep(1)
                    continue

                if ep_sel['action'] == 'select':
                     self.play_tv_episode(media, s_num, ep_sel['value'])

    def play_tv_episode(self, media, s_num, episode):
        title_disp = f"{media['name']} S{s_num}E{episode['episode_number']}"
        with Progress(SpinnerColumn(style="yellow"), TextColumn(f"[yellow]Fetching sources for {title_disp}...[/yellow]"), transient=True) as p:
            p.add_task("fetch")
            data = self.get_sources(media['id'], 'tv', s_num, episode['episode_number'])
        
        self.handle_sources_selection(data, title_disp)

    def handle_sources_selection(self, data, title):
        files = data.get("files", [])
        subtitles = data.get("subtitles", [])
        
        if not files:
            console.print("[red]No streams found.[/red]")
            input("Press Enter...")
            return 'back'

        while True:
            # Simple Source Menu
            def src_fmt(x):
                # Make it look techy: Provider [Quality]
                q = x.get('quality', 'unk').upper()
                p = x.get('provider', 'SRC').upper()
                return f"{p:<10} [{q}]"

            sel = self.selection_menu(files, f"Select Source for {title}", src_fmt)
            
            if sel['action'] in ['back', 'quit', None]:
                return sel['action'] # Propagate back or quit

            selected_file = sel['value']

            # Action Menu Loop
            while True:
                action_menu = ['Play', 'Download', 'Show Source']

                def act_fmt(x): 
                    if x == 'Play': return f"▶ {x}"
                    if x == 'Download': return f"⬇ {x}"
                    if x == 'Show Source': return f"ℹ {x}"
                    return x
                
                act_sel = self.selection_menu(action_menu, f"{title} - Choose Action", act_fmt)
                
                if act_sel['action'] in ['back', 'quit', None]:
                     break # Back to source selection

                if act_sel['value'] == 'Play':
                    self.play_video(selected_file['file'], title, subtitles, selected_file.get('headers'))
                elif act_sel['value'] == 'Show Source':
                    console.print(Panel(f"[bold cyan]Source URL:[/bold cyan]\n{selected_file['file']}", title="Source Info"))
                    if selected_file.get('headers'):
                        console.print(Panel(str(selected_file.get('headers')), title="Headers"))
                    input("Press Enter to continue...")
                elif act_sel['value'] == 'Download':
                    # Sanitize filename
                    safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ' or c=='_']).rstrip().replace(' ', '_')
                    ext = 'mp4'
                    if '.m3u8' in selected_file['file']:
                         ext = 'ts' # naive, but usually m3u8 downloads as stream dump. Actually requests download won't work well for m3u8.
                         # But for now, let's just download what url points to.
                         # If it's m3u8, user might just get the playlist text unless we use ffmpeg.
                         # Let's warn if m3u8?
                         pass
                    
                    filename = f"{safe_title}_{selected_file.get('provider', 'src')}.{ext}"
                    self.download_video(selected_file['file'], filename, selected_file.get('headers'))

    def download_video(self, url, filename, headers=None):
        # Check if it's an HLS stream
        if '.m3u8' in url or '.m3u' in url:
            console.print(f"\n[bold yellow]⚠ Stream is HLS (m3u8). Using MPV to record...[/bold yellow]")
            console.print(f"[dim]Output: {filename}[/dim]")
            
            # Construct MPV record command
            # --vo=null --ao=null : disable playback output (headless-like)
            # --stream-record process
            mpv_cmd = [
                "mpv", url,
                f"--stream-record={filename}",
                "--vo=null", "--ao=null",
                "--msg-level=all=status" # Show progress
            ]

            if headers:
                 # Reconstruct headers for MPV
                 # Reuse logic from play_video roughly
                 ua = headers.get('User-Agent') or headers.get('user-agent')
                 if ua: mpv_cmd.append(f"--user-agent={ua}")
                 ref = headers.get('Referer') or headers.get('referer')
                 if ref: mpv_cmd.append(f"--referrer={ref}")
                 # Other headers... simplified for now
            
            try:
                subprocess.run(mpv_cmd)
                console.print(f"\n[bold green]✔ Recording Complete:[/bold green] {filename}")
            except Exception as e:
                console.print(f"[red]MPV Transfer failed:[/red] {e}")
            
            input("Press Enter to continue...")
            return

        # Fallback to standard HTTP download for .mp4 etc
        try:
            req_headers = {}
            if headers:
                for k, v in headers.items():
                    if ',' not in v:
                        req_headers[k] = v
            
            if 'User-Agent' not in req_headers:
                 req_headers['User-Agent'] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

            console.print(f"\n[yellow]Initialize HTTP download for {filename}...[/yellow]")
            
            with self.session.get(url, headers=req_headers, stream=True, timeout=(10, 300)) as r:
                r.raise_for_status()
                total_length = int(r.headers.get('content-length', 0))
                
                with open(filename, 'wb') as f, Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("{task.percentage:>3.0f}%"),
                    TextColumn("{task.completed}/{task.total}"),
                    transient=True
                ) as progress:
                    task = progress.add_task(f"[cyan]Downloading {filename}...", total=total_length if total_length > 0 else None)
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))
            
            console.print(f"[bold green]✔ Download Complete:[/bold green] {filename}")
            input("Press Enter to continue...")

        except requests.exceptions.Timeout:
            console.print("[red]Download timed out. Please try again.[/red]")
            input("Press Enter...")
        except requests.exceptions.ConnectionError:
            console.print("[red]Connection error during download. Please check your internet connection.[/red]")
            input("Press Enter...")
        except Exception as e:
            console.print(f"[red]Download Failed:[/red] {e}")
            input("Press Enter...") 

if __name__ == "__main__":
    try:
        app = CineProTUI()
        app.input_loop()
    except KeyboardInterrupt:
        sys.exit(0)