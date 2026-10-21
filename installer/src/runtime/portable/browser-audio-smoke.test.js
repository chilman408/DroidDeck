// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
function load() {
  assert.ok(fs.existsSync(path.join(__dirname, 'browser-audio-smoke.js')), 'Generated browser audio observer must exist');
  return require('./browser-audio-smoke');
}
function samples() { return Array.from({ length: 10 }, (_, i) => ({ time: 1 + i * .2, paused: false,
  error: null, sampleRate: 48000, peakHz: 1007.8125, peakDb: -40, toneMatches: true, outputMuted: true, savedAudio: false })); }
test('acceptance requires decoded tone and advancing media, not only received bytes', () => {
  const { summarize } = load(), result = summarize(samples(), 40000);
  assert.equal(result.generatedBrowserAudioVerified, true); assert.equal(result.matchingSamples, 10);
  assert.equal(result.installable, false); assert.equal(result.androidAudioVerified, false);
  assert.equal(result.publicTlsVerified, false); assert.equal(result.savedAudio, false);
});
test('silence, wrong tone, frozen playback, faults and insufficient samples cannot pass', () => {
  const { summarize } = load();
  for (const patch of [{ peakHz: 440, toneMatches: false }, { peakDb: -200, toneMatches: false },
    { time: 1 }, { paused: true }, { error: 3 }, { sampleRate: 44100 }, { outputMuted: false }, { savedAudio: true }]) {
    assert.throws(() => summarize(samples().map(item => ({ ...item, ...patch })), 40000));
  }
  assert.throws(() => summarize(samples().slice(0, 2), 40000));
  assert.throws(() => summarize(samples(), 0));
  const forged = samples().map(item => ({ ...item, peakHz: 440 })); assert.throws(() => summarize(forged, 40000));
});
test('observer rejects nonfinite or oversized aggregate metadata', () => {
  const { summarize } = load();
  for (const bytes of [-1, Infinity, 17 * 1024 * 1024]) assert.throws(() => summarize(samples(), bytes));
  for (const patch of [{ time: NaN }, { time: Infinity }, { peakDb: NaN }, { peakHz: Infinity }]) {
    assert.throws(() => summarize(samples().map(item => ({ ...item, ...patch })), 40000));
  }
});
