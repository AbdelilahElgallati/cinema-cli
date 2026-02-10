import os
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from src.config import BACKEND_URL, TMDB_API_KEY, console
from urllib3.util.retry import Retry


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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "X-Client-Type": "cinema-cli",
        }
    )
    return session


class APIClient:
    # In-memory metadata cache with TTL
    _CACHE_TTL = 300  # 5 minutes

    def __init__(self, settings):
        self.session = create_session_with_retries()
        self.timeout = (10, 30)
        self.settings = settings
        self._cache: dict[str, tuple[float, any]] = {}

    # ── Cache helpers ───────────────────────────────────────
    def _cache_key(self, endpoint: str, params: dict = None) -> str:
        """Generate a deterministic cache key."""
        p = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        return f"{endpoint}?{p}"

    def _cache_get(self, key: str):
        """Return cached value if still valid, else None."""
        entry = self._cache.get(key)
        if entry and (time.time() - entry[0]) < self._CACHE_TTL:
            return entry[1]
        return None

    def _cache_set(self, key: str, value):
        """Store a value in cache with current timestamp."""
        self._cache[key] = (time.time(), value)

    # ── TMDB API ────────────────────────────────────────────
    def get_tmdb_data(self, endpoint, params=None):
        url = f"https://api.themoviedb.org/3/{endpoint}"
        api_key = self.settings.get("tmdb_key") or TMDB_API_KEY
        default_params = {"api_key": api_key, "language": "en-US"}
        if params:
            default_params.update(params)

        # Check cache first
        ckey = self._cache_key(endpoint, default_params)
        cached = self._cache_get(ckey)
        if cached is not None:
            return cached

        try:
            resp = self.session.get(url, params=default_params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            self._cache_set(ckey, data)
            return data
        except Exception as e:
            console.print(f"[bold red]Error fetching data: {e}[/bold red]")
            return None

    # ── Backend Sources ─────────────────────────────────────
    def get_sources_api(self, tmdb_id, media_type, season=None, episode=None):
        base = self.settings.get("backend", BACKEND_URL)
        if media_type == "movie":
            url = f"{base}/movie/{tmdb_id}"
        else:
            url = f"{base}/tv/{tmdb_id}?s={season}&e={episode}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                try:
                    body = resp.text
                except Exception:
                    body = "<unreadable body>"
                console.print(
                    f"[bold red]Backend error {resp.status_code} for {url}: {body}[/bold red]"
                )
                self._show_backend_logs()
                return {}
            data = resp.json()
            if not data or (isinstance(data, dict) and not data.get("files")):
                console.print(
                    f"[yellow]Warning: backend returned no files for {url}. Response: {data}[/yellow]"
                )
                self._show_backend_logs()
            return data
        except Exception:
            console.print(f"[bold red]Error contacting backend at {url}[/bold red]")
            self._show_backend_logs()
            return {}

    def _show_backend_logs(self):
        """Show a brief debug hint instead of dumping the full log."""
        try:
            project_root = Path(__file__).resolve().parents[3]
            log_file = project_root / "backend" / "backend.log"
            if log_file.exists():
                console.print(
                    f"[dim]Check backend log for details: {log_file}[/dim]"
                )
        except Exception:
            pass

    # ── Discovery Endpoints ─────────────────────────────────
    def get_new_movies(self, page=1):
        """Get movies currently in theaters or digitally released recently."""
        import datetime

        today = datetime.date.today()
        past = today - datetime.timedelta(days=45)

        params = {
            "primary_release_date.gte": past.strftime("%Y-%m-%d"),
            "primary_release_date.lte": today.strftime("%Y-%m-%d"),
            "sort_by": "primary_release_date.desc",
            "page": page,
            "with_release_type": "2|3|4",
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
            "sort_by": "popularity.desc",
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

    # ── Cast & Crew ─────────────────────────────────────────
    def get_credits(self, tmdb_id, media_type="movie"):
        """Fetch cast and crew for a movie or TV show."""
        endpoint = f"{media_type}/{tmdb_id}/credits"
        return self.get_tmdb_data(endpoint)

    def search_person(self, name):
        """Search for actors/crew by name."""
        return self.get_tmdb_data("search/person", {"query": name})

    def get_person_credits(self, person_id):
        """Get all movies/shows an actor has appeared in."""
        return self.get_tmdb_data(f"person/{person_id}/combined_credits")
