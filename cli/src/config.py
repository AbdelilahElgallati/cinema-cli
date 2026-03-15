import json
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

APP_VERSION = "2.0.0"

# ─────────────────────────────────────────────────────────────────────────────
# Theme System
# Each theme defines the full colour palette used across the TUI.
# "primary"        – main accent (borders, headers, selected highlight BG)
# "secondary"      – secondary accent (sub-headers, badges)
# "accent"         – prompt / interactive highlights
# "success"        – success messages, confirmations
# "warning"        – caution / speed-test labels
# "text"           – body text colour
# "highlight_fg"   – selected item foreground
# "dim"            – muted / help-bar text
# ─────────────────────────────────────────────────────────────────────────────
THEMES: dict[str, dict[str, str]] = {
    "cinema": {          # Default: bold cinematic red-orange
        "primary":      "#FF4B2B",
        "secondary":    "#FF416C",
        "accent":       "#00D2FF",
        "success":      "#00FF87",
        "warning":      "#FDC830",
        "text":         "#E0E0E0",
        "highlight_fg": "#FFFFFF",
        "dim":          "#888888",
    },
    "blue": {
        "primary":      "#7eb3d4",
        "secondary":    "#9ac9e3",
        "accent":       "#9ac9e3",
        "success":      "#8dbaa3",
        "warning":      "#d9c379",
        "text":         "#E0E0E0",
        "highlight_fg": "#1a2332",
        "dim":          "#888888",
    },
    "purple": {
        "primary":      "#a88dbd",
        "secondary":    "#bda3cf",
        "accent":       "#c77eb8",
        "success":      "#8dbaa3",
        "warning":      "#d9c379",
        "text":         "#E0E0E0",
        "highlight_fg": "#1f1a28",
        "dim":          "#888888",
    },
    "green": {
        "primary":      "#8ba87f",
        "secondary":    "#a3ba98",
        "accent":       "#8dbaa3",
        "success":      "#00FF87",
        "warning":      "#FDC830",
        "text":         "#E0E0E0",
        "highlight_fg": "#1a2318",
        "dim":          "#888888",
    },
    "gold": {
        "primary":      "#c9b87f",
        "secondary":    "#d9ca98",
        "accent":       "#e5d193",
        "success":      "#8ba87f",
        "warning":      "#d9a379",
        "text":         "#E0E0E0",
        "highlight_fg": "#292418",
        "dim":          "#888888",
    },
    "teal": {
        "primary":      "#6b9a9a",
        "secondary":    "#85b0b0",
        "accent":       "#9bd3d3",
        "success":      "#8dbaa3",
        "warning":      "#d9c379",
        "text":         "#E0E0E0",
        "highlight_fg": "#182424",
        "dim":          "#888888",
    },
    "rose": {
        "primary":      "#d97ea8",
        "secondary":    "#e599bd",
        "accent":       "#d9a3ba",
        "success":      "#8ba87f",
        "warning":      "#d9c379",
        "text":         "#E0E0E0",
        "highlight_fg": "#2b1a23",
        "dim":          "#888888",
    },
    "sunset": {
        "primary":      "#e48b7a",
        "secondary":    "#f0a19a",
        "accent":       "#FDC830",
        "success":      "#8ba87f",
        "warning":      "#d9a379",
        "text":         "#E0E0E0",
        "highlight_fg": "#0a1220",
        "dim":          "#888888",
    },
    "mint": {
        "primary":      "#8dbaa3",
        "secondary":    "#a3cbb7",
        "accent":       "#9bd3d3",
        "success":      "#00FF87",
        "warning":      "#FDC830",
        "text":         "#E0E0E0",
        "highlight_fg": "#1a2621",
        "dim":          "#888888",
    },
}

THEME_NAMES = list(THEMES.keys())

# Storage Files
DATA_DIR = os.path.expanduser("~/.cinema-cli")
os.makedirs(DATA_DIR, exist_ok=True)

HISTORY_FILE      = os.path.join(DATA_DIR, "history.json")
FAVORITES_FILE    = os.path.join(DATA_DIR, "favorites.json")
PLAYBACK_FILE     = os.path.join(DATA_DIR, "playback.json")
SETTINGS_FILE     = os.path.join(DATA_DIR, "settings.json")
WATCH_LATER_FILE  = os.path.join(DATA_DIR, "watch_later.json")
PROVIDER_SCORES_FILE = os.path.join(DATA_DIR, "provider_scores.json")
DOWNLOAD_LOG   = os.path.join(DATA_DIR, "download.log")
APP_LOG        = os.path.join(DATA_DIR, "app.log")

# ── Legacy paths – migrate data from old files if they exist ──────────────────
_LEGACY = {
    os.path.expanduser("~/.cinema-cli-history.json"):   HISTORY_FILE,
    os.path.expanduser("~/.cinema-cli-favorites.json"): FAVORITES_FILE,
    os.path.expanduser("~/.cinema-cli-playback.json"):  PLAYBACK_FILE,
    os.path.expanduser("~/.cinema-cli-settings.json"):  SETTINGS_FILE,
}
for _src, _dst in _LEGACY.items():
    if os.path.exists(_src) and not os.path.exists(_dst):
        try:
            import shutil as _shutil
            _shutil.copy2(_src, _dst)
        except Exception:
            pass

# ── Load user theme from settings ─────────────────────────────────────────────
def _load_user_theme() -> str:
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as _f:
                _cfg = json.load(_f)
                _t = _cfg.get("theme", "cinema")
                return _t if _t in THEMES else "cinema"
    except Exception:
        pass
    return "cinema"

_active_theme_name = _load_user_theme()
_theme = THEMES[_active_theme_name]

# ── Public colour constants (imported across the codebase) ────────────────────
PRIMARY   = _theme["primary"]
SECONDARY = _theme["secondary"]
ACCENT    = _theme["accent"]
SUCCESS   = _theme["success"]
WARNING   = _theme["warning"]
TEXT      = _theme["text"]
BG        = "#121212"          # terminal background stays fixed

console = Console()


def apply_theme(theme_name: str) -> bool:
    """Hot-switch the active theme at runtime.

    Updates every public colour constant in this module AND reflects the change
    back into any other module that imported the names directly (by patching
    their module globals via sys.modules).
    """
    import sys as _sys
    global _active_theme_name, _theme
    global PRIMARY, SECONDARY, ACCENT, SUCCESS, WARNING, TEXT

    if theme_name not in THEMES:
        return False

    _active_theme_name = theme_name
    _theme = THEMES[theme_name]

    PRIMARY   = _theme["primary"]
    SECONDARY = _theme["secondary"]
    ACCENT    = _theme["accent"]
    SUCCESS   = _theme["success"]
    WARNING   = _theme["warning"]
    TEXT      = _theme["text"]

    # Patch every already-imported module that grabbed a direct reference.
    _colour_map = {
        "PRIMARY": PRIMARY, "SECONDARY": SECONDARY, "ACCENT": ACCENT,
        "SUCCESS": SUCCESS, "WARNING": WARNING, "TEXT": TEXT,
    }
    for _mod in list(_sys.modules.values()):
        try:
            for _name, _val in _colour_map.items():
                if getattr(_mod, _name, None) is not None and isinstance(getattr(_mod, _name), str):
                    setattr(_mod, _name, _val)
        except Exception:
            pass

    return True
