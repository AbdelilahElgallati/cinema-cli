import os

import requests
from src.config import OPENSUBTITLES_API_KEY


def fetch_subtitle(title, year=None, season=None, episode=None, lang="ar"):
    key = os.getenv("OPENSUBTITLES_API_KEY") or OPENSUBTITLES_API_KEY
    if not key:
        from src.config import console
        from src.ui.theme import theme
        # console.print(f"[{theme.warning}]Warning: OPENSUBTITLES_API_KEY not set. Cannot fetch fallback subtitles.[/{theme.warning}]")
        return None
    headers = {
        "Api-Key": key,
        "User-Agent": "Cinema-CLI v1.1",
        "Content-Type": "application/json"
    }
    params = {"query": title, "languages": lang}
    
    # If it's a series search, sometimes 'query' is better as just the show name
    # especially if season/episode are provided.
    if season and episode:
        import re
        # Precise query for episodes: Show Title Season X Episode Y
        clean_title = re.sub(r'S\d+E\d+.*', '', title, flags=re.IGNORECASE).strip()
        params["query"] = clean_title
        params["season_number"] = season
        params["episode_number"] = episode
    
    # Debug log
    from src.config import console
    from src.ui.theme import theme as ui_theme
    # console.print(f"[{ui_theme.accent}]OS Search: {params.get('query')} S{params.get('season_number')}E{params.get('episode_number')} ({lang})[/{ui_theme.accent}]")

    try:
        r = requests.get(
            "https://api.opensubtitles.com/api/v1/subtitles",
            params=params,
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            # console.print(f"[red]OS API Error: {r.status_code}[/red]")
            return None
        items = r.json().get("data") or []
        if not items:
            return None
        it = items[0]
        attrs = it.get("attributes") or {}
        files = attrs.get("files") or []
        file_id = None
        if files:
            fid = files[0].get("file_id")
            if fid:
                file_id = fid
        if not file_id:
            fid = attrs.get("file_id") or it.get("id")
            file_id = fid
        if not file_id:
            return None
        dr = requests.post(
            "https://api.opensubtitles.com/api/v1/downloads",
            json={"file_id": file_id},
            headers=headers,
            timeout=10,
        )
        if dr.status_code != 200:
            return None
        link = dr.json().get("link")
        if not link:
            return None
        sr = requests.get(link, timeout=15)
        if sr.status_code != 200:
            return None
        ext = "srt"
        if ".vtt" in link:
            ext = "vtt"
        return sr.content, ext
    except:
        return None
