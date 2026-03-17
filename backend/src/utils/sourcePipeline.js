import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import { spawnSync } from 'child_process';
import fetch from 'node-fetch';

const STATS_PATH = path.resolve(process.cwd(), 'src', 'cache', 'provider_stats.json');
const DEFAULT_STATS = { providers: {} };

function ensureDir(filePath) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
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

function saveStats(stats) {
  try {
    ensureDir(STATS_PATH);
    fs.writeFileSync(STATS_PATH, JSON.stringify(stats, null, 2));
  } catch {
    // Never fail request on stats write issues.
  }
}

function normalizeQuality(raw) {
  const q = String(raw || '').toLowerCase().trim();
  if (!q) return 'unknown';
  if (q.includes('4k') || q.includes('2160')) return '2160p';
  if (q.includes('1080')) return '1080p';
  if (q.includes('720')) return '720p';
  if (q.includes('480')) return '480p';
  if (q.includes('360')) return '360p';
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
  const type = String(sub.type || (url.toLowerCase().includes('.vtt') ? 'vtt' : 'srt')).toLowerCase();
  return { url, lang, type };
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
  if (!source || source.type !== 'hls' || source.quality !== 'unknown') {
    return [source];
  }

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
  try {
    const ffprobe = process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe';
    const headerLines = Object.entries(headers || {})
      .filter(([, v]) => v !== undefined && v !== null)
      .map(([k, v]) => `${k}: ${v}`)
      .join('\r\n');

    const args = ['-v', 'error', '-show_entries', 'stream=codec_type', '-select_streams', 'v:0', '-of', 'default=noprint_wrappers=1:nokey=1'];
    if (headerLines) args.push('-headers', `${headerLines}\r\n`);
    args.push(url);

    const result = spawnSync(ffprobe, args, {
      timeout: 5000,
      encoding: 'utf-8',
      windowsHide: true,
    });

    if (result.error) {
      return { status: 'unavailable', hasVideo: null };
    }
    if (result.status !== 0) {
      return { status: 'probe_inconclusive', hasVideo: null };
    }

    const out = String(result.stdout || '').toLowerCase();
    if (out.includes('video')) return { status: 'ok', hasVideo: true, transport: 'ffprobe' };
    return { status: 'no_video', hasVideo: false, transport: 'ffprobe' };
  } catch {
    return { status: 'unavailable', hasVideo: null };
  }
}

async function probeSource(src) {
  const ff = ffprobeCheck(src.file, src.headers);
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
      if (!f || typeof f !== 'object' || typeof f.file !== 'string') continue;
      const file = f.file;
      if (!/^https?:\/\//i.test(file)) continue;

      const source = {
        source_id: sourceIdFor(file, provider),
        provider,
        file,
        type: inferType(file, f.type),
        quality: normalizeQuality(f.quality),
        headers: f.headers && typeof f.headers === 'object' ? f.headers : {},
        probe_result: { status: 'not_probed', hasVideo: null },
      };
      normalizedSources.push(source);
    }

    for (const s of subsRaw) {
      const ns = normalizeSubtitle(s);
      if (ns) normalizedSubs.push(ns);
    }
  }

  stageDurations.collect_ms = Date.now() - tCollectStart;

  const tNormalizeStart = Date.now();
  const expandedSources = [];
  for (const s of normalizedSources) {
    const expanded = await expandHlsVariants(s);
    expandedSources.push(...expanded);
  }

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
  for (const src of deduped) {
    if (probeEnabled) {
      src.probe_result = await probeSource(src);
    }

    const ps = providersStats[src.provider] || {};
    src.score = scoreSource(src, ps);

    if (!providersStats[src.provider]) providersStats[src.provider] = { success: 0, no_video: 0, timeout: 0 };
    if (src.probe_result.status === 'ok') providersStats[src.provider].success += 1;
    if (src.probe_result.status === 'no_video') providersStats[src.provider].no_video += 1;
    if (src.probe_result.status === 'timeout') providersStats[src.provider].timeout += 1;
  }

  stageDurations.probe_ms = Date.now() - tProbeStart;

  const tScoreStart = Date.now();
  saveStats(stats);
  stageDurations.score_ms = Date.now() - tScoreStart;

  const tRankStart = Date.now();
  const ranked = deduped
    .filter((s) => s.probe_result.status !== 'no_video')
    .sort((a, b) => b.score - a.score);
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

  return {
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
      },
      total_ms: Date.now() - startedAt,
    },
  };
}
