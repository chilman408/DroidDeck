// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises'), { promisify } = require('node:util');
const { execFile } = require('node:child_process');
const { normalizeRuntimeConfig, readRuntimeConfig } = require('./runtime-config');
const { validateRuntimeAccount } = require('./runtime-account');
const execute = promisify(execFile);

function parseSupplementalGroups(value, primaryGid) {
  let text;
  if (Buffer.isBuffer(value) && value.length <= 4096) text = new TextDecoder('utf-8', { fatal: true }).decode(value);
  else if (typeof value === 'string' && Buffer.byteLength(value) <= 4096) text = value;
  else throw new Error('Invalid GPU group record');
  text = text.replace(/\r?\n$/, '');
  if (!/^(?:0|[1-9]\d{0,9})(?:[ \t]+(?:0|[1-9]\d{0,9}))*$/.test(text)) throw new Error('Invalid GPU group record');
  const groups = text.split(/[ \t]+/).map(Number);
  if (groups.length > 64 || groups.some(value => value > 2147483647) || new Set(groups).size !== groups.length ||
      !groups.includes(primaryGid)) throw new Error('Invalid GPU group record');
  return Object.freeze(groups.sort((a, b) => a - b));
}
async function lookupSupplementalGroups(user, { signal } = {}) {
  if (process.platform !== 'linux') throw new Error('Linux GPU inspection required');
  const result = await execute('/usr/bin/id', ['-G', '--', user],
    { signal, timeout: 5000, maxBuffer: 4096, encoding: 'buffer', windowsHide: true });
  return result.stdout;
}
function snapshot(info, device = false) {
  const keys = ['dev', 'ino', 'uid', 'gid', 'mode', ...(device ? ['rdev', 'nlink'] : [])];
  if (keys.some(key => !Number.isSafeInteger(info[key]) || info[key] < 0)) throw new Error('Invalid DRM metadata');
  return Object.freeze(Object.fromEntries(keys.map(key => [key, info[key]])));
}
function same(left, right) { return JSON.stringify(left) === JSON.stringify(right); }
function directory(info) {
  if (!info.isDirectory() || (info.mode & 0o170000) !== 0o040000 || info.uid !== 0 || info.mode & 0o022) {
    throw new Error('Unsafe DRM namespace');
  }
  return snapshot(info);
}
function deviceKind(name) {
  const match = /^(card|renderD)(0|[1-9]\d{0,2})$/.exec(name);
  if (!match) { if (/^(card|renderD)/.test(name)) throw new Error('Unsupported DRM name'); return null; }
  const minor = Number(match[2]), kind = match[1] === 'card' ? 'card' : 'render';
  if ((kind === 'card' && minor > 63) || (kind === 'render' && (minor < 128 || minor > 191))) {
    throw new Error('Unsupported DRM layout');
  }
  return { kind, minor };
}
function device(info, minor) {
  const value = snapshot(info, true), number = BigInt(value.rdev);
  const major = Number(((number >> 8n) & 0xfffn) | ((number >> 32n) & 0xfffff000n));
  const actualMinor = Number((number & 0xffn) | ((number >> 12n) & 0xffffff00n));
  if (!info.isCharacterDevice() || (value.mode & 0o170000) !== 0o020000 || value.uid !== 0 ||
      value.nlink !== 1 || value.mode & 0o007 || major !== 226 || actualMinor !== minor) throw new Error('Unsafe DRM device');
  return value;
}
function modeBits(info, uid, groups) {
  return info.uid === uid ? info.mode >> 6 : groups.includes(info.gid) ? info.mode >> 3 : info.mode;
}
async function names(io, signal) {
  signal?.throwIfAborted();
  const value = await io.readdir('/dev/dri');
  signal?.throwIfAborted();
  if (!Array.isArray(value) || value.length > 128 || new Set(value).size !== value.length ||
      value.some(name => typeof name !== 'string' || !name.length || name.length > 255 || /[\\/\0]/.test(name) || name === '.' || name === '..')) {
    throw new Error('Invalid DRM listing');
  }
  return [...value].sort();
}
async function inspectGpuAccess(configuration, { io = fs, lookup, lookupGroups = lookupSupplementalGroups, signal } = {}) {
  signal?.throwIfAborted();
  const config = normalizeRuntimeConfig(configuration);
  await validateRuntimeAccount(config, { lookup, signal });
  const groups = parseSupplementalGroups(await lookupGroups(config.user, { signal }), config.gid);
  signal?.throwIfAborted();
  const directories = [];
  for (const filename of ['/dev', '/dev/dri']) {
    signal?.throwIfAborted(); directories.push([filename, directory(await io.lstat(filename))]);
  }
  const namespace = directories.map(([path, info]) => Object.freeze({ path, gid: info.gid,
    mode: '0' + (info.mode & 0o777).toString(8).padStart(3, '0'),
    groupSearch: Boolean(info.mode & 0o010), searchByMode: Boolean(modeBits(info, config.uid, groups) & 1) }));
  const namespaceSearchByMode = namespace.every(entry => entry.searchByMode);
  const listing = await names(io, signal), records = [], nodes = [];
  for (const name of listing) {
    signal?.throwIfAborted();
    const selected = deviceKind(name); if (!selected) continue;
    const filename = '/dev/dri/' + name, before = device(await io.lstat(filename), selected.minor);
    records.push([filename, before, selected.minor]);
    // This is mode eligibility, not ACL, service sandbox or GL verification.
    const deviceReadWriteByMode = (modeBits(before, config.uid, groups) & 0o6) === 0o6;
    nodes.push(Object.freeze({ path: filename, kind: selected.kind, gid: before.gid,
      mode: '0' + (before.mode & 0o777).toString(8).padStart(3, '0'),
      groupReadWrite: (before.mode & 0o060) === 0o060, deviceReadWriteByMode,
      readWriteByMode: namespaceSearchByMode && deviceReadWriteByMode }));
  }
  for (const [filename, before, minor] of records) {
    signal?.throwIfAborted();
    if (!same(before, device(await io.lstat(filename), minor))) throw new Error('DRM device changed');
  }
  for (const [filename, before] of directories) {
    signal?.throwIfAborted();
    if (!same(before, directory(await io.lstat(filename)))) throw new Error('DRM namespace changed');
  }
  if (!same(listing, await names(io, signal))) throw new Error('DRM listing changed');
  await validateRuntimeAccount(config, { lookup, signal });
  if (!same(groups, parseSupplementalGroups(await lookupGroups(config.user, { signal }), config.gid))) throw new Error('GPU groups changed');
  signal?.throwIfAborted();
  return Object.freeze({ kind: 'gpu-access-preflight', installable: false, gpuAccelerationVerified: false,
    temporaryDesktopAclsIgnored: true, uid: config.uid, groups, nodes: Object.freeze(nodes),
    namespace: Object.freeze(namespace), namespaceSearchByMode,
    requiredDirectoryGroups: Object.freeze([...new Set(namespace.filter(entry => !entry.searchByMode && entry.groupSearch).map(entry => entry.gid))].sort((a, b) => a - b)),
    requiredDeviceGroups: Object.freeze([...new Set(nodes.filter(node => !node.deviceReadWriteByMode && node.groupReadWrite).map(node => node.gid))].sort((a, b) => a - b)),
    unattendedRenderModeAccess: nodes.some(node => node.kind === 'render' && node.readWriteByMode),
    unattendedCardModeAccess: nodes.some(node => node.kind === 'card' && node.readWriteByMode) });
}
function parseGpuProcessCredentials(value) {
  let text;
  if (Buffer.isBuffer(value) && value.length <= 65536) text = new TextDecoder('utf-8', { fatal: true }).decode(value);
  else if (typeof value === 'string' && Buffer.byteLength(value) <= 65536) text = value;
  else throw Error('Invalid GPU process credentials');
  if (/[\x00-\x08\x0b-\x1f\x7f]/.test(text)) throw Error('Invalid GPU process credentials');
  const fields = new Map(), selected = ['Uid', 'Gid', 'Groups', 'CapInh', 'CapPrm', 'CapEff', 'CapAmb'];
  for (const line of text.split('\n')) {
    const colon = line.indexOf(':'), key = line.slice(0, colon);
    if (colon < 0 || !selected.includes(key)) continue;
    if (fields.has(key)) throw Error('Ambiguous GPU process credentials');
    fields.set(key, line.slice(colon + 1).trim());
  }
  if (fields.size !== selected.length) throw Error('Incomplete GPU process credentials');
  const numbers = (key, count) => {
    const text = fields.get(key), parts = text ? text.split(/[ \t]+/) : [];
    if ((count && parts.length !== count) || parts.length > 64 ||
        parts.some(value => !/^(?:0|[1-9]\d{0,9})$/.test(value) || Number(value) > 2147483647)) throw Error('Invalid GPU credential IDs');
    return parts.map(Number);
  };
  const groups = numbers('Groups');
  if (new Set(groups).size !== groups.length) throw Error('Duplicate GPU process groups');
  const capabilities = selected.slice(3).map(key => {
    const value = fields.get(key);
    if (!/^[a-f0-9]{1,16}$/.test(value)) throw Error('Invalid GPU process capabilities');
    return BigInt('0x' + value).toString(16);
  });
  return Object.freeze({ uids: Object.freeze(numbers('Uid', 4)), gids: Object.freeze(numbers('Gid', 4)),
    groups: Object.freeze(groups.sort((a, b) => a - b)), capabilities: Object.freeze(capabilities) });
}
async function readGpuProcessStatus({ signal } = {}) {
  signal?.throwIfAborted();
  if (process.platform !== 'linux') throw Error('Linux GPU process credentials required');
  const fd = await fs.open('/proc/self/status', fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const bytes = Buffer.alloc(65537); let length = 0;
    while (length < bytes.length) {
      signal?.throwIfAborted();
      const result = await fd.read(bytes, length, bytes.length - length, null);
      if (!result.bytesRead) break;
      length += result.bytesRead;
    }
    const value = bytes.subarray(0, length), record = parseGpuProcessCredentials(value);
    if (record.uids[0] !== process.getuid() || record.uids[1] !== process.geteuid() ||
        record.gids[0] !== process.getgid() || record.gids[1] !== process.getegid()) throw Error('GPU process credentials changed');
    signal?.throwIfAborted(); return value;
  } finally { await fd.close(); }
}
async function assertGpuProcessAccess(configuration, { readStatus = readGpuProcessStatus, io, lookup, signal } = {}) {
  signal?.throwIfAborted();
  const config = normalizeRuntimeConfig(configuration);
  async function credentials() {
    signal?.throwIfAborted();
    const record = parseGpuProcessCredentials(await readStatus({ signal }));
    signal?.throwIfAborted();
    if (record.uids.some(uid => uid !== config.uid) || record.gids.some(gid => gid !== config.gid) ||
        record.capabilities.some(value => value !== '0')) throw Error('GPU process credentials do not match unprivileged runtime');
    return record;
  }
  const before = await credentials();
  const check = async () => {
    if (!same(before, await credentials())) throw Error('GPU process credentials changed');
    return [...new Set([...before.groups, config.gid])].sort((a, b) => a - b).join(' ');
  };
  const result = await inspectGpuAccess(config, { io, lookup, signal, lookupGroups: check });
  await check();
  if (!result.unattendedCardModeAccess || !result.unattendedRenderModeAccess) throw Error('GPU process lacks DRM mode access');
  return Object.freeze({ ...result, kind: 'gpu-process-access-preflight', credentialBasis: 'current-process',
    processCredentialsMatched: true, actualProcessGpuModeAccess: true, actualServiceMainProcessVerified: false,
    deviceAccessTested: false, servicesActivated: false, androidOperationsExecuted: false, readyForActivation: false });
}
async function main(args) {
  if (process.platform !== 'linux' || args.length !== 2 || !['inspect', 'inspect-self'].includes(args[0])) throw new Error('Fixed Linux GPU preflight required');
  const inspect = args[0] === 'inspect' ? inspectGpuAccess : assertGpuProcessAccess;
  console.log(JSON.stringify(await inspect(readRuntimeConfig(args[1])), null, 2));
}
if (require.main === module) main(process.argv.slice(2)).catch(() => { console.error('GPU-access preflight rejected'); process.exitCode = 1; });
module.exports = { parseSupplementalGroups, lookupSupplementalGroups, inspectGpuAccess,
  parseGpuProcessCredentials, readGpuProcessStatus, assertGpuProcessAccess };
