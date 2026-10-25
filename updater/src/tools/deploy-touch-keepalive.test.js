// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { deployTouch, BEFORE, AFTER } = require('./deploy-touch-keepalive');
function fixture({ failStart = false, changedIdentity = false, backupExists = false } = {}) {
  const events = [], before = Buffer.from('before'), after = Buffer.from('after');
  let current = before, backup;
  const dependencies = { identity: async () => changedIdentity && events.includes('stop') ? 'changed' : 'original',
    readCurrent: async () => current, readCandidate: async () => after,
    digest: bytes => bytes.toString() === 'before' ? BEFORE : AFTER,
    saveBackup: async bytes => { if (backupExists) throw new Error('Backup exists'); events.push('backup'); backup = bytes; },
    stop: async () => events.push('stop'), assertStopped: async () => events.push('stopped'),
    publish: async bytes => { events.push(bytes === before ? 'restore' : 'publish'); current = bytes; },
    start: async () => { events.push('start'); if (failStart && current === after) throw new Error('Startup failed'); },
    waitReady: async () => events.push('ready') };
  return { dependencies, events, current: () => current };
}
test('touch deployment backs up exact original bytes, fences stopped identity, publishes and waits ready', async () => {
  const f = fixture(); await deployTouch(f.dependencies);
  assert.deepEqual(f.events, ['backup', 'stop', 'stopped', 'publish', 'start', 'ready']);
  assert.equal(f.current().toString(), 'after');
});
test('failed stream startup restores only owned candidate bytes and brings the parent back', async () => {
  const f = fixture({ failStart: true }); await assert.rejects(deployTouch(f.dependencies), /Startup failed/);
  assert.deepEqual(f.events, ['backup', 'stop', 'stopped', 'publish', 'start', 'stop', 'stopped', 'restore', 'start', 'ready']);
  assert.equal(f.current().toString(), 'before');
});
test('changed container identity, corrupt artifacts or existing backup cannot overwrite unrelated work', async () => {
  const moved = fixture({ changedIdentity: true }); await assert.rejects(deployTouch(moved.dependencies), /identity/);
  assert.equal(moved.events.includes('publish'), false); assert.equal(moved.current().toString(), 'before');
  const existing = fixture({ backupExists: true }); await assert.rejects(deployTouch(existing.dependencies), /Backup exists/);
  assert.deepEqual(existing.events, []);
  const corrupt = fixture(); corrupt.dependencies.digest = () => '0'.repeat(64);
  await assert.rejects(deployTouch(corrupt.dependencies), /checksum/); assert.deepEqual(corrupt.events, []);
});

test('failed publication verification restores candidate bytes but never overwrites foreign bytes', async () => {
  const f = fixture(), publish = f.dependencies.publish;
  f.dependencies.publish = async bytes => { await publish(bytes); if (bytes.toString() === 'after') throw new Error('Publish verification failed'); };
  await assert.rejects(deployTouch(f.dependencies), /Publish verification/);
  assert.equal(f.current().toString(), 'before'); assert.equal(f.events.at(-1), 'ready');
  const foreign = fixture(); const originalRead = foreign.dependencies.readCurrent;
  foreign.dependencies.readCurrent = async () => foreign.events.includes('publish') ? Buffer.from('foreign') : originalRead();
  const hash = foreign.dependencies.digest; foreign.dependencies.digest = bytes => bytes.toString() === 'foreign' ? '0'.repeat(64) : hash(bytes);
  foreign.dependencies.waitReady = async () => { throw new Error('Startup failed'); };
  await assert.rejects(deployTouch(foreign.dependencies), /rollback ownership/);
  assert.equal(foreign.events.includes('restore'), false);
});

test('root binary guards reject writable, linked, oversized and nonregular candidates', () => {
  const { protectedStat } = require('./deploy-touch-keepalive');
  const valid = { uid: 0, mode: 0o100700, nlink: 1, size: 1024, isFile: () => true };
  assert.doesNotThrow(() => protectedStat(valid, false));
  for (const changes of [{ uid: 1000 }, { mode: 0o100722 }, { nlink: 2 }, { size: 2097153 }, { isFile: () => false }]) assert.throws(() => protectedStat({ ...valid, ...changes }, false));
});
