import os
import time
import random
import threading
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3
from requests.adapters import HTTPAdapter
from src.config import BACKEND_URL, TMDB_API_KEY, console
from src.utils.app_logger import log_event
from urllib3.util.retry import Retry

# Suppress SSL warnings for external API providers
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Global session for connection pooling
_SHARED_SESSION = None

# Provider reliability scores (higher = more reliable, updated based on success)
PROVIDER_SCORES = {
    "vidsrc": 95,
    "vidsrccc": 90,
    "2embed": 85,
    "twoembed": 85,
    "autoembed": 80,
    "multiembed": 80,
    "vidrock": 75,
    "embedsu": 70,
    "vidzee": 60,
    "primewire": 50,
}


def create_session_with_retries():
    global _SHARED_SESSION
    if _SHARED_SESSION:
        return _SHARED_SESSION

    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy, pool_connections=10, pool_maxsize=10
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "X-Client-Type": "cinema-cli",
        }
    )
    _SHARED_SESSION = session
    return _SHARED_SESSION


class APIClient:
    def __init__(self, settings):
        self.session = create_session_with_retries()
        self.timeout = (10, 30)
        self.settings = settings
        self._source_cache = {}  # Local cache: {cache_key: {"sources": [...], "timestamp": float, "success_count": int}}
        self._cache_ttl = 300  # 5 minutes cache TTL
        self._provider_success = {}  # Track provider success rates: {provider: {"success": 0, "fail": 0}}
        self._lock = threading.Lock()

    def _new_correlation_id(self):
        return f"cinema-{uuid.uuid4().hex[:12]}"

    def _candidate_backends(self):
        """Return backend URL candidates in priority order.

        Order:
        1) User-configured backend from settings
        2) Environment/default BACKEND_URL
        3) Local fallback commonly used by this project (3010)
        """
        candidates = []
        configured = self.settings.get("backend", BACKEND_URL)
        if configured:
            candidates.append(configured.rstrip("/"))
        if BACKEND_URL:
            candidates.append(BACKEND_URL.rstrip("/"))
        candidates.extend([
            "http://localhost:3010",
            "http://127.0.0.1:3010",
        ])

        deduped = []
        seen = set()
        for url in candidates:
            if not isinstance(url, str) or not url.strip():
                continue
            key = url.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(url.strip())
        return deduped

    def _get_cache_key(self, tmdb_id, media_type, season=None, episode=None):
        if media_type == "movie":
            return f"movie_{tmdb_id}"
        return f"tv_{tmdb_id}_s{season}_e{episode}"

    def _is_cache_valid(self, cache_key):
        if cache_key not in self._source_cache:
            return False
        entry = self._source_cache[cache_key]
        return time.time() - entry["timestamp"] < self._cache_ttl

    def _update_provider_stats(self, provider, success):
        """Track provider success/failure for adaptive scoring."""
        with self._lock:
            provider_lower = (provider or "unknown").lower()
            if provider_lower not in self._provider_success:
                self._provider_success[provider_lower] = {"success": 0, "fail": 0}
            if success:
                self._provider_success[provider_lower]["success"] += 1
            else:
                self._provider_success[provider_lower]["fail"] += 1

    def _get_provider_score(self, provider):
        """Get adaptive provider score based on static + dynamic success rate."""
        provider_lower = (provider or "unknown").lower()
        
        # Base score from static config
        base_score = 50
        for key, score in PROVIDER_SCORES.items():
            if key in provider_lower:
                base_score = score
                break
        
        # Adjust based on success history
        with self._lock:
            if provider_lower in self._provider_success:
                stats = self._provider_success[provider_lower]
                total = stats["success"] + stats["fail"]
                if total >= 3:  # Only adjust after 3+ attempts
                    success_rate = stats["success"] / total
                    # Blend static and dynamic: 60% static, 40% dynamic
                    dynamic_score = success_rate * 100
                    return base_score * 0.6 + dynamic_score * 0.4
        
        return base_score

    def get_tmdb_data(self, endpoint, params=None):
        url = f"https://api.themoviedb.org/3/{endpoint}"
        api_key = self.settings.get("tmdb_key") or TMDB_API_KEY
        default_params = {"api_key": api_key, "language": "en-US"}
        if params:
            default_params.update(params)
        try:
            resp = self.session.get(url, params=default_params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            console.print(f"[bold red]Error fetching data: {e}[/bold red]")
            return None

    def get_sources_api(self, tmdb_id, media_type, season=None, episode=None, force_refresh=False):
        """
        Get streaming sources with enhanced reliability.
        
        Args:
            tmdb_id: TMDB ID of the content
            media_type: "movie" or "tv"
            season: Season number (for TV)
            episode: Episode number (for TV)
            force_refresh: If True, bypass cache and fetch fresh sources
        """
        cache_key = self._get_cache_key(tmdb_id, media_type, season, episode)
        
        # Check local cache first (unless forced refresh)
        if not force_refresh and self._is_cache_valid(cache_key):
            cached = self._source_cache[cache_key]
            if cached.get("sources") and len(cached["sources"]) > 0:
                return {"files": cached["sources"], "subtitles": cached.get("subtitles", []), "from_cache": True}
        
        # Fetch from backend with retry logic
        data = self._fetch_sources_with_retry(
            tmdb_id,
            media_type,
            season,
            episode,
            force_refresh=force_refresh,
        )
        
        if data and data.get("files"):
            # Sort sources by provider reliability score
            files = data["files"]
            files = self._sort_sources_by_score(files)
            data["files"] = files
            
            # Update local cache
            self._source_cache[cache_key] = {
                "sources": files,
                "subtitles": data.get("subtitles", []),
                "timestamp": time.time(),
                "success_count": 0
            }
        
        return data

    def _fetch_sources_with_retry(  # NOSONAR
        self,
        tmdb_id,
        media_type,
        season=None,
        episode=None,
        max_retries=2,
        force_refresh=False,
    ):
        """Fetch sources from backend with retry and jitter."""
        backend_bases = self._candidate_backends()
        correlation_id = self._new_correlation_id()
        
        last_error = None
        
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=True
        ) as progress:
            task = progress.add_task("[cyan]Searching for sources...", total=(max_retries + 1) * len(backend_bases))
            
            for attempt in range(max_retries + 1):
                if attempt > 0:
                    progress.update(task, description=f"[cyan]Retry {attempt}/{max_retries}...")

                for base in backend_bases:
                    try:
                        progress.update(task, description=f"[cyan]Scraping {base}...")
                        if media_type == "movie":
                            url = f"{base}/movie/{tmdb_id}"
                        else:
                            url = f"{base}/tv/{tmdb_id}?s={season}&e={episode}"

                        request_headers = {
                            "X-Client-Type": "cinema-cli",
                            "X-Correlation-Id": correlation_id,
                        }
                        params = None
                        if force_refresh:
                            request_headers["X-Bypass-Cache"] = "1"
                            params = {"force_refresh": "1"}

                        # Add cache-busting for retries
                        request_url = url
                        if attempt > 0:
                            separator = "&" if "?" in url else "?"
                            request_url = f"{url}{separator}_retry={attempt}&_t={int(time.time())}"

                        resp = self.session.get(
                            request_url,
                            timeout=self.timeout,
                            headers=request_headers,
                            params=params,
                        )

                        if resp.status_code == 200:
                            data = resp.json()
                            
                            # DEBUG LOG
                            import json, sys
                            print(f"[DEBUG api] raw subtitles from backend: {json.dumps(data.get('subtitles'), indent=2)}", file=sys.stderr)

                            # Check for any valid content (files, subtitles or a list of files)
                            if data and (data.get("files") or data.get("subtitles") or isinstance(data, list)):
                                # Normalize response format
                                if isinstance(data, list):
                                    data = {
                                        "files": data,
                                        "subtitles": [],
                                        "quality_groups": {},
                                        "pipeline": {},
                                    }
                                else:
                                    files = data.get("files")
                                    subtitles = data.get("subtitles")
                                    # Normalize files to list
                                    normalized_files = files if isinstance(files, list) else ([files] if files else [])
                                    # Normalize subtitles to list
                                    normalized_subs = subtitles if isinstance(subtitles, list) else []
                                    
                                    data = {
                                        "files": normalized_files,
                                        "subtitles": normalized_subs,
                                        "quality_groups": data.get("quality_groups") if isinstance(data.get("quality_groups"), dict) else {},
                                        "pipeline": data.get("pipeline") if isinstance(data.get("pipeline"), dict) else {},
                                    }
                                # Persist the winning backend for the rest of this session
                                self.settings["backend"] = base
                                data["correlation_id"] = (
                                    resp.headers.get("x-correlation-id")
                                    or data.get("correlation_id")
                                    or correlation_id
                                )
                                data["backend_used"] = base
                                progress.update(task, completed=(max_retries + 1) * len(backend_bases))
                                log_event(
                                    "api",
                                    f"sources fetched ({media_type}:{tmdb_id}) files={len(data.get('files', []))}",
                                    correlation_id=data["correlation_id"],
                                )
                                return data

                            # Empty response - backend might need time, retry
                            if attempt < max_retries:
                                jitter = random.uniform(0.5, 1.5)
                                time.sleep(1 * (attempt + 1) * jitter)
                                progress.advance(task)
                                continue

                        elif resp.status_code in [429, 503, 504]:
                            # Rate limited or server busy - wait and retry
                            if attempt < max_retries:
                                wait_time = 2 ** attempt + random.uniform(0, 1)
                                progress.update(task, description=f"[yellow]Server busy, waiting {wait_time:.1f}s...")
                                time.sleep(wait_time)
                                progress.advance(task)
                                continue

                        else:
                            last_error = f"HTTP {resp.status_code}"

                    except requests.exceptions.Timeout:
                        last_error = "Timeout"
                    except requests.exceptions.ConnectionError:
                        last_error = "Connection error"
                    except Exception as e:
                        last_error = str(e)
                    
                    progress.advance(task)

            if attempt < max_retries:
                # brief backoff before trying all backends again
                time.sleep(1.0 + attempt)
        
        if last_error:
            console.print(f"[red]  Backend error: {last_error}[/red]")
        
        return {"correlation_id": correlation_id}

    def _sort_sources_by_score(self, sources):
        """Sort sources by provider reliability score (highest first)."""
        if not sources:
            return sources
        
        def get_score(src):
            provider = src.get("provider", "")
            # Get base provider score
            score = self._get_provider_score(provider)
            
            # Boost for quality indicators
            quality = (src.get("quality") or "").lower()
            if "1080" in quality:
                score += 10
            elif "720" in quality:
                score += 5
            elif "4k" in quality or "2160" in quality:
                score += 15
            
            # Boost for HLS streams (more reliable)
            url = src.get("file", "")
            if ".m3u8" in url.lower() or "/hls/" in url.lower():
                score += 5
            
            return score
        
        return sorted(sources, key=get_score, reverse=True)

    def get_sources_enhanced(self, tmdb_id, media_type, season=None, episode=None, min_sources=3):  # NOSONAR
        """
        Enhanced source fetching with multiple attempts and source aggregation.
        Tries to get at least min_sources working sources.
        """
        all_sources = []
        seen_urls = set()
        subtitle_map = {}

        def _merge_subtitles(items):
            if not isinstance(items, list):
                return
            for sub in items:
                if not isinstance(sub, dict):
                    continue
                sub_url = sub.get("url")
                if isinstance(sub_url, str) and sub_url and sub_url not in subtitle_map:
                    subtitle_map[sub_url] = sub
        
        # First attempt - normal fetch
        data = self.get_sources_api(tmdb_id, media_type, season, episode)
        if data and data.get("files"):
            for src in data["files"]:
                url = src.get("file", "")
                if url and url not in seen_urls:
                    all_sources.append(src)
                    seen_urls.add(url)
        _merge_subtitles(data.get("subtitles") if data else [])
        
        # If we don't have enough sources, try fresh fetch
        if len(all_sources) < min_sources:
            console.print(f"[dim]  Found {len(all_sources)} sources, fetching fresh...[/dim]")
            fresh_data = self.get_sources_api(tmdb_id, media_type, season, episode, force_refresh=True)
            if fresh_data and fresh_data.get("files"):
                for src in fresh_data["files"]:
                    url = src.get("file", "")
                    if url and url not in seen_urls:
                        all_sources.append(src)
                        seen_urls.add(url)
            _merge_subtitles(fresh_data.get("subtitles") if fresh_data else [])
        
        # Sort all sources by score
        all_sources = self._sort_sources_by_score(all_sources)
        
        return {
            "files": all_sources,
            "subtitles": list(subtitle_map.values()),
        }

    def report_source_result(self, provider, success):
        """Report whether a source worked or failed (for adaptive scoring)."""
        self._update_provider_stats(provider, success)

    def get_new_movies(self, page=1):
        """Get movies currently in theaters or digitally released recently."""
        # release_date.lte={today}&release_date.gte={30_days_ago}
        import datetime

        today = datetime.date.today()
        past = today - datetime.timedelta(days=45)

        params = {
            "primary_release_date.gte": past.strftime("%Y-%m-%d"),
            "primary_release_date.lte": today.strftime("%Y-%m-%d"),
            "sort_by": "primary_release_date.desc",
            "page": page,
            "with_release_type": "2|3|4",  # Digital, Theatrical
        }
        return self.get_tmdb_data("discover/movie", params)

    def get_new_episodes(self, page=1):
        """Get TV shows that aired episodes in the last 7 days."""
        import datetime

        today = datetime.date.today()
        past = today - datetime.timedelta(days=7)

        params = {
            "air_date.gte": past.strftime("%Y-%m-%d"),
            "air_date.lte": today.strftime("%Y-%m-%d"),
            "sort_by": "popularity.desc",  # popularity is often better for discovery than strict date which might have obscure shows
            "page": page,
            "timezone": "America/New_York",
            "include_null_first_air_dates": "false",
        }
        return self.get_tmdb_data("discover/tv", params)

    def get_trending_tv_today(self, page=1):
        """Get trending TV shows for today."""
        params = {"page": page}
        return self.get_tmdb_data("trending/tv/day", params)

    def get_trending_movies_today(self, page=1):
        """Get trending movies for today."""
        params = {"page": page}
        return self.get_tmdb_data("trending/movie/day", params)
