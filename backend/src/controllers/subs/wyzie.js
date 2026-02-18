import { searchSubtitles } from 'wyzie-lib';

// Languages to fetch — covers the most common user requests.
// wyzie-lib returns results for all of these in a single call when no language filter is set.
const WANTED_LANGS = ['ar', 'en', 'fr', 'es', 'de', 'tr', 'pt', 'it'];

export async function getWyzie(media) {
  // Fetch each language independently so missing ones don't block others.
  const results = await Promise.allSettled(
    WANTED_LANGS.map((lang) =>
      searchSubtitles({
        tmdb_id: media.tmdb,
        imdb_id: media.imdb,
        season: media.season,
        episode: media.episode,
        title: media.title,
        year: media.year,
        language: lang,
      })
    )
  );

  // Deduplicate by URL
  const seen = new Set();
  const subtitles = [];
  for (const result of results) {
    if (result.status !== 'fulfilled') continue;
    for (const sub of result.value) {
      if (!sub.url || seen.has(sub.url)) continue;
      seen.add(sub.url);
      subtitles.push({ url: sub.url, lang: sub.language, type: sub.format });
    }
  }

  return { files: [], subtitles };
}
