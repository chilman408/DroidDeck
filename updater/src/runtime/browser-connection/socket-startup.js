// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises'), net = require('node:net');
const DIRECTORY = '/run/droiddeck', SOCKET = DIRECTORY + '/connection.sock';
function acquireLock() {
  if (process.platform !== 'linux') return Promise.reject(new Error('Portable connection lock requires Linux'));
  return new Promise((resolve, reject) => {
    const lock = net.createServer(socket => socket.destroy());
    lock.once('error', () => reject(new Error('Portable connection helper already active')));
    lock.listen('\0droiddeck-browser-connection-v1', () => resolve(lock));
  });
}
function probeConnection(filename) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ path: filename }); let done = false;
    const finish = (error, active) => {
      if (done) return; done = true; clearTimeout(timer); socket.destroy();
      error ? reject(error) : resolve(active);
    };
    const timer = setTimeout(() => finish(new Error('Connection socket probe timed out')), 1000);
    socket.once('connect', () => finish(null, true));
    socket.once('error', error => ['ECONNREFUSED', 'ENOENT'].includes(error.code) ? finish(null, false) : finish(new Error('Connection socket probe failed')));
  });
}
function ownedSocket(stat, gid) {
  if (!stat.isSocket() || stat.uid !== 0 || stat.gid !== gid || stat.nlink !== 1 ||
      ![0o700, 0o660].includes(stat.mode & 0o777)) throw new Error('Unsafe portable connection socket');
}
async function startProtectedConnectionServer({ server, filename, gid, io = fs, acquireLock: lockFactory = acquireLock, probe = probeConnection }) {
  if (filename !== SOCKET || !Number.isSafeInteger(gid) || gid < 1000 || gid > 2147483647) throw new Error('Invalid portable connection listener');
  const lock = await lockFactory(); let releasing, attempted = false;
  const release = () => releasing ||= new Promise((resolve, reject) => lock.close(error => error ? reject(error) : resolve()));
  try {
    const directory = await io.lstat(DIRECTORY);
    if (!directory.isDirectory() || directory.uid !== 0 || directory.gid !== gid ||
        (directory.mode & 0o777) !== 0o750) throw new Error('Unsafe portable connection socket directory');
    const before = await io.lstat(filename).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
    if (before) {
      ownedSocket(before, gid);
      if (await probe(filename) !== false) throw new Error('Portable connection listener active or unproven');
      const after = await io.lstat(filename); ownedSocket(after, gid);
      if (before.ino !== after.ino || before.dev !== after.dev) throw new Error('Portable connection socket changed');
      await io.unlink(filename);
    }
    server.once('close', () => { void release().catch(() => console.error('Portable connection lock cleanup failed')); });
    attempted = true;
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(filename, resolve); });
    await io.chown(filename, 0, gid); await io.chmod(filename, 0o660);
  } catch (error) {
    try { if (attempted) await server.shutdown(); }
    finally { await release(); }
    throw error;
  }
}
module.exports = { startProtectedConnectionServer, probeConnection };
