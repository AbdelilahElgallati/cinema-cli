"""
Local library browser — scans the downloads folder and presents
downloaded movies, TV shows, seasons, and episodes.
"""

import os
import re


def format_file_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".m4v", ".mov", ".ts", ".flv"}


def _is_video(filename: str) -> bool:
    """Check if a filename is a video file."""
    return os.path.splitext(filename)[1].lower() in _VIDEO_EXTENSIONS


def _parse_episode_info(filename: str) -> dict | None:
    """Try to parse SxxExx pattern from a filename."""
    m = re.search(r"S(\d{1,2})E(\d{1,2})", filename, re.IGNORECASE)
    if m:
        return {"season": int(m.group(1)), "episode": int(m.group(2))}
    return None


def scan_library(downloads_root: str = None) -> dict:
    """
    Scan the downloads folder and return an organized structure.

    Returns:
        {
            "movies": [
                {"title": "...", "path": "...", "size": 12345, "size_human": "1.2 GB"}
            ],
            "tv": [
                {
                    "title": "Show Name",
                    "seasons": [
                        {
                            "season_number": 1,
                            "episodes": [
                                {"episode_number": 1, "path": "...", "filename": "...",
                                 "size": ..., "size_human": "..."}
                            ]
                        }
                    ]
                }
            ],
            "other": [
                {"filename": "...", "path": "...", "size": ..., "size_human": "..."}
            ]
        }
    """
    if not downloads_root:
        downloads_root = os.path.join(os.path.expanduser("~"), "Downloads", "Cinema-CLI")

    result = {"movies": [], "tv": [], "other": []}

    if not os.path.isdir(downloads_root):
        return result

    # ── Movies ──────────────────────────────────────────────
    movies_dir = os.path.join(downloads_root, "movies")
    if os.path.isdir(movies_dir):
        for movie_folder in sorted(os.listdir(movies_dir)):
            folder_path = os.path.join(movies_dir, movie_folder)
            if os.path.isdir(folder_path):
                # Find main video file
                for f in sorted(os.listdir(folder_path)):
                    fpath = os.path.join(folder_path, f)
                    if os.path.isfile(fpath) and _is_video(f):
                        sz = os.path.getsize(fpath)
                        result["movies"].append({
                            "title": movie_folder.replace("_", " "),
                            "filename": f,
                            "path": fpath,
                            "size": sz,
                            "size_human": format_file_size(sz),
                        })
                        break  # Take first video file per folder

    # ── TV Shows ────────────────────────────────────────────
    tv_dir = os.path.join(downloads_root, "tv")
    if os.path.isdir(tv_dir):
        for show_folder in sorted(os.listdir(tv_dir)):
            show_path = os.path.join(tv_dir, show_folder)
            if not os.path.isdir(show_path):
                continue

            show_entry = {
                "title": show_folder.replace("_", " "),
                "seasons": [],
            }

            for season_folder in sorted(os.listdir(show_path)):
                season_path = os.path.join(show_path, season_folder)
                if not os.path.isdir(season_path):
                    continue

                # Parse season number
                sm = re.search(r"(\d+)", season_folder)
                season_num = int(sm.group(1)) if sm else 0

                episodes = []
                for f in sorted(os.listdir(season_path)):
                    fpath = os.path.join(season_path, f)
                    if os.path.isfile(fpath) and _is_video(f):
                        sz = os.path.getsize(fpath)
                        ep_info = _parse_episode_info(f)
                        episodes.append({
                            "episode_number": ep_info["episode"] if ep_info else 0,
                            "season_number": season_num,
                            "filename": f,
                            "path": fpath,
                            "size": sz,
                            "size_human": format_file_size(sz),
                        })

                if episodes:
                    episodes.sort(key=lambda e: e["episode_number"])
                    show_entry["seasons"].append({
                        "season_number": season_num,
                        "episodes": episodes,
                    })

            if show_entry["seasons"]:
                show_entry["seasons"].sort(key=lambda s: s["season_number"])
                result["tv"].append(show_entry)

    # ── Other (loose files in downloads root) ───────────────
    for f in sorted(os.listdir(downloads_root)):
        fpath = os.path.join(downloads_root, f)
        if os.path.isfile(fpath) and _is_video(f):
            sz = os.path.getsize(fpath)
            result["other"].append({
                "filename": f,
                "path": fpath,
                "size": sz,
                "size_human": format_file_size(sz),
            })

    return result


def get_all_files_flat(library: dict) -> list:
    """
    Flatten the library structure into a single list of playable items.
    Each item has: title, path, size_human, media_type, meta_info.
    """
    items = []

    for m in library.get("movies", []):
        items.append({
            "title": m["title"],
            "path": m["path"],
            "size_human": m["size_human"],
            "media_type": "movie",
            "meta_info": "",
        })

    for show in library.get("tv", []):
        for season in show.get("seasons", []):
            for ep in season.get("episodes", []):
                items.append({
                    "title": show["title"],
                    "path": ep["path"],
                    "size_human": ep["size_human"],
                    "media_type": "tv",
                    "meta_info": f"S{ep['season_number']:02d}E{ep['episode_number']:02d}",
                })

    for o in library.get("other", []):
        items.append({
            "title": o["filename"],
            "path": o["path"],
            "size_human": o["size_human"],
            "media_type": "other",
            "meta_info": "",
        })

    return items
