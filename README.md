# CinePro CLI 🎬

A movie and TV CLI application integrated with the CinePro backend, supporting local streaming and downloading with Arabic subtitles.

## Project Structure

- `backend/`: The CinePro scraping backend (Node.js).
- `cli/`: The Python-based CLI application.

## Setup

### 1. Backend Setup

```bash
cd backend
npm install
cp .env_example .env
# Edit .env and add your TMDB_API_KEY
npm start
```

### 2. CLI Setup

```bash
cd cli
pip install -r requirements.txt
# Ensure you have mpv installed for streaming
python main.py
```

## Features

- **Search**: Search for movies and TV shows using TMDB.
- **Stream**: Play content directly in `mpv`.
- **Download**: Download content locally.
- **Arabic Subtitles**: Automatically loads Arabic subtitles if available from the backend.

## Credits

- Inspired by [ani-cli-arabic](https://github.com/np4abdou1/ani-cli-arabic)
- Backend powered by [CinePro Backend](https://github.com/cinepro-org/backend)
