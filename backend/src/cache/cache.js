const DEFAULT_TTL_SECONDS = 600;
const DEFAULT_MAX_ENTRIES = 200;

class LruTtlCache {
  constructor({ maxEntries = DEFAULT_MAX_ENTRIES, ttlSeconds = DEFAULT_TTL_SECONDS } = {}) {
    this.maxEntries = maxEntries;
    this.ttlMs = ttlSeconds * 1000;
    this.store = new Map();
    this.hits = 0;
    this.misses = 0;
    this.keysDeleted = 0;
    this.keysExpired = 0;
  }

  _isExpired(entry) {
    return !entry || entry.expiresAt <= Date.now();
  }

  _pruneExpired() {
    const now = Date.now();
    for (const [key, entry] of this.store.entries()) {
      if (entry.expiresAt <= now) {
        this.store.delete(key);
        this.keysExpired += 1;
      }
    }
  }

  _evictIfNeeded() {
    while (this.store.size > this.maxEntries) {
      const oldestKey = this.store.keys().next().value;
      if (oldestKey === undefined) break;
      this.store.delete(oldestKey);
      this.keysDeleted += 1;
    }
  }

  get(key) {
    const entry = this.store.get(key);
    if (!entry) {
      this.misses += 1;
      return undefined;
    }
    if (this._isExpired(entry)) {
      this.store.delete(key);
      this.keysExpired += 1;
      this.misses += 1;
      return undefined;
    }

    this.store.delete(key);
    this.store.set(key, entry);
    this.hits += 1;
    return entry.value;
  }

  set(key, value) {
    const expiresAt = Date.now() + this.ttlMs;
    if (this.store.has(key)) {
      this.store.delete(key);
    }
    this.store.set(key, { value, expiresAt });
    this._pruneExpired();
    this._evictIfNeeded();
    return true;
  }

  has(key) {
    const entry = this.store.get(key);
    if (!entry) return false;
    if (this._isExpired(entry)) {
      this.store.delete(key);
      this.keysExpired += 1;
      return false;
    }
    return true;
  }

  del(key) {
    if (this.store.delete(key)) {
      this.keysDeleted += 1;
      return 1;
    }
    return 0;
  }

  getStats() {
    this._pruneExpired();
    return {
      keys: this.store.size,
      hits: this.hits,
      misses: this.misses,
      ksize: 0,
      vsize: 0,
      maxEntries: this.maxEntries,
      ttlSeconds: Math.floor(this.ttlMs / 1000),
      keysDeleted: this.keysDeleted,
      keysExpired: this.keysExpired,
    };
  }
}

export const cache = new LruTtlCache({
  maxEntries: Number(process.env.CACHE_MAX_ENTRIES || DEFAULT_MAX_ENTRIES),
  ttlSeconds: Number(process.env.CACHE_TTL_SECONDS || DEFAULT_TTL_SECONDS),
});

export function getCacheKey(media) {
  if (media.type === 'tv') {
    return `${media.type}_${media.tmdb}_${media.season}_${media.episode}`;
  }
  return `${media.type}_${media.tmdb}`;
}

export function getFromCache(key) {
  return cache.get(key);
}

export function setToCache(key, data) {
  return cache.set(key, data);
}

export function getCacheStats() {
  return cache.getStats();
}
