# Cinema CLI 🎬

> **Stream and download movies & TV shows from your terminal.**
> A full-featured TUI client with multi-language subtitles, background downloads, theming, and more.
> Source: 

---

## ⚡ One-command install

### Windows

```bat
git clone https://github.com/AbdelilahElgallati/cinema-cli/
cd cinema-cli
setup.bat
```

### Linux / macOS

```bash
git clone https://github.com/AbdelilahElgallati/cinema-cli/
cd cinema-cli
chmod +x setup.sh && ./setup.sh
```

The setup script will:

* Check **Python 3.9+**, **Node.js**, **mpv**, **ffmpeg**, **yt-dlp**
* Create a virtual environment and install dependencies
* Walk you through a guided first-run wizard (API keys, download folder, theme)
* Create a `cinema` / `cinema.bat` launcher shortcut

---

## 🚀 First use

1. Get a **free TMDB API key** → [https://www.themoviedb.org/settings/api](https://www.themoviedb.org/settings/api)
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

## ✨ Features

| Category               | Description                                                                     |
| ---------------------- | ------------------------------------------------------------------------------- |
| 🔍 **Discover**        | Search, Trending, Popular, Genre browsing, New releases                         |
| 🎬 **Stream**          | Instant playback via `mpv` with quality selection (4K → 360p)                   |
| 📥 **Download**        | Background downloads, resume support, batch downloads                           |
| 🗣️ **Subtitles**      | Multi-language subtitles with OpenSubtitles fallback                            |
| 📌 **Watch Later**     | Bookmark titles and manage lists                                                |
| ⭐ **Favorites**        | Persistent favorites with quick access                                          |
| 📁 **Local Library**   | Scan folders, resume playback, re-download                                      |
| 🎨 **Themes**          | 9 built-in themes (Cinema, Blue, Purple, Green, Gold, Teal, Rose, Sunset, Mint) |
| 💾 **Settings backup** | Export/import full config as JSON                                               |
| 🔔 **Notifications**   | Desktop alerts when downloads finish                                            |
| 🩺 **Health check**    | Startup status shows missing tools                                              |

---

## 📋 Requirements

| Tool        | Required | Purpose                         |
| ----------- | -------- | ------------------------------- |
| Python 3.9+ | ✅        | Runs the TUI                    |
| Node.js 18+ | ✅        | Backend scraper                 |
| mpv         | ✅        | Video playback                  |
| ffmpeg      | ✅        | Subtitle/audio/video processing |
| yt-dlp      | ✅        | Stream downloads                |
| aria2c      | Optional | Faster direct downloads         |

### Install missing tools

| Tool    | Windows                                  | macOS                 | Linux                     |
| ------- | ---------------------------------------- | --------------------- | ------------------------- |
| mpv     | `winget install mpv`                     | `brew install mpv`    | `sudo apt install mpv`    |
| ffmpeg  | `winget install ffmpeg`                  | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| aria2c  | `winget install aria2`                   | `brew install aria2`  | `sudo apt install aria2`  |
| Node.js | [https://nodejs.org](https://nodejs.org) | `brew install node`   | `sudo apt install nodejs` |

---

## ⌨️ Keyboard shortcuts

| Key            | Action                  |
| -------------- | ----------------------- |
| ↑ ↓ or `j` `k` | Navigate                |
| `Enter`        | Select / play / confirm |
| `F`            | Toggle favorite         |
| `W`            | Toggle Watch Later      |
| `D`            | Batch download          |
| `B` / `Esc`    | Go back                 |
| `Q`            | Quit                    |

Inside **mpv**, use standard controls (`space` = pause, `s` = subtitles, etc.).

---

## ❓ FAQ

**Why is a backend needed?**
A small Node.js proxy fetches streaming links from public sources. It runs automatically on launch at `localhost:3000`.

**Will it work without a TMDB key?**
No. TMDB provides metadata (titles, posters, ratings, episodes).

**Does it work offline?**
Partially. Streaming/search require internet, but local library, downloads, and settings work offline.

**How do I update?**

```bash
git pull
setup.bat   # or ./setup.sh
```

**Where are downloads saved?**
Default: `~/Downloads/CinemaCLI` (configurable in Settings).

**How do I back up settings?**
Settings → Export/Import JSON.

**Can I choose download quality?**
Yes, selectable per download or set as default.

---

## 🗂️ Project structure

```
cinema-cli/
├── backend/          Node.js scraper
├── cli/              Python TUI
├── setup.bat         Windows installer
├── setup.sh          Linux/macOS installer
└── .env              API keys
```

---

## 🛠️ CLI flags

```
python main.py --version
python main.py --help
python main.py --setup
```

---

## 🐞 Troubleshooting

| Problem             | Fix                                    |
| ------------------- | -------------------------------------- |
| `mpv not found`     | Install mpv and re-run setup           |
| `No streams found`  | Wait a few seconds; check backend logs |
| Backend won’t start | Ensure `TMDB_API_KEY` exists in `.env` |
| Missing subtitles   | Add `OPENSUBTITLES_API_KEY`            |
| Download stuck      | Retry; resume is automatic             |
| Wrong theme colors  | Restart the app                        |

---

## Contributing
Contributions are welcome! Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) to get started.

## Security
If you find a security issue, please see [`SECURITY.md`](SECURITY.md).

## License
MIT — see [`LICENSE`](LICENSE.txt).

---
