// TS/Segment proxy function based on the working implementation
import fetch from 'node-fetch';
import https from 'https';
import { DEFAULT_USER_AGENT } from './proxyserver.js';

const agent = new https.Agent({
    rejectUnauthorized: false
});

const shouldDebug = process.argv.includes('--debug');

export async function proxyTs(targetUrl, headers, req, res) {
    const start = Date.now();
    if (shouldDebug) {
        console.log(`[TS Proxy] Incoming request for: ${targetUrl.substring(0, 50)}...`);
    }

    try {
        // Handle range requests for video playback
        const fetchHeaders = {
            'User-Agent': DEFAULT_USER_AGENT,
            ...headers
        };

        // Forward range header if present
        if (req.headers.range) {
            fetchHeaders['Range'] = req.headers.range;
        }

        if (shouldDebug) {
            console.log(`[TS Proxy] Fetching upstream with headers:`, JSON.stringify(fetchHeaders));
        }

        const response = await fetch(targetUrl, {
            headers: fetchHeaders,
            agent
        });

        if (!response.ok) {
            console.error(`[TS Proxy] Upstream failed: ${response.status} ${response.statusText} for ${targetUrl.substring(0, 80)}...`);
            res.writeHead(response.status);
            res.end(`TS fetch failed: ${response.status}`);
            return;
        }

        if (shouldDebug) {
            console.log(`[TS Proxy] Upstream OK (${response.status}). Content-Length: ${response.headers.get('content-length')}`);
        }

        // Set response headers
        const contentType =
            response.headers.get('content-type') || 'video/mp2t';
        res.setHeader('Content-Type', contentType);

        // Forward important headers from upstream
        if (response.headers.get('content-length')) {
            res.setHeader(
                'Content-Length',
                response.headers.get('content-length')
            );
        }
        if (response.headers.get('content-range')) {
            res.setHeader(
                'Content-Range',
                response.headers.get('content-range')
            );
        }
        if (response.headers.get('accept-ranges')) {
            res.setHeader(
                'Accept-Ranges',
                response.headers.get('accept-ranges')
            );
        }

        // Set status code for range requests
        if (response.status === 206) {
            res.writeHead(206);
        } else {
            res.writeHead(200);
        }

        // Stream the response directly
        response.body.pipe(res);

        response.body.on('end', () => {
            if (shouldDebug) {
                console.log(`[TS Proxy] Stream finished in ${Date.now() - start}ms`);
            }
        });

    } catch (error) {
        console.error('[TS Proxy Error]:', error.message, `for ${targetUrl.substring(0, 80)}...`);
        if (!res.headersSent) {
            res.writeHead(500);
            res.end(`TS Proxy error: ${error.message}`);
        }
    }
}
