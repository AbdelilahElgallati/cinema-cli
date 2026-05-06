import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.abspath("."))

from src.config import BACKEND_URL, SETTINGS_FILE
from src.utils.api import APIClient
from src.utils.source_strategy import filter_sources_for_quality
from src.utils.storage import load_json_data

PROBE_TIMEOUT_SECONDS = 7
MAX_PROBE_WORKERS = 4


def _parse_fps(value):
    if not value:
        return 0.0
    text = str(value).strip()
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            num = float(left)
            den = float(right)
            if den == 0:
                return 0.0
            return round(num / den, 3)
        except Exception:
            return 0.0
    try:
        return round(float(text), 3)
    except Exception:
        return 0.0


def _probe_stream_video(url, headers):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"ok": False, "error": "ffprobe_not_found"}

    cmd = _build_ffprobe_cmd(ffprobe, url, headers)

    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffprobe_timeout"}
    except Exception as exc:
        return {"ok": False, "error": f"ffprobe_exception:{exc}"}

    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "").strip().splitlines()
        return {"ok": False, "error": (tail[-1] if tail else "ffprobe_failed")}

    return _parse_ffprobe_json(cp.stdout)


def _build_ffprobe_cmd(ffprobe, url, headers):
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,pix_fmt",
        "-of",
        "json",
    ]

    if headers and isinstance(headers, dict):
        header_lines = []
        for k, v in headers.items():
            if v is None:
                continue
            header_lines.append(f"{k}: {v}")
        if header_lines:
            cmd.extend(["-headers", "\r\n".join(header_lines) + "\r\n"])
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            cmd.extend(["-user_agent", str(ua)])

    cmd.append(url)
    return cmd


def _parse_ffprobe_json(stdout_text):
    try:
        data = json.loads(stdout_text or "{}")
        streams = data.get("streams") or []
        if not streams:
            return {"ok": False, "error": "no_video_stream"}
        s0 = streams[0]
        return {
            "ok": True,
            "codec": s0.get("codec_name"),
            "pix_fmt": s0.get("pix_fmt"),
            "width": s0.get("width"),
            "height": s0.get("height"),
            "avg_fps": _parse_fps(s0.get("avg_frame_rate")),
            "real_fps": _parse_fps(s0.get("r_frame_rate")),
        }
    except Exception as exc:
        return {"ok": False, "error": f"json_parse_error:{exc}"}


def _build_row_for_quality(label, files, quality):
    filtered, mode = filter_sources_for_quality(files, quality)
    print(f"[{label}] quality={quality} mode={mode} candidates={len(filtered)}", flush=True)

    row = {
        "quality": quality,
        "mode": mode,
        "candidate_count": len(filtered),
        "probe": None,
    }

    if not filtered:
        row["error"] = "no_candidate_for_quality"
        return row

    first = filtered[0]
    row["provider"] = first.get("provider")
    row["tagged_quality"] = first.get("quality")
    url = first.get("file")
    if not url:
        row["error"] = "missing_url"
        return row

    probe = _probe_stream_video(url, first.get("headers") or {})
    row["probe"] = probe
    if probe.get("ok"):
        print(
            f"[{label}] quality={quality} probe_ok {probe.get('width')}x{probe.get('height')} fps={probe.get('avg_fps')}",
            flush=True,
        )
    else:
        print(f"[{label}] quality={quality} probe_fail {probe.get('error')}", flush=True)
    return row


def _resolve_qualities(full_scan):
    # Fast default: core resolutions users typically switch between.
    # Use --full-scan to include 4K probe as well.
    if full_scan:
        return ["240p", "360p", "480p", "720p", "1080p", "2160p"]
    return ["240p", "360p", "480p", "720p", "1080p"]


def sweep_case(
    label, api, tmdb_id, media_type, season=None, episode=None, force_refresh=False, full_scan=False
):
    qualities = _resolve_qualities(full_scan)
    rows = []

    try:
        data = api.get_sources_api(
            str(tmdb_id),
            media_type,
            int(season) if season else None,
            int(episode) if episode else None,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        return {
            "label": label,
            "tmdb_id": tmdb_id,
            "type": media_type,
            "rows": [],
            "error": f"source_fetch_failed:{exc}",
        }

    files = data.get("files", []) if isinstance(data, dict) else []
    quality_groups = data.get("quality_groups", {}) if isinstance(data, dict) else {}
    print(
        f"[{label}] sources={len(files)} groups={list((quality_groups or {}).keys())}", flush=True
    )

    if not qualities:
        return {
            "label": label,
            "tmdb_id": tmdb_id,
            "type": media_type,
            "sources": len(files),
            "quality_groups": list((quality_groups or {}).keys()),
            "rows": rows,
        }

    with ThreadPoolExecutor(max_workers=min(MAX_PROBE_WORKERS, len(qualities))) as pool:
        futures = {pool.submit(_build_row_for_quality, label, files, q): q for q in qualities}
        by_quality = {}
        for fut in as_completed(futures):
            q = futures[fut]
            try:
                by_quality[q] = fut.result()
            except Exception as exc:
                by_quality[q] = {
                    "quality": q,
                    "mode": "error",
                    "candidate_count": 0,
                    "probe": None,
                    "error": f"probe_exception:{exc}",
                }

    rows = [by_quality[q] for q in qualities]

    return {
        "label": label,
        "tmdb_id": tmdb_id,
        "type": media_type,
        "sources": len(files),
        "quality_groups": list((quality_groups or {}).keys()),
        "rows": rows,
    }


if __name__ == "__main__":
    print("Starting quality sweep...", flush=True)
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    full_refresh = "--full-refresh" in sys.argv
    full_scan = "--full-scan" in sys.argv
    print(
        f"Options: full_refresh={full_refresh}, full_scan={full_scan}, probe_timeout={PROBE_TIMEOUT_SECONDS}s",
        flush=True,
    )
    results = [
        sweep_case("movie_550", api, 550, "movie", force_refresh=full_refresh, full_scan=full_scan),
        sweep_case(
            "tv_1396_s1e1",
            api,
            1396,
            "tv",
            season=1,
            episode=1,
            force_refresh=full_refresh,
            full_scan=full_scan,
        ),
    ]
    print("Sweep complete.", flush=True)
    print(json.dumps(results, indent=2))
