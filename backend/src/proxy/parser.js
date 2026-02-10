import { VIDSRC_HLS_ORIGIN } from '../controllers/providers/VidSrc/VidSrc.js';

export function getOriginFromUrl(url) {
  try {
    const urlObj = new URL(url);
    const origin = urlObj.origin;
    if (origin.includes(VIDSRC_HLS_ORIGIN)) {
      return undefined;
    }
    return origin;
  } catch {
    return undefined;
  }
}

function extractOriginalUrl(proxyUrl) {
  try {
    const url = new URL(proxyUrl);

    // Pattern 1: /proxy/encodedUrl (like hls1.vid1.site/proxy/...  
    if (url.pathname.includes('/proxy/')) {
      const proxyMatch = url.pathname.match(/\/proxy\/(.+)$/);
      if (proxyMatch) {
        let decoded = decodeURIComponent(proxyMatch[1]);
        while (decoded.includes('%2F')) {
          try {
            decoded = decodeURIComponent(decoded);
          } catch {
            break;
          }
        }
        // Validate extracted URL
        try {
          new URL(decoded);
          return decoded;
        } catch {
          // Extracted URL is invalid, return original
          return proxyUrl;
        }
      }
    }

    // for patterns like ?url=encodedUrl (like madplay.site/api/holly/proxy?url=...)
    if (url.searchParams.has('url')) {
      const extracted = decodeURIComponent(url.searchParams.get('url'));
      // Validate extracted URL
      try {
        new URL(extracted);
        return extracted;
      } catch {
        // Extracted URL is invalid, return original
        return proxyUrl;
      }
    }

    // Pattern 3: Other common proxy patterns using regex
    const commonProxyPatterns = [
      /\/api\/[^\/]+\/proxy\?url=(.+)$/, // /api/*/proxy?url=
      /\/proxy\?.*url=([^&]+)/, // /proxy?url= (with other params)
      /\/stream\/proxy\/(.+)$/, // /stream/proxy/
      /\/p\/(.+)$/, // Short proxy like /p/
    ];

    for (const pattern of commonProxyPatterns) {
      const match = proxyUrl.match(pattern);
      if (match) {
        const extracted = decodeURIComponent(match[1]);
        // Validate extracted URL
        try {
          new URL(extracted);
          return extracted;
        } catch {
          // Extracted URL is invalid, continue to next pattern
          continue;
        }
      }
    }

    return proxyUrl; // Return as-is if no proxy pattern found
  } catch {
    return proxyUrl;
  }
}

export { extractOriginalUrl };
