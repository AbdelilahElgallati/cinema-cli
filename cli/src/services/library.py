import os
import re
from concurrent.futures import ThreadPoolExecutor

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"}

class LibraryManager:
    def __init__(self):
        pass

    def scan(self, paths):
        """
        Scans given paths for video files and categorizes them.
        Returns a list of media objects.
        """
        media_items = []
        for path in paths:
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        if os.path.splitext(file)[1].lower() in VIDEO_EXTENSIONS:
                            item = self._parse_filename(file, os.path.join(root, file))
                            if item:
                                media_items.append(item)
        
        # Sort by title
        media_items.sort(key=lambda x: x["title"])
        return media_items

    def _parse_filename(self, filename, filepath):
        # Basic parsing logic
        name = os.path.splitext(filename)[0]
        
        # Check for TV Show pattern SxxExx or dxdd
        tv_pattern = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", name)
        if tv_pattern:
            season = int(tv_pattern.group(1))
            episode = int(tv_pattern.group(2))
            # Title is usually before SxxExx
            title_part = name[:tv_pattern.start()].replace(".", " ").strip(" -_")
            return {
                "title": title_part,
                "name": title_part,
                "type": "tv",
                "media_type": "tv",
                "season": season,
                "episode": episode,
                "path": filepath,
                "filename": filename
            }
        
        # Check for Movie Year
        movie_pattern = re.search(r"[\(\.]((?:19|20)\d{2})[\)\.]", name)
        year = None
        if movie_pattern:
            year = movie_pattern.group(1)
            title_part = name[:movie_pattern.start()].replace(".", " ").strip(" -_")
        else:
            title_part = name.replace(".", " ").strip(" -_")
            
        return {
            "title": title_part,
            "name": title_part,
            "type": "movie",
            "media_type": "movie",
            "year": year,
            "path": filepath,
            "filename": filename
        }
