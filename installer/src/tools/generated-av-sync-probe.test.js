// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { instrumentWorker, syncFacts } = require('./generated-av-sync-probe');
test('sync metadata measures lead sign and retains only bounded numeric observations', () => {
  const audio = [2000, 3000, 4000, 5000, 6000, 7000], video = audio.map(value => value + 120);
  const facts = syncFacts(audio, video, { base: 10, output: 20, secret: 'private' });
  assert.equal(facts.medianAudioLeadMs, 120); assert.equal(facts.spreadMs, 0);
  assert.equal(syncFacts(video, audio).medianAudioLeadMs, -120);
  assert.equal(JSON.stringify(facts).includes('private'), false);
});
test('missing, ambiguous, burst or unsafe observations never imply measured synchronization', () => {
  const value = [2000, 3000, 4000, 5000, 6000, 7000];
  for (const bad of [value.slice(1), [0, 1, 2, 3, 4, 5], value.map(x => x + 1000),
    [2000, 3000, 4000, 5000, 6000, NaN], value.map(x => String(x))]) {
    assert.throws(() => syncFacts(value, bad));
  }
});
test('temporary worker instrumentation refuses unreviewed or arbitrary sources', () => {
  assert.throws(() => instrumentWorker(Buffer.from('private')));
  assert.throws(() => instrumentWorker('source'));
});
