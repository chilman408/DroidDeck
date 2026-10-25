// SPDX-License-Identifier: GPL-3.0-only
// Fixed .90 code-only migration. User preferences and Android apps are never restored or edited.
const fs = require('node:fs/promises'), path = require('node:path'), crypto = require('node:crypto');
const { promisify } = require('node:util'), { execFile } = require('node:child_process');
const run = promisify(execFile);
const ROOT = '/opt/teslaandroid-waydroid', STAGE = '/home/hilman/teslaandroid-waydroid/rear-deploy-stage-20261009';
const BACKUP = '/var/lib/teslaandroid-waydroid/backups/compositor-capture-20261009b';
const RUNTIME = 'tesla-waydroid-runtime.service', SESSION = 'tesla-waydroid-headless-session.service', COMPOSITOR = 'tesla-waydroid-compositor.service';
const files = [
  ['runtime/waydroid-display/runtime-commands.js', ROOT + '/runtime/waydroid-display/runtime-commands.js'],
  ['runtime/waydroid-display/compositor-source.js', ROOT + '/runtime/waydroid-display/compositor-source.js'],
  ['runtime/preview-gateway/compositor-video-launcher.js', ROOT + '/runtime/preview-gateway/compositor-video-launcher.js'],
  ['runtime/preview-gateway/compositor-h264.py', ROOT + '/runtime/preview-gateway/compositor-h264.py'],
  ['runtime/waydroid-display/compositor-pipewire.conf', '/etc/systemd/system/' + COMPOSITOR + '.d/50-compositor-capture.conf'],
  ['runtime/waydroid-display/compositor-capture.conf', '/etc/systemd/system/' + RUNTIME + '.d/50-compositor-capture.conf'],
];
const PREFS = ['/var/lib/teslaandroid-waydroid/display.json', '/var/lib/teslaandroid-waydroid/gps.json',
  '/home/hilman/.config/teslaandroid-waydroid/browser-access.json', '/home/hilman/.config/teslaandroid-waydroid/browser-audio.json',
  '/home/hilman/.config/teslaandroid-waydroid/browser-video.json', '/home/hilman/.config/teslaandroid-waydroid/rear-display.json'];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const service = (...args) => run('/usr/bin/systemctl', args, { timeout: 120000 });
async function optionalStat(filename) {
  try { return await fs.lstat(filename); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
async function hashes() {
  const result = {};
  for (const filename of PREFS) {
    const stat = await optionalStat(filename);
    if (stat && (!stat.isFile() || stat.nlink !== 1)) throw new Error('Unsafe preference file');
    result[filename] = stat ? sha(await fs.readFile(filename)) : null;
  }
  return result;
}
function validateManifest(manifest) {
  if (!manifest || !Array.isArray(manifest.files) || manifest.files.length !== files.length) throw new Error('Invalid capture backup');
  for (const [index, record] of manifest.files.entries()) {
    if (record.target !== files[index][1] || record.saved !== BACKUP + '/code-' + index || typeof record.existed !== 'boolean' ||
        !/^[a-f0-9]{64}$/.test(record.installedSha256) || (record.existed && !/^[a-f0-9]{64}$/.test(record.previousSha256))) {
      throw new Error('Invalid capture backup target');
    }
  }
}
async function stop() {
  await service('stop', RUNTIME);
  await service('stop', SESSION);
  // SIGUSR1 ends the UI but leaves container-side session tracking on Waydroid1.6.
  await run('/usr/sbin/runuser', ['-u', 'hilman', '--', '/usr/bin/env',
    'XDG_RUNTIME_DIR=/run/user/1000', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
    'LD_LIBRARY_PATH=' + ROOT + '/libgbinder', '/usr/bin/waydroid', 'session', 'stop'], { timeout: 30000 });
  await service('stop', COMPOSITOR);
}
async function start() {
  await service('daemon-reload');
  await service('start', COMPOSITOR);
  await service('start', SESSION);
  await service('start', RUNTIME);
}
async function restore(manifest) {
  validateManifest(manifest);
  for (const record of manifest.files) if (record.existed) {
    const saved = await fs.lstat(record.saved);
    if (!saved.isFile() || saved.uid !== 0 || (saved.mode & 0o077) || sha(await fs.readFile(record.saved)) !== record.previousSha256) {
      throw new Error('Unsafe capture rollback artifact');
    }
  }
  await stop();
  for (const record of manifest.files) {
    if (record.existed) { await fs.copyFile(record.saved, record.target); await fs.chmod(record.target, 0o644); }
    else await fs.unlink(record.target).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
  await start();
}
async function main() {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length > 3 ||
      (process.argv[2] !== undefined && !['--rollback', '--retry'].includes(process.argv[2]))) throw new Error('Fixed root capture deployment required');
  if (process.argv[2] === '--rollback') {
    const stat = await fs.lstat(BACKUP + '/manifest.json');
    if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o077)) throw new Error('Unsafe capture manifest');
    await restore(JSON.parse(await fs.readFile(BACKUP + '/manifest.json', 'utf8')));
    console.log('Capture code rollback complete; user preferences untouched'); return;
  }
  for (const unit of [RUNTIME, SESSION, COMPOSITOR]) {
    if ((await service('is-active', unit)).stdout.trim() !== 'active') throw new Error('All private runtime units must be active');
  }
  const before = await hashes(), records = [];
  for (const [index, [source, target]] of files.entries()) {
    const stat = await fs.lstat(STAGE + '/' + source), installed = await optionalStat(target);
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > 1024 * 1024) throw new Error('Unsafe staged capture code');
    if (installed && (!installed.isFile() || installed.nlink !== 1 || installed.uid !== 0 || (installed.mode & 0o022))) throw new Error('Unsafe installed capture code');
    const parent = path.dirname(target);
    if (!await optionalStat(parent) && target.startsWith('/etc/systemd/system/')) {
      const outer = await fs.lstat(path.dirname(parent));
      if (!outer.isDirectory() || outer.uid !== 0 || (outer.mode & 0o022)) throw new Error('Unsafe service directory');
      await fs.mkdir(parent, { mode: 0o755 });
    }
    const directory = await fs.lstat(parent);
    if (!directory.isDirectory() || directory.uid !== 0 || (directory.mode & 0o022)) throw new Error('Unsafe capture directory');
    const data = await fs.readFile(STAGE + '/' + source);
    records.push({ target, saved: BACKUP + '/code-' + index, existed: Boolean(installed), data,
      installedSha256: sha(data), previousSha256: installed ? sha(await fs.readFile(target)) : null });
  }
  const manifest = { preferences: before, files: records.map(({ data, ...record }) => record) };
  validateManifest(manifest);
  if (process.argv[2] === '--retry') {
    const saved = await fs.lstat(BACKUP + '/manifest.json');
    if (!saved.isFile() || saved.uid !== 0 || (saved.mode & 0o077)) throw new Error('Unsafe retry manifest');
    const previous = JSON.parse(await fs.readFile(BACKUP + '/manifest.json', 'utf8'));
    validateManifest(previous);
    if (JSON.stringify(previous.files) !== JSON.stringify(manifest.files)) throw new Error('Retry code differs from original verified candidate or rollback');
  } else {
    await fs.mkdir(BACKUP, { mode: 0o700 });
    for (const record of records) if (record.existed) { await fs.copyFile(record.target, record.saved); await fs.chmod(record.saved, 0o600); }
    await fs.writeFile(BACKUP + '/manifest.json', JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  }
  try {
    await stop();
    for (const record of records) {
      await fs.writeFile(record.target, record.data, { mode: 0o644 });
      await fs.chown(record.target, 0, 0); await fs.chmod(record.target, 0o644);
    }
    await require(ROOT + '/runtime/waydroid-display/runtime-preflight').verifyProtectedTree(ROOT + '/runtime');
    await start();
    const probe = require(ROOT + '/runtime/waydroid-display/host-adapter').execute;
    const deadline = Date.now() + 180000;
    for (;;) {
      try {
        const pid = (await probe('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], { env: process.env })).toString().trim();
        if (!/^\d+$/.test(pid) || Number(pid) < 2) throw new Error('Guest not ready');
        const result = await probe('/usr/bin/nsenter', ['-t', pid, '-n', '/usr/sbin/runuser', '-u', 'hilman', '--', '/usr/bin/env',
          'NODE_PATH=' + ROOT + '/runtime/preview-gateway/node_modules', '/usr/bin/node',
          ROOT + '/runtime/preview-gateway/runtime-probe.js', 'video-relay'], { env: process.env });
        if (result.toString().trim() === 'READY') break;
      } catch (error) { if (Date.now() >= deadline) throw error; }
      if (Date.now() >= deadline) throw new Error('Compositor stream did not recover');
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (JSON.stringify(before) !== JSON.stringify(await hashes())) throw new Error('Preferences changed during capture migration');
    console.log(JSON.stringify({ deployed: true, backup: BACKUP, preferencesUnchanged: true, paidPlaybackAccepted: false }));
  } catch (error) { await restore(manifest); throw error; }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { validateManifest, files, BACKUP };
