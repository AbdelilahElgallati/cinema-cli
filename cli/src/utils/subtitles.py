import os
import re
import gzip
import io
import zipfile
from typing import Dict, List, Optional, Tuple

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
    return (
        (b"WEBVTT" in head)
        or (b" --> " in head)
        or (b"{\\an" in head)
        or (b"Dialogue:" in head)
    )


def _request_with_retry(method: str, url: str, **kwargs):
    """Small retry helper for transient OpenSubtitles/network failures."""
    attempts = int(kwargs.pop("attempts", 2) or 2)
    timeout = kwargs.pop("timeout", 15)
    last_exc = None
    for _ in range(max(1, attempts)):
        try:
            return requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    return None


def _query_variants(title: str) -> List[str]:
    """Generate tolerant title variants for subtitle search.

    Playback titles can include episode markers (S01E01) and extra labels
    that hurt OpenSubtitles matching.
    """
    raw = (title or "").strip()
    if not raw:
        return []

    variants = [raw]

    # Keep left side of "Title - Episode Name" patterns.
    if " - " in raw:
        left = raw.split(" - ", 1)[0].strip()
        if left:
            variants.append(left)

    # Remove season/episode tokens like S1E2, s01e02, etc.
    no_se = re.sub(r"\bS\s*\d{1,2}\s*E\s*\d{1,2}\b", "", raw, flags=re.IGNORECASE).strip()
    no_se = re.sub(r"\s{2,}", " ", no_se)
    if no_se:
        variants.append(no_se)

    # Remove year in parentheses.
    no_year = re.sub(r"\(\s*\d{4}\s*\)", "", raw).strip()
    no_year = re.sub(r"\s{2,}", " ", no_year)
    if no_year:
        variants.append(no_year)

    # De-dupe preserving order.
    out = []
    seen = set()
    for v in variants:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def _extract_subtitle_payload(raw: bytes):
    """Return (content_bytes, ext) for plain/compressed subtitle payload."""
    if not raw:
        return None, None

    plain = _extract_plain_subtitle(raw)
    if plain[0]:
        return plain

    if raw[:2] == b"\x1f\x8b":
        gz = _extract_gzip_subtitle(raw)
        if gz[0]:
            return gz

    if raw[:4] == b"PK\x03\x04":
        z = _extract_zip_subtitle(raw)
        if z[0]:
            return z

    return (None, None)


def _extract_plain_subtitle(payload: bytes):
    if not _looks_like_subtitle(payload):
        return (None, None)
    ext = "vtt" if b"WEBVTT" in payload[:2048] else "srt"
    return (payload, ext)


def _extract_gzip_subtitle(payload: bytes):
    try:
        dec = gzip.decompress(payload)
    except Exception:
        return (None, None)
    return _extract_plain_subtitle(dec)


def _extract_zip_subtitle(payload: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for name in zf.namelist():
                low = name.lower()
                if not low.endswith((".srt", ".vtt", ".ass", ".ssa", ".sub", ".txt")):
                    continue
                dec = zf.read(name)
                if not _looks_like_subtitle(dec):
                    continue
                if low.endswith(".vtt") or b"WEBVTT" in dec[:2048]:
                    return (dec, "vtt")
                if low.endswith((".ass", ".ssa")):
                    return (dec, "ass")
                return (dec, "srt")
    except Exception:
        return (None, None)
    return (None, None)


def fetch_subtitles(  # NOSONAR
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

    headers = {"Api-Key": key, "User-Agent": "cinema-cli v2.0", "Accept": "application/json"}
    langs = [l.strip().lower() for l in (languages or []) if l and l.strip()]
    # de-dupe (preserve order)
    seen = set()
    langs = [l for l in langs if not (l in seen or seen.add(l))]
    if not langs:
        return []

    queries = _query_variants(title)
    if not queries:
        return []
    base_params: Dict[str, object] = {}
    if year:
        base_params["year"] = year
    if season:
        base_params["season_number"] = season
    if episode:
        base_params["episode_number"] = episode

    out: List[Dict[str, object]] = []
    try:
        for lang in langs:
            picked = 0
            for query in queries:
                if picked >= max_per_language:
                    break

                params = dict(base_params)
                params["query"] = query
                params["languages"] = lang
                r = _request_with_retry(
                    "GET",
                    "https://api.opensubtitles.com/api/v1/subtitles",
                    params=params,
                    headers=headers,
                    timeout=15,
                    attempts=2,
                )
                if not r or r.status_code != 200:
                    continue

                items = r.json().get("data") or []
                if not items:
                    continue

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

                    dr = _request_with_retry(
                        "POST",
                        "https://api.opensubtitles.com/api/v1/download",
                        json={"file_id": file_id},
                        headers=headers,
                        timeout=15,
                        attempts=2,
                    )
                    if not dr or dr.status_code != 200:
                        continue
                    link = dr.json().get("link")
                    if not link:
                        continue

                    sr = _request_with_retry("GET", link, timeout=30, attempts=2)
                    if not sr or sr.status_code != 200:
                        continue

                    content, ext = _extract_subtitle_payload(sr.content)
                    if not content:
                        continue

                    out.append({"lang": lang, "content": content, "ext": ext or "srt"})
                    picked += 1

                # Stop trying weaker query variants once we have enough for this lang.
                if picked >= max_per_language:
                    break

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
