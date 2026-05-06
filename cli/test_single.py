import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.utils.api import APIClient
from src.config import TMDB_API_KEY, BACKEND_URL

def test_single():
    tmdb_id = 121091 # The Last of Us
    media_type = "tv"
    season = 1
    episode = 1
    api = APIClient({"backend": BACKEND_URL, "tmdb_key": TMDB_API_KEY})
    print(f"Testing single fetch for {media_type} S{season}E{episode}...")
    data = api.get_sources_api(tmdb_id, media_type, season, episode, force_refresh=True)
    files = data.get("files", [])
    print(f"Found {len(files)} files.")
    for f in files:
        print(f"  - {f.get('provider')}: {f.get('quality')} ({f.get('file')[:50]}...)")

if __name__ == "__main__":
    test_single()
