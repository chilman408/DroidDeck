// SPDX-License-Identifier: GPL-3.0-only
const { promisify } = require('node:util'), { execFile } = require('node:child_process');
const { normalizeRuntimeConfig } = require('./runtime-config');
const execute = promisify(execFile);
function parseRuntimeAccount(value, configuration) {
  try {
    const config = normalizeRuntimeConfig(configuration); let text;
    if (Buffer.isBuffer(value)) {
      if (value.length > 4096) throw new Error();
      text = new TextDecoder('utf-8', { fatal: true }).decode(value);
    } else if (typeof value === 'string' && Buffer.byteLength(value) <= 4096) text = value;
    else throw new Error();
    text = text.replace(/\r?\n$/, '');
    if (/[\0\r\n]/.test(text)) throw new Error();
    const fields = text.split(':');
    if (fields.length !== 7 || fields[0] !== config.user || !/^[1-9]\d{0,9}$/.test(fields[2]) || !/^[1-9]\d{0,9}$/.test(fields[3]) ||
        Number(fields[2]) !== config.uid || Number(fields[3]) !== config.gid || fields[5] !== config.home) throw new Error();
    return Object.freeze({ user: config.user, uid: config.uid, gid: config.gid, home: config.home });
  } catch (_) { throw new Error('Runtime account identity does not match configuration'); }
}
async function lookupAccount(user, { signal } = {}) {
  if (process.platform !== 'linux') throw new Error('Linux account lookup required');
  const result = await execute('/usr/bin/getent', ['passwd', user], { signal, timeout: 5000, maxBuffer: 4096, encoding: 'buffer', windowsHide: true });
  return result.stdout;
}
async function validateRuntimeAccount(configuration, { lookup = lookupAccount, signal } = {}) {
  signal?.throwIfAborted();
  try {
    const config = normalizeRuntimeConfig(configuration);
    const row = await lookup(config.user, { signal }); signal?.throwIfAborted();
    return parseRuntimeAccount(row, config);
  } catch (_) {
    signal?.throwIfAborted();
    throw new Error('Runtime account identity unavailable or mismatched');
  }
}
module.exports = { parseRuntimeAccount, validateRuntimeAccount };
