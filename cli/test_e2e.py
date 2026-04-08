import os
import sys

import pytest

# Ensure imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.api import APIClient
from src.config import TMDB_API_KEY, BACKEND_URL


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_CINEMA_E2E") != "1",
    reason="Set RUN_CINEMA_E2E=1 to run live network E2E checks",
)
def test_all_flows():
    api = APIClient({"backend": BACKEND_URL, "tmdb_key": TMDB_API_KEY})
    
    # 1. Test Movie Search & Source Fetch
    print("Testing Movie Flow (Inception)...")
    res = api.get_tmdb_data('search/movie', {'query': 'Inception'})
    if not res or not res.get('results'):
        print("❌ Movie search failed.")
        sys.exit(1)
        
    movie = res['results'][0]
    movie_id = movie['id']
    sources = api.get_sources_api(movie_id, 'movie', None, None)
    if sources and sources.get("files"):
        print(f"✅ Movie Streaming/Download sources found! {len(sources['files'])} files returned.")
    else:
        print("❌ Movie source retrieval failed.")
        sys.exit(1)

    # 2. Test TV Search & Source Fetch
    print("\nTesting TV Flow (Tracker)...")
    res = api.get_tmdb_data('search/tv', {'query': 'Tracker'})
    if not res or not res.get('results'):
        print("❌ TV search failed.")
        sys.exit(1)
        
    tv = res['results'][0]
    tv_id = tv['id']
    tv_sources = api.get_sources_api(tv_id, 'tv', 1, 1)
    if tv_sources and tv_sources.get("files"):
        print(f"✅ TV Streaming/Download sources found! {len(tv_sources['files'])} files returned.")
    else:
        print("❌ TV source retrieval failed.")
        sys.exit(1)
        
    # 3. Test Batch TV Seasons
    print("\nTesting TV Season/Episode Meta Fetch for Batching...")
    details = api.get_tmdb_data(f'tv/{tv_id}')
    seasons = details.get('seasons', [])
    if seasons:
        episodes_fetch = api.get_tmdb_data(f'tv/{tv_id}/season/{seasons[0]["season_number"]}')
        if episodes_fetch and episodes_fetch.get('episodes'):
            print(f"✅ Batch TV episodes populated successfully! ({len(episodes_fetch['episodes'])} episodes).")
        else:
            print("❌ TV episode fetch failed.")
            sys.exit(1)
    else:
        print("❌ TV seasons fetch failed.")
        sys.exit(1)

    print("\n✅ All core fetching pathways for Streaming, Downloads, and Batch Downloads passed validation!")

if __name__ == "__main__":
    test_all_flows()
