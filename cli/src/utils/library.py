import os
import re
import shutil
import subprocess

def get_video_files(directory):
    """Recursively find all video files in the given directory."""
    video_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv')
    video_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(video_extensions):
                full_path = os.path.join(root, file)
                try:
                    # Ignore files smaller than 1MB (likely error pages)
                    if os.path.getsize(full_path) > 1024 * 1024:
                        video_files.append(full_path)
                except:
                    pass
    return video_files

def parse_media_info(file_path):
    """
    Parses title, season, and episode info from a filename.
    Returns a dict with metadata.
    """
    filename = os.path.basename(file_path)
    # Remove extension
    name, ext = os.path.splitext(filename)
    
    # TV Show patterns: S01E01, 1x01, s1e1
    tv_regex = re.compile(r"(?i)s(?P<season>\d+)e(?P<episode>\d+)")
    tv_regex_alt = re.compile(r"(?P<season>\d+)x(?P<episode>\d+)")
    
    match = tv_regex.search(name) or tv_regex_alt.search(name)
    
    if match:
        # Extract title (everything before the match)
        title_raw = name[:match.start()].strip(" .-_")
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
            "size": os.path.getsize(file_path)
        }
    
    # Movie pattern: Title (Year) or just Title
    year_regex = re.compile(r"\((?P<year>(19|20)\d{2})\)")
    match_year = year_regex.search(name)
    
    if match_year:
        title_raw = name[:match_year.start()].strip(" .-_")
        return {
            "type": "movie",
            "title": title_raw.replace(".", " ").replace("_", " "),
            "year": match_year.group("year"),
            "path": file_path,
            "filename": filename,
            "size": os.path.getsize(file_path)
        }
    
    # Fallback: assume movie
    return {
        "type": "movie",
        "title": name.replace(".", " ").replace("_", " "),
        "path": file_path,
        "filename": filename,
        "size": os.path.getsize(file_path)
    }

def get_media_details(file_path):
    """Uses ffprobe to get resolution and subtitle info."""
    if not shutil.which("ffprobe"):
        return {"resolution": "Unknown", "subtitles": []}
    
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", file_path
        ]
        res = subprocess.check_output(cmd, universal_newlines=True).strip()
        resolution = "Unknown"
        if res and "," in res:
            w, h = res.split(",")
            resolution = f"{w}x{h}"
            
        # Get subtitles
        cmd_sub = [
            "ffprobe", "-v", "error", "-select_streams", "s",
            "-show_entries", "stream=index:stream_tags=language,title", "-of", "csv=p=0", file_path
        ]
        sub_res = subprocess.check_output(cmd_sub, universal_newlines=True).strip()
        subtitles = []
        if sub_res:
            for line in sub_res.split("\n"):
                if line.strip():
                    subtitles.append(line.strip())
                    
        return {"resolution": resolution, "subtitles": subtitles}
    except:
        return {"resolution": "Error", "subtitles": []}

def scan_library(directory):
    """Scans the directory and returns categorized media."""
    if not os.path.exists(directory):
        return {"movies": [], "tv": {}}
        
    files = get_video_files(directory)
    movies = []
    tv_shows = {} # title -> { season -> [episodes] }
    
    for f in files:
        info = parse_media_info(f)
        # Add basic details by default, can be lazy loaded or full scan
        details = get_media_details(f)
        info.update(details)
        
        if info["type"] == "movie":
            movies.append(info)
        else:
            show_title = info["title"]
            if show_title not in tv_shows:
                tv_shows[show_title] = {}
            
            season = info["season"]
            if season not in tv_shows[show_title]:
                tv_shows[show_title][season] = []
            
            tv_shows[show_title][season].append(info)
            
    # Sort episodes and seasons
    for show in tv_shows:
        for season in tv_shows[show]:
            tv_shows[show][season].sort(key=lambda x: x["episode"])
            
    return {
        "movies": sorted(movies, key=lambda x: x["title"]),
        "tv": tv_shows
    }

def format_size(bytes):
    """Converts bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes < 1024.0:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.2f} PB"
