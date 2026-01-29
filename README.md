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

## 📂 Project Structure

- `backend/`: The CinePro scraping backend (Node.js) - *Required for content sources*.
- `cli/`: The Python-based CLI application - *Your main interface*.

## 🛠️ Setup & Installation

### 1. Prerequisites

Ensure you have the following installed on your system (add them to your PATH):

- **Python 3.8+**
- **Node.js** (for the backend)
- **MPV**: Required for streaming video.
- **FFmpeg**: Required for processing downloads.
- **Aria2c** (Optional): Highly recommended for 16x faster multi-threaded downloads.

### 2. Backend Setup

The CLI relies on the local backend to fetch links.

```bash
cd backend
npm install
cp .env_example .env
# Edit .env and add your TMDB_API_KEY
npm start
```

### 3. CLI Setup

Open a new terminal window for the CLI.

```bash
cd cli
pip install -r requirements.txt
python main.py
```

## 🎮 Usage

1.  **Search**: Type a query to find movies or TV shows.
2.  **Navigate**: Use arrow keys to browse results.
3.  **Select**: Choose a result to see details (Seasons/Episodes).
4.  **Action**:
    -   **Play**: Opens `mpv` immediately.
    -   **Download**: Starts a background download. You'll hear a sound when it's done!

## 🤝 Credits

- Backend powered by [CinePro Backend](https://github.com/cinepro-org/backend)
- Inspired by [ani-cli-arabic](https://github.com/np4abdou1/ani-cli-arabic)
