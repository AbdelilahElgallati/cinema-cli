"""
UI Theme system for Cinema CLI.

Provides multiple color themes that users can switch between at runtime.
"""

THEMES = {
    "default": {
        "label": "🎬 Default (Red-Orange)",
        "PRIMARY": "#FF4B2B",
        "SECONDARY": "#FF416C",
        "ACCENT": "#00D2FF",
        "SUCCESS": "#00FF87",
        "WARNING": "#FDC830",
        "BG": "#121212",
        "TEXT": "#E0E0E0",
    },
    "cyberpunk": {
        "label": "🌆 Cyberpunk (Neon Pink/Blue)",
        "PRIMARY": "#FF00FF",
        "SECONDARY": "#FF1493",
        "ACCENT": "#00FFFF",
        "SUCCESS": "#39FF14",
        "WARNING": "#FFD700",
        "BG": "#0D0221",
        "TEXT": "#F0E6FF",
    },
    "nord": {
        "label": "❄️ Nord (Blue/Grey)",
        "PRIMARY": "#88C0D0",
        "SECONDARY": "#81A1C1",
        "ACCENT": "#5E81AC",
        "SUCCESS": "#A3BE8C",
        "WARNING": "#EBCB8B",
        "BG": "#2E3440",
        "TEXT": "#ECEFF4",
    },
    "high_contrast": {
        "label": "♿ High Contrast (Accessibility)",
        "PRIMARY": "#FFFFFF",
        "SECONDARY": "#FFFF00",
        "ACCENT": "#00FF00",
        "SUCCESS": "#00FF00",
        "WARNING": "#FFFF00",
        "BG": "#000000",
        "TEXT": "#FFFFFF",
    },
}


def get_theme(name: str) -> dict:
    """Get a theme dict by name. Returns default if not found."""
    return THEMES.get(name, THEMES["default"])


def list_themes() -> list[str]:
    """Return list of available theme names."""
    return list(THEMES.keys())


def get_theme_label(name: str) -> str:
    """Get the display label for a theme."""
    theme = THEMES.get(name, THEMES["default"])
    return theme.get("label", name.title())


def apply_theme(name: str) -> dict:
    """
    Apply a theme by name. Updates the config module's global color variables.
    Returns the theme dict that was applied.
    """
    import src.config as cfg

    theme = get_theme(name)
    cfg.PRIMARY = theme["PRIMARY"]
    cfg.SECONDARY = theme["SECONDARY"]
    cfg.ACCENT = theme["ACCENT"]
    cfg.SUCCESS = theme["SUCCESS"]
    cfg.WARNING = theme["WARNING"]
    cfg.BG = theme["BG"]
    cfg.TEXT = theme["TEXT"]
    return theme
