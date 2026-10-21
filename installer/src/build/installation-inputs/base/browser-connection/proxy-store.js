// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const { normalizeHostname, DEFAULT_HOSTNAME, DEFAULT_ORIGIN } = require('./policy');
const invalid = message => Object.assign(new Error(message), { status: 400 });
function validateRecord(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.version !== 1 ||
      Object.keys(value).sort().join(',') !== 'hostname,previousHostname,revision,version' ||
      !Number.isSafeInteger(value.revision) || value.revision < 0 || value.revision >= Number.MAX_SAFE_INTEGER ||
      normalizeHostname(value.hostname) !== value.hostname ||
      value.previousHostname !== null && normalizeHostname(value.previousHostname) !== value.previousHostname ||
      value.previousHostname === value.hostname) throw invalid('Invalid connection profile');
  return { ...value };
}
function publicSnapshot(record) {
  validateRecord(record);
  return { ...record, origin: 'https://' + record.hostname,
    previousOrigin: record.previousHostname ? 'https://' + record.previousHostname : null,
    defaultOrigin: DEFAULT_ORIGIN, certificateManagement: 'reverse-proxy', tlsStatus: 'managed-by-proxy' };
}
function createProxyStore(filename, { initialHostname = DEFAULT_HOSTNAME, uid = process.getuid?.() } = {}) {
  const parent = path.dirname(filename);
  const checkParent = () => {
    const stat = fs.lstatSync(parent);
    if (!stat.isDirectory() || process.platform !== 'win32' && (stat.uid !== uid || stat.mode & 0o077)) throw new Error('Unsafe connection directory');
  };
  const read = () => {
    checkParent();
    const stat = fs.lstatSync(filename, { throwIfNoEntry: false });
    if (!stat) return null;
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > 4096 ||
        process.platform !== 'win32' && (stat.uid !== uid || stat.mode & 0o077)) throw new Error('Unsafe connection profile');
    return validateRecord(JSON.parse(fs.readFileSync(filename, 'utf8')));
  };
  let state = read() || { version: 1, revision: 0, hostname: normalizeHostname(initialHostname), previousHostname: null };
  return {
    snapshot: () => publicSnapshot(state),
    update(value) {
      if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join(',') !== 'hostname,revision' ||
          !Number.isSafeInteger(value.revision) || value.revision < 0) throw invalid('Invalid connection update');
      const hostname = normalizeHostname(value.hostname);
      const disk = read();
      if (disk && JSON.stringify(disk) !== JSON.stringify(state)) throw Object.assign(new Error('Connection state changed'), { status: 409 });
      if (value.revision !== state.revision) throw Object.assign(new Error('Connection revision changed'), { status: 409 });
      if (hostname === state.hostname) return publicSnapshot(state);
      const next = validateRecord({ version: 1, revision: state.revision + 1, hostname, previousHostname: state.hostname });
      const temp = path.join(parent, '.connection-' + crypto.randomBytes(16).toString('hex') + '.tmp');
      let fd;
      try {
        fd = fs.openSync(temp, 'wx', 0o600);
        fs.writeFileSync(fd, JSON.stringify(next) + '\n'); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
        fs.renameSync(temp, filename); state = next;
      } finally {
        if (fd !== undefined) fs.closeSync(fd);
        if (fs.existsSync(temp)) fs.unlinkSync(temp);
      }
      return publicSnapshot(state);
    },
  };
}
module.exports = { createProxyStore, validateRecord, publicSnapshot };
