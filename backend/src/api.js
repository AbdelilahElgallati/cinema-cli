import { getTwoEmbed } from './controllers/providers/2Embed/2embed.js';
import { getAutoembed } from './controllers/providers/AutoEmbed/autoembed.js';
import { getPrimewire } from './controllers/providers/PrimeWire/primewire.js';
import { getVidSrcCC } from './controllers/providers/VidSrcCC/vidsrccc.js';
import { getVidSrc } from './controllers/providers/VidSrc/VidSrc.js';
import { getVidRock } from './controllers/providers/VidRock/Vidrock.js';
import { getXprime } from './controllers/providers/xprime/xprime.js';
import { ErrorObject } from './helpers/ErrorObject.js';
import { getVidsrcWtf } from './controllers/providers/VidSrcWtf/VidSrcWtf.js';
import { getVidZee } from './controllers/providers/VidZee/VidZee.js';
import { getWyzie } from './controllers/subs/wyzie.js';
import { getLibre } from './controllers/subs/libresubs.js';
import { getCacheKey, getFromCache, setToCache } from './cache/cache.js';
import { getMultiembed } from './controllers/providers/MultiEmbed/MultiEmbed.js';
import { getEmbedsu } from './controllers/providers/EmbedSu/embedsu.js';
import { getFebbox } from './controllers/subs/febbox.js';
import { validateSources } from './utils/sourceValidator.js';
import { runSourcePipeline } from './utils/sourcePipeline.js';
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

const shouldDebug = process.argv.includes('--debug');
const PROVIDER_TIMEOUTS_MS = {
  getAutoembed: 8000,
  getTwoEmbed: 8000,
  getVidSrcCC: 8000,
  getVidSrc: 12000,
  getMultiembed: 8000,
  getEmbedsu: 8000,
  getFebbox: 8000,
};
const GLOBAL_SCRAPE_TIMEOUT_MS = 45000;
const TIER1_TIMEOUT_MS = 15000;
const TIER2_TIMEOUT_MS = 30000;
const PROVIDER_JITTER_MAX_MS = 200;
import os from 'os';

function getCacheDir() {
  if (process.platform === 'win32') return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'CinemaCLI', 'cache');
  if (process.platform === 'darwin') return path.join(os.homedir(), 'Library', 'Application Support', 'CinemaCLI', 'cache');
  return path.join(os.homedir(), '.local', 'share', 'cinema-cli', 'cache');
}

const PROVIDER_STATS_PATH = path.join(getCacheDir(), 'provider_stats.json');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function loadProviderStats() {
  try {
    const raw = await fs.readFile(PROVIDER_STATS_PATH, 'utf-8');
    const data = JSON.parse(raw);
    return data?.providers && typeof data.providers === 'object' ? data.providers : {};
  } catch {
    return {};
  }
}

function providerSuccessRate(providerName, stats) {
  const entry =
    stats[providerName] ||
    stats[providerName?.toLowerCase?.()] ||
    stats[providerName?.toUpperCase?.()] ||
    null;

  if (!entry || typeof entry !== 'object') return 0;

  const success = Number(entry.success || 0);
  const noVideo = Number(entry.no_video || 0);
  const timeout = Number(entry.timeout || 0);
  const total = success + noVideo + timeout;
  if (total <= 0) return 0;
  return success / total;
}

function splitProviderTiers(providers, stats) {
  const ranked = providers
    .map((provider) => {
      const name = Object.keys(provider)[0];
      return {
        provider,
        name,
        score: providerSuccessRate(name, stats),
      };
    })
    .sort((a, b) => b.score - a.score);

  const tier1 = ranked.slice(0, 3).map((item) => item.provider);
  const tier2 = ranked.slice(3).map((item) => item.provider);
  return { tier1, tier2, ranked };
}

async function invokeProvider(provider, media) {
  const providerName = Object.keys(provider)[0];
  const providerTimeoutMs = PROVIDER_TIMEOUTS_MS[providerName] || 30000;
  const timeout = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`Timeout after ${providerTimeoutMs}ms`)), providerTimeoutMs)
  );

  try {
    await sleep(Math.floor(Math.random() * PROVIDER_JITTER_MAX_MS));
    const data = await Promise.race([provider[providerName](media), timeout]);
    if (data && shouldDebug) {
      console.log(
        `[${providerName}] Succeeded. Files: ${data.files?.length || 0}, Subtitles: ${data.subtitles?.length || 0}`
      );
    }
    if (!data && shouldDebug) {
      console.log(`[${providerName}] Returned null or empty data`);
    }
    return { data, provider: providerName };
  } catch (e) {
    if (shouldDebug) console.error(`[${providerName}] Failed: ${e.message}`);
    return { data: null, provider: providerName };
  }
}

async function runTieredBatch(providers, media, tierLabel, tierTimeoutMs, globalDeadlineMs) {
  if (!providers.length) return { results: [], timedOut: false };
  const remainingMs = Math.max(1, globalDeadlineMs - Date.now());
  const effectiveTimeoutMs = Math.max(1, Math.min(tierTimeoutMs, remainingMs));

  const tierTimeout = new Promise((resolve) =>
    setTimeout(() => resolve({ results: [], timedOut: true }), effectiveTimeoutMs)
  );
  const tierRun = Promise.all(providers.map((provider) => invokeProvider(provider, media))).then((results) => ({
    results,
    timedOut: false,
  }));
  const out = await Promise.race([tierRun, tierTimeout]);

  if (shouldDebug) {
    console.log(`[Scrape] ${tierLabel} complete timedOut=${out.timedOut} providers=${providers.length}`);
  }

  return out;
}

export async function scrapeMedia(media, options = {}) {
  // First thing - check if we already have this data cached (unless you're debugging and want fresh data)
  const cacheKey = getCacheKey(media);
  const bypassCache = shouldDebug || options.forceRefresh === true;

  if (!bypassCache) {
    const cachedResult = getFromCache(cacheKey);

    if (cachedResult) {
      // Found it in cache, then we don't need to scrape again
      if (shouldDebug) {
        console.log(`[CACHE] Cache for ${cacheKey} - serving from memory instead of scraping`);
      }
      return cachedResult;
    }
  }

  // If no cache or bypassed, time to do the actual workkkk
  if (shouldDebug) {
    console.log(
      `[CACHE] ${bypassCache ? 'Cache bypassed' : 'No cache Found'} for ${cacheKey}, work starts now...`
    );
  }
  const providers = [
    // WORKING
    { getTwoEmbed: () => getTwoEmbed(media) },
    { getAutoembed: () => getAutoembed(media) },
    { getVidSrcCC: () => getVidSrcCC(media) },
    { getVidSrc: () => getVidSrc(media) },
    { getVidrock: () => getVidRock(media) },
    { getMultiembed: () => getMultiembed(media) },
    { getEmbedsu: () => getEmbedsu(media) },

    // #### NOTE from Inside4ndroid : i have not looked at anything below this line yet!

    //{ getPrimewire: () => getPrimewire(media) },

    // It does need to be fixed but it acts like it is down sometimes throws 520 or 524 so,
    // You got my point right ?
    { getVidZee: () => getVidZee(media) },

    // SUB SEARCH
    { getWyzie: () => getWyzie(media) },
    { getLibre: () => getLibre(media) },
    { getFebbox: () => getFebbox(media) },
  ];

  const providerStats = await loadProviderStats();
  const { tier1, tier2 } = splitProviderTiers(providers, providerStats);
  const globalDeadlineMs = Date.now() + GLOBAL_SCRAPE_TIMEOUT_MS;

  if (shouldDebug) {
    const rankedDebug = providers
      .map((provider) => {
        const name = Object.keys(provider)[0];
        return `${name}:${providerSuccessRate(name, providerStats).toFixed(3)}`;
      })
      .join(', ');
    console.log(`[Scrape] Provider reliability ranking ${rankedDebug}`);
  }

  const tier1Batch = await runTieredBatch(tier1, media, 'tier1(reliable)', TIER1_TIMEOUT_MS, globalDeadlineMs);
  let results = [...tier1Batch.results];

  const tier1Successful = tier1Batch.results.filter(
    ({ data }) => data && !(data instanceof Error || data instanceof ErrorObject)
  );

  if (tier1Successful.length === 0) {
    const tier2Batch = await runTieredBatch(
      tier2,
      media,
      'tier2(fallback)',
      TIER2_TIMEOUT_MS,
      globalDeadlineMs
    );
    results = results.concat(tier2Batch.results);
  }

  const successfulResults = (Array.isArray(results) ? results : []).filter(
    ({ data }) => data && !(data instanceof Error || data instanceof ErrorObject)
  );

  // Contract + pipeline: collect -> normalize -> probe -> score -> rank -> return
  let pipelineResult;
  try {
    pipelineResult = await runSourcePipeline(successfulResults, {
      probe: !shouldDebug,
      correlationId: options.correlationId,
    });
  } catch (err) {
    console.error(`[Pipeline] Failed: ${err.message}`);
    // Fallback: return raw results if pipeline fails
    pipelineResult = {
      files: successfulResults.flatMap(r => r.data?.files || []),
      subtitles: successfulResults.flatMap(r => r.data?.subtitles || []),
      quality_groups: {},
      pipeline: { error: err.message, total_ms: 0 }
    };
  }

  const files = pipelineResult.files || [];
  const subtitles = pipelineResult.subtitles || [];
  // Here comes the big boy to loook for nothing okay here you go
  // We need finalResult coz you can't cache what doesn't exist yet - lowkey just consolidating the return logic
  // Build it once, cache it, return it - way cleaner than scattered returns everywhere

  let finalResult;
  if (shouldDebug) {
    results
      .filter(({ data }) => data instanceof Error || data instanceof ErrorObject)
      .forEach(({ data }) => {
        if (data instanceof ErrorObject) console.error(data.toString());
        else console.error(data);
      });

    let errors = results
      .filter(({ data }) => data instanceof Error || data instanceof ErrorObject)
      .map(({ data }) => data);

    finalResult = {
      files,
      subtitles,
      quality_groups: pipelineResult.quality_groups || {},
      pipeline: pipelineResult.pipeline,
      correlation_id: options.correlationId || pipelineResult?.pipeline?.correlation_id,
      errors,
    };
  } else {
    finalResult = {
      files,
      subtitles,
      quality_groups: pipelineResult.quality_groups || {},
      pipeline: pipelineResult.pipeline,
      correlation_id: options.correlationId || pipelineResult?.pipeline?.correlation_id,
    };
  }

  // Only cache if we actually found some streams and we're not bypassing cache
  if (files.length > 0 && !bypassCache) {
    setToCache(cacheKey, finalResult);
    if (shouldDebug) {
      console.log(`[CACHE] Cached result for ${cacheKey}, next request will be much faster`);
    }
  } else if (shouldDebug) {
    console.log(`[CACHE] Not caching result for ${cacheKey} - cache is bypassed for debugging`);
  }

  return finalResult;
}

export default { scrapeMedia };
