import json
import re
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from src.config import console


def _extract_url(value):
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


def _get_url_type(url):
    """Classify URL type for appropriate handling."""
    url_lower = url.lower()
    
    if any(x in url_lower for x in [".m3u8", "/hls/", "manifest", ".mpd"]):
        return "hls"
    if any(x in url_lower for x in ["workers.dev", "cloudflare", "cdn"]):
        return "worker"
    if any(x in url_lower for x in [".mp4", ".mkv", ".webm", ".avi"]):
        return "direct"
    return "unknown"


def verify_source(url, headers=None, timeout=8):
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Origin": "https://vidrock.org",
        "Referer": "https://vidrock.org/",
    }
    
    if headers:
        current_headers = default_headers.copy()
        current_headers.update(headers)
    else:
        current_headers = default_headers

    try:
        # Use a Range request which is more reliable for media servers
        current_headers["Range"] = "bytes=0-1024"
        resp = requests.get(
            url, 
            headers=current_headers, 
            timeout=timeout, 
            allow_redirects=True, 
            stream=True, 
            verify=False
        )
        
        # Accept various success codes
        if resp.status_code in [200, 206]:
            # Check content type for media indicators
            content_type = resp.headers.get("content-type", "").lower()
            if any(x in content_type for x in ["video", "audio", "mpegurl", "octet-stream", "application"]):
                return True, "verified"
            # For HLS, content might be text/plain
            if url_type == "hls":
                return True, "hls_verified"
            return True, "status_ok"
        
        # For HLS/worker URLs, be more lenient
        if url_type in ["hls", "worker"] and resp.status_code in [200, 206, 301, 302, 403]:
            return True, "trusted_type"
            
    except requests.exceptions.Timeout:
        # Timeout might just mean slow server - trust HLS/worker URLs
        if url_type in ["hls", "worker"]:
            return True, "timeout_trusted"
        return False, "timeout"
    except requests.exceptions.SSLError:
        # SSL errors are common, yt-dlp handles them with --no-check-certificates
        if url_type in ["hls", "worker"]:
            return True, "ssl_trusted"
        return False, "ssl_error"
    except requests.exceptions.ConnectionError:
        return False, "connection_error"
    except Exception as e:
        if url_type == "hls":
            return True, "hls_fallback"
        return False, str(e)[:50]
    
    # Final fallback: trust HLS streams since yt-dlp is very good at handling them
    if url_type == "hls":
        return True, "hls_fallback"
        
    return False, "unknown_failure"


def verify_source_simple(url, headers=None, timeout=8):
    """Simple wrapper returning just bool for backward compatibility."""
    result, _ = verify_source(url, headers, timeout)
    return result


def select_working_source(sources, skip_validation=False, max_parallel=5, timeout_per_source=6):
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
    priority_providers = ["vidsrc", "vidsrccc", "2embed", "autoembed", "multiembed", "vidrock", "embedsu"]
    
    # Sort sources: prioritize known reliable providers and quality
    def source_priority(src):
        provider = (src.get("provider") or "").lower()
        quality = (src.get("quality") or "").lower()
        url = src.get("file", "").lower()
        
        score = 100  # Base score
        
        # Provider priority
        for i, p in enumerate(priority_providers):
            if p in provider:
                score -= (i * 5)  # First providers get higher priority
                break
        else:
            score -= 50  # Unknown provider
        
        # Quality bonus
        if "1080" in quality:
            score -= 5
        elif "4k" in quality or "2160" in quality:
            score -= 3
        elif "720" in quality:
            score -= 8
        
        # HLS bonus (more reliable with yt-dlp)
        if ".m3u8" in url or "/hls/" in url:
            score -= 2
        
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
        for i, src in enumerate(valid_sources[:max_parallel * 2]):  # Test first 10 sources
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
            except Exception as e:
                pass
    
    # If we found working sources, return the best one
    if working_sources:
        # Sort by original index (which is sorted by priority)
        working_sources.sort(key=lambda x: x[0])
        return working_sources[0][1]
    
    # Fallback: if no sources passed validation, return first HLS URL
    console.print(f"[yellow]  No validated source, trying HLS fallback...[/yellow]")
    for src in sorted_sources:
        url = _extract_url(src.get("file"))
        if url and (".m3u8" in url.lower() or "manifest" in url.lower()):
            console.print(f"[yellow]  Using unvalidated HLS: {src.get('provider')}[/yellow]")
            return src
    
    # Last resort: return first source with any URL
    for src in sorted_sources:
        url = _extract_url(src.get("file"))
        if url and url.startswith("http"):
            console.print(f"[yellow]  Using unvalidated source: {src.get('provider')}[/yellow]")
            return src
            
    return None


def select_multiple_working_sources(sources, count=3, skip_validation=False, max_parallel=8, timeout_per_source=5):
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
    priority_providers = ["vidsrc", "vidsrccc", "2embed", "autoembed", "multiembed", "vidrock"]
    
    def source_priority(src):
        provider = (src.get("provider") or "").lower()
        quality = (src.get("quality") or "").lower()
        
        score = 100
        for i, p in enumerate(priority_providers):
            if p in provider:
                score -= (i * 5)
                break
        
        if "1080" in quality:
            score -= 5
        elif "4k" in quality:
            score -= 3
        
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
                idx, src, is_valid, reason = future.result(timeout=timeout_per_source)
                
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
                if ".m3u8" in url or "/hls/" in url:
                    working_sources.append(src)
                    if len(working_sources) >= count:
                        break
    
    return working_sources[:count]
