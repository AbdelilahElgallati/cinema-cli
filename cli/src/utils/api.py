import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src.config import console, TMDB_API_KEY, BACKEND_URL

def create_session_with_retries():
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json'
    })
    return session

class APIClient:
    def __init__(self, settings):
        self.session = create_session_with_retries()
        self.timeout = (10, 30)
        self.settings = settings

    def get_tmdb_data(self, endpoint, params=None):
        url = f"https://api.themoviedb.org/3/{endpoint}"
        api_key = self.settings.get('tmdb_key') or TMDB_API_KEY
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
    
    def get_sources_api(self, tmdb_id, media_type, season=None, episode=None):
        base = self.settings.get('backend', BACKEND_URL)
        if media_type == 'movie':
            url = f"{base}/movie/{tmdb_id}"
        else:
            url = f"{base}/tv/{tmdb_id}?s={season}&e={episode}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return {}
            return resp.json()
        except Exception:
            return {}
