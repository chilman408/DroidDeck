// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { createHash } = require('node:crypto');
const { FILES, REVISION } = require('./runtime-bundle');
const { readBoundedRegular } = require('./read-bounded');
const BASE_MANIFEST = '9de6dc880c7608aa3f959b94f3b72537eeae9597dc02002038015195ad224f71';
const GUARD_REVISION = '5301fea5f07cf34e4bdd46ffc477693f0778b39d';
const MANIFEST_SHA256 = '6d136e676dccc2a2b520fe8731d52d700e957a1b99f6a9de033915ad275f89c8';
const OVERLAYS = Object.freeze({
  'portable/gpu-access.js': Object.freeze({ size: 11805, sha256: '4cbc6ef0c155d56e26bcf91f68db896e073f035471b6b5bcec8e0dfb029557e0' }),
  'portable/desktop-preflight.js': Object.freeze({ size: 1699, sha256: 'f562f47c7a1ccce89df8660b72a404c121a0b94691c7ab0b1fc4e53cd299654a' }),
  'waydroid-display/headless-session-preflight.js': Object.freeze({ size: 5540, sha256: '83e3f626aa17e0541c85a7db0a2d53b95176647d944f0324e22e2ad1a5c1c0a9' }),
});
const MANIFEST = 'bundle-manifest.json', sha = data => createHash('sha256').update(data).digest('hex');
const encode = value => Buffer.from(JSON.stringify(value, null, 2) + '\n');
class RuntimeCandidateUncertain extends Error {}
function absolute(value) {
  if (typeof value !== 'string' || value.length > 4096 || value.includes('\0') ||
      !path.isAbsolute(value) || path.resolve(value) !== value) throw Error('Canonical candidate path required');
  return value;
}
function identity(stat, file = false) {
  return JSON.stringify(['dev', 'ino', 'uid', 'gid', 'mode', ...(file ? ['nlink', 'size', 'mtimeMs', 'ctimeMs'] : [])].map(key => stat[key]));
}
function namespace(root) {
  const parts = absolute(root).slice(path.parse(root).root.length).split(path.sep).filter(Boolean);
  let current = path.parse(root).root; const records = [];
  for (const part of ['', ...parts]) {
    current = path.join(current, part); const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw Error('Linked candidate namespace');
    records.push([current, identity(stat)]);
  }
  return () => {
    for (const [name, before] of records) if (identity(fs.lstatSync(name)) !== before) throw Error('Candidate namespace changed');
  };
}
function readTree(root, files) {
  const checkNamespace = namespace(root), directories = new Map([['', new Set()]]), metadata = [], bytes = new Map();
  for (const file of files) {
    const parts = file.split('/');
    parts.forEach((part, index) => {
      const parent = parts.slice(0, index).join('/');
      if (!directories.has(parent)) directories.set(parent, new Set()); directories.get(parent).add(part);
    });
  }
  for (const [relative, names] of directories) {
    const name = path.join(root, relative), stat = fs.lstatSync(name);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (process.platform === 'linux' && stat.mode & 0o022) ||
        fs.readdirSync(name).sort().join('\n') !== [...names].sort().join('\n')) throw Error('Invalid candidate inventory');
    metadata.push([name, identity(stat), false, [...names].sort()]);
  }
  for (const relative of files) {
    const name = path.join(root, relative), stat = fs.lstatSync(name);
    if (!stat.isFile() || (process.platform === 'linux' && stat.mode & 0o022)) throw Error('Unsafe candidate source');
    bytes.set(relative, readBoundedRegular(name, 1024 * 1024)); metadata.push([name, identity(stat, true), true]);
  }
  function check() {
    checkNamespace();
    for (const [name, before, file, names] of metadata) {
      if (identity(fs.lstatSync(name), file) !== before || (!file && fs.readdirSync(name).sort().join('\n') !== names.join('\n'))) {
        throw Error('Candidate input generation changed');
      }
    }
  }
  check(); return { bytes, check };
}
function matches(bytes, record) { return bytes.length === record.size && sha(bytes) === record.sha256; }
function candidate(records) {
  return { version: 1, product: 'DroidDeck', kind: 'runtime-source-candidate', baseSourceRevision: REVISION,
    baseManifestSha256: BASE_MANIFEST, guardSourceRevision: GUARD_REVISION, installable: false,
    readyForActivation: false, servicesActivated: false, androidOperationsExecuted: false, files: records };
}
function verifyRuntimeCandidate(directory) {
  const root = absolute(directory), check = namespace(root), encoded = readBoundedRegular(path.join(root, MANIFEST), 1024 * 1024);
  if (sha(encoded) !== MANIFEST_SHA256) throw Error('Unreviewed runtime candidate');
  const manifest = JSON.parse(encoded), tree = readTree(root, [...FILES, MANIFEST]);
  if (!tree.bytes.get(MANIFEST).equals(encoded) || manifest.files.length !== FILES.length ||
      !encode(candidate(manifest.files)).equals(encoded)) throw Error('Candidate metadata changed');
  manifest.files.forEach((record, index) => {
    if (record.path !== FILES[index] || !matches(tree.bytes.get(record.path), record)) throw Error('Candidate payload changed');
  });
  tree.check(); check(); return manifest;
}
function syncDirectory(directory) {
  if (process.platform !== 'linux') return;
  const fd = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function writeFile(filename, bytes) {
  const fd = fs.openSync(filename, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function buildRuntimeCandidate({ base, guard, output }) {
  [base, guard, output].forEach(absolute);
  if ([base, guard].some(root => output === root || output.startsWith(root + path.sep) || root.startsWith(output + path.sep))) throw Error('Candidate source/output overlap');
  const parent = path.dirname(output), checkParent = namespace(parent);
  try { fs.lstatSync(output); throw Error('New candidate output required'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const baseline = readTree(base, [...FILES, MANIFEST]), overlay = readTree(guard, Object.keys(OVERLAYS));
  if (sha(baseline.bytes.get(MANIFEST)) !== BASE_MANIFEST) throw Error('Unreviewed runtime base');
  const manifest = JSON.parse(baseline.bytes.get(MANIFEST));
  if (manifest.sourceRevision !== REVISION || manifest.files.length !== FILES.length) throw Error('Runtime base recipe changed');
  const blobs = new Map();
  const records = manifest.files.map((record, index) => {
    if (record.path !== FILES[index] || !matches(baseline.bytes.get(record.path), record)) throw Error('Runtime base bytes changed');
    const bytes = Object.hasOwn(OVERLAYS, record.path) ? overlay.bytes.get(record.path) : baseline.bytes.get(record.path);
    if (Object.hasOwn(OVERLAYS, record.path) && !matches(bytes, OVERLAYS[record.path])) throw Error('Unreviewed runtime overlay');
    blobs.set(record.path, bytes); return { path: record.path, size: bytes.length, sha256: sha(bytes) };
  });
  const encoded = encode(candidate(records));
  if (sha(encoded) !== MANIFEST_SHA256) throw Error('Runtime candidate recipe changed');
  const check = () => { baseline.check(); overlay.check(); checkParent(); };
  check();
  try {
    fs.mkdirSync(output, { mode: 0o700 });
    for (const group of [...new Set(FILES.map(file => file.split('/')[0]))]) fs.mkdirSync(path.join(output, group), { mode: 0o700 });
    const created = [...new Set(FILES.map(file => file.split('/')[0]))].map(group => namespace(path.join(output, group)));
    const checkCreated = () => { check(); for (const guard of created) guard(); };
    for (const [file, bytes] of blobs) { checkCreated(); writeFile(path.join(output, file), bytes); checkCreated(); }
    for (const group of [...new Set(FILES.map(file => file.split('/')[0]))]) syncDirectory(path.join(output, group));
    checkCreated(); writeFile(path.join(output, MANIFEST), encoded); syncDirectory(output); syncDirectory(parent);
    checkCreated(); const result = verifyRuntimeCandidate(output); checkCreated(); return result;
  } catch (_) { throw new RuntimeCandidateUncertain('Candidate output may exist; preserve and inspect before resuming'); }
}
function privateHome(filename) {
  const home = path.resolve(os.homedir()), root = absolute(filename);
  if (!root.startsWith(home + path.sep)) throw Error('Private user-home candidate path required');
  let current = home;
  for (const part of ['', ...path.relative(home, root).split(path.sep)]) {
    current = path.join(current, part);
    let stat; try { stat = fs.lstatSync(current); } catch (error) { if (error.code === 'ENOENT' && current === root) break; throw error; }
    if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid() || stat.mode & 0o022) throw Error('Unsafe private candidate path');
  }
}
function main(args) {
  if (process.platform !== 'linux' || !process.getuid || process.getuid() === 0) throw Error('Unprivileged Linux candidate command required');
  let manifest;
  if (args.length === 4 && args[0] === 'build') {
    args.slice(1).forEach(privateHome); manifest = buildRuntimeCandidate({ base: args[1], guard: args[2], output: args[3] });
  } else if (args.length === 2 && args[0] === 'verify') { privateHome(args[1]); manifest = verifyRuntimeCandidate(args[1]); }
  else throw Error('Fixed candidate command required');
  console.log(JSON.stringify({ kind: manifest.kind, files: manifest.files.length, manifestSha256: MANIFEST_SHA256,
    installable: false, readyForActivation: false, servicesActivated: false, androidOperationsExecuted: false }));
}
if (require.main === module) {
  try { main(process.argv.slice(2)); } catch (error) { console.error('Runtime candidate rejected'); process.exitCode = error instanceof RuntimeCandidateUncertain ? 3 : 1; }
}
module.exports = { BASE_MANIFEST, GUARD_REVISION, MANIFEST_SHA256, OVERLAYS, RuntimeCandidateUncertain, buildRuntimeCandidate, verifyRuntimeCandidate };
