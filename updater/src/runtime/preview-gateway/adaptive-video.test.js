// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { createVideoSettings, readVideoSettings } = require('./video-settings');
const { createAdaptiveVideo } = require('./adaptive-video');
function fixture(t, profile = { bitRate: 4000000, maxFps: 60 }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-adaptive-')); fs.chmodSync(dir, 0o700);
  t.after(() => fs.rmSync(dir, { force: true, recursive: true }));
  const file = path.join(dir, 'video.json'), store = createVideoSettings(file);
  store.update({ revision: 0, ...profile });
  let now = 0, packets = 0, bytes = 0;
  const controller = createAdaptiveVideo({ store, clock: () => now });
  const sample = (changes = {}, authority = 'primary') => {
    now += 2000; packets += 20; bytes += 60000;
    return controller.observe({ available: true, flow: true, streamId: 'a'.repeat(16), ackedPackets: packets,
      acknowledgedBytes: bytes, pendingFrames: 0, pendingBytes: 0, oldestMs: 0, ackDelayMs: 50, ...changes }, authority);
  };
  const enable = () => store.configureAutomatic({ revision: store.automaticSnapshot().revision, profileRevision: store.snapshot().revision, enabled: true });
  return { file, store, controller, sample, enable };
}
test('manual default never adapts; automatic congestion is sustained and rate limited, recovery is gradual and capped', t => {
  const f = fixture(t), bad = { oldestMs: 1200, ackDelayMs: 1000, pendingFrames: 30 };
  for (let n = 0; n < 5; n++) f.sample(bad);
  assert.equal(f.store.snapshot().bitRate, 4000000); f.enable();
  assert.equal(f.sample(bad), null); assert.equal(f.sample(bad), null);
  assert.equal(f.sample(bad).bitRate, 3000000);
  for (let n = 0; n < 5; n++) f.sample(bad);
  assert.equal(f.store.snapshot().bitRate, 3000000);
  f.sample(bad); assert.equal(f.store.snapshot().bitRate, 2000000);
  for (let n = 0; n < 20; n++) f.sample(bad);
  assert.deepEqual([f.store.snapshot().bitRate, f.store.snapshot().maxFps], [1000000, 30]);
  for (let n = 0; n < 26; n++) f.sample();
  assert.deepEqual([f.store.snapshot().bitRate, f.store.snapshot().maxFps], [1000000, 45]);
  for (let n = 0; n < 150; n++) f.sample();
  assert.deepEqual([f.store.snapshot().bitRate, f.store.snapshot().maxFps], [4000000, 60]);
});
test('idle, missing feedback, changed authority/stream and malformed samples cannot promote quality or accumulate prior evidence', t => {
  const f = fixture(t); f.enable(); const bad = { oldestMs: 1200 };
  f.sample(bad); f.sample(bad); f.sample(bad); assert.equal(f.store.snapshot().bitRate, 3000000);
  for (let n = 0; n < 60; n++) f.sample({ ackedPackets: 0, acknowledgedBytes: 0 });
  assert.equal(f.store.snapshot().bitRate, 3000000);
  for (let n = 0; n < 20; n++) f.sample({ available: false });
  f.sample(bad, 'new-owner'); f.sample(bad, 'other-owner');
  f.sample({ ...bad, streamId: 'b'.repeat(16) }); f.sample({ ...bad, pendingFrames: NaN });
  assert.equal(f.store.snapshot().bitRate, 3000000);
});
test('manual choice disables automatic even at the same effective targets; stale policy writes cannot override it', t => {
  const f = fixture(t); f.enable(); const policy = f.store.automaticSnapshot();
  f.sample({ oldestMs: 1000 }); f.sample({ oldestMs: 1000 }); f.sample({ oldestMs: 1000 });
  const profile = f.store.snapshot(); f.store.update({ revision: profile.revision, bitRate: profile.bitRate, maxFps: profile.maxFps });
  assert.equal(f.store.automaticSnapshot().enabled, false);
  assert.throws(() => f.store.adapt({ revision: profile.revision, bitRate: 1000000, maxFps: 30 }, policy.revision), /authority changed/);
  assert.throws(() => f.store.configureAutomatic({ revision: policy.revision, profileRevision: profile.revision, enabled: true }), e => e.status === 409);
  for (let n = 0; n < 30; n++) f.sample({ oldestMs: 5000 });
  assert.equal(f.store.snapshot().bitRate, profile.bitRate);
});
test('automatic policy persists separately; disabling restores selected ceilings and the encoder file remains original schema', t => {
  const f = fixture(t); f.enable();
  f.sample({ oldestMs: 1000 }); f.sample({ oldestMs: 1000 }); f.sample({ oldestMs: 1000 });
  const reopened = createVideoSettings(f.file), policy = reopened.automaticSnapshot(), profile = reopened.snapshot();
  assert.equal(policy.enabled, true); assert.equal(profile.bitRate, 3000000);
  assert.deepEqual(Object.keys(readVideoSettings(f.file)).sort(), ['bitRate','maxFps','revision','version']);
  assert.throws(() => reopened.configureAutomatic({ revision: policy.revision, profileRevision: profile.revision, enabled: false, shell: 'x' }), e => e.status === 400);
  reopened.configureAutomatic({ revision: policy.revision, profileRevision: profile.revision, enabled: false });
  assert.deepEqual([reopened.snapshot().bitRate, reopened.snapshot().maxFps], [4000000, 60]);
  assert.equal(reopened.automaticSnapshot().enabled, false);
  fs.writeFileSync(f.file.replace('.json','-automatic.json'), '{}');
  assert.throws(() => createVideoSettings(f.file), /automatic video policy/);
});
