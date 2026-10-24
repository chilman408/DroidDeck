// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), { createHash } = require('node:crypto');
const { readBoundedRegular } = require('./read-bounded');
const SOURCES = Object.freeze(Object.fromEntries([
  ['machine-gpu-entry.py', 'runtime/portable/machine-gpu-entry.py', 4890, '534bcdd629dca1c48f92663be42d5708d313ba62f7b37dc9155ace18db50ca70'],
  ['machine-gpu-bootstrap.py', 'runtime/portable/machine-gpu-bootstrap.py', 8079, '2eb30a22d93a4b897536f3654016710429aadc5c3b60fe4a6c389db79b3d3bf3'],
  ['machine-gpu-runtime/runtime/portable/promote-images.py', 'runtime/portable/promote-images.py', 253820, 'e4f67d1c43c8a81486b1e6de02a157b3a5f52d1665730053cfff7afbc8a75616'],
  ['machine-gpu-runtime/runtime/portable/stage-images.py', 'runtime/portable/stage-images.py', 12509, '077de6f524d575ee67750d7f54df62d3c08115ec0490937bff14f4e99cedc59a'],
  ['machine-gpu-runtime/runtime/portable/machine-gpu-supervision.py', 'runtime/portable/machine-gpu-supervision.py', 13262, '34cb29f173e2f0ab65f69db7605e87a1e9bb49c789c8a7f435a5369fd54326c3'],
  ['machine-gpu-runtime/runtime/portable/effective-units.py', 'runtime/portable/effective-units.py', 5285, '5df4aeb90df7d10fcf2d052438b53465b411a475ecc0ebc37afdddb89d2a6f4e'],
  ['machine-gpu-runtime/tools/waydroid-layer-property.py', 'tools/waydroid-layer-property.py', 5278, '568e12583f931d6ad464bff79692b11c3dbc4ff0de88725c7f0e214a2861698f'],
  ['machine-gpu-runtime/tools/waydroid-gpu-health-policy.py', 'tools/waydroid-gpu-health-policy.py', 1127, '806611c8c8615f4bf6d259c82bc33282a809301b80f3568a47dadc815cb5dc4e'],
  ['machine-gpu-runtime/runtime/portable/machine-gpu-peer-policy.py', 'runtime/portable/machine-gpu-peer-policy.py', 8491, '78e01a854a7abff59337332cae2c25a4582ca843964a28fac9c0a7c18d42d976'],
  ['machine-gpu-runtime/runtime/portable/machine-gpu-lifecycle.py', 'runtime/portable/machine-gpu-lifecycle.py', 9370, '06abee4309cbcb885c1000b05be74a5b62512d4c7c423c0beaf179c450a43e7c'],
  ['machine-gpu-runtime/runtime/portable/machine-gpu-lifecycle-state.py', 'runtime/portable/machine-gpu-lifecycle-state.py', 16461, '3d3053db581d07aa8f9cd35519e1995c8053ec804a63b31a75092628994485da']
].map(([destination, source, size, sha256]) => [destination, Object.freeze({ source, size, sha256 })])));
const FALSE_FLAGS = Object.freeze(['installedEntryVerified', 'effectiveGraphVerified', 'independentRecoveryArmed',
  'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted']);
const FILES = Object.keys(SOURCES).sort(), MANIFEST = 'bundle-manifest.json';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const encode = value => Buffer.from(JSON.stringify(value, null, 2) + '\n');
function canonical(value) {
  if (typeof value !== 'string' || !value.length || value.length > 4096 || value.includes('\0') ||
      !path.isAbsolute(value) || path.resolve(value) !== value || value === path.parse(value).root) throw Error('Canonical nonroot bundle path required');
  return value;
}
function directories(target, privateRoot, exactMode = true) {
  let current = path.parse(canonical(target)).root;
  for (const part of target.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const info = fs.lstatSync(current);
    if (!info.isDirectory() || info.isSymbolicLink()) throw Error('Unsafe bundle ancestor');
    if (privateRoot && (current === privateRoot || current.startsWith(privateRoot + path.sep)) && process.platform === 'linux' &&
        (info.uid !== process.getuid() || (exactMode ? (info.mode & 0o7777) !== 0o700 : info.mode & 0o022))) throw Error('Private bundle directory required');
  }
}
function manifest() {
  return { version: 1, product: 'DroidDeck', kind: 'machine-gpu-source-component',
    ...Object.fromEntries(FALSE_FLAGS.map(key => [key, false])),
    files: FILES.map(name => ({ path: name, size: SOURCES[name].size, sha256: SOURCES[name].sha256 })) };
}
function inventories(includeManifest) {
  const result = new Map([['', new Set(includeManifest ? [MANIFEST] : [])]]);
  for (const file of FILES) {
    const parts = file.split('/');
    parts.forEach((part, i) => {
      const parent = parts.slice(0, i).join('/');
      if (!result.has(parent)) result.set(parent, new Set());
      result.get(parent).add(part);
    });
  }
  return result;
}
function inspect(root, includeManifest) {
  for (const [relative, entries] of inventories(includeManifest)) {
    const directory = path.join(root, relative); directories(directory, root);
    if (fs.readdirSync(directory).sort().join('\n') !== [...entries].sort().join('\n')) throw Error('Closed source bundle inventory required');
  }
  for (const name of [...FILES, ...(includeManifest ? [MANIFEST] : [])]) {
    const file = path.join(root, name), info = fs.lstatSync(file);
    if (process.platform === 'linux' && (info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600)) throw Error('Private source bundle file required');
    const expected = name === MANIFEST ? encode(manifest()) : null;
    const bytes = readBoundedRegular(file, expected ? 16384 : SOURCES[name].size);
    if (expected ? !bytes.equals(expected) : bytes.length !== SOURCES[name].size || sha(bytes) !== SOURCES[name].sha256) throw Error('Independent source bundle recipe differs');
  }
  return manifest();
}
function verifyMachineGpuBundle(directory) { return inspect(canonical(directory), true); }
function write(filename, bytes) {
  const fd = fs.openSync(filename, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function flush(directory) {
  if (process.platform !== 'linux') return;
  const fd = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function buildMachineGpuBundle(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options) || !Object.hasOwn(options, 'output') ||
      Object.keys(options).some(key => !['output', 'sourceRoot'].includes(key))) throw Error('Closed source bundle options required');
  const root = canonical(options.output), sourceRoot = canonical(options.sourceRoot === undefined ? path.resolve(__dirname, '../..') : options.sourceRoot);
  const payload = new Map(); directories(sourceRoot); directories(path.dirname(root));
  for (const name of FILES) {
    const record = SOURCES[name], source = path.join(sourceRoot, record.source); directories(path.dirname(source));
    const raw = readBoundedRegular(source, record.size * 2 + 1);
    const bytes = Buffer.from(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw).replace(/\r\n/g, '\n'));
    if (bytes.length !== record.size || sha(bytes) !== record.sha256) throw Error('Build source differs from independent pin');
    payload.set(name, bytes);
  }
  fs.mkdirSync(root, { mode: 0o700 });
  for (const relative of [...inventories(false).keys()].filter(Boolean).sort((a, b) => a.length - b.length)) {
    fs.mkdirSync(path.join(root, relative), { mode: 0o700 });
  }
  for (const [name, bytes] of payload) write(path.join(root, name), bytes);
  inspect(root, false);
  for (const relative of [...inventories(false).keys()].sort((a, b) => b.length - a.length)) flush(path.join(root, relative));
  write(path.join(root, MANIFEST), encode(manifest()));
  flush(root); flush(path.dirname(root));
  return verifyMachineGpuBundle(root);
}
function main(args) {
  if (args.length !== 2 || !['build', 'verify'].includes(args[0])) throw Error('Use build NEW_OUTPUT or verify BUNDLE');
  let m;
  if (args[0] === 'build') {
    if (process.platform !== 'linux' || process.getuid() === 0) throw Error('Unprivileged Linux source build required');
    const root = canonical(args[1]), home = path.resolve(os.homedir());
    if (!root.startsWith(home + path.sep)) throw Error('Build outside private home refused');
    directories(path.dirname(root), home, false);
    m = buildMachineGpuBundle({ output: root });
  } else m = verifyMachineGpuBundle(args[1]);
  console.log(JSON.stringify({ kind: m.kind, files: m.files.length, ...Object.fromEntries(FALSE_FLAGS.map(key => [key, false])) }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) { console.error('Machine GPU source bundle refused; no activation performed'); process.exitCode = 1; } }
module.exports = { SOURCES, FALSE_FLAGS, buildMachineGpuBundle, verifyMachineGpuBundle, main };
