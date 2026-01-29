import os
from rich.console import Console
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:3000")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

# Modern Color Scheme
PRIMARY = "#FF4B2B"    # Vibrant Red-Orange
SECONDARY = "#FF416C"  # Pinkish Red
ACCENT = "#00D2FF"     # Bright Blue
SUCCESS = "#00FF87"    # Neon Green
WARNING = "#FDC830"    # Golden Yellow
BG = "#121212"         # Dark Background
TEXT = "#E0E0E0"       # Off-white text

# Storage Files
HISTORY_FILE = os.path.expanduser("~/.cinema-cli-history.json")
FAVORITES_FILE = os.path.expanduser("~/.cinema-cli-favorites.json")
SETTINGS_FILE = os.path.expanduser("~/.cinema-cli-settings.json")

console = Console()
