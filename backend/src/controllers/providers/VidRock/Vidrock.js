import { webcrypto } from 'crypto';
import { languageMap } from '../../../utils/languages.js';
import { ErrorObject } from '../../../helpers/ErrorObject.js';

const DOMAIN = 'https://vidrock.net';
function getPassphrase() {
  const passphrase = (process.env.VIDROCK_PASSPHRASE || '').trim();
  if (!passphrase) {
    throw new Error(
      '[VidRock] VIDROCK_PASSPHRASE environment variable is required but not set.'
    );
  }
  return passphrase;
}
const shouldDebug = () => process.argv.includes('--debug') || process.env.DEBUG === 'true' || process.env.LOG_LEVEL === 'debug';

export async function getVidRock(media) {
  if (shouldDebug()) {
    console.log('[getVidRock] Function called');
    console.log('[getVidRock] Media input:', JSON.stringify(media, null, 2));
  }

  // media should contain: { type, tmdb, season?, episode? }
  const link = await getLink(media);
  if (shouldDebug()) {
    console.log('[getVidRock] Generated link from getLink():', link);
  }

  try {
    const requestHeaders = {
      Accept: 'application/json, text/plain, */*',
      'Accept-Language': 'en-US,en;q=0.9',
      'Cache-Control': 'no-cache',
      Origin: DOMAIN,
      Referer: `${DOMAIN}/movie/${media.tmdb}`,
      'Sec-Fetch-Dest': 'empty',
      'Sec-Fetch-Mode': 'cors',
      'Sec-Fetch-Site': 'same-origin',
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
      'sec-ch-ua-mobile': '?0',
      'sec-ch-ua-platform': '"Windows"',
    };

    if (shouldDebug()) {
      console.log('[getVidRock] Request headers:', JSON.stringify(requestHeaders, null, 2));
      console.log('[getVidRock] Making fetch request to:', link);
    }

    let sources = await fetch(link, {
      headers: requestHeaders,
    });

    if (shouldDebug()) {
      console.log('[getVidRock] Fetch response status:', sources.status);
      console.log('[getVidRock] Fetch response ok:', sources.ok);
    }

    if (!sources.ok) {
      let errorBody = '';
      try {
        errorBody = await sources.text();
      } catch (readError) {
        // ignored
      }

      return new ErrorObject(
        'Failed to scrape sources',
        'Vidrock',
        sources.status,
        `Failed to fetch sources from ${link}. Status: ${sources.status}. Body: ${errorBody.substring(0, 200)}`,
        true,
        true
      );
    }

    const rawResponse = await sources.json();
    if (shouldDebug()) {
      console.log('[getVidRock] Raw response keys:', Object.keys(rawResponse));
      console.log('[getVidRock] Raw response sample:', JSON.stringify(rawResponse).substring(0, 500));
    }

    // Aggressive subtitle extraction: look at root, look for common keys, and look inside source objects
    const subtitles = [];
    const seenUrls = new Set();

    function addSub(s) {
      if (!s || typeof s !== 'object') return;
      const url = s.url || s.file || s.link;
      const lang = s.language || s.lang || s.label || s.code || 'und';
      if (url && typeof url === 'string' && url.startsWith('http') && !seenUrls.has(url)) {
        seenUrls.add(url);
        subtitles.push({
          url: url,
          lang: languageMap[lang] || lang,
          label: lang,
          type: (() => {
            try {
              const ext = new URL(url).pathname.split('.').pop().toLowerCase();
              return ['srt', 'vtt', 'ass', 'ssa'].includes(ext) ? ext : 'srt';
            } catch {
              return 'srt';
            }
          })(),
        });
      }
    }

    // 1. Check root level keys
    const rootSubs = rawResponse.subtitles || rawResponse.tracks || rawResponse.subs || [];
    if (Array.isArray(rootSubs)) rootSubs.forEach(addSub);

    // 2. Check if the entire response is an array of sources, and look for subtitles inside them
    const rootValues = Object.values(rawResponse);
    rootValues.forEach(val => {
      if (val && typeof val === 'object') {
        // If this value is a source object with its own subtitles array
        if (Array.isArray(val.subtitles)) val.subtitles.forEach(addSub);
        if (Array.isArray(val.tracks)) val.tracks.forEach(addSub);
        // Or if this value IS a subtitle object itself (root level array of mixed content)
        if (val.url && (val.language || val.lang || val.label)) addSub(val);
      }
    });

    if (shouldDebug()) {
      process.stderr.write(`[VidRock] subtitles count: ${subtitles.length}\n`);
      if (subtitles.length > 0) {
        process.stderr.write(`[VidRock] first: ${JSON.stringify(subtitles[0])}\n`);
      }
    }

    const rawSources = Array.isArray(rawResponse)
      ? rawResponse
      : Object.entries(rawResponse).map(([key, val]) => {
          if (val && typeof val === 'object') {
            val.quality = val.quality || key;
            return val;
          } else if (typeof val === 'string') {
            return {
              url: val,
              quality: key
            };
          }
          return null;
        }).filter(Boolean);

    const formattedSources = rawSources
      .filter((source) => source && (source.url || source.file) && typeof (source.url || source.file) === 'string')
      .map((source) => {
        let url = source.url || source.file;
        let quality = source.quality || 'unknown';

        if (url.includes('.m3u8')) {
          // Transform variant HLS to master playlist URL to ensure audio availability.
          const masterUrl = url.replace(/\/(\d+)\/playlist\.m3u8$/, '/playlist.m3u8');
          if (masterUrl !== url) {
            url = masterUrl;
            if (quality === 'unknown') {
              const qMatch = source.url.match(/\/(\d+)\/playlist\.m3u8$/);
              if (qMatch) quality = qMatch[1] + 'p';
            }
          }
        }

        return {
          file: url,
          quality: quality,
          type: url.includes('.m3u8') ? 'hls' : (url.includes('.mp4') ? 'mp4' : 'unknown'),
          lang: languageMap[source.language] || source.language,
          headers: {
            Referer: media.type === 'tv' 
              ? `${DOMAIN}/tv/${media.tmdb}/${media.season}/${media.episode}`
              : `${DOMAIN}/movie/${media.tmdb}`,
            Origin: DOMAIN,
            'User-Agent':
              'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
          },
        };
      });

    if (formattedSources.length === 0) {
      if (subtitles.length > 0) {
        return {
          files: [],
          subtitles: subtitles,
        };
      }
      return new ErrorObject(
        'No valid sources found',
        'Vidrock',
        404,
        'The API returned sources, but none were valid. Check the source URLs or API response.',
        true,
        true
      );
    }

    return {
      files: formattedSources,
      subtitles: subtitles,
    };
  } catch (error) {
    return new ErrorObject(
      `Unexpected error: ${error.message}`,
      'Vidrock',
      500,
      'Check the implementation or server status.',
      true,
      true
    );
  }
}

/**
 * Encrypt item ID using AES-CBC with fixed passphrase
 */
async function encryptItemId(itemId) {
  try {
    const textEncoder = new TextEncoder();
    const passphraseBytes = textEncoder.encode(getPassphrase());

    let keyData = passphraseBytes;
    if (![16, 24, 32].includes(keyData.length)) {
      // Accept arbitrary passphrase lengths by deriving a 32-byte AES key.
      const digest = await webcrypto.subtle.digest('SHA-256', passphraseBytes);
      keyData = new Uint8Array(digest);
      if (shouldDebug()) {
        console.warn('[VidRock] VIDROCK_PASSPHRASE length is non-standard; using SHA-256 derived key.');
      }
    }

    let iv = passphraseBytes.slice(0, 16);
    if (iv.length < 16) {
      const paddedIv = new Uint8Array(16);
      paddedIv.set(iv);
      iv = paddedIv;
    }

    const key = await webcrypto.subtle.importKey('raw', keyData, { name: 'AES-CBC' }, false, [
      'encrypt',
    ]);

    const itemIdBytes = textEncoder.encode(itemId);
    const paddingLength = 16 - (itemIdBytes.length % 16);
    const paddedData = new Uint8Array(itemIdBytes.length + paddingLength);
    paddedData.set(itemIdBytes);
    paddedData.fill(paddingLength, itemIdBytes.length);

    const encrypted = await webcrypto.subtle.encrypt({ name: 'AES-CBC', iv: iv }, key, paddedData);

    const encryptedArray = new Uint8Array(encrypted);
    const binaryString = String.fromCharCode(...encryptedArray);
    const base64 = Buffer.from(binaryString, 'binary').toString('base64');

    return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  } catch (error) {
    console.error('[encryptItemId] Encryption error:', error);
    throw error;
  }
}

async function getLink(media) {
  if (shouldDebug()) {
    console.log('[getLink] Starting link generation');
  }

  let itemId;
  let itemType;

  if (media.type === 'tv') {
    itemId = `${media.tmdb}_${media.season}_${media.episode}`;
    itemType = 'tv';
  } else {
    itemId = media.tmdb.toString();
    itemType = 'movie';
  }

  const encrypted = await encryptItemId(itemId);
  return `${DOMAIN}/api/${itemType}/${encrypted}`;
}
