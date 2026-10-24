// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { createProxyStore } = require('./proxy-store');
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-proxy-store-'));
  fs.chmodSync(dir, 0o700);
  t.after(() => { assert.ok(dir.startsWith(path.resolve(os.tmpdir()) + path.sep)); fs.rmSync(dir, { recursive: true, force: true }); });
  return path.join(dir, 'profile.json');
}
test('new default and upgrade origin remain distinct; rename persists and preserves previous origin', t => {
  const file = fixture(t), initialHostname = 'teslavm.mondriandsm.synology.me';
  const store = createProxyStore(file, { initialHostname });
  assert.equal(store.snapshot().origin, 'https://' + initialHostname);
  assert.equal(store.snapshot().defaultOrigin, 'https://device.teslaandroid.com');
  const next = store.update({ revision: 0, hostname: 'droiddeck.mondriandsm.synology.me' });
  assert.equal(next.previousOrigin, 'https://' + initialHostname);
  assert.equal(next.revision, 1);
  assert.equal(next.tlsStatus, 'managed-by-proxy');
  assert.deepEqual(createProxyStore(file).snapshot(), next);
  assert.throws(() => store.update({ revision: 0, hostname: initialHostname }), error => error.status === 409);
  assert.deepEqual(store.update({ revision: 1, hostname: next.hostname }), next);
  assert.equal(fs.readdirSync(path.dirname(file)).length, 1);
});
test('invalid input and corrupted profile never overwrite or reset a working connection', t => {
  const file = fixture(t), store = createProxyStore(file);
  for (const value of [null, [], { revision: 0, hostname: 'https://bad.example' },
    { revision: 0, hostname: 'deck.example', key: 'secret' }, { revision: -1, hostname: 'deck.example' }]) {
    assert.throws(() => store.update(value));
  }
  assert.equal(fs.existsSync(file), false);
  fs.writeFileSync(file, '{bad', { mode: 0o600 });
  assert.throws(() => createProxyStore(file));
});
test('private profile rejects links and writable directories', { skip: process.platform === 'win32' }, t => {
  const file = fixture(t);
  createProxyStore(file).update({ revision: 0, hostname: 'deck.example' });
  const copy = file + '.copy'; fs.renameSync(file, copy); fs.symlinkSync(copy, file);
  assert.throws(() => createProxyStore(file)); fs.unlinkSync(file);
  fs.linkSync(copy, file); assert.throws(() => createProxyStore(file)); fs.unlinkSync(file);
  fs.renameSync(copy, file); fs.chmodSync(file, 0o644); assert.throws(() => createProxyStore(file));
  fs.chmodSync(file, 0o600); fs.chmodSync(path.dirname(file), 0o777); assert.throws(() => createProxyStore(file));
});
