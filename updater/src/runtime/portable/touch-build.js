// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto'), { spawnSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const root = path.resolve(__dirname, '../..');
const revision = '8d2e2f2923d15baba6101d879fc9593ef4a0ef78';
const upstreamFiles = Object.freeze(['LICENSE', 'base64.c', 'base64.h', 'handshake.c', 'sha1.c', 'sha1.h', 'utf8.c', 'utf8.h', 'ws.c', 'ws.h']);
const ownFiles = Object.freeze(['tesla-android-x86-touch.c', 'touch_protocol.c', 'touch_protocol.h', 'touch_mapping.c', 'touch_mapping.h', 'touch_keepalive.c', 'touch_keepalive.h']);
const tests = Object.freeze(['test_touch_protocol', 'test_touch_mapping', 'test_touch_keepalive']);
const patches = Object.freeze(['libws-loopback.patch', 'libws-case-insensitive-handshake.patch']);
const patchHashes = Object.freeze(['942143dec375151cfb1287c539da2f34bdc3c8365773dad1682a0c8bca6c9998', 'eeaf8e54606861929fbb4d81290655dc9f73f4451b7f0bd19d48d9bb6cc7caef']);
const flags = Object.freeze(['-D_GNU_SOURCE', '-std=c11', '-O2', '-Wall', '-Wextra', '-pthread', '-static', '-no-pie', '-Wl,--build-id=none']);
const plan = Object.freeze({ upstreamRevision: revision, upstreamFiles, ownFiles, patches, tests, flags, installable: false });
const env = Object.freeze({ PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', TZ: 'UTC', SOURCE_DATE_EPOCH: '0',
  GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0', GIT_NO_REPLACE_OBJECTS: '1',
  GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' });
const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');
const normalize = data => Buffer.from(data.toString('utf8').replace(/\r\n/g, '\n'));
function touchBuildPlan() { return plan; }
function validateElfReport(text) {
  if (typeof text !== 'string' || text.length > 65536 ||
      !/^\s*Class:\s+ELF64\s*$/m.test(text) || !/^\s*Data:\s+2's complement, little endian\s*$/m.test(text) ||
      !/^\s*Type:\s+EXEC\b/m.test(text) || !/^\s*Machine:\s+Advanced Micro Devices X86-64\s*$/m.test(text) ||
      !/^\s*Program Headers:/m.test(text) || !/^\s*LOAD\s/m.test(text) || /^\s*(INTERP|DYNAMIC)\s/m.test(text)) {
    throw new Error('Unexpected ELF layout');
  }
  return true;
}
function run(tool, args, cwd, maxBuffer = 1024 * 1024) {
  const result = spawnSync('/usr/bin/' + tool, args, { cwd, env, timeout: 30000, maxBuffer });
  if (result.error || result.signal || result.status !== 0) throw new Error('Native build command failed');
  return { stdout: result.stdout, stderr: result.stderr };
}
function directories(filename, privateBelow) {
  const absolute = path.resolve(filename);
  let current = path.parse(absolute).root;
  for (const part of absolute.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Linked or non-directory path');
    if (privateBelow && (current === privateBelow || current.startsWith(privateBelow + path.sep)) &&
        (stat.uid !== process.getuid() || (stat.mode & 0o022))) throw new Error('Non-private build parent');
  }
  return absolute;
}
function writeNew(filename, data, mode = 0o600) {
  const fd = fs.openSync(filename, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, mode);
  try { fs.writeFileSync(fd, data); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function buildTouch({ libwsRepo, output } = {}) {
  if (typeof output !== 'string' || !path.isAbsolute(output) || fs.existsSync(output)) throw new Error('Output must be new');
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.getuid || process.getuid() === 0) throw new Error('Unprivileged Linux x86_64 required');
  const home = directories(os.homedir());
  const destination = path.resolve(output);
  if (!destination.startsWith(home + path.sep)) throw new Error('Output must be below caller home');
  directories(path.dirname(destination), home);
  if (typeof libwsRepo !== 'string' || !path.isAbsolute(libwsRepo)) throw new Error('Absolute source repository required');
  const repository = directories(libwsRepo);
  const git = args => run('git', ['--no-pager', '--no-replace-objects', '--no-lazy-fetch', '-C', repository, ...args]);
  if (git(['rev-parse', '--verify', revision + '^{commit}']).stdout.toString().trim() !== revision) throw new Error('Pinned source missing');
  const entries = new Map(git(['ls-tree', '-z', revision]).stdout.toString().split('\0').filter(Boolean).map(line => {
    const match = /^(\d+) blob ([a-f0-9]{40})\t(.+)$/.exec(line);
    return match ? [match[3], { mode: match[1], oid: match[2] }] : [line, null];
  }));
  const captured = [], upstream = [];
  for (const name of upstreamFiles) {
    if (entries.get(name)?.mode !== '100644') throw new Error('Unsafe pinned source entry');
    const bytes = git(['cat-file', 'blob', entries.get(name).oid]).stdout;
    if (!bytes.length || bytes.length > 1024 * 1024) throw new Error('Source bound');
    upstream.push({ name, bytes: normalize(bytes), originalSha256: sha256(bytes), gitBlob: entries.get(name).oid });
  }
  function capture(relative) {
    const filename = path.join(root, relative);
    directories(path.dirname(filename)); const bytes = readBoundedRegular(filename, 1024 * 1024);
    captured.push({ filename, digest: sha256(bytes) });
    return normalize(bytes);
  }
  const own = ownFiles.map(name => ({ name, bytes: capture('runtime/touch/' + name) }));
  const nativeTests = tests.map(name => ({ name, bytes: capture('tests/native/' + name + '.c') }));
  const patchData = patches.map((name, i) => {
    const bytes = capture('runtime/patches/' + name);
    if (sha256(bytes) !== patchHashes[i]) throw new Error('Unreviewed patch');
    return { name, bytes };
  });
  const target = run('gcc', ['-dumpmachine'], repository).stdout.toString().trim();
  if (target !== 'x86_64-linux-gnu') throw new Error('Unexpected compiler target');
  const compilerVersion = run('gcc', ['-dumpfullversion'], repository).stdout.toString().trim();
  if (!/^\d+\.\d+\.\d+$/.test(compilerVersion)) throw new Error('Unexpected compiler version');
  fs.mkdirSync(destination, { mode: 0o700 });
  for (const directory of ['sources', 'sources/libws', 'sources/runtime', 'sources/runtime/touch', 'sources/tests', 'sources/tests/native', 'patches', 'work']) fs.mkdirSync(path.join(destination, directory), { mode: 0o700 });
  for (const item of upstream) writeNew(path.join(destination, 'sources/libws', item.name), item.bytes);
  for (const item of own) writeNew(path.join(destination, 'sources/runtime/touch', item.name), item.bytes);
  for (const item of nativeTests) writeNew(path.join(destination, 'sources/tests/native', item.name + '.c'), item.bytes);
  for (const item of patchData) {
    writeNew(path.join(destination, 'patches', item.name), item.bytes);
    const args = ['--no-pager', '--no-replace-objects', 'apply', '--no-index', '--ignore-space-change', '--directory=sources/libws'];
    run('git', [...args, '--check', 'patches/' + item.name], destination);
    run('git', [...args, 'patches/' + item.name], destination);
    item.application = 'applied';
  }
  const include = ['-Isources/libws', '-Isources/runtime/touch'];
  const ownC = ownFiles.filter(name => name.endsWith('.c')).map(name => 'sources/runtime/touch/' + name);
  run('gcc', ['-D_GNU_SOURCE', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread', '-fsyntax-only', ...include, ...ownC], destination);
  const link = run('gcc', [...flags, ...include, ...ownC, ...upstreamFiles.filter(name => name.endsWith('.c')).map(name => 'sources/libws/' + name), '-o', 'tesla-android-x86-touch'], destination);
  writeNew(path.join(destination, 'work/link-warnings.txt'), link.stderr);
  fs.chmodSync(path.join(destination, 'tesla-android-x86-touch'), 0o700);
  for (const name of tests) {
    const unit = name.replace('test_', '');
    run('gcc', ['-D_GNU_SOURCE', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread', ...include,
      'sources/tests/native/' + name + '.c', 'sources/runtime/touch/' + unit + '.c', '-o', 'work/' + name], destination);
    const result = spawnSync(path.join(destination, 'work', name), [], { cwd: destination, env, timeout: 10000, maxBuffer: 65536 });
    if (result.error || result.signal || result.status !== 0) throw new Error('Native test failed');
  }
  const report = run('readelf', ['-h', '-l', '-W', 'tesla-android-x86-touch'], destination, 65536).stdout.toString();
  validateElfReport(report);
  writeNew(path.join(destination, 'work/readelf.txt'), report);
  // Only the isolated generated tests run; the real uinput server is never launched.
  for (const item of captured) {
    directories(path.dirname(item.filename));
    if (sha256(readBoundedRegular(item.filename, 1024 * 1024)) !== item.digest) throw new Error('Source generation changed');
  }
  const artifact = readBoundedRegular(path.join(destination, 'tesla-android-x86-touch'), 8 * 1024 * 1024);
  const receipt = {
    format: 1, component: 'touch-native-preview', installable: false, upstreamRevision: revision,
    compiler: { executable: '/usr/bin/gcc', target, version: compilerVersion }, flags,
    nativeTestsPassed: true, elfStaticX86_64: true, tests,
    sources: [...upstream.map(item => ({ path: 'libws/' + item.name, sha256: sha256(item.bytes), originalSha256: item.originalSha256, gitBlob: item.gitBlob })),
      ...own.map(item => ({ path: 'runtime/touch/' + item.name, sha256: sha256(item.bytes) })),
      ...nativeTests.map(item => ({ path: 'tests/native/' + item.name + '.c', sha256: sha256(item.bytes) }))],
    patches: patchData.map(item => ({ name: item.name, sha256: sha256(item.bytes), application: item.application })),
    patchContextPolicy: 'git apply --ignore-space-change; reviewed patches use spaces, pinned source uses tabs',
    patchedSources: ['ws.c', 'handshake.c'].map(name => ({ name, sha256: sha256(readBoundedRegular(path.join(destination, 'sources/libws', name), 1024 * 1024)) })),
    artifact: { name: 'tesla-android-x86-touch', size: artifact.length, sha256: sha256(artifact) },
    warningSha256: sha256(link.stderr), readelfSha256: sha256(report),
    limitations: ['Not an installer, signature or license clearance', 'Ubuntu/glibc preview, not an Android Bionic module',
      'Reproducibility requires the same compiler and sysroot; receipt does not authenticate a hostile same-UID writer'],
  };
  writeNew(path.join(destination, 'touch-build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  return receipt;
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 3 || args[0] !== 'build') throw new Error('Invalid invocation');
    const result = buildTouch({ libwsRepo: args[1], output: args[2] });
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch {
    process.stderr.write('Native touch build rejected; partial output is not installable\n');
    process.exitCode = 1;
  }
}
module.exports = { touchBuildPlan, validateElfReport, buildTouch };
