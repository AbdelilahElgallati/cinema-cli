import os
from pathlib import Path

from src.utils.cache import cached_api_call

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
    def __init__(self, settings):
        self.session = create_session_with_retries()
        self.timeout = (10, 30)
        self.settings = settings

    @cached_api_call
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

    def search_person(self, query, page=1):
        return self.get_tmdb_data("search/person", {"query": query, "page": page})

    def get_person_details(self, person_id):
        return self.get_tmdb_data(f"person/{person_id}")

    def get_person_credits(self, person_id):
        return self.get_tmdb_data(f"person/{person_id}/combined_credits")

    def get_media_credits(self, media_type, tmdb_id):
        return self.get_tmdb_data(f"{media_type}/{tmdb_id}/credits")

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
                # Show last N lines of backend log to help debugging
                try:
                    project_root = Path(__file__).resolve().parents[3]
                    log_file = project_root / "backend" / "backend.log"
                    if log_file.exists():
                        console.print("[bold]Last backend log lines:[/bold]")
                        with open(
                            log_file, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            lines = f.readlines()
                            tail = lines[-50:]
                            for l in tail:
                                console.print(l.rstrip())
                except Exception:
                    pass
                return {}
            data = resp.json()
            # Debug: if no files returned, log the whole response for troubleshooting
            if not data or (isinstance(data, dict) and not data.get("files")):
                console.print(
                    f"[yellow]Warning: backend returned no files for {url}. Response: {data}[/yellow]"
                )
                # also show last N lines of backend log
                try:
                    project_root = Path(__file__).resolve().parents[3]
                    log_file = project_root / "backend" / "backend.log"
                    if log_file.exists():
                        console.print("[bold]Last backend log lines:[/bold]")
                        with open(
                            log_file, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            lines = f.readlines()
                            tail = lines[-50:]
                            for l in tail:
                                console.print(l.rstrip())
                except Exception:
                    pass
            return data
        except Exception:
            console.print(f"[bold red]Error contacting backend at {url}[/bold red]")
            # show last backend logs if available
            try:
                project_root = Path(__file__).resolve().parents[3]
                log_file = project_root / "backend" / "backend.log"
                if log_file.exists():
                    console.print("[bold]Last backend log lines:[/bold]")
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        tail = lines[-50:]
                        for l in tail:
                            console.print(l.rstrip())
            except Exception:
                pass
            return {}

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
