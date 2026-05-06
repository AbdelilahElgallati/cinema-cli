from __future__ import annotations

from typing import Any, Dict, List, Tuple


def canonicalize_quality(quality: str) -> str:
    q = (quality or "").lower().strip()
    if not q:
        return "unknown"

    compact = q.replace(" ", "").replace("-", "").replace("_", "")

    if "4k" in compact or "2160" in compact:
        return "2160p"
    if "1080" in compact:
        return "1080p"
    if "720" in compact:
        return "720p"
    if "480" in compact:
        return "480p"
    if "360" in compact:
        return "360p"
    if "240" in compact:
        return "240p"

    # Provider-specific labels (VidRock and similar)
    if compact in {"sol", "zenith"}:
        return "2160p"
    if compact in {"astra", "atlas", "orion"}:
        return "1080p"
    if compact in {"nova", "luna"}:
        return "720p"
    if compact in {"vega", "draco"}:
        return "480p"
    if compact in {"nyx"}:
        return "360p"

    # Generic non-standard tags
    if compact in {"auto", "adaptive", "best"}:
        return "auto"
    if "fhd" in compact or "fullhd" in compact:
        return "1080p"
    if compact == "hd" or "high" in compact:
        return "1080p"
    if "sd" in compact:
        return "480p"
    if "medium" in compact:
        return "720p"
    if "low" in compact:
        return "360p"

    return q


def quality_sort_key(quality: str) -> int:
    q = canonicalize_quality(quality)
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
    if "240" in q:
        return 5
    return 6


def sort_manifest_qualities(files: List[Dict[str, Any]]) -> List[str]:
    qualities: List[str] = []
    for item in files or []:
        q = canonicalize_quality(item.get("quality")) if isinstance(item, dict) else None
        if q and q not in qualities:
            qualities.append(q)
    qualities.sort(key=quality_sort_key)
    return qualities


def build_quality_menu_options(
    files: List[Dict[str, Any]], include_adaptive: bool = False
) -> List[Dict[str, str]]:
    """Build quality menu from qualities actually detected for this title.

    If providers do not expose quality tags, only Auto/Adaptive are shown.
    """
    qualities = [q for q in sort_manifest_qualities(files) if (q or "").lower() != "unknown"]

    options: List[Dict[str, str]] = [{"name": "✨ Best Available (Auto)", "value": "auto"}]
    if include_adaptive:
        options.append({"name": "🔄 Adaptive (match connection speed)", "value": "adaptive"})

    if qualities:
        options.extend([{"name": f"📺 {q}", "value": q} for q in qualities])
    return options


def filter_sources_for_quality(
    files: List[Dict[str, Any]], selected_quality: str
) -> Tuple[List[Dict[str, Any]], str]:
    """Filter sources by quality using deterministic rules.

    Returns: (filtered_files, mode)
    mode:
      - "ok_exact": exact tagged quality sources found
      - "enforced_manifest": no provider tags; keep all and enforce in player/downloader
      - "fallback_tagged": provider has tags, requested quality missing; fallback to closest
      - "auto": quality not constrained
    """
    if selected_quality in ("auto", "adaptive", None, ""):
        return list(files or []), "auto"

    sources = list(files or [])
    target_quality = canonicalize_quality(selected_quality)
    has_quality_tags = any(
        isinstance(f, dict) and canonicalize_quality(f.get("quality")) not in ("unknown", "auto")
        for f in sources
    )
    exact = [
        f for f in sources
        if isinstance(f, dict) and canonicalize_quality(f.get("quality")) == target_quality
    ]

    if exact:
        return exact, "ok_exact"
    
    if has_quality_tags:
        target_rank = quality_sort_key(target_quality)
        ranked = sorted(
            sources,
            key=lambda f: (
                abs(quality_sort_key(canonicalize_quality(f.get("quality") if isinstance(f, dict) else "")) - target_rank),
                quality_sort_key(canonicalize_quality(f.get("quality") if isinstance(f, dict) else "")),
            ),
        )
        return ranked, "fallback_tagged"

    return sources, "enforced_manifest"


def adaptive_quality_from_speed(speed_mbps: float) -> str:
    if speed_mbps > 20:
        return "1080p"
    if speed_mbps > 8:
        return "720p"
    if speed_mbps > 3:
        return "480p"
    return "360p"
