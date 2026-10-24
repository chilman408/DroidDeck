// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto'), { spawnSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const { validateElfReport } = require('./touch-build');
const root = path.resolve(__dirname, '../..');
const revision = '6e3707cbeafa4a9a29d1e77ab3ad0e793836ace2';
const upstreamFiles = Object.freeze(['LICENSE', 'httplib.h']);
const ownFiles = Object.freeze(['runtime/api/tesla-android-x86-api.cpp', 'tests/native/test_api.cpp']);
const flags = Object.freeze(['-DCPPHTTPLIB_NO_EXCEPTIONS', '-std=c++17', '-O2', '-Wall', '-Wextra', '-pthread', '-static', '-no-pie', '-Wl,--build-id=none']);
const tests = Object.freeze(['test_api']);
const plan = Object.freeze({ upstreamRevision: revision, upstreamFiles, ownFiles, flags, tests, installable: false, serverExecuted: false });
const env = Object.freeze({ PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', TZ: 'UTC', SOURCE_DATE_EPOCH: '0',
  GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0', GIT_NO_REPLACE_OBJECTS: '1',
  GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' });
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const normalize = bytes => Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'));
function apiBuildPlan() { return plan; }
function run(tool, args, cwd, maxBuffer = 1024 * 1024) {
  const result = spawnSync('/usr/bin/' + tool, args, { cwd, env, timeout: 30000, maxBuffer });
  if (result.error || result.signal || result.status !== 0) throw new Error('Native API command failed');
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
function writeNew(filename, bytes, mode = 0o600) {
  const fd = fs.openSync(filename, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, mode);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function generatedExecutable(filename, args, cwd, expected) {
  const result = spawnSync(filename, args, { cwd, env, timeout: 10000, maxBuffer: 65536 });
  if (result.error || result.signal || result.status !== expected) throw new Error('Generated API test failed');
  return result;
}
function buildApi({ upstreamRepo, output } = {}) {
  if (typeof output !== 'string' || !path.isAbsolute(output) || fs.existsSync(output)) throw new Error('Output must be new');
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.getuid || process.getuid() === 0) throw new Error('Unprivileged Linux x86_64 required');
  const home = directories(os.homedir()), destination = path.resolve(output);
  if (!destination.startsWith(home + path.sep)) throw new Error('Output must be below home');
  directories(path.dirname(destination), home);
  if (typeof upstreamRepo !== 'string' || !path.isAbsolute(upstreamRepo)) throw new Error('Absolute source repository required');
  const repository = directories(upstreamRepo);
  const git = args => run('git', ['--no-pager', '--no-replace-objects', '--no-lazy-fetch', '-C', repository, ...args]);
  if (git(['rev-parse', '--verify', revision + '^{commit}']).stdout.toString().trim() !== revision) throw new Error('Pinned source missing');
  const entries = new Map(git(['ls-tree', '-z', revision]).stdout.toString().split('\0').filter(Boolean).map(line => {
    const match = /^(\d+) blob ([a-f0-9]{40})\t(.+)$/.exec(line);
    return match ? [match[3], { mode: match[1], oid: match[2] }] : [line, null];
  }));
  const upstream = upstreamFiles.map(name => {
    if (entries.get(name)?.mode !== '100644') throw new Error('Unsafe pinned source');
    const bytes = git(['cat-file', 'blob', entries.get(name).oid]).stdout;
    if (!bytes.length || bytes.length > 1024 * 1024) throw new Error('Source bound');
    return { path: 'upstream/' + name, bytes: normalize(bytes), originalSha256: sha256(bytes), gitBlob: entries.get(name).oid };
  });
  const own = ownFiles.map(relative => {
    const filename = path.join(root, relative);
    directories(path.dirname(filename)); const bytes = readBoundedRegular(filename, 1024 * 1024);
    if (!bytes.length) throw new Error('Empty own source');
    return { path: relative, filename, digest: sha256(bytes), bytes: normalize(bytes) };
  });
  const target = run('g++', ['-dumpmachine'], repository).stdout.toString().trim();
  if (target !== 'x86_64-linux-gnu') throw new Error('Unexpected compiler target');
  const version = run('g++', ['-dumpfullversion'], repository).stdout.toString().trim();
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Unexpected compiler version');
  fs.mkdirSync(destination, { mode: 0o700 });
  for (const name of ['sources', 'sources/upstream', 'sources/runtime', 'sources/runtime/api', 'sources/tests', 'sources/tests/native', 'work']) fs.mkdirSync(path.join(destination, name), { mode: 0o700 });
  for (const item of [...upstream, ...own]) writeNew(path.join(destination, 'sources', item.path), item.bytes);
  const include = ['-Isources/upstream'];
  const source = 'sources/runtime/api/tesla-android-x86-api.cpp', testSource = 'sources/tests/native/test_api.cpp';
  const syntax = ['-DCPPHTTPLIB_NO_EXCEPTIONS', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread', '-fsyntax-only', ...include];
  run('g++', [...syntax, source], destination);
  run('g++', [...syntax, testSource], destination);
  const binary = 'tesla-android-x86-api';
  // Fixed object names keep complete linker diagnostics reproducible, too.
  const compile = run('g++', [...flags, ...include, '-c', source, '-o', 'work/api.o'], destination);
  writeNew(path.join(destination, 'work/compile-warnings.txt'), compile.stderr);
  const link = run('g++', [...flags, 'work/api.o', '-o', binary], destination);
  writeNew(path.join(destination, 'work/link-warnings.txt'), link.stderr);
  fs.chmodSync(path.join(destination, binary), 0o700);
  const testCompile = run('g++', [...flags, ...include, '-c', testSource, '-o', 'work/test_api.o'], destination);
  writeNew(path.join(destination, 'work/test-compile-warnings.txt'), testCompile.stderr);
  const testLink = run('g++', [...flags, 'work/test_api.o', '-o', 'work/test_api'], destination);
  writeNew(path.join(destination, 'work/test-link-warnings.txt'), testLink.stderr);
  const native = generatedExecutable(path.join(destination, 'work/test_api'), [], destination, 0);
  writeNew(path.join(destination, 'work/native-test.txt'), native.stdout);
  // Invalid arguments exit before the API constructs a server or binds a socket.
  for (const args of [[], ['0', '100'], ['100', '0'], ['8193', '100']]) generatedExecutable(path.join(destination, binary), args, destination, 2);
  const report = run('readelf', ['-h', '-l', '-W', binary], destination, 65536).stdout.toString();
  validateElfReport(report);
  writeNew(path.join(destination, 'work/readelf.txt'), report);
  for (const item of own) {
    directories(path.dirname(item.filename));
    if (sha256(readBoundedRegular(item.filename, 1024 * 1024)) !== item.digest) throw new Error('Source generation changed');
  }
  const artifact = readBoundedRegular(path.join(destination, binary), 8 * 1024 * 1024);
  const receipt = {
    format: 1, component: 'api-native-preview', installable: false, serverExecuted: false, upstreamRevision: revision,
    compiler: { executable: '/usr/bin/g++', target, version }, flags, include, syntaxFlags: syntax,
    nativeTestsPassed: true, elfStaticX86_64: true, tests, invalidArgumentChecks: 4,
    sources: [...upstream.map(item => ({ path: item.path, sha256: sha256(item.bytes), originalSha256: item.originalSha256, gitBlob: item.gitBlob })),
      ...own.map(item => ({ path: item.path, sha256: sha256(item.bytes) }))],
    artifact: { name: binary, size: artifact.length, sha256: sha256(artifact) },
    warningSha256: sha256(link.stderr), testWarningSha256: sha256(testLink.stderr), readelfSha256: sha256(report),
    compileWarningSha256: sha256(compile.stderr), testCompileWarningSha256: sha256(testCompile.stderr),
    limitations: ['Not an installer, signature or license clearance', 'Ubuntu/glibc preview, not Android Bionic runtime acceptance',
      'Same compiler/sysroot repeatability only; receipt does not authenticate hostile same-UID changes', 'No HTTP server, Android or streaming integration was tested'],
  };
  writeNew(path.join(destination, 'api-build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  return receipt;
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 3 || args[0] !== 'build') throw new Error('Invalid invocation');
    const result = buildApi({ upstreamRepo: args[1], output: args[2] });
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch {
    process.stderr.write('Native API build rejected; partial output is not installable\n');
    process.exitCode = 1;
  }
}
module.exports = { apiBuildPlan, buildApi };
