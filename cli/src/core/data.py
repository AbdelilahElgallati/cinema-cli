import json
import os
import time
from src.config import HISTORY_FILE, FAVORITES_FILE, PLAYBACK_FILE

class DataManager:
    def __init__(self):
        self.history_file = HISTORY_FILE
        self.favorites_file = FAVORITES_FILE
        self.playback_file = PLAYBACK_FILE
        
        self.history = self._load(self.history_file)
        self.favorites = self._load(self.favorites_file)
        self.playback = self._load(self.playback_file)

    def _load(self, filepath):
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else [] # History/Favorites are lists
            except Exception:
                pass
        return []

    def _load_dict(self, filepath):
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
            except Exception:
                pass
        return {}
    
    # Reload playback specifically as it might be a dict
    def reload_playback(self):
        self.playback = self._load_dict(self.playback_file)

    def save_history(self):
        self._save(self.history_file, self.history)

    def save_favorites(self):
        self._save(self.favorites_file, self.favorites)

    def save_playback(self):
        self._save(self.playback_file, self.playback)

    def _save(self, filepath, data):
        try:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def add_to_history(self, media):
        # Remove existing if present to move to top
        self.history = [h for h in self.history if h.get("id") != media.get("id")]
        self.history.insert(0, media)
        self.history = self.history[:50] # Keep last 50
        self.save_history()

    def update_history_progress(self, media_id, stats, episode=None):
        existing = None
        for item in self.history:
            if item.get("id") == media_id:
                existing = item
                break
        
        if not existing:
            return # Should have been added via add_to_history first

        existing["last_watched"] = time.time()

        if episode:
            existing["last_episode"] = {
                "season": episode.get("season_number"),
                "episode": episode.get("episode_number"),
                "name": episode.get("name"),
                "position": stats.get("position"),
                "duration": stats.get("duration"),
            }
        else:
            existing["position"] = stats.get("position")
            existing["duration"] = stats.get("duration")
            existing["finished"] = stats.get("finished")
        
        self.save_history()

    def toggle_favorite(self, media):
        item_id = media.get("id")
        exists = any(f.get("id") == item_id for f in self.favorites)
        if exists:
            self.favorites = [f for f in self.favorites if f.get("id") != item_id]
            self.save_favorites()
            return False # Removed
        else:
            self.favorites.insert(0, media)
            self.save_favorites()
            return True # Added
    
    def is_favorite(self, media_id):
        return any(f.get("id") == media_id for f in self.favorites)
