import json
import os
from src.config import SETTINGS_FILE, BACKEND_URL

class SettingsManager:
    def __init__(self):
        self.settings_file = SETTINGS_FILE
        self.settings = self._load_settings()

    def _load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save(self):
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=2)
        except Exception:
            pass

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        self.settings[key] = value
        self.save()

    # Specific getters/setters for typed access
    @property
    def backend_url(self):
        return self.settings.get("backend", BACKEND_URL)

    @backend_url.setter
    def backend_url(self, value):
        self.settings["backend"] = value
        self.save()

    @property
    def tmdb_key(self):
        return self.settings.get("tmdb_key", "")

    @tmdb_key.setter
    def tmdb_key(self, value):
        self.settings["tmdb_key"] = value
        self.save()

    @property
    def filename_template_movie(self):
        return self.settings.get("filename_template", "{title}.{year}")

    @filename_template_movie.setter
    def filename_template_movie(self, value):
        self.settings["filename_template"] = value
        self.save()

    @property
    def filename_template_tv(self):
        return self.settings.get("filename_template_tv", "{title}.S{season}E{episode}")

    @filename_template_tv.setter
    def filename_template_tv(self, value):
        self.settings["filename_template_tv"] = value
        self.save()
    
    @property
    def theme(self):
        return self.settings.get("theme", "default")
    
    @theme.setter
    def theme(self, value):
        self.settings["theme"] = value
        self.save()

    @property
    def local_library_paths(self):
        return self.settings.get("local_library_paths", [])

    @local_library_paths.setter
    def local_library_paths(self, value):
        self.settings["local_library_paths"] = value
        self.save()

    @property
    def subtitle_languages(self):
        return self.settings.get("subtitle_languages", ["ar", "en", "fr"])

    @subtitle_languages.setter
    def subtitle_languages(self, value):
        self.settings["subtitle_languages"] = value
        self.save()

    @property
    def default_subtitle_language(self):
        return self.settings.get("default_subtitle_language", "ar")

    @default_subtitle_language.setter
    def default_subtitle_language(self, value):
        self.settings["default_subtitle_language"] = value
        self.save()

    @property
    def use_idm(self):
        return self.settings.get("use_idm", False)

    @use_idm.setter
    def use_idm(self, value):
        self.settings["use_idm"] = value
        self.save()

    @property
    def idm_path(self):
        # Default path for IDM on 64-bit Windows
        return self.settings.get("idm_path", r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe")

    @idm_path.setter
    def idm_path(self, value):
        self.settings["idm_path"] = value
        self.save()
