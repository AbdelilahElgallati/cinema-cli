import requests
from concurrent.futures import ThreadPoolExecutor
from src.config import console
from src.ui.theme import theme
from src.utils.api import create_session_with_retries

class SourceManager:
    def __init__(self, api_client):
        self.api = api_client
        self.session = create_session_with_retries()

    def get_best_source(self, tmdb_id, media_type, season=None, episode=None, preferred_quality="1080p"):
        """
        Fetches sources, sorts by quality logic, and returns the first valid (HEAD OK) source.
        """
        # Fetch sources from backend
        data = self.api.get_sources_api(tmdb_id, media_type, season, episode)
        if not data or not data.get("files"):
            return None, []

        files = data["files"]
        subtitles = data.get("subtitles") or []

        # Sort files based on preference
        # Logic: 
        # 1. Exact match preferred quality
        # 2. Higher quality (normalized)
        # 3. Size (if available)
        
        # Normalize quality strings to comparable integers if possible for sorting
        def qualify_rank(q):
            q = q.lower()
            if "4k" in q or "2160" in q: return 40
            if "1080" in q: return 30
            if "720" in q: return 20
            if "480" in q: return 10
            return 0

        target_rank = qualify_rank(preferred_quality or "1080p")

        # Sort: Primary key is difference from target rank (closer is better?), 
        # actually let's just prioritize: Target > Higher > Lower
        # But commonly users want "Best available" or "At least 1080p".
        # Let's stick to: If pref is Auto/High, sort Descending.
        # If pref is specific (e.g. 720p), put 720p first, then 1080p, then 480p.
        
        preferred_quality = (preferred_quality or "auto").lower()
        
        if preferred_quality in ["auto", "best", "4k"]:
            # Sort generic descending
            files.sort(key=lambda x: qualify_rank(x.get("quality", "")), reverse=True)
        else:
            # Sort by specific match first, then descending
            def sort_key(x):
                r = qualify_rank(x.get("quality", ""))
                match = 1 if r == target_rank else 0
                return (match, r) # Tuples compared element-wise
            
            files.sort(key=sort_key, reverse=True)

        # Validate sources
        # with console.status(f"[{theme.accent}]Testing sources...[/{theme.accent}]", spinner="dots"):
        if True: # Replace with contextless block
            chunk_size = 5
            for i in range(0, len(files), chunk_size):
                chunk = files[i:i+chunk_size]
                results = {}
                with ThreadPoolExecutor(max_workers=chunk_size) as executor:
                    # Only submit if url exists (key is 'file' based on debug)
                    future_to_file = {}
                    for f in chunk:
                        url = f.get("file") or f.get("url") # Support both just in case
                        if url:
                            # Normalize key for consistency
                            f["url"] = url 
                            future_to_file[executor.submit(self._check_source, f)] = f
                    
                    for future in future_to_file:
                        f = future_to_file[future]
                        url = f.get("url")
                        try:
                            is_valid = future.result()
                            results[url] = is_valid
                        except:
                            results[url] = False
                
                # Now check in order of chunk
                for f in chunk:
                    url = f.get("url")
                    if url and results.get(url):
                        # console.print(f"[{theme.success}]Found working source: {f.get('quality')} - {f.get('provider')}[/{theme.success}]")
                        return f, subtitles
        
        # Fallback: If no valid source found, try the first one that has a URL
        if files:
            # console.print(f"[{theme.warning}]No verified sources found. Trying first available source...[/{theme.warning}]")
            for f in files:
                if f.get("file") or f.get("url"):
                    # Ensure url key is set for player
                    f["url"] = f.get("file") or f.get("url")
                    return f, subtitles

        return None, subtitles

    def _check_source(self, file_info):
        url = file_info.get("url") or file_info.get("file")
        headers = file_info.get("headers") or {}
        
        try:
            # HEAD request with short timeout
            # If server doesn't support HEAD, fallback to GET with stream=True
            r = self.session.head(url, headers=headers, timeout=3, allow_redirects=True)
            if r.status_code < 400:
                return True
            if r.status_code in [403, 405]: # Forbidden or Method Not Allowed
                 r = self.session.get(url, headers=headers, stream=True, timeout=3)
                 if r.status_code < 400:
                     r.close()
                     return True
        except Exception:
            pass
        return False
