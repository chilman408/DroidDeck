// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { startProtectedConnectionServer, probeConnection } = require('./socket-startup');
const filename = '/run/droiddeck/connection.sock';
const missing = () => Object.assign(new Error('missing'), { code: 'ENOENT' });
function fixture(stale = false) {
  const calls = [], server = new EventEmitter(); server.listening = false;
  server.listen = (path, done) => { calls.push(['listen', path]); server.listening = true; done(); };
  server.shutdown = async () => { calls.push(['shutdown']); server.listening = false; server.emit('close'); };
  const socket = { uid: 0, gid: 1201, mode: 0o140660, nlink: 1, dev: 1, ino: 2, isSocket: () => true };
  const directory = { uid: 0, gid: 1201, mode: 0o40750, isDirectory: () => true };
  return { calls, server, socket, directory, filename, gid: 1201,
    acquireLock: async () => { calls.push(['lock']); return { close: done => { calls.push(['unlock']); done?.(); } }; },
    probe: async () => { calls.push(['probe']); return false; },
    io: { lstat: async path => { calls.push(['stat', path]); if (path === '/run/droiddeck') return directory;
      if (!stale) throw missing(); return { ...socket }; }, unlink: async path => calls.push(['unlink', path]),
      chown: async (...args) => calls.push(['chown', ...args]), chmod: async (...args) => calls.push(['chmod', ...args]) } };
}

test('portable listener startup serializes, removes only proven stale owned socket and retains lock until close', async () => {
  for (const stale of [false, true]) {
    const f = fixture(stale); await startProtectedConnectionServer(f);
    assert.equal(f.calls[0][0], 'lock'); assert.equal(f.calls.some(call => call[0] === 'unlock'), false);
    assert.deepEqual(f.calls.filter(call => call[0] === 'unlink'), stale ? [['unlink', filename]] : []);
    assert.deepEqual(f.calls.filter(call => call[0] === 'chown'), [['chown', filename, 0, 1201]]);
    assert.deepEqual(f.calls.filter(call => call[0] === 'chmod'), [['chmod', filename, 0o660]]);
    await f.server.shutdown(); assert.equal(f.calls.filter(call => call[0] === 'unlock').length, 1);
  }
});

test('active, foreign, symlink-like, changed or unprobeable sockets cannot be unlinked or bound', async () => {
  for (const patch of [{ uid: 1200 }, { gid: 1000 }, { isSocket: () => false }, { nlink: 2 }, { mode: 0o140666 }]) {
    const f = fixture(true); Object.assign(f.socket, patch);
    await assert.rejects(startProtectedConnectionServer(f));
    assert.equal(f.calls.some(call => ['unlink', 'listen'].includes(call[0])), false);
    assert.equal(f.calls.filter(call => call[0] === 'unlock').length, 1);
  }
  for (const probe of [async () => true, async () => undefined, async () => { throw new Error('timeout'); }]) {
    const f = fixture(true); f.probe = probe; await assert.rejects(startProtectedConnectionServer(f));
    assert.equal(f.calls.some(call => ['unlink', 'listen'].includes(call[0])), false);
  }
  const changed = fixture(true); changed.probe = async () => { changed.socket.ino++; return false; };
  await assert.rejects(startProtectedConnectionServer(changed), /changed/);
  assert.equal(changed.calls.some(call => ['unlink', 'listen'].includes(call[0])), false);
});

test('private pre-chmod crash socket can recover, but disappeared or altered metadata cannot be deleted', async () => {
  const privateSocket = fixture(true); privateSocket.socket.mode = 0o140700;
  await startProtectedConnectionServer(privateSocket); await privateSocket.server.shutdown();
  assert.equal(privateSocket.calls.filter(call => call[0] === 'unlink').length, 1);
  for (const mutate of [f => { f.socket.mode = 0o140777; }, f => { f.socket.gid = 1000; }, f => {
    const before = f.io.lstat; f.io.lstat = async name => { if (name === filename) throw missing(); return before(name); };
  }]) {
    const f = fixture(true); f.probe = async () => { mutate(f); return false; };
    await assert.rejects(startProtectedConnectionServer(f)); assert.equal(f.calls.some(call => call[0] === 'unlink'), false);
  }
});

test('startup errors close the listener and release lock, while invalid paths or identities cannot lock', async () => {
  for (const key of ['filename', 'gid']) {
    const f = fixture(); f[key] = key === 'filename' ? '/tmp/other.sock' : 0;
    await assert.rejects(startProtectedConnectionServer(f)); assert.equal(f.calls.length, 0);
  }
  const unsafe = fixture(); unsafe.directory.mode = 0o40777;
  await assert.rejects(startProtectedConnectionServer(unsafe)); assert.equal(unsafe.calls.some(call => call[0] === 'listen'), false);
  const chmod = fixture(); chmod.io.chmod = async () => { throw new Error('mode failed'); };
  await assert.rejects(startProtectedConnectionServer(chmod), /mode failed/);
  assert.equal(chmod.server.listening, false); assert.equal(chmod.calls.filter(call => call[0] === 'unlock').length, 1);
  const lock = fixture(); lock.acquireLock = async () => { throw new Error('occupied'); };
  await assert.rejects(startProtectedConnectionServer(lock), /occupied/); assert.equal(lock.calls.length, 0);
});

test('real Linux socket probe sends no application bytes and distinguishes active from missing', { skip: process.platform !== 'linux' }, async () => {
  const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path'), net = require('node:net');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'droiddeck-socket-probe-')), socket = path.join(directory, 'fixture.sock');
  let bytes = 0; const server = net.createServer(client => client.on('data', data => { bytes += data.length; }));
  try {
    assert.equal(await probeConnection(socket), false);
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socket, resolve); });
    assert.equal(await probeConnection(socket), true);
    await new Promise(resolve => server.close(resolve)); assert.equal(bytes, 0);
    assert.equal(await probeConnection(socket), false);
  } finally { if (server.listening) await new Promise(resolve => server.close(resolve)); await fs.rm(directory, { recursive: true, force: true }); }
});
