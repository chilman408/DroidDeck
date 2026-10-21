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
async function main(args) {
  if (process.platform !== 'linux' || args.length !== 2 || args[0] !== 'inspect') throw new Error('Fixed Linux GPU preflight required');
  console.log(JSON.stringify(await inspectGpuAccess(readRuntimeConfig(args[1])), null, 2));
}
if (require.main === module) main(process.argv.slice(2)).catch(() => { console.error('GPU-access preflight rejected'); process.exitCode = 1; });
module.exports = { parseSupplementalGroups, inspectGpuAccess };
