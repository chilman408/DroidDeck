// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const { constants } = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const CONFIG = '/var/lib/waydroid/lxc/waydroid/config';
const LIMIT = 262144;
const HOOK = '/usr/bin/node /opt/teslaandroid-waydroid/runtime/waydroid-display/pid-compat-hook.js';
const PORTABLE_HOOK = '/usr/bin/node /opt/droiddeck/runtime/waydroid-display/pid-compat-hook.js';
function selectedHook(portable) {
  if (typeof portable !== 'boolean') throw new Error('Invalid PID compatibility mode');
  return portable ? PORTABLE_HOOK : HOOK;
}

function entries(text) {
  if (typeof text !== 'string' || text.includes('\0') || Buffer.byteLength(text) > LIMIT) {
    throw new Error('Invalid PID configuration text');
  }
  return text.split(/\r?\n/).filter(line => !line.trim().startsWith('#')).map(line => {
    const index = line.indexOf('=');
    return index < 0 ? [] : [line.slice(0, index).trim(), line.slice(index + 1).trim()];
  });
}

function checkSettings(settings, hook = HOOK) {
  if (settings.some(([key, value]) => key === 'lxc.sysctl.kernel.pid_max' ||
      (value?.includes('pid-compat-hook.js') && (key !== 'lxc.hook.pre-mount' || value !== hook)))) {
    throw new Error('Conflicting or duplicate PID configuration');
  }
  const hooks = settings.filter(([key, value]) => key === 'lxc.hook.pre-mount' && value === hook);
  if (hooks.length > 1) throw new Error('Duplicate PID configuration hook');
  return hooks.length;
}

function renderPidCompatibility(text, { portable = false } = {}) {
  const hook = selectedHook(portable);
  if (checkSettings(entries(text), hook)) return text;
  return text + (text.endsWith('\n') ? '' : '\n') +
    '# TeslaAndroid: keep guest thread IDs compatible with 32-bit Android bionic.\n' +
    `lxc.hook.pre-mount = ${hook}\n`;
}

async function preparePidCompatibility({ filename = CONFIG, boundary = path.dirname(filename), expectedUid = 0, portable = false } = {}) {
  const command = selectedHook(portable);
  filename = path.resolve(filename); boundary = path.resolve(boundary);
  const snapshots = new Map();
  const inside = name => name.startsWith(boundary + path.sep);
  const protectedStat = (stat, directory = false) => {
    if (!(directory ? stat.isDirectory() : stat.isFile() && stat.nlink === 1) ||
        (process.platform !== 'win32' && (stat.uid !== expectedUid || (stat.mode & 0o022)))) {
      throw new Error('PID configuration is not protected');
    }
  };
  async function directories(directory, stop) {
    for (;;) {
      protectedStat(await fs.lstat(directory), true);
      if (directory === stop) return;
      const parent = path.dirname(directory);
      if (parent === directory) throw new Error('Invalid PID configuration boundary');
      directory = parent;
    }
  }
  await directories(boundary, filename === CONFIG ? path.parse(boundary).root : boundary);
  if (filename === CONFIG) {
    const hook = command.slice('/usr/bin/node '.length);
    await directories(path.dirname(hook), '/');
    protectedStat(await fs.lstat(hook));
  }
  async function read(name) {
    if (!inside(name)) throw new Error('Unsafe PID configuration include');
    await directories(path.dirname(name), boundary);
    protectedStat(await fs.lstat(name));
    const fd = await fs.open(name, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
    try {
      const stat = await fd.stat(); protectedStat(stat);
      if (stat.size > LIMIT) throw new Error('PID configuration too large');
      const bytes = await fd.readFile();
      return { stat, bytes, text: new TextDecoder('utf-8', { fatal: true }).decode(bytes) };
    } finally { await fd.close(); }
  }
  const settings = [];
  async function visit(name) {
    if (snapshots.has(name) || snapshots.size >= 16) throw new Error('Repeated or excessive PID configuration include');
    const snapshot = await read(name); snapshots.set(name, snapshot);
    for (const [key, value] of entries(snapshot.text)) {
      settings.push([key, value]);
      if (key === 'lxc.include') {
        if (!path.isAbsolute(value) || /[\0*?\[\]]/.test(value)) throw new Error('Unsafe PID configuration include');
        await visit(path.resolve(value));
      }
    }
  }
  await visit(filename);
  if (checkSettings(settings, command)) return 'unchanged';
  const original = snapshots.get(filename);
  const temporary = path.join(path.dirname(filename), `.ta-pid-${crypto.randomBytes(16).toString('hex')}.tmp`);
  let fd;
  try {
    fd = await fs.open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW || 0), 0o600);
    await fd.writeFile(renderPidCompatibility(original.text, { portable }));
    await fd.chmod(original.stat.mode & 0o777); await fd.sync(); await fd.close(); fd = null;
    // Refuse concurrent regeneration; never replace a different Waydroid config.
    for (const [name, snapshot] of snapshots) {
      const current = await read(name);
      if (current.stat.dev !== snapshot.stat.dev || current.stat.ino !== snapshot.stat.ino || !current.bytes.equals(snapshot.bytes)) {
        throw new Error('PID configuration changed during preparation');
      }
    }
    await fs.rename(temporary, filename);
    if (process.platform !== 'win32') {
      const directory = await fs.open(path.dirname(filename), 'r');
      try { await directory.sync(); } finally { await directory.close(); }
    }
    return 'updated';
  } finally {
    if (fd) await fd.close();
    await fs.unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
}

if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid() !== 0 || process.argv.length !== 2) {
    process.stderr.write('PID compatibility preparation requires Linux root and no arguments\n'); process.exitCode = 1;
  } else {
    preparePidCompatibility().then(result => process.stdout.write(`Guest PID compatibility: ${result}\n`))
      .catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
  }
}
module.exports = { renderPidCompatibility, preparePidCompatibility };
