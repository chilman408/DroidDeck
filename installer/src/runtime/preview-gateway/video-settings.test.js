// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const profile = () => require('./video-settings');
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ta-video-settings-'));
  fs.chmodSync(dir, 0o700);
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return path.join(dir, 'video.json');
}
test('video targets default to current encoder values, persist atomically and reject stale writes', t => {
  const filename = fixture(t), { createVideoSettings, readVideoSettings } = profile();
  const store = createVideoSettings(filename);
  assert.deepEqual(store.snapshot(), { version: 1, revision: 0, bitRate: 4000000, maxFps: 30 });
  const next = store.update({ revision: 0, bitRate: 2000000, maxFps: 45 });
  assert.deepEqual(next, { version: 1, revision: 1, bitRate: 2000000, maxFps: 45 });
  assert.deepEqual(readVideoSettings(filename), next);
  assert.deepEqual(createVideoSettings(filename).snapshot(), next);
  assert.throws(() => store.update({ revision: 0, bitRate: 4000000, maxFps: 30 }), error => error.status === 409);
  assert.deepEqual(store.update({ revision: 1, bitRate: 2000000, maxFps: 45 }), next);
  assert.equal(fs.readdirSync(path.dirname(filename)).length, 1);
});
test('only bounded integer encoder targets are allowed; corrupt files never fall back silently', t => {
  const filename = fixture(t), { createVideoSettings } = profile(), store = createVideoSettings(filename);
  for (const change of [{ bitRate: 0 }, { maxFps: 120 }, { bitRate: '4000000' }, { maxFps: 30.1 },
    { command: 'sh' }, { revision: -1 }, { revision: Number.MAX_SAFE_INTEGER }, { bitRate: 6000000 }]) {
    assert.throws(() => store.update({ revision: 0, bitRate: 4000000, maxFps: 30, ...change }));
  }
  assert.equal(fs.existsSync(filename), false);
  fs.writeFileSync(filename, JSON.stringify({ version: 1, revision: 0, bitRate: 0, maxFps: 30 }), { mode: 0o600 });
  assert.throws(() => createVideoSettings(filename), /video/i);
});

test('3 Mbps persists across store recreation without changing defaults or frame rate', t => {
  const filename = fixture(t), { createVideoSettings, readVideoSettings, BIT_RATES } = profile();
  assert.deepEqual(BIT_RATES, [1000000, 2000000, 3000000, 4000000, 8000000, 12000000]);
  const store = createVideoSettings(filename);
  assert.equal(store.snapshot().bitRate, 4000000);
  const next = store.update({ revision: 0, bitRate: 3000000, maxFps: 30 });
  assert.equal(next.bitRate, 3000000);
  assert.equal(next.maxFps, 30);
  assert.deepEqual(readVideoSettings(filename), next);
  assert.deepEqual(createVideoSettings(filename).snapshot(), next);
});
test('profile reader rejects links and insecure permissions at the privilege boundary', { skip: process.platform === 'win32' }, t => {
  const filename = fixture(t), { readVideoSettings, createVideoSettings } = profile();
  createVideoSettings(filename).update({ revision: 0, bitRate: 8000000, maxFps: 60 });
  const other = filename + '.other'; fs.renameSync(filename, other); fs.symlinkSync(other, filename);
  assert.throws(() => readVideoSettings(filename)); fs.unlinkSync(filename);
  fs.linkSync(other, filename); assert.throws(() => readVideoSettings(filename)); fs.unlinkSync(filename);
  fs.renameSync(other, filename); fs.chmodSync(filename, 0o644); assert.throws(() => readVideoSettings(filename));
  fs.chmodSync(filename, 0o600); fs.chmodSync(path.dirname(filename), 0o777);
  assert.throws(() => readVideoSettings(filename));
});
