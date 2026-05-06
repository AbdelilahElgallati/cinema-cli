import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import { spawn, spawnSync } from 'child_process';
import fetch from 'node-fetch';

import os from 'os';

function getCacheDir() {
  if (process.platform === 'win32') return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'CinemaCLI', 'cache');
  if (process.platform === 'darwin') return path.join(os.homedir(), 'Library', 'Application Support', 'CinemaCLI', 'cache');
  return path.join(os.homedir(), '.local', 'share', 'cinema-cli', 'cache');
}

const STATS_PATH = path.join(getCacheDir(), 'provider_stats.json');
const DEFAULT_STATS = { providers: {} };

async function ensureDir(filePath) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    await fs.promises.mkdir(dir, { recursive: true });
  }
}

function loadStats() {
  try {
    if (!fs.existsSync(STATS_PATH)) return { ...DEFAULT_STATS };
    const raw = fs.readFileSync(STATS_PATH, 'utf-8');
    const data = JSON.parse(raw);
    if (!data || typeof data !== 'object') return { ...DEFAULT_STATS };
    if (!data.providers || typeof data.providers !== 'object') data.providers = {};
    return data;
  } catch {
    return { ...DEFAULT_STATS };
  }
}

async function saveStats(stats) {
  try {
    await ensureDir(STATS_PATH);
    await fs.promises.writeFile(STATS_PATH, JSON.stringify(stats, null, 2), 'utf-8');
  } catch (err) {
    console.error(`[Pipeline] Failed to persist provider stats: ${err?.message || err}`);
  }
}

function normalizeQuality(raw) {
  const q = String(raw || '').toLowerCase().trim();
  const compact = q.replace(/[\s_-]/g, '');
  if (!q) return 'unknown';
  if (compact.includes('4k') || compact.includes('2160')) return '2160p';
  if (compact.includes('1080')) return '1080p';
  if (compact.includes('720')) return '720p';
  if (compact.includes('480')) return '480p';
  if (compact.includes('360')) return '360p';
  
  // VidRock specific mappings
  if (['sol', 'zenith'].includes(compact)) return '2160p';
  if (['astra', 'atlas', 'orion'].includes(compact)) return '1080p';
  if (['nova', 'luna'].includes(compact)) return '720p';
  if (['vega', 'draco'].includes(compact)) return '480p';
  if (['nyx'].includes(compact)) return '360p';

  if (['auto', 'adaptive', 'best'].includes(compact)) return 'unknown';
  if (compact.includes('fhd') || compact.includes('fullhd')) return '1080p';
  if (compact === 'hd' || compact.includes('high')) return '1080p';
  if (compact.includes('sd')) return '480p';
  if (compact.includes('low')) return '360p';
  if (compact.includes('medium')) return '720p';

  if (compact.includes('uhd')) return '2160p';
  return q;
}

function inferType(url, rawType) {
  const type = String(rawType || '').toLowerCase().trim();
  if (type) return type;
  const u = String(url || '').toLowerCase();
  if (u.includes('.m3u8') || u.includes('/hls/') || u.includes('m3u8')) return 'hls';
  if (u.includes('.mpd') || u.includes('dash')) return 'dash';
  if (u.includes('.mp4')) return 'mp4';
  return 'unknown';
}

function normalizeSubtitle(sub) {
  if (!sub || typeof sub !== 'object') return null;
  const url = typeof sub.url === 'string' ? sub.url : null;
  if (!url) return null;
  const lang = String(sub.lang || sub.language || 'und').toLowerCase();
  const label = sub.label || sub.language || sub.lang || 'Unknown';
  const type = String(sub.type || (url.toLowerCase().includes('.vtt') ? 'vtt' : 'srt')).toLowerCase();
  return { url, lang, label, type };
}

function sourceIdFor(file, provider) {
  return crypto
    .createHash('sha1')
    .update(`${provider}|${file}`)
    .digest('hex')
    .slice(0, 16);
}

function absolutizeUrl(baseUrl, maybeRelative) {
  try {
    return new URL(maybeRelative, baseUrl).toString();
  } catch {
    return maybeRelative;
  }
}

function qualityFromStreamInf(attrsLine = '') {
  const line = String(attrsLine || '');

  const resolutionMatch = line.match(/RESOLUTION\s*=\s*(\d+)x(\d+)/i);
  if (resolutionMatch) {
    return normalizeQuality(`${resolutionMatch[2]}p`);
  }

  const nameMatch = line.match(/NAME\s*=\s*"([^"]+)"/i);
  if (nameMatch) {
    return normalizeQuality(nameMatch[1]);
  }

  const bandwidthMatch = line.match(/BANDWIDTH\s*=\s*(\d+)/i);
  if (bandwidthMatch) {
    const bandwidth = Number(bandwidthMatch[1]);
    if (bandwidth >= 12000000) return '2160p';
    if (bandwidth >= 5000000) return '1080p';
    if (bandwidth >= 2500000) return '720p';
    if (bandwidth >= 1200000) return '480p';
    if (bandwidth >= 500000) return '360p';
  }

  return 'unknown';
}

async function expandHlsVariants(source) {
  if (!source || source.type !== 'hls') {
    return [source];
  }

  // If already expanded, don't re-expand
  if (source.parent_source_id) return [source];

  try {
    const mergedHeaders = {
      'User-Agent': 'cinema-cli-backend/1.0',
      ...(source.headers || {}),
    };

    const res = await fetch(source.file, {
      method: 'GET',
      headers: mergedHeaders,
      redirect: 'follow',
      signal: AbortSignal.timeout(4500),
    });

    if (!res.ok) return [source];

    const contentType = String(res.headers.get('content-type') || '').toLowerCase();
    const body = await res.text();
    const text = String(body || '');
    const looksLikeM3u8 = contentType.includes('mpegurl') || text.includes('#EXTM3U');
    const isMasterPlaylist = text.includes('#EXT-X-STREAM-INF');

    if (!looksLikeM3u8 || !isMasterPlaylist) {
      return [source];
    }

    const lines = text.split(/\r?\n/);
    const variants = [];

    for (let i = 0; i < lines.length; i += 1) {
      const line = String(lines[i] || '').trim();
      if (!line.startsWith('#EXT-X-STREAM-INF')) continue;

      let variantUrl = '';
      for (let j = i + 1; j < lines.length; j += 1) {
        const candidate = String(lines[j] || '').trim();
        if (!candidate || candidate.startsWith('#')) continue;
        variantUrl = candidate;
        break;
      }

      if (!variantUrl) continue;

      const quality = qualityFromStreamInf(line);
      const absoluteUrl = absolutizeUrl(source.file, variantUrl);
      
      variants.push({
        ...source,
        file: absoluteUrl,
        quality,
        source_id: sourceIdFor(absoluteUrl, source.provider),
        parent_source_id: source.source_id,
      });
    }

    const usefulVariants = variants.filter((variant) => variant.quality !== 'unknown');
    if (usefulVariants.length > 0) {
      // Keep the original master source as a fallback. Some hosts require
      // tokens/query semantics that work only when the player starts from master.
      return [source, ...usefulVariants];
    }
    if (variants.length > 0) {
      return [source, ...variants];
    }
    return [source];
  } catch {
    return [source];
  }
}

async function httpProbe(url, headers) {
  const merged = {
    'User-Agent': 'cinema-cli-backend/1.0',
    ...(headers || {}),
    Range: 'bytes=0-1024',
  };
  try {
    const res = await fetch(url, {
      method: 'GET',
      headers: merged,
      redirect: 'follow',
      signal: AbortSignal.timeout(4500),
    });
    if ([200, 206].includes(res.status)) {
      const ct = String(res.headers.get('content-type') || '').toLowerCase();
      if (ct.includes('video') || ct.includes('mpegurl') || ct.includes('application/octet-stream')) {
        return { status: 'ok', hasVideo: true, transport: 'http' };
      }
      return { status: 'ok', hasVideo: true, transport: 'http' };
    }
    if ([403, 429].includes(res.status)) return { status: 'http_error', code: res.status, hasVideo: false };
    return { status: 'http_error', code: res.status, hasVideo: false };
  } catch (err) {
    const msg = String(err?.message || 'probe_error').toLowerCase();
    if (msg.includes('timeout')) return { status: 'timeout', hasVideo: false };
    return { status: 'probe_error', hasVideo: false };
  }
}

function ffprobeCheck(url, headers) {
  if (!url || !url.startsWith('http')) {
    return Promise.resolve({ status: 'invalid_url', hasVideo: null });
  }
  return new Promise((resolve) => {
    try {
      const ffprobe = process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe';
      // Sanitize headers to prevent CRLF injection
      const sanitizeHeader = (s) => String(s).replace(/[\r\n\0]/g, '');
      const headerLines = Object.entries(headers || {})
        .filter(([, v]) => v !== undefined && v !== null)
        .map(([k, v]) => `${sanitizeHeader(k)}: ${sanitizeHeader(v)}`)
        .join('\r\n');
      
      const headersWithTrailingCRLF = headerLines ? headerLines + '\r\n' : '';

      const args = ['-v', 'error', '-show_entries', 'stream=codec_type', '-select_streams', 'v:0', '-of', 'default=noprint_wrappers=1:nokey=1'];
      if (headersWithTrailingCRLF) args.push('-headers', headersWithTrailingCRLF);
      args.push(url);

      const child = spawn(ffprobe, args, {
        windowsHide: true,
      });

      let stdout = '';
      let stderr = '';
      const timeout = setTimeout(() => {
        child.kill();
        resolve({ status: 'timeout', hasVideo: null });
      }, 5000);

      child.stdout.on('data', (data) => {
        stdout += data;
      });

      child.stderr.on('data', (data) => {
        stderr += data;
      });

      child.on('error', () => {
        clearTimeout(timeout);
        resolve({ status: 'unavailable', hasVideo: null });
      });

      child.on('close', (code) => {
        clearTimeout(timeout);
        if (code !== 0) {
          resolve({ 
            status: 'probe_inconclusive', 
            hasVideo: null, 
            error: stderr.trim() || `Exit code ${code}` 
          });
          return;
        }

        const out = String(stdout || '').toLowerCase();
        if (out.includes('video')) return resolve({ status: 'ok', hasVideo: true, transport: 'ffprobe' });
        resolve({ status: 'no_video', hasVideo: false, transport: 'ffprobe' });
      });
    } catch {
      resolve({ status: 'unavailable', hasVideo: null });
    }
  });
}

async function probeSource(src) {
  const ff = await ffprobeCheck(src.file, src.headers);
  if (ff.status === 'ok' || ff.status === 'no_video') return ff;
  const http = await httpProbe(src.file, src.headers);
  return http;
}

function scoreSource(src, providerStat) {
  const qualityBoost = {
    '2160p': 25,
    '1080p': 20,
    '720p': 15,
    '480p': 10,
    '360p': 5,
    unknown: 0,
  };

  let score = 50;
  score += qualityBoost[src.quality] || 0;
  if (src.type === 'hls') score += 5;
  if (src.probe_result?.status === 'ok') score += 30;
  if (src.probe_result?.status === 'no_video') score -= 100;
  if (src.probe_result?.status === 'timeout') score -= 30;

  if (providerStat) {
    const successes = providerStat.success || 0;
    const noVideo = providerStat.no_video || 0;
    const timeouts = providerStat.timeout || 0;
    const total = successes + noVideo + timeouts;
    if (total > 0) {
      const successRate = successes / total;
      score += Math.round(successRate * 20 - 10);
    }
  }

  return score;
}

async function batch(items, fn, limit = 5) {
  const results = [];
  for (let i = 0; i < items.length; i += limit) {
    const chunk = items.slice(i, i + limit);
    results.push(...(await Promise.all(chunk.map(fn))));
  }
  return results;
}

const shouldDebug = () =>
  process.argv.includes('--debug') ||
  process.env.DEBUG === 'true' ||
  process.env.LOG_LEVEL === 'debug';

export async function runSourcePipeline(rawProviderResults, options = {}) {
  const startedAt = Date.now();
  const stageDurations = {};
  const correlationId = String(options.correlationId || '').trim() || null;

  const tCollectStart = Date.now();
  const stats = loadStats();
  const providersStats = stats.providers;

  const normalizedSources = [];
  const normalizedSubs = [];

  for (const entry of rawProviderResults) {
    const provider = String(entry.provider || 'unknown');
    const data = entry.data || {};

    const filesRaw = Array.isArray(data.files) ? data.files : data.files ? [data.files] : [];
    const subsRaw = Array.isArray(data.subtitles) ? data.subtitles : [];

    for (const f of filesRaw) {
      let file = null;
      let type = null;
      let quality = null;
      let headers = {};

      if (typeof f === 'string') {
        file = f;
      } else if (f && typeof f === 'object') {
        file = f.file || f.url;
        type = f.type || null;
        quality = f.quality || null;
        headers = f.headers && typeof f.headers === 'object' ? f.headers : {};
      }

      if (typeof file !== 'string') continue;
      
      // Handle protocol-relative URLs
      if (file.startsWith('//')) {
        file = 'https:' + file;
      }
      
      if (!/^https?:\/\//i.test(file)) continue;

      const source = {
        source_id: sourceIdFor(file, provider),
        provider,
        file,
        type: inferType(file, type),
        quality: normalizeQuality(quality),
        headers,
        probe_result: { status: 'not_probed', hasVideo: null },
      };
      normalizedSources.push(source);
    }

    for (const s of subsRaw) {
      const ns = normalizeSubtitle(s);
      if (ns) normalizedSubs.push(ns);
    }
  }

  if (shouldDebug()) {
    console.log(`[Pipeline] Collected ${normalizedSources.length} files and ${normalizedSubs.length} subtitles from providers`);
  }

  stageDurations.collect_ms = Date.now() - tCollectStart;

  const tNormalizeStart = Date.now();
  // Parallelize HLS expansion with concurrency limit
  const expandedResults = await batch(normalizedSources, (s) => expandHlsVariants(s), 8);
  const expandedSources = expandedResults.flat();

  const deduped = [];
  const seen = new Set();
  for (const s of expandedSources) {
    const dedupeKey = `${s.file}|${s.quality}|${s.provider}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    deduped.push(s);
  }

  stageDurations.normalize_ms = Date.now() - tNormalizeStart;

  const probeEnabled = options.probe !== false;
  const tProbeStart = Date.now();

  // Parallelize probing with a concurrency limit
  if (probeEnabled) {
    await batch(
      deduped,
      async (src) => {
        src.probe_result = await probeSource(src);
      },
      12
    );
  }

  // Update stats and scores after all probes are done
  for (const src of deduped) {
    const ps = providersStats[src.provider] || {};
    src.score = scoreSource(src, ps);

    if (!providersStats[src.provider]) {
      providersStats[src.provider] = { success: 0, no_video: 0, timeout: 0 };
    }
    
    if (src.probe_result.status === 'ok') {
      providersStats[src.provider].success += 1;
    } else if (src.probe_result.status === 'no_video') {
      providersStats[src.provider].no_video += 1;
    } else if (src.probe_result.status === 'timeout') {
      providersStats[src.provider].timeout += 1;
    }
  }

  stageDurations.probe_ms = Date.now() - tProbeStart;

  const tScoreStart = Date.now();
  await saveStats(stats);
  stageDurations.score_ms = Date.now() - tScoreStart;

  const tRankStart = Date.now();
  const nonNoVideo = deduped.filter((s) => s.probe_result.status !== 'no_video');
  const rankingPool = nonNoVideo.length > 0 ? nonNoVideo : deduped;
  const ranked = rankingPool.slice().sort((a, b) => b.score - a.score);
  stageDurations.rank_ms = Date.now() - tRankStart;

  const groupedByQuality = {};
  for (const src of ranked) {
    const q = src.quality || 'unknown';
    if (!groupedByQuality[q]) groupedByQuality[q] = [];
    groupedByQuality[q].push(src.source_id);
  }

  const subtitles = [];
  const seenSub = new Set();
  for (const sub of normalizedSubs) {
    if (seenSub.has(sub.url)) continue;
    seenSub.add(sub.url);
    subtitles.push(sub);
  }

  if (shouldDebug()) {
    console.log(`[Pipeline] Final subtitle count: ${subtitles.length}`);
  }

  const result = {
    files: ranked,
    subtitles,
    quality_groups: groupedByQuality,
    pipeline: {
      correlation_id: correlationId,
      stages: ['collect', 'normalize', 'probe', 'score', 'rank', 'return'],
      timings_ms: stageDurations,
      totals: {
        input: normalizedSources.length,
        output: ranked.length,
        subtitles: subtitles.length,
      },
      total_ms: Date.now() - startedAt,
    },
  };

  if (shouldDebug()) {
    process.stderr.write(
      `[DIAG-B] subtitles in pipeline response: ${result?.subtitles?.length} items\n`
    );
  }
  return result;
}

