# CinePro CLI 🎬

A powerful, feature-rich movie and TV CLI application integrated with the CinePro backend. Experience high-quality local streaming and turbo-charged downloads, optimized for Arabic users with smart subtitle handling.

## ✨ Features

### 🎥 **Smart Streaming**
- **Seamless Playback**: Stream content directly in high quality using `mpv`.
- **Auto-Arabic Subtitles**: Automatically prioritizes and loads Arabic subtitles if available.
- **Interactive Menu**: "Finished Watching" menu lets you easily jump to the next episode, replay, or browse details.

### 🚀 **Turbo Downloads**
- **High-Speed Engine**: Powered by `yt-dlp` and `aria2c` (optional) for maximum bandwidth utilization.
- **Background Downloads**: Downloads run in the background thread—continue browsing and watching other content while you wait!
- **Notifications**: Get desktop notifications and sound alerts when your download is ready.
- **Smart Management**: Automatic cleanup of fragment files and temporary data.

### 🔍 **Rich Discovery**
- **TMDB Integration**: Search with rich metadata including release years, ratings, and posters (if your terminal supports it).
- **Favorites & History**: Keep track of what you're watching and save your favorites.
# CinePro CLI 🎬

A terminal-first movie and TV client that uses a local CinePro backend to find playable streams and subtitles. Designed for fast browsing, smooth playback with `mpv`, and reliable downloads.

This repository contains two main parts:
- `backend/` — Node.js scraper and proxy that finds media sources.
- `cli/` — Python TUI (terminal UI) client that queries the backend and plays or downloads media.

Highlights
- Stream playback via `mpv` (with optional `yt-dlp` support).
- Automatic Arabic subtitle discovery and local subtitle downloads.
- Background download manager with `aria2c` support (optional).
- Built-in proxying for HLS and segment requests to avoid CORS/auth issues.

Table of contents
- Features
- Requirements
- Quick start (dev)
- Configuration (.env)
- Usage
- Troubleshooting
- Contributing
- License

## Features

- Interactive text UI with keyboard navigation.
- TMDB integration for search and metadata.
- Plays streams directly in `mpv` with header/referrer support.
- Downloads managed in background worker threads.
- Subtitle fetching and local caching (prefers Arabic subtitles).
- Local backend auto-start from the CLI (configurable). Logs are written to `backend/backend.log`.

## Requirements

- Python 3.8+ (for the CLI)
- Node.js (for the backend)
- `mpv` (for playback)
- `ffmpeg` (for certain download tasks)
- Optional: `yt-dlp` for improved stream handling, `aria2c` for faster downloads

## Quick start (development)

1. Clone the repo and change to the project root:

```bash
git clone https://github.com/AbdelilahElgallati/cinema-cli
cd cinema-cli
cp .env_example .env
# Edit .env and add your TMDB_API_KEY and other settings
```

2. Backend (one-time setup):

```bash
cd backend
npm install
npm start
```

3. CLI (in a second terminal):

```bash
cd cli
pip install -r requirements.txt
python -m cli.main
```

4. Single-terminal convenience (CLI will auto-start backend):

```powershell
$env:AUTO_START_BACKEND_SHOW_LOGS = '1'  # optional: stream backend logs into the CLI
python -m cli.main
```

## Configuration (.env)

Create a `.env` at project root or update `backend/.env`/`cli/.env` (the project now prefers the root `.env`). Key variables:

- `TMDB_API_KEY` — required for TMDB lookups.
- `PORT` — backend port (default 3000).
- `BACKEND_URL` — base URL for the backend (e.g. `http://localhost:3000`).
- `OPENSUBTITLES_API_KEY` — optional subtitle provider key.
- `DISABLE_CACHE` — set to `true` to disable server-side cache.

Note: `API_URL` was deprecated — use `BACKEND_URL` only.

## Usage

- Search for movies/TV in the CLI using the prompt.
- Navigate results with arrow keys and press Enter to open details.
- From a media entry you can: play in `mpv`, download, copy URL, or save to favorites.

Player behavior

- If `yt-dlp` is installed, the CLI prefers `yt-dlp` for complex stream handling.
- Headers like `Referer` and `User-Agent` are propagated to `mpv`/`yt-dlp` when possible.

Downloads

- Downloads run in background threads; check `cli/downloads/` and `cli/downloads.json` (or the configured storage) for status.

## Troubleshooting

- Backend fails to start: ensure `TMDB_API_KEY` and `PORT` are set in `.env`. The CLI auto-start writes logs to `backend/backend.log` — inspect the last lines when problems occur.
- "No streams found": enable backend logs (`$env:AUTO_START_BACKEND_SHOW_LOGS = '1'`) and retry; the CLI will surface the last backend log lines when errors happen.
- `mpv` not found: install `mpv` and ensure it's on your PATH. Without `mpv` you cannot play streams.
- If `npm start` fails, run the backend manually in `backend/` to see full `npm` errors.

Commands to check backend health (PowerShell):

```powershell
Invoke-RestMethod http://localhost:3000/
Invoke-RestMethod http://localhost:3000/proxy/status
```

## Contributing

See `CONTRIBUTING.md` for guidelines. Basic suggestions:

- Run formatters before committing: `black .` (Python), `npx prettier --write` (JS).
- Add tests and keep PRs small.

## Development tips and CI

- Add a GitHub Action to run `black`, `isort`, `flake8`, and `npm test` on PRs.
- Consider adding `pre-commit` hooks to run formatters automatically.

## License

This project includes code from multiple authors. Check the `LICENSE` or repository root for license details.

---

If there's a specific part of the README you'd like expanded (examples, screenshots, architecture diagram), tell me and I'll add it.
