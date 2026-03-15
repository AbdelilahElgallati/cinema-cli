import test from 'node:test';
import assert from 'node:assert/strict';

import { runSourcePipeline } from '../src/utils/sourcePipeline.js';


test('source pipeline returns contract shape with quality groups and pipeline metadata', async () => {
  const rawProviderResults = [
    {
      provider: 'TestProvider',
      data: {
        files: [
          {
            file: 'https://example.com/video/master.m3u8',
            quality: '1080p',
            type: 'hls',
            headers: { Referer: 'https://example.com' },
          },
        ],
        subtitles: [
          { url: 'https://example.com/subs/en.vtt', lang: 'en' },
        ],
      },
    },
  ];

  const out = await runSourcePipeline(rawProviderResults, {
    probe: false,
    correlationId: 'test-corr-123',
  });

  assert.ok(Array.isArray(out.files));
  assert.ok(Array.isArray(out.subtitles));
  assert.equal(typeof out.quality_groups, 'object');
  assert.equal(typeof out.pipeline, 'object');

  assert.ok(Array.isArray(out.pipeline.stages));
  assert.equal(out.pipeline.correlation_id, 'test-corr-123');
  assert.equal(typeof out.pipeline.timings_ms, 'object');
  assert.equal(typeof out.pipeline.totals, 'object');
  assert.equal(typeof out.pipeline.total_ms, 'number');

  assert.ok(out.files.length >= 1);
  const source = out.files[0];
  assert.equal(typeof source.source_id, 'string');
  assert.equal(typeof source.probe_result, 'object');

  const qualityKeys = Object.keys(out.quality_groups);
  assert.ok(qualityKeys.length >= 1);
});
