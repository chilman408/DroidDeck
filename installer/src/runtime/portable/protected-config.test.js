// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { readProtectedRuntimeConfig, readPortableConfig, CONFIG_FILE } = require('./protected-config');
function fixture() {
  const reads = [], leaf = { uid: 0, mode: 0o644, nlink: 1, size: 120, ino: 2, dev: 1, mtimeMs: 1, ctimeMs: 1,
    isFile: () => true, isDirectory: () => false };
  const directory = { uid: 0, mode: 0o755, isDirectory: () => true, isFile: () => false };
  const nodes = new Map([['/', directory], ['/etc', directory], ['/etc/droiddeck', directory], ['/etc/droiddeck/runtime.json', leaf]]);
  const record = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
  const options = { io: { lstatSync: file => { reads.push(file); return { ...nodes.get(file) }; } }, read: file => { assert.equal(file, CONFIG_FILE); return record; } };
  return { reads, nodes, leaf, options, record };
}
test('protected config uses fixed root-owned path and default opt-out does not access files', () => {
  const f = fixture(); assert.equal(readPortableConfig({}, f.options), undefined); assert.equal(f.reads.length, 0);
  const profile = readPortableConfig({ DROIDDECK_PORTABLE_RUNTIME: '1' }, f.options);
  assert.equal(profile.user, 'deck'); assert.equal(Object.isFrozen(profile), true);
  assert.equal(profile.hostname, 'device.teslaandroid.com'); assert.equal(CONFIG_FILE, '/etc/droiddeck/runtime.json');
});
test('foreign, writable, linked, nonregular and oversized metadata paths fail before JSON read', () => {
  for (const [file, patch] of [
    ['/etc/droiddeck', { uid: 1200 }], ['/etc', { mode: 0o777 }], ['/', { isDirectory: () => false }],
    [CONFIG_FILE, { uid: 1200 }], [CONFIG_FILE, { mode: 0o666 }], [CONFIG_FILE, { nlink: 2 }],
    [CONFIG_FILE, { isFile: () => false }], [CONFIG_FILE, { size: 16385 }],
  ]) {
    const f = fixture(); f.nodes.set(file, { ...f.nodes.get(file), ...patch }); let parsed = false;
    f.options.read = () => { parsed = true; return f.record; };
    assert.throws(() => readProtectedRuntimeConfig(f.options)); assert.equal(parsed, false);
  }
});
test('generation changes, malformed secrets and invalid opt-in values fail generically', () => {
  const f = fixture(); f.options.read = () => { f.nodes.set(CONFIG_FILE, { ...f.leaf, ino: 3 }); return f.record; };
  assert.throws(() => readProtectedRuntimeConfig(f.options));
  for (const data of [{ ...f.record, password: 'private-token' }, null]) {
    const bad = fixture(); bad.options.read = () => data;
    assert.throws(() => readProtectedRuntimeConfig(bad.options), error => !/password|private-token/.test(error.message));
  }
  for (const value of ['0', 'true', '', 1]) {
    const invalid = fixture(); assert.throws(() => readPortableConfig({ DROIDDECK_PORTABLE_RUNTIME: value }, invalid.options));
    assert.equal(invalid.reads.length, 0);
  }
});
