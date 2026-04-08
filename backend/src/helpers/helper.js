/**
 * @description Check if the given text could be a valid TMDB ID.
 * @param text {string} The text to check.
 * @returns {boolean} True if the text could be a valid TMDB ID, false otherwise.
 *
 * @example
 * // checkIfPossibleTmdbId("155"); // true
 * // checkIfPossibleTmdbId("1234567890abc"); // false
 */
export function checkIfPossibleTmdbId(text) {
  let regex = /^[0-9]+$/;
  return regex.test(text);
}

/**
 * @description Handle error response.
 * @param res {Response} The response object.
 * @param errorObject {ErrorObject} The error object to handle.
 */
export function handleErrorResponse(res, errorObject) {
  res.status(errorObject._responseCode).json(errorObject.toJSON());
}

/**
 * @description Validates if a URL is a known and allowed streaming CDN domain.
 * Blocks all private/loopback IP addresses to prevent SSRF.
 */
const ALLOWED_STREAMING_DOMAINS = [
  /vidsrc\.xyz/, /vidsrc\.to/, /vidsrc\.me/, /vidsrc\.cc/, /vidsrc\.pm/,
  /vidplay\.online/, /filemoon\.sx/, /streamtape\.com/, /mixdrop\.co/,
  /doodstream\.com/, /embed\.su/, /multiembed\.mov/, /rive\.one/,
  /\.m3u8(\?|$)/, /\.ts(\?|$)/, /akamaized\.net/, /cloudfront\.net/,
  /fastly\.net/, /cdn\d*\./, /stream\d*\./,
];

export function isAllowedStreamingUrl(urlString) {
  try {
    const url = new URL(urlString);
    const hostname = url.hostname;
    // Block internal/private IPs absolutely
    if (
      hostname === 'localhost' ||
      hostname === '127.0.0.1' ||
      hostname.startsWith('192.168.') ||
      hostname.startsWith('10.') ||
      hostname.startsWith('172.16.') ||
      hostname === '0.0.0.0' ||
      hostname === '::1'
    ) {
      return false;
    }
    return ALLOWED_STREAMING_DOMAINS.some((pattern) => pattern.test(urlString));
  } catch {
    return false;
  }
}
