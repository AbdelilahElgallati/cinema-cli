import os
import re
import subprocess
import time

from src.utils.system_tools import find_executable

_DETAILS_CACHE: dict[str, tuple[float, int, dict]] = {}
_SCAN_CACHE: dict[tuple[str, bool], tuple[float, dict]] = {}
_SCAN_TTL_SECONDS = 10


def clear_library_cache(directory=None):
    """Invalidate cached scan results/details.

    If directory is provided, only matching scan entries are cleared.
    """
    _DETAILS_CACHE.clear()
    if directory is None:
        _SCAN_CACHE.clear()
        return

    target = os.path.abspath(directory)
    stale_keys = [k for k in _SCAN_CACHE if os.path.abspath(k[0]) == target]
    for key in stale_keys:
        _SCAN_CACHE.pop(key, None)


def get_video_files(directory):
    """Recursively find all video files in the given directory."""
    video_extensions = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv")
    video_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(video_extensions):
                full_path = os.path.join(root, file)
                try:
                    # Ignore files smaller than 1MB (likely error pages)
                    if os.path.getsize(full_path) > 1024 * 1024:
                        video_files.append(full_path)
                except Exception:
                    pass
    return video_files


def parse_media_info(file_path):
    """
    Parses title, season, and episode info from a filename.
    Returns a dict with metadata.
    """
    filename = os.path.basename(file_path)
    # Remove extension
    name, _ = os.path.splitext(filename)

    # TV Show patterns: S01E01, 1x01, s1e1
    tv_regex = re.compile(r"(?i)s(?P<season>\d+)e(?P<episode>\d+)")
    tv_regex_alt = re.compile(r"(?P<season>\d+)x(?P<episode>\d+)")

    match = tv_regex.search(name) or tv_regex_alt.search(name)

    if match:
        # Extract title (everything before the match)
        title_raw = name[: match.start()].strip(" .-_")
        if not title_raw:
            # If empty, try parent directory name
            title_raw = os.path.basename(os.path.dirname(os.path.dirname(file_path)))

        return {
            "type": "tv",
            "title": title_raw.replace(".", " ").replace("_", " "),
            "season": int(match.group("season")),
            "episode": int(match.group("episode")),
            "path": file_path,
            "filename": filename,
            "size": os.path.getsize(file_path),
        }

    # Movie pattern: Title (Year) or just Title
    year_regex = re.compile(r"\((?P<year>(19|20)\d{2})\)")
    match_year = year_regex.search(name)

    if match_year:
        title_raw = name[: match_year.start()].strip(" .-_")
        return {
            "type": "movie",
            "title": title_raw.replace(".", " ").replace("_", " "),
            "year": match_year.group("year"),
            "path": file_path,
            "filename": filename,
            "size": os.path.getsize(file_path),
        }

    # Fallback: assume movie
    return {
        "type": "movie",
        "title": name.replace(".", " ").replace("_", " "),
        "path": file_path,
        "filename": filename,
        "size": os.path.getsize(file_path),
    }


def _file_sig(file_path):
    """Return a lightweight file signature for cache validity checks."""
    st = os.stat(file_path)
    return st.st_mtime, st.st_size


def _get_cached_media_details(file_path):
    try:
        sig = _file_sig(file_path)
        cached = _DETAILS_CACHE.get(file_path)
        if cached and cached[0] == sig[0] and cached[1] == sig[1]:
            return dict(cached[2])
    except Exception:
        return None
    return None


def _cache_media_details(file_path, details):
    try:
        sig = _file_sig(file_path)
        _DETAILS_CACHE[file_path] = (sig[0], sig[1], details)
    except Exception:
        return


def _probe_resolution(ffprobe, file_path):
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        file_path,
    ]
    res = subprocess.check_output(cmd, universal_newlines=True, stderr=subprocess.DEVNULL).strip()
    if not res or "," not in res:
        return "Unknown"
    width, height = res.split(",", maxsplit=1)
    return f"{width}x{height}"


def _probe_subtitles(ffprobe, file_path):
    cmd_sub = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index:stream_tags=language,title",
        "-of",
        "csv=p=0",
        file_path,
    ]
    sub_res = subprocess.check_output(
        cmd_sub, universal_newlines=True, stderr=subprocess.DEVNULL
    ).strip()
    if not sub_res:
        return []
    return [line.strip() for line in sub_res.split("\n") if line.strip()]


def get_media_details(file_path, use_cache=True):
    """Uses ffprobe to get resolution and subtitle info (with optional cache)."""
    if use_cache:
        cached = _get_cached_media_details(file_path)
        if cached is not None:
            return cached

    ffprobe = find_executable("ffprobe")
    if not ffprobe:
        return {"resolution": "Unknown", "subtitles": []}

    try:
        resolution = _probe_resolution(ffprobe, file_path)
        subtitles = _probe_subtitles(ffprobe, file_path)
        details = {"resolution": resolution, "subtitles": subtitles}
        if use_cache:
            _cache_media_details(file_path, details)
        return details
    except Exception:
        return {"resolution": "Error", "subtitles": []}


def _get_cached_scan(cache_key):
    cached = _SCAN_CACHE.get(cache_key)
    if not cached:
        return None
    if (time.time() - cached[0]) > _SCAN_TTL_SECONDS:
        return None
    return cached[1]


def _append_tv_episode(tv_shows, info):
    show_title = info["title"]
    season = info["season"]
    if show_title not in tv_shows:
        tv_shows[show_title] = {}
    if season not in tv_shows[show_title]:
        tv_shows[show_title][season] = []
    tv_shows[show_title][season].append(info)


def _sort_tv_episodes(tv_shows):
    for show in tv_shows:
        for season in tv_shows[show]:
            tv_shows[show][season].sort(key=lambda x: x["episode"])


def scan_library(directory, include_details=False, use_cache=True):
    """Scans the directory and returns categorized media.

    include_details=False keeps scans fast and is recommended for menu listing.
    """
    if not os.path.exists(directory):
        return {"movies": [], "tv": {}}

    cache_key = (os.path.abspath(directory), bool(include_details))
    if use_cache:
        cached_scan = _get_cached_scan(cache_key)
        if cached_scan is not None:
            return cached_scan

    files = get_video_files(directory)
    movies = []
    tv_shows = {}  # title -> { season -> [episodes] }

    for f in files:
        info = parse_media_info(f)
        if include_details:
            details = get_media_details(f)
            info.update(details)

        if info["type"] == "movie":
            movies.append(info)
        else:
            _append_tv_episode(tv_shows, info)

    _sort_tv_episodes(tv_shows)

    result = {"movies": sorted(movies, key=lambda x: x["title"]), "tv": tv_shows}

    if use_cache:
        _SCAN_CACHE[cache_key] = (time.time(), result)

    return result


def format_size(bytes):
    """Converts bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes < 1024.0:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.2f} PB"
