// M3U8 proxy function based on the working implementation
import fetch from 'node-fetch';
import https from 'https';
import { DEFAULT_USER_AGENT } from './proxyserver.js';
import { isAllowedStreamingUrl } from '../helpers/helper.js';

const agent = new https.Agent({
  rejectUnauthorized: false,
});

const shouldDebug = process.argv.includes('--debug');

const AbortController = globalThis.AbortController;

export async function proxyM3U8(targetUrl, headers, res, serverUrl) {
  if (!targetUrl || !isAllowedStreamingUrl(targetUrl)) {
    res.writeHead(403, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Forbidden: URL not in streaming allowlist.' }));
    return;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000);

  if (shouldDebug) {
    console.log(`[M3U8 Proxy] Fetching: ${targetUrl}`);
  }
  try {
    const response = await fetch(targetUrl, {
      headers: {
        'User-Agent': DEFAULT_USER_AGENT,
        ...headers,
      },
      agent,
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      console.error(
        `[M3U8 Proxy] Upstream failed: ${response.status} ${response.statusText} for ${targetUrl.substring(0, 80)}...`
      );
      res.writeHead(response.status);
      res.end(`M3U8 fetch failed: ${response.status}`);
      return;
    }

    const m3u8Content = await response.text();
    if (shouldDebug) {
      console.log(`[M3U8 Proxy] Upstream Content Preview:\n${m3u8Content.substring(0, 300)}`);
    }

    // Use Base64 encoding for headers to avoid shell/argument mangling in players
    const encodedHeaders = Buffer.from(JSON.stringify(headers)).toString('base64');

    // Process M3U8 content line by line - key difference from our previous implementation
    const processedLines = m3u8Content.split('\n').map((line) => {
      line = line.trim();

      // Skip empty lines and comments (except special ones)
      if (!line || (line.startsWith('#') && !line.includes('URI='))) {
        return line;
      }

      // Handle URI in #EXT-X-MEDIA tags (for audio/subtitle tracks)
      if (line.startsWith('#EXT-X-MEDIA:') && line.includes('URI=')) {
        const uriMatch = line.match(/URI="([^"]+)"/);
        if (uriMatch) {
          const mediaUrl = new URL(uriMatch[1], targetUrl).href;
          const proxyUrl = `${serverUrl}/m3u8-proxy?url=${encodeURIComponent(mediaUrl)}&headers=${encodedHeaders}`;
          return line.replace(`URI="${uriMatch[1]}"`, `URI="${proxyUrl}"`);
        }
        return line;
      }

      // Handle #EXT-X-MAP (fMP4 init segments)
      if (line.startsWith('#EXT-X-MAP:') && line.includes('URI=')) {
        const uriMatch = line.match(/URI="([^"]+)"/);
        if (uriMatch) {
          const mapUrl = new URL(uriMatch[1], targetUrl).href;
          const proxyUrl = `${serverUrl}/ts-proxy?url=${encodeURIComponent(mapUrl)}&headers=${encodedHeaders}`;
          return line.replace(`URI="${uriMatch[1]}"`, `URI="${proxyUrl}"`);
        }
        return line;
      }

      // Handle encryption keys
      if (line.startsWith('#EXT-X-KEY:') && line.includes('URI=')) {
        const uriMatch = line.match(/URI="([^"]+)"/);
        if (uriMatch) {
          const keyUrl = new URL(uriMatch[1], targetUrl).href;
          const proxyUrl = `${serverUrl}/ts-proxy?url=${encodeURIComponent(keyUrl)}&headers=${encodedHeaders}`;
          return line.replace(`URI="${uriMatch[1]}"`, `URI="${proxyUrl}"`);
        }
        return line;
      }

      // Handle #EXT-X-I-FRAME-STREAM-INF URI
      if (line.startsWith('#EXT-X-I-FRAME-STREAM-INF:') && line.includes('URI=')) {
        const uriMatch = line.match(/URI="([^"]+)"/);
        if (uriMatch) {
          const iframeUrl = new URL(uriMatch[1], targetUrl).href;
          const proxyUrl = `${serverUrl}/m3u8-proxy?url=${encodeURIComponent(iframeUrl)}&headers=${encodedHeaders}`;
          return line.replace(`URI="${uriMatch[1]}"`, `URI="${proxyUrl}"`);
        }
        return line;
      }

      // Handle segment URLs (non-comment lines)
      if (!line.startsWith('#')) {
        try {
          const segmentUrl = new URL(line, targetUrl).href;

          // Check if it's another m3u8 file (master playlist)
          if (line.includes('.m3u8') || line.includes('m3u8')) {
            return `${serverUrl}/m3u8-proxy?url=${encodeURIComponent(segmentUrl)}&headers=${encodedHeaders}`;
          } else {
            // It's a media segment
            return `${serverUrl}/ts-proxy?url=${encodeURIComponent(segmentUrl)}&headers=${encodedHeaders}`;
          }
        } catch (e) {
          return line; // Return original if URL parsing fails
        }
      }

      return line;
    });

    const processedContent = processedLines.join('\n');
    if (shouldDebug) {
      console.log(`[M3U8 Proxy] Processed Content Preview:\n${processedContent.substring(0, 300)}`);
    }

    // Set proper headers
    res.setHeader('Content-Type', 'application/vnd.apple.mpegurl');
    res.setHeader('Content-Length', Buffer.byteLength(processedContent));
    res.setHeader('Cache-Control', 'no-cache');

    res.writeHead(200);
    res.end(processedContent);
  } catch (error) {
    clearTimeout(timeoutId);
    console.error('[M3U8 Proxy Error]:', error.message, `for ${targetUrl.substring(0, 80)}...`);
    if (error.name === 'AbortError') {
      res.writeHead(504, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Upstream timeout.' }));
    } else {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Proxy fetch failed.' }));
    }
  }
}
