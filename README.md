<div align="center">

# 🎬 Cinema CLI

**A premium, feature-rich command-line tool for streaming and downloading movies & TV shows.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()

![Cinema CLI Screenshot](<!-- SCREENSHOT: Main Menu -->)

</div>

---

## ✨ Features

### 🔍 Search & Discovery
- **Multi-source search** — Search movies and TV shows across TMDB
- **Discovery panel** — New movies, airing episodes, trending content, movie of the day
- **Genre browser** — Browse by genre for movies and TV
- **Trending & Popular** — Weekly trends and popular picks

### 🎬 Smart Streaming & Downloading
- **Automatic source selection** — Tests all available sources and picks the first working one
- **Quality preferences** — Set preferred quality (4K, 1080p, 720p, etc.) and sources are prioritized automatically
- **Batch downloads** — Select multiple episodes and download them all with auto-source selection
- **Resume playback** — Pick up where you left off
- **MPV integration** — Full-featured playback with subtitle support
- **Robust Download Manager** — **Real-time progress updates**, auto-recovery, and detailed error logging

### 🌍 Subtitles
- **Auto-injection** — Automatically fetches and injects subtitles for streaming
- **Multi-language support** — **[NEW]** Configure your preferred subtitle language (e.g., Arabic, English, French)
- **OpenSubtitles integration** — Falls back to OpenSubtitles API when source subtitles aren't available
- **Download embedding** — Subtitles are downloaded alongside videos and optionally embedded via ffmpeg

### ⏭️ Smart Autoplay
- **Next episode countdown** — 10-second countdown after each episode
- **Auto-source finding** — Automatically finds a working source for the next episode
- **Subtitle continuity** — Subtitles auto-fetched for the next episode too

### 🎭 Cast & Crew
- **Top cast display** — See actor names and character names before watching
- **Actor search** — Search by actor name and browse their full filmography
- **TMDB integration** — Real-time data from The Movie Database

### 📁 Local Library
- **Offline browsing** — Scan your downloads folder and browse content
- **Auto-detection** — Movies, shows, seasons, and episodes detected automatically
- **Direct playback** — Play downloaded files right from the CLI
- **File metadata** — View title, season, episode, and file size

### 🎨 Themes
Switch between themes at runtime:

| Theme | Description |
|-------|-------------|
| 🎬 **Default** | Vibrant red-orange with bright blue accents |
| 🌆 **Cyberpunk** | Neon pink and electric blue on dark purple |
| ❄️ **Nord** | Cool blue-grey inspired by the Nord palette |
| ♿ **High Contrast** | White/yellow/green on black for accessibility |

### ⭐ Personalization
- **Favorites** — Save content with `F` key, access anytime
- **Watch history** — Auto-tracked, last 50 items
- **Custom filename templates** — Configure naming for downloads
- **Persistent settings** — All preferences saved between sessions
- **Custom Download Directory** — **[NEW]** Choose where your downloads are saved

### ⚡ Performance & Stability
- **API response caching** — 5-minute TTL cache for TMDB metadata
- **Connection pooling** — Reusable HTTP sessions with retry logic
- **Lazy loading** — Menus load only what's needed
- **Detailed Logging** — **[NEW]** Comprehensive logs at `~/.cinema-cli/download.log` for debugging

---

## 🆕 What's New in v2.1

- ⚙️ **Enhanced Settings** — Configurable **Subtitle Language** and **Download Directory** directly from the CLI.
- 📥 **Improved Download Manager** — **Real-time** progress percentages, speed, and ETA. No more static screens!
- 🛠️ **Robustness** — Fixed `PermissionError` on Windows by using safe user directories.
- 🐛 **Bug Fixes** — Smarter handling of `yt-dlp` warnings; failed downloads are now retried intelligently.
- 📝 **Logging** — New log file for troubleshooting difficult downloads.

---

## 📦 Installation

### Prerequisites
- **Python 3.10+**
- **Node.js 16+** (for the backend)
- **MPV** (for playback) — [Install MPV](https://mpv.io/installation/)
- **yt-dlp** (for downloads) — `pip install yt-dlp`
- Optional: **aria2c** (faster downloads), **ffmpeg** (subtitle embedding)

### Setup

```bash
# Clone the repository
git clone https://github.com/AbdelilahElgallworking/cinema-cli.git
cd cinema-cli

# Configure environment
cp .env_example .env
# Edit .env with your API keys:
#   TMDB_API_KEY=your_tmdb_key
#   OPENSUBTITLES_API_KEY=your_opensubtitles_key
#   BACKEND_URL=http://localhost:3000

# Install backend dependencies
cd backend
npm install
cd ..

# Install CLI dependencies
cd cli
pip install -r requirements.txt
```

### Running

```bash
# From the project root
cd cli
python main.py
```

> The CLI will automatically start the backend if it's pointing to localhost (default port 3000).

---

## 🎮 Usage

### Main Menu Navigation
```
  ▶ 🔍 Search Movies & TV
    🌍 Discovery
    📈 Trending This Week
    🔥 Popular Content
    🎭 Browse by Genre
    🎬 Search by Actor
    📁 Local Library
    📥 Download Manager     
    ⭐ My Favorites
    🕒 Watch History
    ⚙️ Settings
    ❌ Exit
```

### Keyboard Shortcuts
| Key | Action |
|-----|--------|
| `↑/↓` | Navigate menus |
| `Enter` | Select item |
| `F` | Toggle favorite |
| `D` | Batch download |
| `B` | Go back |
| `Q` | Quit |

### Example: Streaming a Movie
```
1. Select "🔍 Search Movies & TV"
2. Type "Inception"
3. Select the movie → Cast & crew are displayed
4. Sources are automatically tested
5. First working source starts playback
```

### Example: Batch Download
```
1. Search for a TV show
2. Navigate to a season
3. Press "D" for batch download
4. Select episodes with Space, confirm with Enter
5. Sources are auto-found for each episode
6. Downloads queue with subtitles
```

### Settings
Configure these options in the **Settings** menu:
```
1. Backend URL
2. TMDB API Key
3. Movie Filename Template    (tokens: {title}, {year}, {quality}, {provider})
4. TV Filename Template       (tokens: {title}, {season}, {episode}, ...)
5. Preferred Quality          (auto, 4K, 1080p, 720p, 480p)
6. 🎨 Theme                  (Default, Cyberpunk, Nord, High Contrast)
7. Subtitle Language          (Default: ar)  ← NEW
8. Download Directory         (Default: ~/Downloads/Cinema-CLI)  ← NEW
```

---

## 📂 Project Structure

```
cinema-cli/
├── backend/              # Node.js scraping backend
│   ├── index.js          # Main entry & port logic
│   ├── src/
│   └── package.json
├── cli/                  # Python CLI application
│   ├── main.py           # Entry point + CinemaCLI class
│   ├── requirements.txt
│   └── src/
│       ├── config.py     # Configuration & theme globals
│       ├── themes.py     # Theme definitions
│       ├── ui/
│       │   └── ui.py     # Rich/prompt_toolkit UI components
│       └── utils/
│           ├── api.py            # TMDB + backend API client (cached)
│           ├── download_manager.py  # Download manager (logging enabled)
│           ├── library.py        # Local library scanner
│           ├── player.py         # MPV player integration
│           ├── source_checker.py # Auto source validation
│           ├── storage.py        # JSON persistence
│           ├── subtitles.py      # OpenSubtitles integration
│           └── utils.py          # Filename utilities
├── .env                  # Environment variables
└── README.md
```

---

## 🔧 Configuration

### Environment Variables (`.env`)
| Variable | Description | Required |
|----------|-------------|----------|
| `TMDB_API_KEY` | The Movie Database API key | ✅ |
| `BACKEND_URL` | Backend server URL | ✅ |
| `OPENSUBTITLES_API_KEY` | OpenSubtitles API key | Optional |
| `DISABLE_CACHE` | Set to `true` to disable backend caching | Optional |


---

## 🐞 Debugging

If you encounter issues with downloads, check the log file:
- **Windows**: `C:\Users\YourUser\.cinema-cli\download.log`
- **Linux/Mac**: `~/.cinema-cli/download.log`

This file contains detailed information about download attempts, `yt-dlp` commands, and file organization steps.

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is open source. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community guidelines.

---

<div align="center">

**Built by Abdelilah Elgallati with ❤️ using Python, Rich, and prompt_toolkit**

</div>
