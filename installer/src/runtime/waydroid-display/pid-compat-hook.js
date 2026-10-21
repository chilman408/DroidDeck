// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const CONFIG = '/var/lib/waydroid/lxc/waydroid/config';
const SYSCTL = '/proc/sys/kernel/pid_max';

async function applyGuestPidLimit({ io = fs, environment = process.env, args = process.argv.slice(2), pid = process.pid } = {}) {
  if (environment.LXC_NAME !== 'waydroid' || environment.LXC_CONFIG_FILE !== CONFIG ||
      args.length !== 3 || args.join('/') !== 'waydroid/lxc/pre-mount') {
    throw new Error('PID compatibility requires the fixed Waydroid pre-mount hook');
  }
  const current = await io.readlink('/proc/self/ns/pid');
  const initial = await io.readlink('/proc/1/ns/pid');
  const status = await io.readFile('/proc/self/status', 'utf8');
  const pids = /^NSpid:\s+(\d+(?:[\t ]+\d+)*)$/m.exec(status)?.[1].split(/[\t ]+/).map(Number);
  if (!/^pid:\[\d+\]$/.test(current) || !/^pid:\[\d+\]$/.test(initial) || current === initial ||
      !pids || pids.length < 2 || pids.at(-1) !== pid || !Number.isSafeInteger(pid) || pid < 1 || pid > 65535 ||
      (await io.statfs('/proc/sys/kernel')).type !== 0x9fa0) {
    throw new Error('Refusing PID compatibility outside a verified guest PID namespace');
  }
  const readLimit = async () => {
    const value = (await io.readFile(SYSCTL, 'utf8')).trim();
    if (!/^\d+$/.test(value) || Number(value) < 1024 || Number(value) > 4194304) throw new Error('Invalid guest PID limit');
    return Number(value);
  };
  if (await readLimit() <= 65535) return 'unchanged';
  // proc sysctl lookup uses the caller's active PID namespace, not the proc mount's owner.
  // This runs before LXC installs the guest's readonly mounts; no remount is needed.
  await io.writeFile(SYSCTL, '65535\n');
  if (await readLimit() !== 65535) throw new Error('Guest PID compatibility verification failed');
  return 'updated';
}

if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid() !== 0) {
    process.stderr.write('PID compatibility hook requires Linux root\n'); process.exitCode = 1;
  } else {
    applyGuestPidLimit().then(result => process.stdout.write(`Guest PID compatibility hook: ${result}\n`))
      .catch(error => { process.stdout.write(error.message + '\n'); process.exitCode = 1; });
  }
}
module.exports = { applyGuestPidLimit };
