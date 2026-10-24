// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto'), { spawnSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded'), { validateElfReport } = require('./touch-build');
const root = path.resolve(__dirname, '../..'), revision = '8d2e2f2923d15baba6101d879fc9593ef4a0ef78';
const upstreamFiles = Object.freeze(['LICENSE', 'base64.c', 'base64.h', 'handshake.c', 'sha1.c', 'sha1.h', 'utf8.c', 'utf8.h', 'ws.c', 'ws.h']);
const patches = Object.freeze(['libws-loopback.patch', 'libws-case-insensitive-handshake.patch']);
const patchHashes = Object.freeze(['942143dec375151cfb1287c539da2f34bdc3c8365773dad1682a0c8bca6c9998', 'eeaf8e54606861929fbb4d81290655dc9f73f4451b7f0bd19d48d9bb6cc7caef']);
const ownFiles = Object.freeze(['runtime/display/tesla-android-x86-display.c', 'tests/native/test_display_capture.c', 'tests/native/display_capture_fixture.c']);
const flags = Object.freeze(['-D_GNU_SOURCE', '-std=c11', '-O2', '-Wall', '-Wextra', '-pthread', '-static', '-no-pie', '-Wl,--build-id=none']);
const plan = Object.freeze({ upstreamRevision: revision, upstreamFiles, ownFiles, patches, flags, installable: false,
  serverExecuted: false, androidOperationsExecuted: false, browserPngFallback: false });
const env = Object.freeze({ PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', TZ: 'UTC', SOURCE_DATE_EPOCH: '0',
  GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0', GIT_NO_REPLACE_OBJECTS: '1', GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' });
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const normalize = bytes => Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'));
function displayBuildPlan() { return plan; }
function directories(filename, privateBelow) {
  const absolute = path.resolve(filename); let current = path.parse(absolute).root;
  for (const part of absolute.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Linked build path');
    if (privateBelow && (current === privateBelow || current.startsWith(privateBelow + path.sep)) &&
        (stat.uid !== process.getuid() || (stat.mode & 0o022))) throw new Error('Non-private build parent');
  }
  return absolute;
}
function writeNew(filename, bytes) {
  const fd = fs.openSync(filename, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function run(tool, args, cwd, log) {
  const executable = '/usr/bin/' + tool, stat = fs.statSync(executable);
  if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022)) throw new Error('Untrusted system tool');
  const result = spawnSync(executable, args, { cwd, env, timeout: 30000, maxBuffer: 1024 * 1024 });
  if (log) { writeNew(log + '-stdout.txt', result.stdout || Buffer.alloc(0)); writeNew(log + '-stderr.txt', result.stderr || Buffer.alloc(0)); }
  if (result.error || result.signal || result.status !== 0) throw new Error('Display build command failed');
  return result;
}
function buildDisplay({ libwsRepo, output } = {}) {
  if (typeof output !== 'string' || !path.isAbsolute(output) || fs.existsSync(output)) throw new Error('New output required');
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.getuid || process.getuid() === 0) throw new Error('Unprivileged Linux x86_64 required');
  const home = directories(os.homedir()), destination = path.resolve(output);
  if (!destination.startsWith(home + path.sep)) throw new Error('Output outside home');
  directories(path.dirname(destination), home);
  if (typeof libwsRepo !== 'string' || !path.isAbsolute(libwsRepo)) throw new Error('Absolute source repository required');
  const repository = directories(libwsRepo), git = args => run('git', ['--no-pager', '--no-replace-objects', '--no-lazy-fetch', '-C', repository, ...args], repository).stdout;
  if (git(['rev-parse', '--verify', revision + '^{commit}']).toString().trim() !== revision) throw new Error('Missing pin');
  const entries = new Map(git(['ls-tree', '-z', revision]).toString().split('\0').filter(Boolean).map(entry => {
    const match = /^(\d+) blob ([a-f0-9]{40})\t(.+)$/.exec(entry);
    return match ? [match[3], { mode: match[1], oid: match[2] }] : [entry, null];
  }));
  const upstream = upstreamFiles.map(name => {
    if (entries.get(name)?.mode !== '100644') throw new Error('Unsafe pinned source');
    const bytes = git(['cat-file', 'blob', entries.get(name).oid]);
    if (!bytes.length || bytes.length > 1024 * 1024) throw new Error('Source bound');
    return { path: 'sources/libws/' + name, bytes: normalize(bytes), gitBlob: entries.get(name).oid, originalSha256: sha(bytes) };
  });
  const own = [...ownFiles, ...patches.map(name => 'runtime/patches/' + name)].map(relative => {
    const filename = path.join(root, relative); directories(path.dirname(filename));
    const bytes = readBoundedRegular(filename, 1024 * 1024); if (!bytes.length) throw new Error('Empty source');
    return { path: relative, filename, digest: sha(bytes), bytes: normalize(bytes) };
  });
  for (let i = 0; i < patches.length; i++) if (sha(own[ownFiles.length + i].bytes) !== patchHashes[i]) throw new Error('Unreviewed patch');
  const compiler = run('gcc', ['-dumpfullversion'], home).stdout.toString().trim();
  if (run('gcc', ['-dumpmachine'], home).stdout.toString().trim() !== 'x86_64-linux-gnu') throw new Error('Unexpected compiler target');
  fs.mkdirSync(destination, { mode: 0o700 });
  for (const name of ['sources', 'sources/libws', 'sources/runtime', 'sources/runtime/display', 'sources/runtime/patches', 'sources/tests', 'sources/tests/native', 'work']) fs.mkdirSync(path.join(destination, name), { mode: 0o700 });
  for (const item of upstream) writeNew(path.join(destination, item.path), item.bytes);
  for (const item of own) writeNew(path.join(destination, 'sources', item.path), item.bytes);
  const stagedSources = [...upstream.map(item => ({ path: item.path, sha256: sha(item.bytes) })),
    ...own.map(item => ({ path: 'sources/' + item.path, sha256: sha(item.bytes) }))];
  function verifyStaged() {
    for (const source of stagedSources) {
      const filename = path.join(destination, source.path); directories(path.dirname(filename), destination);
      if (sha(readBoundedRegular(filename, 1024 * 1024)) !== source.sha256) throw new Error('Staged source changed');
    }
  }
  verifyStaged();
  for (let i = 0; i < patches.length; i++) {
    verifyStaged();
    const args = ['--no-pager', '--no-replace-objects', 'apply', '--no-index', '--ignore-space-change', '--directory=sources/libws'];
    run('git', [...args, '--check', 'sources/runtime/patches/' + patches[i]], destination);
    run('git', [...args, 'sources/runtime/patches/' + patches[i]], destination);
    const changed = 'sources/libws/' + (i === 0 ? 'ws.c' : 'handshake.c');
    stagedSources.find(item => item.path === changed).sha256 = sha(readBoundedRegular(path.join(destination, changed), 1024 * 1024));
  }
  const include = ['-Isources/libws'], libraryC = upstreamFiles.filter(name => name.endsWith('.c')).map(name => 'sources/libws/' + name);
  const source = 'sources/runtime/display/tesla-android-x86-display.c', testSource = 'sources/tests/native/test_display_capture.c', fixtureSource = 'sources/tests/native/display_capture_fixture.c';
  verifyStaged();
  run('gcc', ['-D_GNU_SOURCE', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread', '-fsyntax-only', ...include, source, testSource, fixtureSource], destination);
  const artifactPath = 'tesla-android-x86-display-latest';
  run('gcc', [...flags, ...include, source, ...libraryC, '-o', artifactPath], destination, path.join(destination, 'work/link'));
  fs.chmodSync(path.join(destination, artifactPath), 0o700);
  run('gcc', [...flags, ...include, testSource, ...libraryC, '-o', 'work/test_display_capture'], destination, path.join(destination, 'work/test-link'));
  const fixtures = [];
  for (let mode = 0; mode < 6; mode++) {
    const relative = 'work/capture_fixture_' + mode;
    run('gcc', ['-D_GNU_SOURCE', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-DFIXTURE_MODE=' + mode, fixtureSource, '-o', relative], destination);
    fixtures.push(path.join(destination, relative));
  }
  verifyStaged();
  const native = spawnSync(path.join(destination, 'work/test_display_capture'), fixtures, { cwd: destination, env, timeout: 10000, maxBuffer: 65536 });
  writeNew(path.join(destination, 'work/native-test-stdout.txt'), native.stdout || Buffer.alloc(0)); writeNew(path.join(destination, 'work/native-test-stderr.txt'), native.stderr || Buffer.alloc(0));
  if (native.error || native.signal || native.status !== 0 || native.stdout.toString().trim() !== 'Synthetic diagnostic capture checks passed (6 cases); no server or Android' ||
      native.stderr.toString().trim() !== 'usage: diagnostic-test [SCREENCAP_PATH]') throw new Error('Synthetic capture checks failed');
  const report = run('readelf', ['-h', '-l', '-W', artifactPath], destination).stdout.toString(); validateElfReport(report);
  writeNew(path.join(destination, 'work/readelf.txt'), report);
  for (const item of own) { directories(path.dirname(item.filename)); if (sha(readBoundedRegular(item.filename, 1024 * 1024)) !== item.digest) throw new Error('Source generation changed'); }
  verifyStaged();
  const bytes = readBoundedRegular(path.join(destination, artifactPath), 8 * 1024 * 1024); if (!bytes.length) throw new Error('Empty artifact');
  const receipt = { format: 1, component: 'diagnostic-display-native-preview', installable: false, serverExecuted: false, androidOperationsExecuted: false, browserPngFallback: false,
    upstreamRevision: revision, compiler, flags, nativeTestsPassed: true, syntheticCaptureCases: 6, elfStaticX86_64: true, stagedSources,
    upstream: upstream.map(item => ({ path: item.path, gitBlob: item.gitBlob, originalSha256: item.originalSha256 })),
    own: own.map(item => ({ path: item.path, sha256: sha(item.bytes) })), patches: patches.map((name, i) => ({ name, sha256: patchHashes[i], application: 'applied' })),
    artifact: { path: artifactPath, size: bytes.length, sha256: sha(bytes) }, readelfSha256: sha(report),
    limitations: ['Not an installer, signature or legal clearance', 'Same compiler/sysroot only; not Bionic acceptance',
      'Synthetic signature/bounds tests are not PNG decoding or Android screenshot compatibility', 'No hostile same-UID authentication or global network sandbox'] };
  writeNew(path.join(destination, 'display-build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); return receipt;
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2); if (args.length !== 3 || args[0] !== 'build') throw new Error('Invalid invocation');
    process.stdout.write(JSON.stringify(buildDisplay({ libwsRepo: args[1], output: args[2] })) + '\n');
  } catch { process.stderr.write('Display build rejected; partial output is not installable\n'); process.exitCode = 1; }
}
module.exports = { displayBuildPlan, buildDisplay };
