// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const VIDEO_SETTINGS_FILE = '/home/hilman/.config/teslaandroid-waydroid/browser-video.json';
const BIT_RATES = Object.freeze([1000000, 2000000, 3000000, 4000000, 8000000, 12000000]);
const FRAME_RATES = Object.freeze([30, 45, 60]);
const DEFAULT = Object.freeze({ version: 1, revision: 0, bitRate: 4000000, maxFps: 30 });
function validTargets(state) {
  return state && typeof state === 'object' && !Array.isArray(state) && BIT_RATES.includes(state.bitRate) &&
    FRAME_RATES.includes(state.maxFps) && Number.isSafeInteger(state.revision) && state.revision >= 0 &&
    state.revision < Number.MAX_SAFE_INTEGER;
}
function validateProfile(state) {
  if (!validTargets(state) || state.version !== 1 || Object.keys(state).length !== 4) throw new Error('Invalid video profile');
  return { ...state };
}
function readVideoSettings(filename, { uid = process.getuid?.(), validate = validateProfile, missing = DEFAULT } = {}) {
  const parent = fs.lstatSync(path.dirname(filename));
  if (!parent.isDirectory() || (process.platform !== 'win32' && (parent.uid !== uid || (parent.mode & 0o077)))) {
    throw new Error('Unsafe video settings directory');
  }
  let fd;
  try {
    fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
  } catch (error) { if (error.code === 'ENOENT') return { ...missing }; throw error; }
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > 1024 ||
        (process.platform !== 'win32' && (stat.uid !== uid || (stat.mode & 0o077)))) throw new Error('Unsafe video settings file');
    const bytes = Buffer.alloc(1025), size = fs.readSync(fd, bytes, 0, bytes.length, 0);
    if (size > 1024) throw new Error('Video settings exceeded limit');
    return validate(JSON.parse(bytes.subarray(0, size).toString('utf8')));
  } finally { fs.closeSync(fd); }
}
function writeSettings(filename, next) {
  const temporary = filename + '.' + crypto.randomBytes(8).toString('hex') + '.tmp';
  let fd;
  try {
    fd = fs.openSync(temporary, 'wx', 0o600);
    fs.writeFileSync(fd, JSON.stringify(next) + '\n'); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
    fs.renameSync(temporary, filename);
  } finally { if (fd !== undefined) fs.closeSync(fd); if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
}
function validateAutomatic(state) {
  if (!state || Array.isArray(state) || Object.keys(state).sort().join(',') !== 'ceilingBitRate,ceilingMaxFps,enabled,revision,version' ||
      state.version !== 1 || typeof state.enabled !== 'boolean' || !validTargets({ ...state, bitRate: state.ceilingBitRate, maxFps: state.ceilingMaxFps })) {
    throw new Error('Invalid automatic video policy');
  }
  return { ...state };
}
function createVideoSettings(filename) {
  const automaticFile = filename.replace(/\.json$/, '') + '-automatic.json';
  const snapshot = () => readVideoSettings(filename);
  const automaticSnapshot = () => readVideoSettings(automaticFile, { validate: validateAutomatic,
    missing: { version: 1, revision: 0, enabled: false, ceilingBitRate: snapshot().bitRate, ceilingMaxFps: snapshot().maxFps } });
  snapshot(); automaticSnapshot();
  function targets(payload) {
    if (!validTargets(payload) || Object.keys(payload).length !== 3) throw Object.assign(new Error('Invalid video targets'), { status: 400 });
    const current = snapshot();
    if (payload.revision !== current.revision) throw Object.assign(new Error('Video targets changed; reload settings'), { status: 409 });
    return current;
  }
  function writeTargets(payload, current) {
    if (payload.bitRate === current.bitRate && payload.maxFps === current.maxFps) return current;
    const next = validateProfile({ version: 1, ...payload, revision: current.revision + 1 });
    writeSettings(filename, next); return next;
  }
  function policy(value) {
    const next = validateAutomatic(value); writeSettings(automaticFile, next); return next;
  }
  return {
    snapshot, automaticSnapshot,
    update(payload) {
      const current = targets(payload), automatic = automaticSnapshot();
      // Manual choices always take over, including selecting the current effective value.
      if (automatic.enabled) policy({ ...automatic, enabled: false, revision: automatic.revision + 1 });
      return writeTargets(payload, current);
    },
    configureAutomatic(payload) {
      if (!payload || Array.isArray(payload) || Object.keys(payload).sort().join(',') !== 'enabled,profileRevision,revision' ||
          typeof payload.enabled !== 'boolean') throw Object.assign(new Error('Closed automatic policy required'), { status: 400 });
      const current = snapshot(), automatic = automaticSnapshot();
      if (payload.revision !== automatic.revision || payload.profileRevision !== current.revision) {
        throw Object.assign(new Error('Video settings changed; reload settings'), { status: 409 });
      }
      if (payload.enabled === automatic.enabled) return automatic;
      if (payload.enabled) return policy({ version: 1, revision: automatic.revision + 1, enabled: true,
        ceilingBitRate: current.bitRate, ceilingMaxFps: current.maxFps });
      const next = policy({ ...automatic, enabled: false, revision: automatic.revision + 1 });
      writeTargets({ revision: current.revision, bitRate: automatic.ceilingBitRate, maxFps: automatic.ceilingMaxFps }, current);
      return next;
    },
    adapt(payload, policyRevision) {
      const current = targets(payload), automatic = automaticSnapshot();
      if (!automatic.enabled || automatic.revision !== policyRevision || payload.bitRate > automatic.ceilingBitRate || payload.maxFps > automatic.ceilingMaxFps) {
        throw Object.assign(new Error('Automatic video authority changed'), { status: 409 });
      }
      return writeTargets(payload, current);
    },
  };
}
module.exports = { VIDEO_SETTINGS_FILE, BIT_RATES, FRAME_RATES, validateProfile, readVideoSettings, createVideoSettings };
