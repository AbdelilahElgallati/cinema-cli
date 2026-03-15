from __future__ import annotations

from typing import Any, Dict, List, Tuple


def quality_sort_key(quality: str) -> int:
    q = (quality or "").lower()
    if "4k" in q or "2160" in q:
        return 0
    if "1080" in q:
        return 1
    if "720" in q:
        return 2
    if "480" in q:
        return 3
    if "360" in q:
        return 4
    return 5


def sort_manifest_qualities(files: List[Dict[str, Any]]) -> List[str]:
    qualities: List[str] = []
    for item in files or []:
        q = item.get("quality") if isinstance(item, dict) else None
        if q and q not in qualities:
            qualities.append(q)
    qualities.sort(key=quality_sort_key)
    return qualities


def filter_sources_for_quality(
    files: List[Dict[str, Any]], selected_quality: str
) -> Tuple[List[Dict[str, Any]], str]:
    """Filter sources by quality using deterministic rules.

    Returns: (filtered_files, mode)
    mode:
      - "ok_exact": exact tagged quality sources found
      - "enforced_manifest": no provider tags; keep all and enforce in player/downloader
      - "unavailable_tagged": provider has tags, requested quality missing
      - "auto": quality not constrained
    """
    if selected_quality in ("auto", "adaptive", None, ""):
        return list(files or []), "auto"

    sources = list(files or [])
    has_quality_tags = any((isinstance(f, dict) and f.get("quality")) for f in sources)
    exact = [f for f in sources if isinstance(f, dict) and f.get("quality") == selected_quality]

    if exact:
        return exact, "ok_exact"
    if has_quality_tags:
        return [], "unavailable_tagged"
    return sources, "enforced_manifest"


def adaptive_quality_from_speed(speed_mbps: float) -> str:
    if speed_mbps > 20:
        return "1080p"
    if speed_mbps > 8:
        return "720p"
    if speed_mbps > 3:
        return "480p"
    return "360p"
