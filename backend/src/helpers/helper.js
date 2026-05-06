import dns from 'dns';
import ipaddr from 'ipaddr.js';

/**
 * @description Check if the given text could be a valid TMDB ID.
 * @param text {string} The text to check.
 * @returns {boolean} True if the text could be a valid TMDB ID, false otherwise.
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

function isPrivateOrLocalAddress(address) {
  if (!ipaddr.isValid(address)) {
    return true;
  }

  const parsed = ipaddr.process(address);

  if (parsed.kind() === 'ipv4') {
    return (
      parsed.match(ipaddr.parseCIDR('127.0.0.0/8')) ||
      parsed.match(ipaddr.parseCIDR('10.0.0.0/8')) ||
      parsed.match(ipaddr.parseCIDR('172.16.0.0/12')) ||
      parsed.match(ipaddr.parseCIDR('192.168.0.0/16')) ||
      parsed.match(ipaddr.parseCIDR('169.254.0.0/16'))
    );
  }

  return (
    parsed.match(ipaddr.parseCIDR('::1/128')) ||
    parsed.match(ipaddr.parseCIDR('fc00::/7'))
  );
}

export async function isAllowedStreamingUrl(urlString) {
  const url = new URL(urlString);
  const { address } = await dns.promises.lookup(url.hostname);

  if (isPrivateOrLocalAddress(address)) {
    return false;
  }

  const pathname = url.pathname.toLowerCase();
  return pathname.endsWith('.m3u8') || pathname.endsWith('.ts');
}
