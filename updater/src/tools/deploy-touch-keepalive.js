// SPDX-License-Identifier: GPL-3.0-only
// Fixed disposable VM binary update; no Android, codec or preference changes.
const fs = require('node:fs/promises'), crypto = require('node:crypto');
const { execFile } = require('node:child_process'), { promisify } = require('node:util');
const execute = promisify(execFile);
const BEFORE = 'd98f6e1e03b6c2fd34195acab4a49b43666a9965ca819bad2707f7b109757f57';
const AFTER = '9b19bf943e03bc6fb2f84a5eb3d8783cab01c3182d8abf621e8cdf114e61a32a';
const ROOT = '/opt/teslaandroid-waydroid/touch-keepalive-20261012s';
const BACKUP = '/var/lib/teslaandroid-waydroid/backups/touch-keepalive-20261012s';
const UNIT = 'tesla-waydroid-runtime.service';
const BINARY = '/data/local/tmp/tesla-waydroid-test-20261003/bin/tesla-android-x86-touch';
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
async function deployTouch({ identity, readCurrent, readCandidate, saveBackup, stop, assertStopped, publish, start, waitReady, digest: hash = digest }) {
  const originalIdentity = await identity(), original = await readCurrent(), candidate = await readCandidate();
  if (hash(original) !== BEFORE || hash(candidate) !== AFTER) throw new Error('Touch artifact checksum mismatch');
  await saveBackup(original);
  let stopped = false, installed = false;
  try {
    await stop(); stopped = true; await assertStopped();
    if (await identity() !== originalIdentity) throw new Error('Container identity changed');
    if (hash(await readCurrent()) !== BEFORE) throw new Error('Touch artifact changed');
    installed = true; await publish(candidate);
    await start(); await waitReady();
  } catch (error) {
    if (await identity() !== originalIdentity) throw new Error('Container identity changed; inspect recovery checkpoint');
    if (installed) {
      await stop(); await assertStopped();
      if (await identity() !== originalIdentity) throw new Error('Touch rollback ownership changed');
      const currentHash = hash(await readCurrent());
      if (![BEFORE, AFTER].includes(currentHash)) throw new Error('Touch rollback ownership changed');
      if (currentHash === AFTER) await publish(original);
    }
    if (stopped) { await start(); await waitReady(); }
    throw error;
  }
}
function protectedStat(stat, directory, privateFile = false) {
  if (stat.uid !== 0 || stat.mode & (privateFile ? 0o077 : 0o022) ||
      !(directory ? stat.isDirectory() : stat.isFile()) || (!directory && (stat.nlink !== 1 || stat.size > 2 * 1024 * 1024))) throw new Error('Unsafe touch deployment artifact');
}
async function boundedFile(filename, privateFile = false) {
  const initial = await fs.lstat(filename); protectedStat(initial, false, privateFile);
  const handle = await fs.open(filename, require('node:fs').constants.O_RDONLY | require('node:fs').constants.O_NOFOLLOW);
  try {
    const opened = await handle.stat(); protectedStat(opened, false, privateFile);
    if (initial.ino !== opened.ino || initial.dev !== opened.dev || initial.size !== opened.size) throw new Error('Touch artifact changed');
    const bytes = Buffer.alloc(opened.size + 1); let length = 0;
    while (length < bytes.length) {
      const result = await handle.read(bytes, length, bytes.length - length, null);
      if (!result.bytesRead) break; length += result.bytesRead;
    }
    const final = await handle.stat(); protectedStat(final, false, privateFile);
    if (length !== opened.size || final.size !== opened.size || final.mtimeMs !== opened.mtimeMs || final.ctimeMs !== opened.ctimeMs) throw new Error('Touch artifact changed');
    return bytes.subarray(0, length);
  } finally { await handle.close(); }
}
async function main(args) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || args.length !== 1 || !['status', 'apply', 'rollback'].includes(args[0])) throw new Error('Fixed Linux root touch deployment required');
  const run = (file, values, options = {}) => execute(file, values, { timeout: 65000, maxBuffer: 1024 * 1024, ...options });
  const service = (...values) => run('/usr/bin/systemctl', values);
  let pid;
  async function identity() {
    const value = (await run('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'])).stdout.trim();
    if (!/^[1-9]\d*$/.test(value) || Number(value) < 2 || !Number.isSafeInteger(Number(value))) throw new Error('Invalid container identity');
    pid ??= value;
    if (pid !== value) throw new Error('Container identity changed');
    const stat = await fs.readFile('/proc/' + pid + '/stat', 'utf8');
    const birth = stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19];
    if (!/^\d+$/.test(birth)) throw new Error('Invalid container birth');
    return pid + ':' + birth;
  }
  const stamp = await identity(), target = '/proc/' + pid + '/root' + BINARY;
  const before = digest(await boundedFile(target));
  if (args[0] === 'status') {
    const backup = await fs.lstat(BACKUP).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
    console.log(JSON.stringify({ guestPid: Number(pid), binarySha256: before, backupExists: Boolean(backup), live: before === AFTER })); return;
  }
  for (const directory of ['/opt', '/opt/teslaandroid-waydroid', ROOT,
    '/var/lib/teslaandroid-waydroid', '/var/lib/teslaandroid-waydroid/backups',
    '/proc/' + pid + '/root/data/local/tmp/tesla-waydroid-test-20261003',
    '/proc/' + pid + '/root/data/local/tmp/tesla-waydroid-test-20261003/bin']) protectedStat(await fs.lstat(directory), true);
  const expected = args[0] === 'apply' ? BEFORE : AFTER;
  if (before !== expected) throw new Error('Touch artifact checksum mismatch');
  if (args[0] === 'apply' && (await service('show', UNIT, '--property=ActiveState', '--value')).stdout.trim() !== 'active') throw new Error('Streaming parent must be active before deployment');
  const publish = async bytes => {
    if (await identity() !== stamp) throw new Error('Container identity changed');
    const temporary = target + '.keepalive-20261012s.tmp';
    await fs.writeFile(temporary, bytes, { flag: 'wx', mode: 0o700 });
    await fs.chown(temporary, 0, 0); await fs.chmod(temporary, 0o700);
    await fs.rename(temporary, target);
    if (digest(await boundedFile(target)) !== digest(bytes)) throw new Error('Touch publication failed');
  };
  const stop = () => service('stop', UNIT), start = () => service('start', UNIT);
  const assertStopped = async () => {
    for (const unit of [UNIT, 'tesla-waydroid-touch-preview.service', 'tesla-waydroid-display-helper-preview.service']) {
      const state = (await service('show', unit, '--property=ActiveState', '--value')).stdout.trim();
      if (!['inactive', 'failed'].includes(state)) throw new Error('Touch runtime must be stopped');
    }
    try {
      await run('/usr/bin/nsenter', ['-t', pid, '-m', '-p', '-r', '--', '/system/bin/toybox', 'pidof', 'tesla-android-x86-touch']);
    } catch (error) {
      if (error.code === 1) return;
      throw error;
    }
    throw new Error('Guest touch process must be stopped');
  };
  const waitReady = async () => {
    for (let attempt = 0; attempt < 40; attempt++) {
      if (await identity() !== stamp) throw new Error('Container identity changed');
      const output = (await service('show', UNIT, '--property=ActiveState', '--value')).stdout.trim();
      const ready = (await run('/usr/bin/journalctl', ['-u', UNIT, '-n', '1', '--no-pager', '-o', 'cat'])).stdout.trim();
      if (output === 'active' && ready === 'Runtime ready') return;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error('Streaming parent did not become ready');
  };
  if (args[0] === 'rollback') {
    protectedStat(await fs.lstat(BACKUP), true);
    const original = await boundedFile(BACKUP + '/original', true);
    if (digest(original) !== BEFORE) throw new Error('Rollback checksum mismatch');
    await stop(); await assertStopped();
    if (digest(await boundedFile(target)) !== AFTER) throw new Error('Rollback ownership changed');
    await publish(original); await start(); await waitReady();
    console.log(JSON.stringify({ rolledBack: true, guestPid: Number(pid), androidRestarted: false })); return;
  }
  const saveBackup = async bytes => {
    await fs.mkdir(BACKUP, { mode: 0o700 });
    await fs.writeFile(BACKUP + '/original', bytes, { flag: 'wx', mode: 0o600 });
    await fs.chown(BACKUP + '/original', 0, 0);
    if (digest(await boundedFile(BACKUP + '/original', true)) !== BEFORE) throw new Error('Backup verification failed');
  };
  await deployTouch({ identity, readCurrent: () => boundedFile(target), readCandidate: () => boundedFile(ROOT + '/tesla-android-x86-touch'),
    saveBackup, stop, assertStopped, publish, start, waitReady });
  console.log(JSON.stringify({ installed: true, guestPid: Number(pid), binarySha256: AFTER, androidRestarted: false, preferencesChanged: false }));
}
if (require.main === module) main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { deployTouch, BEFORE, AFTER, protectedStat };
