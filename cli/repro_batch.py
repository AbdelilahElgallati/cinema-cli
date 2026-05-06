import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure imports work from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.api import APIClient
from src.config import TMDB_API_KEY, BACKEND_URL, console

def repro():
    # The Boys (ID: 71712)
    tmdb_id = 71712 
    season = 1
    episodes = [1, 2, 3, 4]
    
    api = APIClient({"backend": BACKEND_URL, "tmdb_key": TMDB_API_KEY})
    
    print(f"Testing parallel fetch for {len(episodes)} episodes...")
    
    results = {}
    
    def fetch_one(ep_num):
        print(f"Starting fetch for E{ep_num}...")
        try:
            # force_refresh=True to simulate the batch download triggering fresh scrapes
            data = api.get_sources_enhanced(tmdb_id, "tv", season, ep_num, min_sources=2, quiet=True)
            files = data.get("files", [])
            print(f"Finished fetch for E{ep_num}: found {len(files)} files.")
            return ep_num, len(files)
        except Exception as e:
            print(f"Error for E{ep_num}: {e}")
            return ep_num, 0

    # Simulate MAX_CONCURRENT_SOURCE_FETCHES = 3
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_one, ep): ep for ep in episodes}
        for future in as_completed(futures):
            ep_num, count = future.result()
            results[ep_num] = count

    print("\nSummary:")
    for ep in episodes:
        status = "✅" if results.get(ep, 0) > 0 else "❌"
        print(f"  E{ep}: {status} ({results.get(ep, 0)} sources)")

if __name__ == "__main__":
    repro()
