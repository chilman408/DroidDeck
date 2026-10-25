// SPDX-License-Identifier: GPL-3.0-only
// Recovery must remain independent of the mutable installed display tree.
// Keep only bounded command execution and process identities here, with builtins.
const fs = require('node:fs/promises');
const { spawn } = require('node:child_process');
const ENV = Object.freeze({ PATH: '/usr/sbin:/usr/bin:/sbin:/bin', LANG: 'C', LC_ALL: 'C' });

function execute(file, args, { signal, timeoutMs = 10000 } = {}) {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 10000) throw Error('Invalid recovery command timeout');
    const grouped = process.platform !== 'win32';
    const child = spawn(file, args, { env: ENV, cwd: '/', shell: false, detached: grouped,
      stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    const output = []; let size = 0, errors = 0, failure, closed = false;
    const stop = error => {
      if (closed) return;
      failure ||= error;
      try {
        if (grouped && child.pid) process.kill(-child.pid, 'SIGKILL');
        else child.kill('SIGKILL');
      } catch (error) { if (error.code !== 'ESRCH') failure = error; }
    };
    const abort = () => stop(signal.reason || Error('Recovery command cancelled'));
    const timer = setTimeout(() => stop(Error('Recovery command timed out')), timeoutMs);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    child.stdout.on('data', chunk => {
      size += chunk.length;
      if (size > 4 * 1024 * 1024) stop(Error('Recovery output exceeds bound'));
      else output.push(chunk);
    });
    child.stderr.on('data', chunk => {
      errors += chunk.length;
      if (errors > 65536) stop(Error('Recovery error output exceeds bound'));
    });
    child.on('error', error => { failure ||= error; });
    child.on('close', code => {
      closed = true; clearTimeout(timer); signal?.removeEventListener('abort', abort);
      // Never forward guest stderr: it is not health evidence.
      if (!failure && code !== 0) failure = Error('Recovery command failed');
      if (failure) reject(failure); else resolve(Buffer.concat(output));
    });
  });
}

function pid(value) {
  if (!Number.isSafeInteger(value) || value < 2) throw Error('Invalid recovery process PID');
  return value;
}
function parseProcessStart(text, process) {
  pid(process);
  if (typeof text !== 'string' || Buffer.byteLength(text) > 16384 || text.includes('\0')) throw Error('Invalid recovery process stat');
  const end = text.lastIndexOf(') '), fields = text.slice(end + 2).trim().split(/\s+/);
  if (!text.startsWith(`${process} (`) || end < 0 || !/^[1-9]\d*$/.test(fields[19] || '')) {
    throw Error('Invalid recovery process identity');
  }
  return fields[19];
}
async function readContainerIdentity(process, { io = fs, signal } = {}) {
  pid(process); signal?.throwIfAborted();
  const start = parseProcessStart(await io.readFile(`/proc/${process}/stat`, { encoding: 'utf8', signal }), process);
  const namespace = await io.stat(`/proc/${process}/ns/net`, { bigint: true });
  if (typeof namespace.dev !== 'bigint' || typeof namespace.ino !== 'bigint' || namespace.dev < 0n || namespace.ino < 1n) {
    throw Error('Invalid recovery namespace identity');
  }
  signal?.throwIfAborted();
  return `${namespace.dev}:${namespace.ino}:${start}`;
}
async function readAndroidIdentity(process, options = {}, { run = execute, io = fs } = {}) {
  pid(process); const identities = [];
  for (const name of ['system_server', 'surfaceflinger']) {
    options.signal?.throwIfAborted();
    const raw = await run('/usr/bin/nsenter', ['-t', String(process), '-m', '-p', '-r', '--',
      '/system/bin/toybox', 'pidof', name], options);
    if (!(typeof raw === 'string' || Buffer.isBuffer(raw)) || Buffer.byteLength(raw) > 128) throw Error('Invalid Android process PID');
    const value = raw.toString().trim(), guest = Number(value);
    if (!/^[1-9]\d*$/.test(value)) throw Error('Ambiguous Android process PID');
    pid(guest);
    const stat = await io.readFile(`/proc/${process}/root/proc/${guest}/stat`, { encoding: 'utf8', signal: options.signal });
    identities.push(`${guest}:${parseProcessStart(stat, guest)}`);
  }
  options.signal?.throwIfAborted();
  return identities.join('/');
}
module.exports = { execute, parseProcessStart, readContainerIdentity, readAndroidIdentity };
