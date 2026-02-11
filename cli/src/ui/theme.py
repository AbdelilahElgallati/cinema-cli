from src.config import PRIMARY, SECONDARY, ACCENT, SUCCESS, WARNING, BG, TEXT

# Default Theme (Vibrant Dark)
THEME_DEFAULT = {
    "name": "default",
    "primary": PRIMARY,
    "secondary": SECONDARY,
    "accent": ACCENT,
    "success": SUCCESS,
    "warning": WARNING,
    "bg": BG,
    "text": TEXT,
}

# Cyberpunk (Neon Blue/Pink/Yellow)
THEME_CYBERPUNK = {
    "name": "cyberpunk",
    "primary": "#F700FF", # Neon Pink
    "secondary": "#00F0FF", # Cyan
    "accent": "#FAFF00", # Yellow
    "success": "#00FF9F", # Green
    "warning": "#FF3C00", # OrangeRed
    "bg": "#0B0B0B",    # Almost Black
    "text": "#FAFAFA",
}

# Nord (Cool Blue/Grey)
THEME_NORD = {
    "name": "nord",
    "primary": "#88C0D0", # Frost Blue
    "secondary": "#81A1C1", # Blue
    "accent": "#5E81AC", # Dark Blue
    "success": "#A3BE8C", # Green
    "warning": "#EBCB8B", # Yellow
    "bg": "#2E3440",    # Dark Grey
    "text": "#ECEFF4",  # Snow
}

# High Contrast (B/W/Yellow)
THEME_HIGH_CONTRAST = {
    "name": "high_contrast",
    "primary": "#FFFFFF",
    "secondary": "#FFFF00", # Yellow
    "accent": "#00FFFF",  # Cyan
    "success": "#00FF00", # Green
    "warning": "#FF0000", # Red
    "bg": "#000000",
    "text": "#FFFFFF",
}

# Oceanic (Deep Blue / Teal)
THEME_OCEANIC = {
    "name": "oceanic",
    "primary": "#1E90FF", # DodgerBlue
    "secondary": "#00CED1", # DarkTurquoise
    "accent": "#FF7F50", # Coral
    "success": "#32CD32", # LimeGreen
    "warning": "#FFD700", # Gold
    "bg": "#001F3F",    # Navy
    "text": "#E0FFFF",  # LightCyan
}

# Midnight (Deep Purple / Indigo)
THEME_MIDNIGHT = {
    "name": "midnight",
    "primary": "#9370DB", # MediumPurple
    "secondary": "#4B0082", # Indigo
    "accent": "#FF1493", # DeepPink
    "success": "#00FA9A", # MediumSpringGreen
    "warning": "#FFA500", # Orange
    "bg": "#0A001F",    # Dark Midnight
    "text": "#F5F5F5",
}

# Aura (Soft Pastel / Neon)
THEME_AURA = {
    "name": "aura",
    "primary": "#A29BFE", # Soft Purple
    "secondary": "#81ECEC", # Soft Teal
    "accent": "#FAB1A0", # Soft Salmon
    "success": "#55EFC4", # Mint
    "warning": "#FFEAA7", # Pastel Yellow
    "bg": "#1D1D2B",    # Deep Blue Grey
    "text": "#DFE6E9",
}

THEMES = {
    "default": THEME_DEFAULT,
    "cyberpunk": THEME_CYBERPUNK,
    "nord": THEME_NORD,
    "high_contrast": THEME_HIGH_CONTRAST,
    "oceanic": THEME_OCEANIC,
    "midnight": THEME_MIDNIGHT,
    "aura": THEME_AURA,
}

class ThemeManager:
    _instance = None

    def __new__(cls, settings_manager=None):
        if cls._instance is None:
            cls._instance = super(ThemeManager, cls).__new__(cls)
            cls._instance.current_theme = THEME_DEFAULT
            cls._instance.settings = settings_manager
        if settings_manager is not None: # Always update settings if provided
             cls._instance.settings = settings_manager
        return cls._instance

    def set_settings_manager(self, settings_manager):
        self.settings = settings_manager
        self.load_theme()

    def load_theme(self):
        if self.settings:
            theme_name = self.settings.get("theme", "default")
            self.current_theme = THEMES.get(theme_name, THEME_DEFAULT)

    def set_theme(self, theme_name):
        if theme_name in THEMES:
            self.current_theme = THEMES[theme_name]
            if self.settings:
                self.settings.theme = theme_name

    # Accessors
    @property
    def primary(self): return self.current_theme["primary"]
    @property
    def secondary(self): return self.current_theme["secondary"]
    @property
    def accent(self): return self.current_theme["accent"]
    @property
    def success(self): return self.current_theme["success"]
    @property
    def warning(self): return self.current_theme["warning"]
    @property
    def bg(self): return self.current_theme["bg"]
    @property
    def text(self): return self.current_theme["text"]

# Global Instance
theme = ThemeManager()
