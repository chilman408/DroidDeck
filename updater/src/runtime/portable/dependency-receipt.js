// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), { createHash } = require('node:crypto');
const { isDeepStrictEqual } = require('node:util');
const { readBoundedRegular } = require('./read-bounded');
const ROLES = Object.freeze({ display: 'teslaandroid-waydroid-display', gateway: 'teslaandroid-x86-preview-gateway' });
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
function json(filename, limit) {
  const bytes = readBoundedRegular(filename, limit);
  return { bytes, value: JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) };
}
function regularDirectory(filename) {
  if (!fs.lstatSync(filename).isDirectory()) throw new Error('Unsafe dependency artifact directory');
}
function inspectDependencyStage(role, directory) {
  if (typeof role !== 'string' || !Object.hasOwn(ROLES, role)) throw new Error('Unknown dependency role');
  regularDirectory(directory);
  const manifest = json(path.join(directory, 'package.json'), 16384), lock = json(path.join(directory, 'package-lock.json'), 1024 * 1024);
  const root = lock.value.packages?.[''];
  if (manifest.value.name !== ROLES[role] || manifest.value.private !== true || manifest.value.license !== 'GPL-3.0-only' ||
      lock.value.name !== manifest.value.name || lock.value.version !== manifest.value.version || lock.value.lockfileVersion !== 3 ||
      root?.name !== manifest.value.name || root.version !== manifest.value.version ||
      !isDeepStrictEqual(root.dependencies, manifest.value.dependencies) || !isDeepStrictEqual(root.devDependencies, manifest.value.devDependencies)) {
    throw new Error('Dependency manifest and lock mismatch');
  }
  const expected = new Map();
  for (const [location, entry] of Object.entries(lock.value.packages)) {
    if (location === '') continue;
    if (!/^node_modules\/[a-z0-9][a-z0-9-]{0,100}$/.test(location) || !entry || typeof entry !== 'object' || Array.isArray(entry) ||
        (entry.dev !== undefined && typeof entry.dev !== 'boolean') || entry.link === true) throw new Error('Unsupported locked dependency layout');
    if (entry.dev) continue;
    const name = location.slice('node_modules/'.length);
    if (typeof entry.version !== 'string' || !/^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$/.test(entry.version) ||
        entry.resolved !== 'https://registry.npmjs.org/' + name + '/-/' + name + '-' + entry.version + '.tgz' ||
        typeof entry.integrity !== 'string' || !entry.integrity.startsWith('sha512-')) throw new Error('Invalid dependency source pin');
    const sri = entry.integrity.slice(7), decoded = Buffer.from(sri, 'base64');
    if (decoded.length !== 64 || decoded.toString('base64') !== sri) throw new Error('Invalid dependency integrity');
    expected.set(name, entry);
  }
  if (!expected.size || expected.size > 128) throw new Error('Invalid dependency count');
  if (!manifest.value.dependencies || Array.isArray(manifest.value.dependencies) || typeof manifest.value.dependencies !== 'object' ||
      Object.entries(manifest.value.dependencies).some(([name, version]) => typeof version !== 'string' || expected.get(name)?.version !== version)) {
    throw new Error('Direct dependencies must match exact source pins');
  }
  const modules = path.join(directory, 'node_modules'); regularDirectory(modules);
  const installed = fs.readdirSync(modules).filter(name => name !== '.package-lock.json').sort();
  if (!isDeepStrictEqual(installed, [...expected.keys()].sort())) throw new Error('Missing or extra production dependencies');
  if (fs.existsSync(path.join(modules, '.package-lock.json'))) json(path.join(modules, '.package-lock.json'), 1024 * 1024);
  const records = [], packages = [], names = new Set(); let bytes = 0, entries = 0;
  function walk(base, relative) {
    if (++entries > 10000 || relative.length > 240 || relative.split('/').length > 20) throw new Error('Dependency inventory exceeded limit');
    const file = path.join(base, ...relative.split('/')), stat = fs.lstatSync(file), key = relative.toLowerCase();
    if (names.has(key)) throw new Error('Case-colliding dependency artifacts'); names.add(key);
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(file).sort()) {
        if (!/^[A-Za-z0-9._-]+$/.test(name) || name.endsWith('.') || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) throw new Error('Unsafe dependency artifact name');
        walk(base, relative + '/' + name);
      }
    } else {
      const data = readBoundedRegular(file, 8 * 1024 * 1024); bytes += data.length;
      if (bytes > 64 * 1024 * 1024) throw new Error('Dependency inventory exceeded limit');
      records.push({ path: relative, size: data.length, sha256: sha(data) });
    }
  }
  for (const name of installed) {
    const entry = expected.get(name), base = path.join(modules, name); regularDirectory(base);
    const metadata = json(path.join(base, 'package.json'), 256 * 1024);
    if (metadata.value.name !== name || metadata.value.version !== entry.version) throw new Error('Installed dependency version mismatch');
    const licenseFiles = fs.readdirSync(base).filter(file => /^licen[cs]e(?:[-_.][A-Za-z0-9.-]+)?$/i.test(file)).sort();
    if (!licenseFiles.length || licenseFiles.some(file => !fs.lstatSync(path.join(base, file)).isFile())) throw new Error('Dependency license artifact missing');
    for (const file of licenseFiles) if (!readBoundedRegular(path.join(base, file), 256 * 1024).length) throw new Error('Empty dependency license artifact');
    walk(modules, name);
    if (records.find(record => record.path === name + '/package.json')?.sha256 !== sha(metadata.bytes)) throw new Error('Dependency metadata changed during inventory');
    packages.push(Object.freeze({ name, version: entry.version, resolved: entry.resolved, integrity: entry.integrity,
      licenseMetadata: typeof entry.license === 'string' && entry.license.length <= 256 ? entry.license : null,
      licenseFiles: Object.freeze(licenseFiles) }));
  }
  records.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  return Object.freeze({ kind: 'dependency-receipt', installable: false, role, manifestSha256: sha(manifest.bytes), lockSha256: sha(lock.bytes),
    packages: Object.freeze(packages), fileCount: records.length, totalBytes: bytes, filesSha256: sha(JSON.stringify(records)) });
}
if (require.main === module) {
  try {
    if (process.argv.length !== 5 || process.argv[2] !== 'inspect') throw new Error('Use inspect ROLE STAGE');
    console.log(JSON.stringify(inspectDependencyStage(process.argv[3], process.argv[4]), null, 2));
  } catch (_) { console.error('Dependency stage rejected'); process.exitCode = 1; }
}
module.exports = { inspectDependencyStage };
