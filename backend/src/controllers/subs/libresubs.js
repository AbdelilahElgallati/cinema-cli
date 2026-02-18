const WANTED_LANGS = ['ar', 'en', 'fr', 'es', 'de', 'tr', 'pt', 'it'];

export async function getLibre(media) {
  const BASE = `https://libre-subs.fifthwit.net/search?id=${media.tmdb}`;
  const suffix = media.type === 'movie' ? '' : `&season=${media.season}&episode=${media.episode}`;

  const results = await Promise.allSettled(
    WANTED_LANGS.map((lang) => {
      const url = `${BASE}${suffix}&lang=${lang}`;
      return fetch(url).then((r) => (r.ok ? r.json() : []));
    })
  );

  const seen = new Set();
  const subtitles = [];
  for (const result of results) {
    if (result.status !== 'fulfilled') continue;
    for (const sub of Array.isArray(result.value) ? result.value : []) {
      if (!sub.url || seen.has(sub.url)) continue;
      seen.add(sub.url);
      subtitles.push({ url: sub.url, lang: sub.language, type: sub.format });
    }
  }

  return { files: [], subtitles };
}
