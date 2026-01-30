import os
import requests
from src.config import OPENSUBTITLES_API_KEY

def fetch_arabic_subtitle(title, year=None, season=None, episode=None):
    key = os.getenv("OPENSUBTITLES_API_KEY") or OPENSUBTITLES_API_KEY
    if not key:
        return None
    headers = {"Api-Key": key}
    params = {"query": title, "languages": "ar"}
    if year:
        params["year"] = year
    if season:
        params["season_number"] = season
    if episode:
        params["episode_number"] = episode
    try:
        r = requests.get("https://api.opensubtitles.com/api/v1/subtitles", params=params, headers=headers, timeout=10)
        if r.status_code != 200:
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
        dr = requests.post("https://api.opensubtitles.com/api/v1/downloads", json={"file_id": file_id}, headers=headers, timeout=10)
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
