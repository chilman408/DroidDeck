// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const REVISION = '76c89fbb8de9a672a9ec843808f9eca6fd8c9528';
const GROUPS = Object.freeze({
  'browser-connection': 'helper policy proxy-store socket-startup tls-import',
  portable: 'configuration-generation desktop-preflight gpu-access protected-config read-bounded runtime-account runtime-config worker-config worker-entry',
  'preview-gateway': 'audio-gain audio-launcher audio-relay audio-settings audio-stream browser-access capture-layout capture-lease compositor-video-launcher device-info display-control display-helper-client gps-control gps-helper-client gps-location h264-relay rear-display-settings runtime-probe scrcpy-stream server video-capture-controller video-launcher video-settings',
  'waydroid-display': 'compositor-source frame-check gps-helper gps-lifecycle gps-protocol gps-store graphics-restart headless-session-preflight helper host-adapter location-adapter pid-compat pid-compat-hook probe runtime-commands runtime-discovery runtime-lock runtime-loop runtime-main runtime-native runtime-ownership runtime-preflight runtime-readiness runtime-supervisor session-lifecycle session-preflight session-probe session-recovery store touch-adapter touch-geometry transaction'
});
const FILES = Object.freeze([
  ...Object.entries(GROUPS).flatMap(([dir, names]) => names.split(' ').map(n => dir + '/' + n + '.js')),
  'preview-gateway/compositor-h264.py', 'preview-gateway/package.json', 'preview-gateway/package-lock.json',
  'waydroid-display/native-stop.py', 'waydroid-display/logical-screencap.sh',
  'waydroid-display/package.json', 'waydroid-display/package-lock.json'
].sort());
const MANIFEST = 'bundle-manifest.json', MAX_FILE = 1024 * 1024, MAX_TOTAL = 8 * 1024 * 1024;
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function git(repo, args, limit = MAX_FILE) {
  return execFileSync('git', ['--no-pager', '--no-replace-objects', '--no-lazy-fetch', '-C', path.resolve(repo), ...args],
    { env: { ...process.env, GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' }, maxBuffer: limit,
      timeout: 30000, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
}
function validateManifest(m) {
  if (!m || Array.isArray(m) || Object.keys(m).sort().join(',') !== 'files,installable,kind,product,sourceRevision,version' ||
      m.version !== 1 || m.product !== 'DroidDeck' || m.kind !== 'runtime-source-component' || m.installable !== false ||
      typeof m.sourceRevision !== 'string' || !/^[a-f0-9]{40}$/.test(m.sourceRevision) ||
      !Array.isArray(m.files) || m.files.length !== FILES.length) throw new Error('Invalid runtime source manifest');
  let total = 0;
  m.files.forEach((f, index) => {
    if (!f || Array.isArray(f) || Object.keys(f).sort().join(',') !== 'path,sha256,size' || f.path !== FILES[index] ||
        !Number.isSafeInteger(f.size) || f.size < 1 || f.size > MAX_FILE ||
        typeof f.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(f.sha256)) throw new Error('Invalid runtime source record');
    total += f.size;
  });
  if (total > MAX_TOTAL) throw new Error('Oversized runtime source component');
  return m;
}
function verifyRuntimeBundle(directory) {
  const root = path.resolve(directory);
  if (!fs.lstatSync(root).isDirectory()) throw new Error('Unsafe runtime bundle root');
  const m = validateManifest(JSON.parse(readBoundedRegular(path.join(root, MANIFEST), MAX_FILE)));
  const expectedDirs = new Set(FILES.map(f => f.split('/')[0]));
  if (fs.readdirSync(root).sort().join('\n') !== [...expectedDirs, MANIFEST].sort().join('\n')) throw new Error('Unexpected runtime root entries');
  for (const dir of expectedDirs) {
    const filename = path.join(root, dir);
    if (!fs.lstatSync(filename).isDirectory() || fs.readdirSync(filename).sort().join('\n') !==
        FILES.filter(f => f.startsWith(dir + '/')).map(f => f.slice(dir.length + 1)).sort().join('\n')) throw new Error('Unexpected runtime directory entries');
  }
  for (const f of m.files) {
    const b = readBoundedRegular(path.join(root, f.path), MAX_FILE);
    if (b.length !== f.size || sha(b) !== f.sha256) throw new Error('Runtime source bytes changed');
  }
  return m;
}
function buildRuntimeBundle({ repo, output, revision = REVISION }) {
  // Fixture revisions are explicit API inputs; production CLI has no override.
  if (typeof revision !== 'string' || !/^[a-f0-9]{40}$/.test(revision)) throw new Error('Pinned source revision required');
  const tree = git(repo, ['ls-tree', '-rz', '--long', revision, 'runtime'], MAX_FILE).toString('utf8').split('\0').filter(Boolean);
  const entries = new Map();
  for (const line of tree) {
    const match = /^(\d{6}) (\w+) ([a-f0-9]{40}) +([0-9-]+)\t(.+)$/.exec(line);
    if (!match) throw new Error('Malformed source tree');
    const [, mode, type, object, size, filename] = match;
    entries.set(filename, { mode, type, object, size: Number(size) });
  }
  const blobs = [];
  for (const filename of FILES) {
    const e = entries.get('runtime/' + filename);
    if (!e || !['100644', '100755'].includes(e.mode) || e.type !== 'blob' || !Number.isSafeInteger(e.size) ||
        e.size < 1 || e.size > MAX_FILE) throw new Error('Missing or unsafe selected source');
    const bytes = git(repo, ['cat-file', 'blob', e.object]);
    if (bytes.length !== e.size) throw new Error('Source object size mismatch');
    blobs.push({ filename, bytes });
  }
  if (blobs.reduce((sum, f) => sum + f.bytes.length, 0) > MAX_TOTAL) throw new Error('Oversized source tree');
  const root = path.resolve(output);
  fs.mkdirSync(root, { mode: 0o700 });
  for (const { filename, bytes } of blobs) {
    fs.mkdirSync(path.dirname(path.join(root, filename)), { recursive: true, mode: 0o700 });
    fs.writeFileSync(path.join(root, filename), bytes, { flag: 'wx', mode: 0o600 });
  }
  const manifest = validateManifest({ version: 1, product: 'DroidDeck', kind: 'runtime-source-component',
    installable: false, sourceRevision: revision,
    files: blobs.map(f => ({ path: f.filename, size: f.bytes.length, sha256: sha(f.bytes) })) });
  fs.writeFileSync(path.join(root, MANIFEST), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return verifyRuntimeBundle(root);
}
function main(args) {
  let m;
  if (args.length === 3 && args[0] === 'build') m = buildRuntimeBundle({ repo: args[1], output: args[2] });
  else if (args.length === 2 && args[0] === 'verify') m = verifyRuntimeBundle(args[1]);
  else throw new Error('Use build REPO NEW_OUTPUT or verify OUTPUT');
  console.log(JSON.stringify({ kind: m.kind, sourceRevision: m.sourceRevision, files: m.files.length, installable: false }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (error) { console.error(error.message); process.exitCode = 1; } }
module.exports = { FILES, REVISION, validateManifest, buildRuntimeBundle, verifyRuntimeBundle };
