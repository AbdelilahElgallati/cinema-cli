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
import { get111Movies } from './controllers/providers/111movies/111movies.js';
import { getCinemaOS } from './controllers/providers/CinemaOS/CinemaOS.js';
import { getMultiembed } from './controllers/providers/MultiEmbed/MultiEmbed.js';
import { getEmbedsu } from './controllers/providers/EmbedSu/embedsu.js';
import { getFebbox } from './controllers/subs/febbox.js';
import { validateSources } from './utils/sourceValidator.js';
import { runSourcePipeline } from './utils/sourcePipeline.js';

const shouldDebug = process.argv.includes('--debug');

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
    // This seems to be intermittent i will need to look into it more. i have a hunch its rate limited.
    // { getCinemaOS: () => getCinemaOS(media) },

    // #### NOTE from Inside4ndroid : i have not looked at anything below this line yet!

    //{ getPrimewire: () => getPrimewire(media) },

    // It does need to be fixed but it acts like it is down sometimes throws 520 or 524 so,
    // You got my point right ?
    { getVidZee: () => getVidZee(media) },

    // The Ones That are using Cloudfare so no way to make it work
    // { get111Movies: () => get111Movies(media) },
    // { getXprime: () => getXprime(media) },

    // Need to Fix which can be fixed
    // { getVidsrcWtf: () => getVidsrcWtf(media) },
    // SUB SEARCH
    { getWyzie: () => getWyzie(media) },
    { getLibre: () => getLibre(media) },
    { getFebbox: () => getFebbox(media) },
  ];

  const results = await Promise.all(
    providers.map(async (provider) => {
      const providerName = Object.keys(provider)[0];

      const timeout = new Promise((_, reject) =>
        setTimeout(() => reject(new Error('Timeout after 30s')), 30000)
      );

      try {
        const data = await Promise.race([provider[providerName](), timeout]);
        if (data) {
          console.log(`[${providerName}] Succeeded. Files: ${data.files?.length || 0}, Subtitles: ${data.subtitles?.length || 0}`);
        } else {
          console.log(`[${providerName}] Returned null or empty data`);
        }
        return {
          data: data,
          provider: providerName,
        };
      } catch (e) {
        console.error(`[${providerName}] Failed: ${e.message}`);
        return { data: null, provider: providerName };
      }
    })
  );

  const successfulResults = results.filter(
    ({ data }) => data && !(data instanceof Error || data instanceof ErrorObject)
  );

  // Contract + pipeline: collect -> normalize -> probe -> score -> rank -> return
  const pipelineResult = await runSourcePipeline(successfulResults, {
    probe: !shouldDebug,
    correlationId: options.correlationId,
  });

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
