# Cinema CLI ðŸŽ¬

> **Stream and download movies & TV shows from your terminal.**  
> A full-featured TUI client with multi-language subtitles, background downloads, theming, and more.

---

## âš¡ One-command install

**Windows**
```bat
git clone https://github.com/cinepro-org/cinema-cli
cd cinema-cli
setup.bat
```

**Linux / macOS**
```bash
git clone https://github.com/cinepro-org/cinema-cli
cd cinema-cli
chmod +x setup.sh && ./setup.sh
```

The setup script will:
- Check Python 3.9+, Node.js, mpv, ffmpeg, yt-dlp
- Create a virtual environment and install all dependencies
- Walk you through a guided first-run wizard (API keys, download folder, theme)
- Create a `cinema` / `cinema.bat` launcher shortcut

---

## ðŸš€ First use

1. Get a **free TMDB API key** â†’ [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api)  
2. Run the setup script above (it asks for the key interactively)  
3. Launch:

```bash
./cinema          # Linux / macOS
cinema.bat        # Windows
```

Or without the shortcut:
```bash
.venv/bin/python cli/main.py        # Linux / macOS
.venv\Scripts\python cli\main.py    # Windows
```

---

## âœ¨ Features

| Category | What you get |
|---|---|
| ðŸ” **Discover** | Search, Trending, Popular, Genre browse, New Releases |
| ðŸŽ¬ **Stream** | Instant playback via `mpv` with quality selection (4K â†’ 360p) |
| ðŸ“¥ **Download** | Background downloads, resume interrupted files, batch movie downloads |
| ðŸ—£ï¸ **Subtitles** | Multi-language tracks embedded in downloads; OpenSubtitles fallback |
| ðŸ“Œ **Watch Later** | Bookmark anything, review your list, bulk-remove watched items |
| â­ **Favorites** | Persistent favorites with quick-access from the main menu |
| ðŸ“ **Local Library** | Scan existing folders, resume playback, re-download |
| ðŸŽ¨ **Themes** | 9 built-in themes: Cinema, Blue, Purple, Green, Gold, Teal, Rose, Sunset, Mint |
| ðŸ’¾ **Settings backup** | Export / import your entire config as a single JSON file |
| ðŸ”” **Notifications** | Desktop notification when a download finishes |
| ðŸ©º **Health check** | Startup status bar shows which tools are present/missing |

---

## ðŸ“‹ Requirements

| Tool | Required? | Purpose |
|---|---|---|
| Python 3.9+ | âœ… Required | Runs the TUI |
| Node.js 18+ | âœ… Required | Runs the backend scraper |
| mpv | âœ… Required | Video playback |
| ffmpeg | âœ… Required | Subtitle embedding, audio/video muxing |
| yt-dlp | âœ… Required | Stream downloads (auto-installed via pip) |
| aria2c | Optional | Faster direct-file downloads |

### Install missing tools

| Tool | Windows | macOS | Linux |
|---|---|---|---|
| mpv | `winget install mpv` | `brew install mpv` | `sudo apt install mpv` |
| ffmpeg | `winget install ffmpeg` | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| aria2c | `winget install aria2` | `brew install aria2` | `sudo apt install aria2` |
| Node.js | [nodejs.org](https://nodejs.org/) | `brew install node` | `sudo apt install nodejs` |

---

## âŒ¨ï¸ Keyboard shortcuts

| Key | Action |
|---|---|
| `â†‘` `â†“` or `j` `k` | Navigate lists |
| `Enter` | Select / play / confirm |
| `F` | Toggle favourite |
| `W` | Toggle Watch Later |
| `D` | Batch download (all movies in current list) |
| `B` / `Esc` | Go back |
| `Q` | Quit |

Inside `mpv`, use its standard controls (`space` = pause, `s` = cycle subtitles, etc.).

---

## â“ FAQ

**Why is a backend needed?**  
The backend is a small Node.js proxy that fetches streaming links from public sources. Cinema CLI talks to it over HTTP on `localhost:3000`. The backend starts automatically when you launch the CLI â€” you don't need to manage it manually.

**What is mpv?**  
`mpv` is a free, open-source media player. Cinema CLI pipes stream URLs directly to mpv for zero-buffering playback. It handles all subtitle track loading and quality switching.

**Will it work without a TMDB key?**  
No. TMDB provides all metadata (titles, posters, ratings, episode lists). The key is free â€” sign up at [themoviedb.org](https://www.themoviedb.org/settings/api) in under a minute.

**Does it work offline?**  
Partially. Search, Browse, Trending, and streaming require internet. Your Watch History, Watch Later list, Local Library, Download Manager, and Settings all work offline.

**How do I update Cinema CLI?**  
```bash
git pull
setup.bat   # or ./setup.sh
```
The setup script is safe to re-run â€” it only updates packages and skips steps that are already done.

**Where are my downloads saved?**  
The first-run wizard asks you. Default is `~/Downloads/CinemaCLI`. Change it any time in âš™ï¸ Settings â†’ Library Directory.

**How do I back up my settings?**  
âš™ï¸ Settings â†’ **12. Export Settings** â†’ saves a JSON with your theme, API keys, subtitle preferences, and provider health scores. Use **13. Import Settings** to restore on a new machine.

**Can I choose download quality?**  
Yes. When you download a movie or episode, Cinema CLI shows all available qualities (1080p, 720p, 480p, â€¦). Set a default in âš™ï¸ Settings.

---

## ðŸ—‚ï¸ Project structure

```
cinema-cli/
â”œâ”€â”€ backend/          Node.js scraper â€” finds stream URLs from providers
â”‚   â””â”€â”€ src/
â”‚       â”œâ”€â”€ api.js
â”‚       â””â”€â”€ controllers/providers/   One folder per provider
â”œâ”€â”€ cli/              Python TUI â€” the app you actually use
â”‚   â”œâ”€â”€ main.py       Entry point
â”‚   â””â”€â”€ src/
â”‚       â”œâ”€â”€ config.py         Themes, paths, API keys
â”‚       â”œâ”€â”€ ui/ui.py          Menus, selection widget
â”‚       â””â”€â”€ utils/
â”‚           â”œâ”€â”€ api.py            TMDB + backend client
â”‚           â”œâ”€â”€ download_manager.py  Background download engine
â”‚           â”œâ”€â”€ player.py            mpv integration
â”‚           â”œâ”€â”€ subtitles.py         OpenSubtitles fallback
â”‚           â”œâ”€â”€ validator.py         Provider health scoring
â”‚           â””â”€â”€ first_run.py         Setup wizard
â”œâ”€â”€ setup.bat         Windows installer
â”œâ”€â”€ setup.sh          Linux/macOS installer
â””â”€â”€ .env              API keys (create from .env_example)
```

---

## ðŸ› ï¸ CLI flags

```
python main.py --version    Print version and exit
python main.py --help       Show help and keyboard shortcuts
python main.py --setup      Re-run the first-run setup wizard
```

---

## ðŸ› Troubleshooting

| Problem | Fix |
|---|---|
| `mpv not found` | Install mpv and ensure it's on your PATH. Run `--setup` to re-check. |
| `No streams found` | The backend may be starting up â€” wait 5 s and retry. Check `backend/backend.log`. |
| Backend won't start | Ensure `TMDB_API_KEY` is in `.env`. Run `node backend/index.js` manually to see errors. |
| Subtitles missing | Add an `OPENSUBTITLES_API_KEY` in Settings â†’ item 2 or directly in `.env`. |
| Download stuck at 0% | Check your internet connection. Resume is automatic â€” just retry the download. |
| Wrong theme colours | Theme applies on next launch. Run `python main.py` again after changing. |

Still stuck? Open an issue and paste the last lines from `~/.cinema-cli/app.log`.

---

## ðŸ¤ Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Quick summary:
- Keep PRs small and focused
- Run `black .` (Python) before committing
- All new features should have at least one test in `cli/test_features.py`

---

## ðŸ“„ License

See [LICENSE](LICENSE) or the repository root for details.
