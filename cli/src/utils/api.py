import os
import time
import random
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3
from requests.adapters import HTTPAdapter
from src.config import BACKEND_URL, TMDB_API_KEY, console
from urllib3.util.retry import Retry

# Suppress SSL warnings for external API providers
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    return session


class APIClient:
    def __init__(self, settings):
        self.session = create_session_with_retries()
        self.timeout = (10, 30)
        self.settings = settings
        self._source_cache = {}  # Local cache: {cache_key: {"sources": [...], "timestamp": float, "success_count": int}}
        self._cache_ttl = 300  # 5 minutes cache TTL
        self._provider_success = {}  # Track provider success rates: {provider: {"success": 0, "fail": 0}}
        self._lock = threading.Lock()

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
        data = self._fetch_sources_with_retry(tmdb_id, media_type, season, episode)
        
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

    def _fetch_sources_with_retry(self, tmdb_id, media_type, season=None, episode=None, max_retries=2):
        """Fetch sources from backend with retry and jitter."""
        base = self.settings.get("backend", BACKEND_URL)
        
        if media_type == "movie":
            url = f"{base}/movie/{tmdb_id}"
        else:
            url = f"{base}/tv/{tmdb_id}?s={season}&e={episode}"
        
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                # Add cache-busting for retries
                request_url = url
                if attempt > 0:
                    separator = "&" if "?" in url else "?"
                    request_url = f"{url}{separator}_retry={attempt}&_t={int(time.time())}"
                    console.print(f"[dim]  Retry {attempt}/{max_retries}...[/dim]")
                
                resp = self.session.get(request_url, timeout=self.timeout)
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data and (data.get("files") or isinstance(data, list)):
                        # Normalize response format
                        if isinstance(data, list):
                            data = {"files": data, "subtitles": []}
                        return data
                    
                    # Empty response - backend might need time, retry
                    if attempt < max_retries:
                        jitter = random.uniform(0.5, 1.5)
                        time.sleep(1 * (attempt + 1) * jitter)
                        continue
                
                elif resp.status_code in [429, 503, 504]:
                    # Rate limited or server busy - wait and retry
                    if attempt < max_retries:
                        wait_time = 2 ** attempt + random.uniform(0, 1)
                        console.print(f"[yellow]  Server busy, waiting {wait_time:.1f}s...[/yellow]")
                        time.sleep(wait_time)
                        continue
                
                else:
                    last_error = f"HTTP {resp.status_code}"
                    
            except requests.exceptions.Timeout:
                last_error = "Timeout"
                if attempt < max_retries:
                    time.sleep(1)
                    continue
            except requests.exceptions.ConnectionError:
                last_error = "Connection error"
                if attempt < max_retries:
                    time.sleep(2)
                    continue
            except Exception as e:
                last_error = str(e)
        
        if last_error:
            console.print(f"[red]  Backend error: {last_error}[/red]")
        
        return {}

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

    def get_sources_enhanced(self, tmdb_id, media_type, season=None, episode=None, min_sources=3):
        """
        Enhanced source fetching with multiple attempts and source aggregation.
        Tries to get at least min_sources working sources.
        """
        all_sources = []
        seen_urls = set()
        
        # First attempt - normal fetch
        data = self.get_sources_api(tmdb_id, media_type, season, episode)
        if data and data.get("files"):
            for src in data["files"]:
                url = src.get("file", "")
                if url and url not in seen_urls:
                    all_sources.append(src)
                    seen_urls.add(url)
        
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
        
        # Sort all sources by score
        all_sources = self._sort_sources_by_score(all_sources)
        
        return {
            "files": all_sources,
            "subtitles": data.get("subtitles", []) if data else []
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
