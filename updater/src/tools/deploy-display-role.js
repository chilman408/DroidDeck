// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises'), { execFile } = require('node:child_process'), { promisify } = require('node:util');
const execute = promisify(execFile);
const ROOT = '/opt/teslaandroid-waydroid/display-role-deploy-20261012t';
const HELPER = ROOT + '/deploy-auto-h264-recovery.js';
const UNIT = 'tesla-waydroid-runtime.service';
async function deployRole({ preflight, stop, installRuntime, installFrontend, rollbackFrontend, rollbackRuntime, start, waitReady }) {
  await preflight(); let stopped = false, runtime = false, frontend = false;
  try {
    await stop(); stopped = true;
    await installRuntime(); runtime = true;
    await installFrontend(); frontend = true;
    await start(); await waitReady();
  } catch (error) {
    if (stopped) {
      await stop();
      if (frontend) await rollbackFrontend();
      if (runtime) await rollbackRuntime();
      await start(); await waitReady();
    }
    throw error;
  }
}
async function main() {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length !== 2) throw new Error('Fixed Linux root display role deployment required');
  const run = (file, args) => execute(file, args, { timeout: 65000, maxBuffer: 1024 * 1024 });
  const service = (...args) => run('/usr/bin/systemctl', args);
  const guestPid = async () => {
    const value = (await run('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'])).stdout.trim();
    if (!/^[1-9]\d*$/.test(value) || Number(value) < 2) throw new Error('Invalid guest identity');
    return value;
  };
  const pid = await guestPid();
  const preflight = async () => {
    for (const directory of ['/opt', '/opt/teslaandroid-waydroid', ROOT]) {
      const stat = await fs.lstat(directory);
      if (!stat.isDirectory() || stat.uid !== 0 || stat.mode & 0o022) throw new Error('Unsafe deployment directory');
    }
    const stat = await fs.lstat(HELPER);
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== 0 || stat.mode & 0o022) throw new Error('Unsafe deployment helper');
    for (const kind of ['runtime', 'frontend']) {
      try { await fs.lstat('/var/lib/teslaandroid-waydroid/backups/display-role-' + kind + '-20261012t'); }
      catch (error) { if (error.code === 'ENOENT') continue; throw error; }
      throw new Error('Display role backup already exists; do not replay deployment');
    }
    if ((await service('show', UNIT, '--property=ActiveState', '--value')).stdout.trim() !== 'active') throw new Error('Streaming parent must be active');
  };
  const waitReady = async () => {
    for (let attempt = 0; attempt < 60; attempt++) {
      if (await guestPid() !== pid) throw new Error('Android guest changed');
      const active = (await service('show', UNIT, '--property=ActiveState', '--value')).stdout.trim();
      const phase = (await run('/usr/bin/journalctl', ['-u', UNIT, '-n', '1', '--no-pager', '-o', 'cat'])).stdout.trim();
      if (active === 'active' && phase === 'Runtime ready') return;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error('Streaming parent did not become ready');
  };
  const apply = (kind, rollback) => run('/usr/bin/node', [HELPER, '--display-role-' + kind, ...(rollback ? ['--rollback'] : [])]);
  await deployRole({ preflight, stop: () => service('stop', UNIT), start: () => service('start', UNIT), waitReady,
    installRuntime: () => apply('runtime'), installFrontend: () => apply('frontend'),
    rollbackFrontend: () => apply('frontend', true), rollbackRuntime: () => apply('runtime', true) });
  console.log(JSON.stringify({ installed: true, guestPid: Number(pid), androidRestarted: false, preferenceWrites: false }));
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { deployRole };
