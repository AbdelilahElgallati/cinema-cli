import os
import re
import gzip
import io
import zipfile
from typing import Dict, List, Optional, Tuple

import requests

from src.config import OPENSUBTITLES_API_KEY, SUBDL_API_KEY
from src.utils.utils import normalize_lang


def _get_api_key() -> Optional[str]:
    return os.getenv("OPENSUBTITLES_API_KEY") or OPENSUBTITLES_API_KEY or None


def _get_subdl_key() -> Optional[str]:
    return os.getenv("SUBDL_API_KEY") or SUBDL_API_KEY or None


def _looks_like_subtitle(payload: bytes) -> bool:
    if not payload or len(payload) < 32:
        return False
    head = payload[:2048]
    # Avoid HTML error pages
    low = head.lower()
    if b"<html" in low or b"<!doctype html" in low:
        return False
    # Accept common subtitle patterns (srt/vtt/ass)
    head_upper = head.upper()
    return (
        (b"WEBVTT" in head_upper)
        or (b" --> " in head)
        or (b"{\\AN" in head_upper)
        or (b"DIALOGUE:" in head_upper)
    )


def _request_with_retry(method: str, url: str, **kwargs):
    """Small retry helper for transient OpenSubtitles/network failures.
    Includes special handling for 429 (Too Many Requests).
    """
    attempts = int(kwargs.pop("attempts", 2) or 2)
    timeout = kwargs.pop("timeout", 15)

    # Honor TLS verification config
    from src.config import DEFAULT_SUBTITLE_VERIFY_TLS
    try:
        from src.utils.storage import load_json_data
        from src.config import SETTINGS_FILE
        _settings = load_json_data(SETTINGS_FILE) or {}
        _verify_tls = _settings.get("SUBTITLE_VERIFY_TLS", DEFAULT_SUBTITLE_VERIFY_TLS)
    except Exception:
        _verify_tls = DEFAULT_SUBTITLE_VERIFY_TLS

    last_exc = None
    for attempt in range(max(1, attempts)):
        try:
            r = requests.request(method, url, timeout=timeout, verify=_verify_tls, **kwargs)
            if r.status_code == 429:
                import time
                # Wait longer on second attempt for 429
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                continue
            return r
        except requests.RequestException as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    return None


def _query_variants(title: str) -> List[str]:
    """Generate tolerant title variants for subtitle search.

    Playback titles can include episode markers (S01E01) and extra labels
    that hurt OpenSubtitles matching. We try to find the "base" title.
    """
    raw = (title or "").strip()
    if not raw:
        return []

    variants = [raw]

    # 1. Keep left side of "Title - Episode Name" patterns.
    if " - " in raw:
        left = raw.split(" - ", 1)[0].strip()
        if left:
            variants.append(left)

    # 2. Remove season/episode tokens from all variants gathered so far.
    #    (e.g., "Tracker S01E01" -> "Tracker")
    for v in list(variants):
        no_se = re.sub(r"\bS\s*\d{1,2}\s*E\s*\d{1,2}\b", "", v, flags=re.IGNORECASE).strip()
        no_se = re.sub(r"\s{2,}", " ", no_se)
        if no_se and no_se not in variants:
            variants.append(no_se)

    # 3. Remove year in parentheses from all variants.
    for v in list(variants):
        no_year = re.sub(r"\(\s*\d{4}\s*\)", "", v).strip()
        no_year = re.sub(r"\s{2,}", " ", no_year)
        if no_year and no_year not in variants:
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


def fetch_subtitles_subdl(
    title: str,
    languages: List[str],
    *,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_per_language: int = 1,
) -> List[Dict[str, object]]:
    """Fetch subtitles from SubDL.com as a secondary fallback.
    
    SubDL is particularly good for Arabic/Persian/etc.
    """
    key = _get_subdl_key()
    if not key:
        return []

    # 1. Search for the film/series ID
    queries = _query_variants(title)
    if not queries:
        return []
        
    sd_id = None
    media_type = "tv" if (season or episode) else "movie"
    
    for query in queries:
        params = {"api_key": key, "film_name": query, "type": media_type}
        if year and media_type == "movie":
            params["year"] = year
            
        try:
            r = _request_with_retry("GET", "https://api.subdl.com/api/v1/subtitles/search", params=params, timeout=8)
            if r and r.status_code == 200:
                data = r.json()
                if data.get("status") and data.get("results"):
                    # Use first matching result
                    sd_id = data["results"][0].get("sd_id")
                    if sd_id:
                        break
        except Exception as e:
            from src.utils import app_logger
            app_logger.debug(f"SUBDL Search Error: {e}")
            continue

    if not sd_id:
        return []

    # 2. List subtitles for the found ID
    out: List[Dict[str, object]] = []
    langs_str = ",".join([l.upper() for l in languages])
    params = {
        "api_key": key,
        "sd_id": sd_id,
        "languages": langs_str,
    }
    if season:
        params["season"] = season
    if episode:
        params["episode"] = episode

    try:
        r = _request_with_retry("GET", "https://api.subdl.com/api/v1/subtitles/list", params=params, timeout=8)
        if not r or r.status_code != 200:
            return []
            
        data = r.json()
        if not data.get("status") or not data.get("subtitles"):
            return []

        # SubDL groups results by language
        subs_by_lang = data["subtitles"]
        for lang_code, subs in subs_by_lang.items():
            lang_low = lang_code.lower()
            picked = 0
            for s in subs:
                if picked >= max_per_language:
                    break
                
                url = s.get("url")
                if not url:
                    continue
                
                # SubDL URLs usually require the API key as a query param or redirect
                dl_url = f"{url}"
                if "?" in dl_url:
                    dl_url += f"&api_key={key}"
                else:
                    dl_url += f"?api_key={key}"
                
                sr = _request_with_retry("GET", dl_url, timeout=8, attempts=2)
                if not sr or sr.status_code != 200:
                    continue

                content, ext = _extract_subtitle_payload(sr.content)
                if not content:
                    continue

                out.append({"lang": lang_low, "content": content, "ext": ext or "srt"})
                picked += 1
                
    except Exception as e:
        from src.utils import app_logger
        app_logger.debug(f"SUBDL List Error: {e}")
        pass

    return out


def fetch_subtitles(  # NOSONAR
    title: str,
    languages: List[str],
    *,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_per_language: int = 1,
) -> List[Dict[str, object]]:
    """Fetch subtitles for multiple languages.
    
    Tries OpenSubtitles first, then falls back to SubDL for any language
    that didn't get enough results.
    """
    final_out: List[Dict[str, object]] = []
    langs = [normalize_lang(l) for l in (languages or []) if l and str(l).strip()]
    # de-dupe (preserve order)
    seen_langs = set()
    langs = [l for l in langs if l and l != "und" and not (l in seen_langs or seen_langs.add(l))]
    if not langs:
        return []

    # 1. Try OpenSubtitles
    os_results: List[Dict[str, object]] = []
    os_key = _get_api_key()
    if os_key:
        try:
            os_results = _fetch_from_opensubtitles(
                title, langs, year=year, season=season, episode=episode, 
                max_per_language=max_per_language, key=os_key
            )
        except Exception as e:
            from src.utils import app_logger
            app_logger.error(f"OpenSubtitles lookup failed for '{title}' (langs: {langs}): {e}")

    # Add OS results to final_out
    final_out.extend(os_results)

    # 2. Check if we need more from SubDL
    needed_langs = []
    for lang in langs:
        count = sum(1 for item in final_out if item["lang"] == lang)
        if count < max_per_language:
            needed_langs.append(lang)
            
    if needed_langs and _get_subdl_key():
        try:
            subdl_results = fetch_subtitles_subdl(
                title, needed_langs, year=year, season=season, episode=episode, 
                max_per_language=max_per_language
            )
            # Merge results, being careful not to exceed max_per_language per lang
            for sr in subdl_results:
                lang = sr["lang"]
                current_count = sum(1 for item in final_out if item["lang"] == lang)
                if current_count < max_per_language:
                    final_out.append(sr)
        except Exception as e:
            from src.utils import app_logger
            app_logger.error(f"SubDL lookup failed for '{title}' (langs: {needed_langs}): {e}")

    return final_out


def _fetch_from_opensubtitles(
    title: str,
    languages: List[str],
    *,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_per_language: int = 1,
    key: str = "",
) -> List[Dict[str, object]]:
    """Internal helper for OpenSubtitles logic."""
    headers = {"Api-Key": key, "User-Agent": "cinema-cli v2.0", "Accept": "application/json"}
    langs = [normalize_lang(l) for l in (languages or []) if l and str(l).strip()]
    # de-dupe (preserve order)
    seen = set()
    langs = [l for l in langs if l and l != "und" and not (l in seen or seen.add(l))]
    if not langs:
        return []

    queries = _query_variants(title)
    if not queries:
        return []
    base_params: Dict[str, object] = {}
    if year and not (season or episode):
        base_params["year"] = year
    if season:
        base_params["season_number"] = season
    if episode:
        base_params["episode_number"] = episode

    out: List[Dict[str, object]] = []
    # OpenSubtitles allows comma-separated languages in a single request.
    # We search all at once for each query variant.
    langs_param = ",".join(langs)
    
    for query in queries:
        params = dict(base_params)
        params["query"] = query
        params["languages"] = langs_param
        
        try:
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

            data = r.json().get("data") or []
            if not data:
                continue

            # Group results by language to respect max_per_language
            for it in data:
                attrs = it.get("attributes") or {}
                # The API returns 'language' as a 2-letter code in attributes
                sub_lang = normalize_lang(attrs.get("language"))
                
                # Check if we still need more for this language
                current_count = sum(1 for x in out if x["lang"] == sub_lang)
                if current_count >= max_per_language:
                    continue
                if sub_lang not in langs:
                    continue

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

                out.append({"lang": sub_lang, "content": content, "ext": ext or "srt"})
        except Exception as e:
            from src.utils import app_logger
            app_logger.debug(f"OpenSubtitles query error: {e}")
            continue

        # If we have at least one result for every requested language, we can stop querying variants
        if all(sum(1 for x in out if x["lang"] == l) >= max_per_language for l in langs):
            break

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
