"""
Source checker utilities for automatic source selection.

Provides functions to test if a streaming source URL is alive,
sort sources by quality preference, and find the first working source.
"""

import re

import requests


# Quality ranking: higher index = higher quality
_QUALITY_ORDER = ["360p", "480p", "720p", "1080p", "4K"]


def _quality_index(quality_str: str) -> int:
    """Return a numeric index for a quality string. Higher = better."""
    if not quality_str:
        return -1
    q = quality_str.strip().upper()
    for i, label in enumerate(_QUALITY_ORDER):
        if label.upper() in q or q in label.upper():
            return i
    # Try to extract a number
    m = re.search(r"(\d+)", q)
    if m:
        num = int(m.group(1))
        if num >= 2160:
            return 4
        if num >= 1080:
            return 3
        if num >= 720:
            return 2
        if num >= 480:
            return 1
        return 0
    return -1


def check_source(url: str, headers: dict = None, timeout: int = 8) -> bool:
    """
    Validate a source URL by sending a HEAD request (fallback to GET with stream).
    Returns True if the URL responds with HTTP 2xx and a plausible content type.
    """
    if not url:
        return False

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if headers:
        req_headers.update(headers)

    try:
        # First try HEAD to avoid downloading content
        resp = requests.head(url, headers=req_headers, timeout=timeout, allow_redirects=True)
        if resp.status_code < 400:
            ct = (resp.headers.get("Content-Type") or "").lower()
            # Accept video types, HLS playlists, or generic octet-stream
            if any(t in ct for t in ["video", "mpegurl", "octet-stream", "mp2t", "application"]):
                return True
            # Some servers don't return content-type on HEAD; treat 2xx as success
            if resp.status_code < 300:
                return True
    except Exception:
        pass

    # Fallback: streaming GET with just first bytes
    try:
        resp = requests.get(
            url, headers=req_headers, timeout=timeout, stream=True, allow_redirects=True
        )
        if resp.status_code < 400:
            # Read a tiny chunk to confirm it's actually serving data
            chunk = next(resp.iter_content(1024), None)
            resp.close()
            return chunk is not None and len(chunk) > 0
        resp.close()
    except Exception:
        pass

    return False


def sort_sources_by_quality(files: list, preferred_quality: str = None) -> list:
    """
    Sort source list so that sources matching the preferred quality come first,
    followed by nearest-quality sources (descending).
    """
    if not files:
        return []

    pref_idx = _quality_index(preferred_quality) if preferred_quality else -1

    def sort_key(source):
        q = source.get("quality", "")
        idx = _quality_index(q)
        if pref_idx >= 0:
            # Exact match gets highest priority (distance = 0)
            # Otherwise sort by distance to preferred, then by quality descending
            distance = abs(idx - pref_idx)
            return (distance, -idx)
        else:
            # No preference: sort by quality descending
            return (0, -idx)

    return sorted(files, key=sort_key)


def find_working_source(
    files: list,
    headers: dict = None,
    preferred_quality: str = None,
    on_progress=None,
) -> dict | None:
    """
    Iterate sources sorted by quality preference and return the first one
    that responds successfully. Returns None if no source works.

    on_progress: optional callback(current_index, total, source) for UI updates.
    """
    if not files:
        return None

    sorted_files = sort_sources_by_quality(files, preferred_quality)

    for i, source in enumerate(sorted_files):
        if on_progress:
            on_progress(i, len(sorted_files), source)

        url = source.get("file")
        src_headers = source.get("headers") or headers
        if check_source(url, src_headers):
            return source

    return None
