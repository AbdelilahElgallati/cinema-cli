import json
import os
import re
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from src.config import console


# ── Persistent provider health scores ────────────────────────────────────────

class ProviderScoreStore:
    """Thread-safe, disk-backed provider success/failure counters.

    Scores are keyed by lower-cased provider name.  Each entry holds:
      - ``successes`` / ``failures``  – raw counts
      - ``score``                     – float in [0, 100]; starts at 50 (neutral)
      - ``last_updated``              – epoch seconds

    The score is updated with an EWA (exponentially weighted average):
        score = 0.8 * score + 0.2 * (100 if success else 0)

    This means a provider needs ~5 consecutive failures to drop from 50→~33,
    or ~5 successes to climb from 50→~67 — gradual and noise-resistant.
    """

    _EWA_ALPHA = 0.2   # weight for the newest sample
    _NEUTRAL   = 50.0  # starting score for an unknown provider

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self._data = raw
        except Exception:
            self._data = {}

    def _save(self):
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            pass

    def _entry(self, provider: str) -> dict:
        key = provider.lower().strip()
        if key not in self._data:
            self._data[key] = {
                "score":        self._NEUTRAL,
                "successes":    0,
                "failures":     0,
                "last_updated": time.time(),
            }
        return self._data[key]

    # ── Public API ────────────────────────────────────────────────────────────

    def report(self, provider: str, success: bool):
        """Record one outcome for *provider* and persist asynchronously."""
        if not provider:
            return
        with self._lock:
            e = self._entry(provider)
            if success:
                e["successes"] += 1
            else:
                e["failures"] += 1
            e["score"] = (1 - self._EWA_ALPHA) * e["score"] + self._EWA_ALPHA * (100 if success else 0)
            e["last_updated"] = time.time()
        # Write in background so callers are never blocked
        threading.Thread(target=self._save, daemon=True).start()

    def get_score(self, provider: str) -> float:
        """Return the current health score (0–100; 50 = unknown)."""
        with self._lock:
            return self._entry(provider.lower().strip())["score"]

    def summary(self) -> dict:
        """Return a copy of all provider data for display."""
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def reset(self, provider: str | None = None):
        """Reset scores — all providers if *provider* is None, or a specific one."""
        with self._lock:
            if provider is None:
                self._data = {}
            else:
                self._data.pop(provider.lower().strip(), None)
        self._save()


# Module-level singleton — initialised lazily on first import
_score_store: ProviderScoreStore | None = None

def _get_score_store() -> ProviderScoreStore:
    global _score_store
    if _score_store is None:
        try:
            from src.config import PROVIDER_SCORES_FILE
            _score_store = ProviderScoreStore(PROVIDER_SCORES_FILE)
        except Exception:
            _score_store = ProviderScoreStore(
                os.path.join(os.path.expanduser("~"), ".cinema-cli", "provider_scores.json")
            )
    return _score_store


def report_source_result(provider: str, success: bool):
    """Public helper — call this after every download/stream attempt."""
    try:
        _get_score_store().report(provider, success)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────


def _extract_url(value):  # NOSONAR
    def find_in_obj(obj):
        if isinstance(obj, str):
            if "http" in obj:
                m = re.search(r"https?://[^\"\s]+", obj)
                if m:
                    return m.group(0)
            return None
        if isinstance(obj, dict):
            for k in ["file", "url", "hls", "hls4", "source"]:
                v = obj.get(k)
                found = find_in_obj(v)
                if found:
                    return found
            for v in obj.values():
                found = find_in_obj(v)
                if found:
                    return found
        if isinstance(obj, (list, tuple)):
            for v in obj:
                found = find_in_obj(v)
                if found:
                    return found
        return None

    if isinstance(value, dict):
        return find_in_obj(value) or value
    if isinstance(value, str):
        if value.strip().startswith("{"):
            try:
                parsed = json.loads(value)
                return find_in_obj(parsed) or value
            except Exception:
                pass
        found = find_in_obj(value)
        return found or value
    return value


_M3U8_SIG = ".m3u8"
_HLS_SIG = "/hls/"
_HLS_SIGS = (_M3U8_SIG, _HLS_SIG, "manifest", ".mpd")
_WORKER_SIGS = ("workers.dev", "cloudflare", "cdn")
_DIRECT_SIGS = (".mp4", ".mkv", ".webm", ".avi")

def _get_url_type(url):
    """Classify URL type for appropriate handling."""
    url_lower = url.lower()
    
    if any(x in url_lower for x in _HLS_SIGS):
        return "hls"
    if any(x in url_lower for x in _WORKER_SIGS):
        return "worker"
    if any(x in url_lower for x in _DIRECT_SIGS):
        return "direct"
    return "unknown"


def verify_source(url, headers=None, timeout=8):  # NOSONAR
    """
    Verifies if a media source URL is accessible and active.
    Uses a lenient validation approach that works with various streaming services.
    
    Returns: (bool, str) - (is_valid, reason)
    """
    url = _extract_url(url)
    if not url or not isinstance(url, str):
        return False, "Invalid URL"
    
    url_type = _get_url_type(url)
    
    # Standard headers to mimic browser/CLI requests
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    
    # Add Origin/Referer if we can guess them from URL or they are provided
    url_origin = f"{url.split('://')[0]}://{url.split('://')[1].split('/')[0]}" if "://" in url else None
    if url_origin:
        if "vidrock" in url_origin:
            default_headers["Origin"] = "https://vidrock.net"
            default_headers["Referer"] = "https://vidrock.net/"
        elif "vidzee" in url_origin:
            default_headers["Origin"] = "https://player.vidzee.wtf"
            default_headers["Referer"] = "https://player.vidzee.wtf/"

    if headers:
        current_headers = default_headers.copy()
        current_headers.update(headers)
    else:
        current_headers = default_headers

    try:
        # Use a Range request which is more reliable for media servers
        # BUT some CDNs (like VidRock/Cloudflare) might block it if not handled perfectly
        # So we try it, but if it fails with 403, we might still trust HLS
        current_headers["Range"] = "bytes=0-1024"
        resp = requests.get(
            url, 
            headers=current_headers, 
            timeout=timeout, 
            allow_redirects=True, 
            stream=True, 
            verify=True,  # noqa: S4830
        )
        
        # Accept various success codes
        if resp.status_code in [200, 206]:
            # Check content type for media indicators
            content_type = resp.headers.get("content-type", "").lower()
            if any(x in content_type for x in ["video", "audio", "mpegurl", "octet-stream", "application"]):
                return True, "verified"
            # For HLS, content might be text/plain or other
            if url_type == "hls":
                return True, "hls_verified"
            return True, "status_ok"
        
        # For HLS/worker URLs, be more lenient with 403/429/503
        # because the player (yt-dlp/mpv) often has better retry/bypass logic
        if url_type in ["hls", "worker"] and resp.status_code in [200, 206, 301, 302, 403, 429, 503]:
            return True, f"trusted_type_{resp.status_code}"
            
    except requests.exceptions.Timeout:
        if url_type in ["hls", "worker"]:
            return True, "timeout_trusted"
        return False, "timeout"
    except requests.exceptions.SSLError:
        if url_type in ["hls", "worker"]:
            return True, "ssl_trusted"
        return False, "ssl_error"
    except requests.exceptions.ConnectionError:
        # Even on connection error, if it's HLS, we might want to try it
        if url_type == "hls":
            return True, "conn_trusted_hls"
        return False, "connection_error"
    except Exception as e:
        if url_type == "hls":
            return True, "hls_fallback"
        return False, str(e)[:50]
    
    # Final fallback: trust HLS streams
    if url_type == "hls":
        return True, "hls_final_fallback"
        
    return False, "unknown_failure"


def verify_source_simple(url, headers=None, timeout=8):
    """Simple wrapper returning just bool for backward compatibility."""
    result, _ = verify_source(url, headers, timeout)
    return result


def select_working_source(sources, skip_validation=False, max_parallel=5, timeout_per_source=6):  # NOSONAR
    """
    Tests sources in parallel and returns the first working one.
    
    Args:
        sources: List of source dictionaries with 'file', 'headers', 'provider', 'quality'
        skip_validation: If True, return first source without validation
        max_parallel: Maximum parallel validation threads
        timeout_per_source: Timeout for each source validation
    """
    if not sources:
        return None
    
    # Priority providers that are known to be reliable
    priority_providers = ["vidsrc", "vidsrccc", "2embed", "autoembed", "multiembed", "vidrock", "embedsu", "vidzee"]
    
    # Sort sources: prioritize known reliable providers and quality
    def source_priority(src):
        provider = (src.get("provider") or "").lower()
        quality = (src.get("quality") or "").lower()
        url = src.get("file", "").lower()

        score = 100  # Base score

        # Persistent health score: higher health → lower sort key value (picked first).
        try:
            health = _get_score_store().get_score(provider)
            score -= int((health - 50) * 0.6)
        except Exception:
            pass

        # Provider priority
        for i, p in enumerate(priority_providers):
            if p in provider:
                score -= (i * 10)  # Increase gap
                break
        else:
            score -= 20  # Unknown provider
        
        # Quality bonus
        if "2160" in quality or "4k" in quality:
            score -= 15
        elif "1080" in quality:
            score -= 10
        elif "720" in quality:
            score -= 5
        
        # HLS bonus (more reliable with yt-dlp)
        if any(sig in url for sig in _HLS_SIGS):
            score -= 5
        
        return score
    
    sorted_sources = sorted(sources, key=source_priority)
    
    # If skip validation requested, return first source with valid URL
    if skip_validation:
        for src in sorted_sources:
            url = _extract_url(src.get("file"))
            if url and isinstance(url, str) and url.startswith("http"):
                return src
        return None
    
    # Normalize URLs first
    for src in sorted_sources:
        url = _extract_url(src.get("file"))
        if url and isinstance(url, str) and url != src.get("file"):
            src["file"] = url
    
    # Filter to sources with valid URLs
    valid_sources = [s for s in sorted_sources if s.get("file", "").startswith("http")]
    
    if not valid_sources:
        return None
    
    # Parallel validation for faster results
    console.print(f"[dim]  Testing {len(valid_sources)} sources...[/dim]")
    
    working_sources = []
    
    def test_source(src_tuple):
        idx, src = src_tuple
        url = src.get("file")
        headers = src.get("headers")
        is_valid, reason = verify_source(url, headers, timeout_per_source)
        return idx, src, is_valid, reason
    
    # Test sources in parallel batches
    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        # Submit first batch
        futures = {}
        for i, src in enumerate(valid_sources[:max_parallel * 3]):  # Test more sources
            future = executor.submit(test_source, (i, src))
            futures[future] = i
        
        for future in as_completed(futures, timeout=timeout_per_source * 2):
            try:
                idx, src, is_valid, reason = future.result(timeout=timeout_per_source)
                provider = src.get("provider", "Unknown")
                quality = src.get("quality", "auto")
                
                if is_valid:
                    console.print(f"[green]  ✓ Source {idx+1}: {provider} [{quality}] - {reason}[/green]")
                    working_sources.append((idx, src))
                    # Return first working source immediately for speed
                    if len(working_sources) >= 1:
                        executor.shutdown(wait=False, cancel_futures=True)
                        return working_sources[0][1]
                else:
                    console.print(f"[dim]  ✗ Source {idx+1}: {provider} - {reason}[/dim]")
                    
            except TimeoutError:
                pass
            except Exception:
                pass
    
    # If we found working sources, return the best one
    if working_sources:
        # Sort by original index (which is sorted by priority)
        working_sources.sort(key=lambda x: x[0])
        return working_sources[0][1]
    
    # Fallback: if no sources passed validation, return first HLS URL
    console.print("[yellow]  No validated source, trying HLS fallback...[/yellow]")
    for src in sorted_sources:
        url = _extract_url(src.get("file"))
        if url and any(sig in url.lower() for sig in _HLS_SIGS):
            console.print(f"[yellow]  Using unvalidated HLS: {src.get('provider')}[/yellow]")
            return src
    
    # Last resort: return first source with any URL
    for src in sorted_sources:
        url = _extract_url(src.get("file"))
        if url and url.startswith("http"):
            console.print(f"[yellow]  Using unvalidated source: {src.get('provider')}[/yellow]")
            return src
            
    return None


def select_multiple_working_sources(sources, count=3, skip_validation=False, max_parallel=8, timeout_per_source=5):  # NOSONAR
    """
    Tests sources in parallel and returns multiple working sources.
    Useful for providing fallbacks.
    
    Args:
        sources: List of source dictionaries
        count: Number of working sources to return
        skip_validation: If True, return sources without validation
        max_parallel: Maximum parallel validation threads
        timeout_per_source: Timeout for each source validation
    
    Returns:
        List of working source dictionaries (up to `count`)
    """
    if not sources:
        return []
    
    # Priority providers
    priority_providers = ["vidsrc", "vidsrccc", "2embed", "autoembed", "multiembed", "vidrock", "embedsu", "vidzee"]
    
    def source_priority(src):
        provider = (src.get("provider") or "").lower()
        quality = (src.get("quality") or "").lower()
        url = src.get("file", "").lower()

        score = 100
        # Persistent health bias
        try:
            health = _get_score_store().get_score(provider)
            score -= int((health - 50) * 0.6)
        except Exception:
            pass

        for i, p in enumerate(priority_providers):
            if p in provider:
                score -= (i * 10)
                break
        
        if "2160" in quality or "4k" in quality:
            score -= 15
        elif "1080" in quality:
            score -= 10
        elif "720" in quality:
            score -= 5

        if any(sig in url for sig in _HLS_SIGS):
            score -= 5
        
        return score
    
    sorted_sources = sorted(sources, key=source_priority)
    
    # Normalize URLs
    for src in sorted_sources:
        url = _extract_url(src.get("file"))
        if url and isinstance(url, str) and url != src.get("file"):
            src["file"] = url
    
    valid_sources = [s for s in sorted_sources if s.get("file", "").startswith("http")]
    
    if not valid_sources:
        return []
    
    if skip_validation:
        return valid_sources[:count]
    
    working_sources = []
    seen_providers = set()  # Ensure diversity
    
    def test_source(src_tuple):
        idx, src = src_tuple
        url = src.get("file")
        headers = src.get("headers")
        is_valid, reason = verify_source(url, headers, timeout_per_source)
        return idx, src, is_valid, reason
    
    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {executor.submit(test_source, (i, src)): i for i, src in enumerate(valid_sources)}
        
        for future in as_completed(futures, timeout=timeout_per_source * 3):
            try:
                _, src, is_valid, _ = future.result(timeout=timeout_per_source)
                
                if is_valid:
                    provider = (src.get("provider") or "unknown").lower()
                    # Ensure provider diversity
                    if provider not in seen_providers:
                        working_sources.append(src)
                        seen_providers.add(provider)
                        
                        if len(working_sources) >= count:
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
                            
            except Exception:
                pass
    
    # If not enough working sources, add HLS fallbacks
    if len(working_sources) < count:
        for src in sorted_sources:
            if src not in working_sources:
                url = src.get("file", "").lower()
                if any(sig in url for sig in _HLS_SIGS):
                    working_sources.append(src)
                    if len(working_sources) >= count:
                        break
    
    return working_sources[:count]
