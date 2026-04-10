import express from 'express';
import { scrapeMedia } from './src/api.js';
import { createProxyRoutes, processApiResponse } from './src/proxy/proxyserver.js';
import { getMovieFromTmdb, getTvFromTmdb } from './src/helpers/tmdb.js';
import cors from 'cors';
import { strings } from './src/strings.js';
import { checkIfPossibleTmdbId, handleErrorResponse } from './src/helpers/helper.js';
import { ErrorObject } from './src/helpers/ErrorObject.js';
import { getCacheStats, getCacheKey, cache } from './src/cache/cache.js';
import { startup } from './src/utils/startup.js';
import { fileURLToPath } from 'url';
import crypto from 'crypto';

// Derive PORT from BACKEND_URL when not set explicitly
if (!process.env.PORT && process.env.BACKEND_URL) {
  try {
    const _u = new URL(process.env.BACKEND_URL);
    process.env.PORT = _u.port || (_u.protocol === 'https:' ? '443' : '80');
  } catch (_) {
    process.env.PORT = '3010';
  }
}
const PORT = process.env.PORT || 3010;

const app = express();

function getCorrelationId(req) {
  const incoming = req.headers['x-correlation-id'];
  if (typeof incoming === 'string' && incoming.trim()) return incoming.trim();
  return `backend-${crypto.randomBytes(6).toString('hex')}`;
}

// Robust CORS for all clients including CLI players
app.use(cors({
  origin: '*',
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization', 'X-Client-Type', 'X-Correlation-Id', 'X-Bypass-Cache', 'Range'],
  exposedHeaders: ['Content-Range', 'X-Correlation-Id'],
  maxAge: 86400
}));

createProxyRoutes(app);

// Add a helper to clear cache entry
function clearCacheEntry(media) {
  const key = getCacheKey(media);
  if (cache.has(key)) {
    cache.del(key);
  }
}

app.get('/', (req, res) => {
  res.status(200).json({
    home: strings.HOME_NAME,
    routes: strings.ROUTES,
    information: strings.INFORMATION,
    license: strings.LICENSE,
    source: strings.SOURCE,
  });
});

app.get('/movie/:tmdbId', async (req, res) => {
  const correlationId = getCorrelationId(req);
  res.setHeader('X-Correlation-Id', correlationId);

  if (!checkIfPossibleTmdbId(req.params.tmdbId)) {
    return handleErrorResponse(
      res,
      new ErrorObject(
        strings.INVALID_MOVIE_ID,
        'user',
        405,
        strings.INVALID_MOVIE_ID_HINT,
        true,
        false
      )
    );
  }

  const media = await getMovieFromTmdb(req.params.tmdbId);
  if (media instanceof ErrorObject) {
    return handleErrorResponse(res, media);
  }

  const forceRefresh =
    req.query.force_refresh === '1' ||
    req.query.force_refresh === 'true' ||
    req.query.refresh === '1' ||
    req.query.refresh === 'true' ||
    req.headers['x-bypass-cache'] === '1' ||
    req.headers['x-bypass-cache'] === 'true';

  if (forceRefresh) clearCacheEntry(media);

  const output = await scrapeMedia(media, { forceRefresh, correlationId });
  if (output instanceof ErrorObject) {
    return handleErrorResponse(res, output);
  }
  const processedOutput = processApiResponse(output, `${req.protocol}://${req.get('host')}`, req);

  res.status(200).json(processedOutput);
});

app.get('/tv/:tmdbId', async (req, res) => {
  const correlationId = getCorrelationId(req);
  res.setHeader('X-Correlation-Id', correlationId);

  if (
    !checkIfPossibleTmdbId(req.params.tmdbId) ||
    !checkIfPossibleTmdbId(req.query.s) ||
    !checkIfPossibleTmdbId(req.query.e)
  ) {
    return handleErrorResponse(
      res,
      new ErrorObject(strings.INVALID_TV_ID, 'user', 405, strings.INVALID_TV_ID_HINT, true, false)
    );
  }

  const media = await getTvFromTmdb(req.params.tmdbId, req.query.s, req.query.e);
  if (media instanceof ErrorObject) {
    return handleErrorResponse(res, media);
  }

  const forceRefresh =
    req.query.force_refresh === '1' ||
    req.query.force_refresh === 'true' ||
    req.query.refresh === '1' ||
    req.query.refresh === 'true' ||
    req.headers['x-bypass-cache'] === '1' ||
    req.headers['x-bypass-cache'] === 'true';

  if (forceRefresh) clearCacheEntry(media);

  const output = await scrapeMedia(media, { forceRefresh, correlationId });
  if (output instanceof ErrorObject) {
    return handleErrorResponse(res, output);
  }
  const processedOutput = processApiResponse(output, `${req.protocol}://${req.get('host')}`, req);

  res.status(200).json(processedOutput);
});

app.get('/movie/', (req, res) => {
  handleErrorResponse(
    res,
    new ErrorObject(
      strings.INVALID_MOVIE_ID,
      'user',
      405,
      strings.INVALID_MOVIE_ID_HINT,
      true,
      false
    )
  );
});

app.get('/tv/', (req, res) => {
  handleErrorResponse(
    res,
    new ErrorObject(strings.INVALID_TV_ID, 'user', 405, strings.INVALID_TV_ID_HINT, true, false)
  );
});

// Endpoint to flex how well our cache is doing - because who doesn't love stats
// Hell Yeah we love it, Because STONE COLD SAID SOOOOO
app.get('/cache-stats', (req, res) => {
  const stats = getCacheStats();
  res.status(200).json({
    ...stats,
    cacheEnabled: true,
    ttl: '3 hours (10800 seconds)',
  });
});

// Health check endpoint for CLI startup probe
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'ok', version: '1.0.2' });
});

app.get('/ping', (req, res) => {
  res.status(200).send('pong');
});

// 404 Handler - Catch all unmatched routes
app.use((req, res) => {
  handleErrorResponse(
    res,
    new ErrorObject(strings.ROUTE_NOT_FOUND, 'user', 404, strings.ROUTE_NOT_FOUND_HINT, true, false)
  );
});
const isMain = process.argv[1] === fileURLToPath(import.meta.url);

if (isMain) {
  startup();
  app.listen(PORT, '127.0.0.1', () => {
    console.log(`Server is running on http://127.0.0.1:${PORT}`);
    if (process.argv.includes('--debug')) {
      console.log(`Debug mode is enabled.`);
      console.log('Cache is disabled.');
    } else {
      console.log('Debug mode is disabled.');
      console.log('Cache is enabled.');
    }
  });
}

export default app;
