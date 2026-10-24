// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { generationArgs, audioFacts } = require('./generated-av-sync-fixture');
const { fixtureDirectory, fixtureProfile, fixturePage } = require('../runtime/waydroid-display/generated-transition-fixture');
test('generated flash/tone fixture has fixed time, tone, codecs and nonreplacement target', () => {
  const args = generationArgs();
  assert.ok(args.includes('-n')); assert.ok(args.includes('libaom-av1')); assert.ok(args.includes('aac'));
  assert.ok(args.includes('color=c=black:size=640x360:rate=30:duration=8'));
  assert.equal(args.at(-1), '/home/hilman/teslaandroid-waydroid/generated-av-sync-20261011s/fixture.mp4');
  assert.equal(fixtureProfile(fixtureDirectory('--serve-av1-av-sync')), 'av1');
  assert.notEqual(fixtureDirectory('--serve-av1-av-sync'), fixtureDirectory('--serve-av1-av-sync-delay'));
  assert.ok(args.some(value => value.includes('1000*t')));
  assert.ok(args.some(value => value.startsWith("aevalsrc='") && value.includes("':s=48000:d=8")));
});
test('sound is enabled only by a gesture on the fixed sync fixture, other fixtures remain muted', () => {
  const page = fixturePage('av-sync');
  assert.match(page, /controls playsinline/);
  assert.match(page, /pointerdown.*v\.muted=false/);
  assert.doesNotMatch(page, /muted autoplay/);
  assert.match(fixturePage('av1'), /muted autoplay/);
  assert.match(fixturePage('avc'), /muted autoplay/);
  assert.throws(() => fixturePage('foreign'));
});
test('source audio must have exact zero origin, sample rate and duration', () => {
  const stream = { codec_name: 'aac', sample_rate: '48000', channels: 2, start_time: '0.000000', duration: '8.000000' };
  assert.equal(audioFacts({ streams: [stream] }).startSeconds, 0);
  for (const change of [{ channels: 1 }, { start_time: '0.100000' }, { duration: '9.000000' }, { codec_name: 'ac3' }]) {
    assert.throws(() => audioFacts({ streams: [{ ...stream, ...change }] }));
  }
});
