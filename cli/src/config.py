import os
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

# Load environment variables from working directory first
load_dotenv()

# If important keys are missing (e.g. when running from `cli/`),
# also try the project root `.env` (two levels up from this file).
if not os.getenv("TMDB_API_KEY") or not os.getenv("BACKEND_URL"):
    root_env = str(Path(__file__).resolve().parents[2] / ".env")
    load_dotenv(root_env)

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:3000")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
OPENSUBTITLES_API_KEY = os.getenv("OPENSUBTITLES_API_KEY", "")

# Modern Color Scheme
PRIMARY = "#FF4B2B"  # Vibrant Red-Orange
SECONDARY = "#FF416C"  # Pinkish Red
ACCENT = "#00D2FF"  # Bright Blue
SUCCESS = "#00FF87"  # Neon Green
WARNING = "#FDC830"  # Golden Yellow
BG = "#121212"  # Dark Background
TEXT = "#E0E0E0"  # Off-white text

# Storage Files
HISTORY_FILE = os.path.expanduser("~/.cinema-cli-history.json")
FAVORITES_FILE = os.path.expanduser("~/.cinema-cli-favorites.json")
PLAYBACK_FILE = os.path.expanduser("~/.cinema-cli-playback.json")
SETTINGS_FILE = os.path.expanduser("~/.cinema-cli-settings.json")

console = Console()
