import os
from typing import Dict, List, Optional

import requests

from src.config import OPENSUBTITLES_API_KEY


def _get_api_key() -> Optional[str]:
    return os.getenv("OPENSUBTITLES_API_KEY") or OPENSUBTITLES_API_KEY or None


def _looks_like_subtitle(payload: bytes) -> bool:
    if not payload or len(payload) < 32:
        return False
    head = payload[:2048]
    # Avoid HTML error pages
    low = head.lower()
    if b"<html" in low or b"<!doctype html" in low:
        return False
    # Accept common subtitle patterns (srt/vtt/ass)
    return (b"WEBVTT" in head) or (b" --> " in head) or (b"{\\an" in head)


def fetch_subtitles(
    title: str,
    languages: List[str],
    *,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_per_language: int = 1,
) -> List[Dict[str, object]]:
    """Fetch subtitles from OpenSubtitles for multiple languages.

    Returns: [{"lang": "en", "content": bytes, "ext": "srt"}, ...]
    Best-effort: skips any language that fails.
    """

    key = _get_api_key()
    if not key:
        return []

    headers = {"Api-Key": key}
    langs = [l.strip().lower() for l in (languages or []) if l and l.strip()]
    # de-dupe (preserve order)
    seen = set()
    langs = [l for l in langs if not (l in seen or seen.add(l))]
    if not langs:
        return []

    base_params: Dict[str, object] = {"query": title}
    if year:
        base_params["year"] = year
    if season:
        base_params["season_number"] = season
    if episode:
        base_params["episode_number"] = episode

    out: List[Dict[str, object]] = []
    try:
        for lang in langs:
            params = dict(base_params)
            params["languages"] = lang
            r = requests.get(
                "https://api.opensubtitles.com/api/v1/subtitles",
                params=params,
                headers=headers,
                timeout=10,
            )
            if r.status_code != 200:
                continue
            items = r.json().get("data") or []
            if not items:
                continue

            picked = 0
            for it in items:
                if picked >= max_per_language:
                    break

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
                    continue

                dr = requests.post(
                    "https://api.opensubtitles.com/api/v1/downloads",
                    json={"file_id": file_id},
                    headers=headers,
                    timeout=10,
                )
                if dr.status_code != 200:
                    continue
                link = dr.json().get("link")
                if not link:
                    continue

                sr = requests.get(link, timeout=30)
                if sr.status_code != 200:
                    continue
                if not _looks_like_subtitle(sr.content):
                    continue

                ext = "srt"
                if ".vtt" in link.lower() or b"WEBVTT" in sr.content[:2048]:
                    ext = "vtt"

                out.append({"lang": lang, "content": sr.content, "ext": ext})
                picked += 1

    except Exception:
        return out

    return out


def fetch_arabic_subtitle(title, year=None, season=None, episode=None):
    """Backward-compatible wrapper (returns first Arabic subtitle if available)."""
    res = fetch_subtitles(
        title,
        ["ar"],
        year=year,
        season=season,
        episode=episode,
        max_per_language=1,
    )
    if not res:
        return None
    first = res[0]
    return first["content"], first["ext"]
