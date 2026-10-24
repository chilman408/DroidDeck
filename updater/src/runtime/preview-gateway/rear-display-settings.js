// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const DEFAULT = Object.freeze({ version: 1, revision: 0, enabled: false, prioritised: false });
const failure = (message, status) => Object.assign(new Error(message), { status });
function validPreference(value) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    typeof value.enabled === 'boolean' && typeof value.prioritised === 'boolean' &&
    Number.isSafeInteger(value.revision) && value.revision >= 0 && value.revision < Number.MAX_SAFE_INTEGER;
}
function validateProfile(value) {
  if (!validPreference(value) || value.version !== 1 || Object.keys(value).length !== 4) {
    throw new Error('Invalid rear display profile');
  }
  return { ...value };
}
function readRearDisplaySettings(filename, { uid = process.getuid?.() } = {}) {
  const parent = fs.lstatSync(path.dirname(filename));
  if (!parent.isDirectory() || (process.platform !== 'win32' && (parent.uid !== uid || (parent.mode & 0o077)))) {
    throw new Error('Unsafe rear display settings directory');
  }
  let fd;
  try {
    fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
  } catch (error) { if (error.code === 'ENOENT') return { ...DEFAULT }; throw error; }
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > 1024 ||
        (process.platform !== 'win32' && (stat.uid !== uid || (stat.mode & 0o077)))) {
      throw new Error('Unsafe rear display settings file');
    }
    const buffer = Buffer.alloc(1025), size = fs.readSync(fd, buffer, 0, buffer.length, 0);
    if (size > 1024) throw new Error('Rear display settings exceeded limit');
    return validateProfile(JSON.parse(buffer.subarray(0, size).toString('utf8')));
  } finally { fs.closeSync(fd); }
}
function createRearDisplaySettings(filename) {
  const snapshot = () => readRearDisplaySettings(filename);
  snapshot();
  return {
    snapshot,
    update(payload) {
      if (!validPreference(payload) || Object.keys(payload).length !== 3) {
        throw failure('Invalid rear display preferences', 400);
      }
      const current = snapshot();
      if (payload.revision !== current.revision) throw failure('Rear display preferences changed; reload settings', 409);
      if (payload.enabled === current.enabled && payload.prioritised === current.prioritised) return current;
      const next = validateProfile({ version: 1, ...payload, revision: current.revision + 1 });
      const temporary = filename + '.' + crypto.randomBytes(8).toString('hex') + '.tmp';
      let fd;
      try {
        fd = fs.openSync(temporary, 'wx', 0o600);
        fs.writeFileSync(fd, JSON.stringify(next) + '\n'); fs.fsyncSync(fd);
        fs.closeSync(fd); fd = undefined;
        fs.renameSync(temporary, filename);
      } finally {
        if (fd !== undefined) fs.closeSync(fd);
        if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
      }
      return next;
    },
  };
}
function preferredRole(preferences) {
  return preferences.enabled && preferences.prioritised ? 'rear' : 'primary';
}
function canDriveSize(preferences, role) {
  if (role !== null && !['primary', 'rear'].includes(role)) throw new Error('Invalid display role');
  return !preferences.enabled || role === preferredRole(preferences);
}
module.exports = { createRearDisplaySettings, readRearDisplaySettings, validateProfile, preferredRole, canDriveSize };
