// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { ToneEnergy, createToneStats } = require('./generated-tone-stats');
function tone(frequency, amplitude = 3) {
  const result = new ToneEnergy();
  for (let i = 0; i < 48000; i++) result.push(Math.round(amplitude * Math.sin(2 * Math.PI * frequency * i / 48000)));
  return result.finish();
}
test('quiet quantized generated 1kHz tone is distinguishable without raising volume', () => {
  assert.deepEqual(tone(1000), { frequencyHz: 1000, sampleRate: 48000, windows: 100, matchingWindows: 100 });
  assert.equal(tone(1000, 1).matchingWindows, 100);
});
test('silence, DC, unrelated frequencies and deterministic broadband noise fail', () => {
  assert.equal(tone(1000, 0).matchingWindows, 0);
  for (const frequency of [0, 100, 440, 2000, 10000]) assert.equal(tone(frequency).matchingWindows, 0);
  const dc = new ToneEnergy(), noise = new ToneEnergy(); let value = 1;
  for (let i = 0; i < 48000; i++) { dc.push(3); value = (Math.imul(value, 1664525) + 1013904223) >>> 0; noise.push((value % 7) - 3); }
  assert.equal(dc.finish().matchingWindows, 0); assert.equal(noise.finish().matchingWindows, 0);
});
test('parser frame observer adds only aggregate tone evidence without retaining sound', () => {
  class Parser {
    constructor(onFrame) { this.onFrame = onFrame; }
    finish() { return { frames: 4800, peak: 3 }; }
  }
  const stats = createToneStats(Parser);
  for (let i = 0; i < 4800; i++) { const sample = Math.round(3 * Math.sin(2 * Math.PI * i / 48)); stats.onFrame(sample, sample); }
  assert.deepEqual(stats.finish(), { frames: 4800, peak: 3,
    tone: { frequencyHz: 1000, sampleRate: 48000, windows: 10, matchingWindows: 10 } });
});
test('incomplete final spectral window does not inflate complete-window evidence', () => {
  const energy = new ToneEnergy(); for (let i = 0; i < 479; i++) energy.push(3);
  assert.equal(energy.finish().windows, 0); assert.equal(energy.finish().matchingWindows, 0);
});
