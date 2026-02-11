import time
import json
import os
import hashlib
from functools import wraps

CACHE_DIR = os.path.join(os.getcwd(), ".data", "api_cache")
TTL = 300  # 5 minutes

class SimpleCache:
    def __init__(self):
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR, exist_ok=True)

    def _get_cache_key(self, func_name, args, kwargs):
        key_str = f"{func_name}:{args}:{kwargs}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, key):
        path = os.path.join(CACHE_DIR, f"{key}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if time.time() - data["timestamp"] < TTL:
                        return data["value"]
            except:
                pass
        return None

    def set(self, key, value):
        path = os.path.join(CACHE_DIR, f"{key}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "value": value}, f)
        except:
            pass

    def clear(self):
        # Cleanup old files
        now = time.time()
        for f in os.listdir(CACHE_DIR):
            path = os.path.join(CACHE_DIR, f)
            try:
                if now - os.path.getmtime(path) > TTL:
                    os.remove(path)
            except:
                pass

cache = SimpleCache()

def cached_api_call(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # Check if DISABLE_CACHE is set
        if os.getenv("DISABLE_CACHE") == "true":
            return func(self, *args, **kwargs)
            
        key = cache._get_cache_key(func.__name__, args, kwargs)
        cached_val = cache.get(key)
        if cached_val:
            return cached_val
        
        result = func(self, *args, **kwargs)
        if result:
            cache.set(key, result)
        return result
    return wrapper
