// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const SOURCE_REVISION = require('../../config/source-lock.json').frontend.revision;
const MAX_FILE = 8 * 1024 * 1024, MAX_TOTAL = 64 * 1024 * 1024, MAX_COUNT = 1024;
const MANIFEST = 'bundle-manifest.json';
const ASSETS = ['assets/donations-qr.svg', 'assets/droiddeck-logo.png'];
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
const sorted = values => [...values].sort();
function safePath(value) {
  if (typeof value !== 'string' || value.length > 240 || !value.split('/').every(part => safeSegment(part))) throw new Error('Unsafe bundle path');
  if (value === 'LICENSE' || value === 'index.html') return value;
  const rules = { css: /\.css$/, js: /\.(js|wasm)$/, assets: /\.(png|svg|ttf)$/, fonts: /\.(otf|ttf)$/ };
  if (!rules[value.split('/')[0]]?.test(value) || value.split('/').length < 2) throw new Error('Unsupported bundle path');
  return value;
}
function safeSegment(value) {
  return /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value) && !value.endsWith('.') &&
    !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(value);
}
function checkNamespace(file, names) {
  const parts = file.split('/');
  for (let i = 1; i <= parts.length; i++) {
    const name = parts.slice(0, i).join('/'), key = name.toLowerCase(), kind = i === parts.length ? 'file' : 'directory';
    const existing = names.get(key);
    if (existing && (existing.name !== name || existing.kind !== kind)) throw new Error('Case-colliding file or directory');
    names.set(key, { name, kind });
  }
}
function textFile(file) { return file === 'LICENSE' || /\.(html|js|css|svg)$/.test(file); }
function normalize(file, data) {
  if (!textFile(file)) return data;
  return Buffer.from(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(data).replace(/\r\n/g, '\n'));
}
function readRegular(file, limit = MAX_FILE) {
  return readBoundedRegular(file, limit);
}
function inventory(directory) {
  if (!fs.lstatSync(directory).isDirectory()) throw new Error('Unsafe bundle directory');
  const records = [], directories = []; let total = 0;
  function walk(relative) {
    for (const name of sorted(fs.readdirSync(path.join(directory, relative)))) {
      const file = relative ? relative + '/' + name : name, absolute = path.join(directory, file), stat = fs.lstatSync(absolute);
      if (stat.isDirectory()) {
        if (!file.split('/').every(safeSegment) || !['assets', 'css', 'js', 'fonts'].includes(file.split('/')[0])) throw new Error('Unsafe extra directory');
        directories.push(file); walk(file);
      } else if (file === MANIFEST) {
        readRegular(absolute, 1024 * 1024);
      } else {
        safePath(file); const data = readRegular(absolute);
        total += data.length;
        if (total > MAX_TOTAL || records.length >= MAX_COUNT) throw new Error('Oversized bundle');
        records.push({ path: file, size: data.length, sha256: sha(data) });
      }
    }
  }
  walk(''); records.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  if (directories.some(dir => !records.some(record => record.path.startsWith(dir + '/')))) throw new Error('Empty extra directory');
  return records;
}
function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || sorted(Object.keys(value)).join(',') !== sorted(keys).join(',')) throw new Error('Invalid bundle metadata');
}
function validateManifest(value) {
  exactKeys(value, ['version', 'product', 'productVersion', 'kind', 'installable', 'sourceRevision', 'patchSha256', 'files']);
  if (value.version !== 1 || value.product !== 'DroidDeck' || value.productVersion !== '0.1.0-preview' ||
      value.kind !== 'frontend-component' || value.installable !== false || typeof value.sourceRevision !== 'string' || !/^[a-f0-9]{40}$/.test(value.sourceRevision) ||
      typeof value.patchSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.patchSha256) || !Array.isArray(value.files) || value.files.length < 2 || value.files.length > MAX_COUNT) throw new Error('Invalid bundle metadata');
  const seen = new Set(), directories = new Map(); let previous = '', total = 0;
  for (const record of value.files) {
    exactKeys(record, ['path', 'size', 'sha256']); safePath(record.path);
    checkNamespace(record.path, directories);
    if (record.path <= previous || seen.has(record.path.toLowerCase()) || !Number.isSafeInteger(record.size) || record.size < 0 || record.size > MAX_FILE || typeof record.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(record.sha256)) throw new Error('Invalid bundle record');
    previous = record.path; seen.add(record.path.toLowerCase()); total += record.size;
  }
  if (total > MAX_TOTAL || !seen.has('license') || !seen.has('index.html')) throw new Error('Incomplete or oversized bundle');
  return value;
}
function verifyBundle(directory) {
  const root = path.resolve(directory);
  if (!fs.lstatSync(root).isDirectory()) throw new Error('Unsafe bundle root');
  const manifest = validateManifest(JSON.parse(readRegular(path.join(root, MANIFEST), 1024 * 1024).toString('utf8')));
  if (JSON.stringify(inventory(root)) !== JSON.stringify(manifest.files)) throw new Error('Bundle inventory or hashes differ');
  return manifest;
}
function validatePatch(data) {
  if (data.length > 4 * 1024 * 1024) throw new Error('Oversized patch');
  const text = new TextDecoder('utf-8', { fatal: true }).decode(data).replace(/\r\n/g, '\n');
  let target, count = 0;
  for (const line of text.split('\n')) {
    if (line.startsWith('diff --git ')) {
      const match = /^diff --git a\/(\S+) b\/(\S+)$/.exec(line);
      if (!match || match[1] !== match[2]) throw new Error('Unsafe patch path');
      target = safePath(match[1]); if (!textFile(target) || target === 'LICENSE') throw new Error('Non-text or license patch'); count++;
    } else if (line.startsWith('--- ') || line.startsWith('+++ ')) {
      const prefix = line.startsWith('--- ') ? '--- a/' : '+++ b/';
      if (!target || (line !== prefix + target && line !== line.slice(0, 4) + '/dev/null')) throw new Error('Unsafe patch header');
    } else if (/^(GIT binary patch|Binary files |rename |copy |similarity |dissimilarity |old mode |new mode |deleted file mode |new file mode )/.test(line)) {
      throw new Error('Unsupported patch operation');
    }
  }
  if (!count) throw new Error('Empty patch');
  return Buffer.from(text);
}
function git(repo, args, input, limit = MAX_FILE) {
  return execFileSync('git', ['--no-pager', '--no-replace-objects', '--no-lazy-fetch', '-C', path.resolve(repo), ...args],
    { input, env: { ...process.env, GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' }, maxBuffer: limit,
      timeout: 30000, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
}
function buildFrontend({ upstreamRepo, output, revision = SOURCE_REVISION, patchFile, assets, assetPins = {} }) {
  if (typeof revision !== 'string' || !/^[a-f0-9]{40}$/.test(revision)) throw new Error('Pinned revision required');
  exactKeys(assets, ASSETS);
  if (!assetPins || typeof assetPins !== 'object' || Array.isArray(assetPins) ||
      Object.entries(assetPins).some(([file, pin]) => !ASSETS.includes(file) || typeof pin !== 'string' || !/^[a-f0-9]{64}$/.test(pin))) {
    throw new Error('Invalid approved asset pins');
  }
  const patch = validatePatch(readRegular(patchFile, 4 * 1024 * 1024));
  const root = path.resolve(output);
  fs.mkdirSync(root, { mode: 0o755 });
  const entries = git(upstreamRepo, ['ls-tree', '-rz', '--long', revision], undefined, 1024 * 1024).toString('utf8').split('\0').filter(Boolean);
  let total = 0, count = 0; const seen = new Set(), directories = new Map();
  function write(file, data) {
    safePath(file); fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true, mode: 0o755 });
    fs.writeFileSync(path.join(root, file), normalize(file, data), { flag: 'wx', mode: 0o644 });
  }
  for (const entry of entries) {
    const match = /^(\d{6}) (\w+) ([a-f0-9]{40}) +([0-9-]+)\t(.+)$/.exec(entry);
    if (!match) throw new Error('Invalid source tree');
    const [, mode, type, object, size, file] = match;
    if (file !== 'LICENSE' && file !== 'index.html' && !['assets', 'css', 'js', 'fonts'].includes(file.split('/')[0])) continue;
    safePath(file);
    checkNamespace(file, directories);
    if (!['100644', '100755'].includes(mode) || type !== 'blob' || !/^\d+$/.test(size) || Number(size) > MAX_FILE || seen.has(file.toLowerCase())) throw new Error('Unsafe source object');
    total += Number(size); count++; seen.add(file.toLowerCase());
    if (total > MAX_TOTAL || count > MAX_COUNT) throw new Error('Oversized source');
    write(file, git(upstreamRepo, ['cat-file', 'blob', object]));
  }
  git(root, ['apply', '--no-index', '--check', '-'], patch);
  git(root, ['apply', '--no-index', '-'], patch);
  for (const file of ASSETS) {
    const target = path.join(root, file), data = normalize(file, readRegular(assets[file]));
    if (Object.hasOwn(assetPins, file) && sha(data) !== assetPins[file]) throw new Error('Approved asset bytes changed');
    if (fs.existsSync(target)) fs.writeFileSync(target, data, { flag: 'w', mode: 0o644 });
    else write(file, data);
  }
  const files = inventory(root);
  // Git applies to LF source; normalize text once more to fence platform settings.
  for (const record of files.filter(item => textFile(item.path))) {
    const file = path.join(root, record.path); fs.writeFileSync(file, normalize(record.path, readRegular(file)));
  }
  const manifest = validateManifest({ version: 1, product: 'DroidDeck', productVersion: '0.1.0-preview',
    kind: 'frontend-component', installable: false, sourceRevision: revision, patchSha256: sha(patch), files: inventory(root) });
  fs.writeFileSync(path.join(root, MANIFEST), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o644 });
  return verifyBundle(root);
}
function main(args) {
  if (args.length === 2 && args[0] === 'verify') {
    const manifest = verifyBundle(args[1]); console.log(JSON.stringify({ verified: true, kind: manifest.kind, installable: false, files: manifest.files.length })); return;
  }
  if (args.length !== 3 || args[0] !== 'build') throw new Error('Use build UPSTREAM OUTPUT or verify OUTPUT');
  const repo = path.resolve(__dirname, '../..');
  const logo = require('../branding/logo').verifyLogo();
  const manifest = buildFrontend({ upstreamRepo: args[1], output: args[2], revision: SOURCE_REVISION,
    patchFile: path.join(repo, 'runtime/patches/frontend-droiddeck-portable.patch'),
    assetPins: { 'assets/droiddeck-logo.png': logo.sha256 },
    assets: { 'assets/droiddeck-logo.png': logo.path,
      'assets/donations-qr.svg': path.join(repo, 'runtime/branding/donations-qr.svg') } });
  console.log(JSON.stringify({ built: true, kind: manifest.kind, installable: false, sourceRevision: manifest.sourceRevision, files: manifest.files.length }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (error) { console.error(error.message); process.exitCode = 1; } }
module.exports = { buildFrontend, verifyBundle, inventory, validateManifest, validatePatch, SOURCE_REVISION };
