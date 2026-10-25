// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto'), { spawnSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const root = path.resolve(__dirname, '../..');
const gbinderRevision = '08252c411e63f89c2a7b056ed5bdd0b9728704ec', glibutilRevision = 'e2fe548f8cce21551fd59a461618ea586b5f98fb';
const targets = Object.freeze(['release']);
const plan = Object.freeze({ gbinderRevision, glibutilRevision, targets, installable: false, binderOperationsExecuted: false });
const env = Object.freeze({ PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', TZ: 'UTC', SOURCE_DATE_EPOCH: '0',
  GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0', GIT_NO_REPLACE_OBJECTS: '1',
  GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' });
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const normalize = bytes => Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'));
function binderBuildPlan() { return plan; }
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
function run(tool, args, cwd, options = {}) {
  const executable = '/usr/bin/' + tool, stat = fs.statSync(executable);
  if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022)) throw new Error('Untrusted system tool');
  const result = spawnSync(executable, args, { cwd, env, timeout: 120000, maxBuffer: 2 * 1024 * 1024 });
  if (options.log) {
    writeNew(options.log + '-stdout.txt', result.stdout || Buffer.alloc(0));
    writeNew(options.log + '-stderr.txt', result.stderr || Buffer.alloc(0));
  }
  if (result.error || result.signal || result.status !== 0) throw new Error('Binder build command failed');
  return result;
}
function snapshot(repository, revision, name) {
  if (typeof repository !== 'string' || !path.isAbsolute(repository)) throw new Error('Absolute repository required');
  const checked = directories(repository);
  const git = args => run('git', ['--no-pager', '--no-replace-objects', '--no-lazy-fetch', '-C', checked, ...args], checked).stdout;
  if (git(['rev-parse', '--verify', revision + '^{commit}']).toString().trim() !== revision) throw new Error('Pinned source missing');
  const selected = [];
  for (const entry of git(['ls-tree', '-r', '-z', revision]).toString().split('\0').filter(Boolean)) {
    const match = /^(\d+) blob ([a-f0-9]{40})\t(.+)$/.exec(entry);
    if (!match) throw new Error('Unexpected Git entry');
    const filename = match[3];
    if (!['Makefile', 'LICENSE', 'README', 'AUTHORS', 'lib' + name + '.pc.in', 'lib' + name + '.ver'].includes(filename) &&
        !/^(include|src)\/[A-Za-z0-9_]+\.[ch]$/.test(filename)) continue;
    if (match[1] !== '100644') throw new Error('Unsafe source mode');
    const bytes = git(['cat-file', 'blob', match[2]]);
    if (!bytes.length || bytes.length > 1024 * 1024) throw new Error('Source bound');
    selected.push({ name: filename, bytes: normalize(bytes), gitBlob: match[2], originalSha256: sha(bytes) });
  }
  if (selected.length > 256 || selected.reduce((total, item) => total + item.bytes.length, 0) > 16 * 1024 * 1024 ||
      !['Makefile', 'LICENSE', 'README'].every(required => selected.some(item => item.name === required)) ||
      !selected.some(item => item.name.endsWith('.c'))) throw new Error('Incomplete source inventory');
  return selected;
}
function elf(filename, soname, destination) {
  const report = run('readelf', ['-h', '-d', '-W', filename], destination).stdout.toString();
  if (!/^\s*Class:\s+ELF64\s*$/m.test(report) || !/^\s*Type:\s+DYN\b/m.test(report) ||
      !/^\s*Machine:\s+Advanced Micro Devices X86-64\s*$/m.test(report) ||
      /\((RPATH|RUNPATH)\)/.test(report) || !report.includes('Library soname: [' + soname + ']')) throw new Error('Unexpected shared ELF');
  const dependencies = [...report.matchAll(/\(NEEDED\).*Shared library: \[([^\]]+)\]/g)].map(match => match[1]).sort();
  const allowed = new Set(['libglibutil.so.1', 'libgobject-2.0.so.0', 'libglib-2.0.so.0', 'libc.so.6', 'libpthread.so.0', 'libm.so.6']);
  if (!dependencies.length || dependencies.some(name => !allowed.has(name))) throw new Error('Unexpected dependency');
  if (soname === 'libgbinder.so.1' && !dependencies.includes('libglibutil.so.1')) throw new Error('Private dependency missing');
  return { soname, dependencies, report };
}
function buildBinder({ gbinderRepo, glibutilRepo, output } = {}) {
  if (typeof output !== 'string' || !path.isAbsolute(output) || fs.existsSync(output)) throw new Error('New output required');
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.getuid || process.getuid() === 0) throw new Error('Unprivileged Linux x86_64 required');
  const home = directories(os.homedir()), destination = path.resolve(output);
  if (!destination.startsWith(home + path.sep)) throw new Error('Output outside home');
  directories(path.dirname(destination), home);
  const inventories = { glibutil: snapshot(glibutilRepo, glibutilRevision, 'glibutil'), gbinder: snapshot(gbinderRepo, gbinderRevision, 'gbinder') };
  const testSource = path.join(root, 'tests/native/test_binder_library.c'); directories(path.dirname(testSource));
  const ownBytes = readBoundedRegular(testSource, 1024 * 1024), ownHash = sha(ownBytes), stagedOwn = normalize(ownBytes);
  const compiler = run('gcc', ['-dumpfullversion'], home).stdout.toString().trim();
  if (run('gcc', ['-dumpmachine'], home).stdout.toString().trim() !== 'x86_64-linux-gnu') throw new Error('Unexpected compiler target');
  const glibVersion = run('pkg-config', ['--modversion', 'glib-2.0', 'gobject-2.0'], home).stdout.toString().trim().split('\n');
  fs.mkdirSync(destination, { mode: 0o700 });
  for (const name of ['sources', 'sources/glibutil', 'sources/gbinder', 'libs', 'work']) fs.mkdirSync(path.join(destination, name), { mode: 0o700 });
  const sources = [];
  for (const [name, inventory] of Object.entries(inventories)) for (const item of inventory) {
    const relative = 'sources/' + name + '/' + item.name, directory = path.dirname(path.join(destination, relative));
    if (!fs.existsSync(directory)) fs.mkdirSync(directory, { mode: 0o700 });
    writeNew(path.join(destination, relative), item.bytes);
    sources.push({ stagedPath: relative, sha256: sha(item.bytes), originalSha256: item.originalSha256, gitBlob: item.gitBlob });
  }
  writeNew(path.join(destination, 'work/test_binder_library.c'), stagedOwn);
  sources.push({ stagedPath: 'work/test_binder_library.c', sha256: sha(stagedOwn) });
  function verifyStagedSources() {
    for (const source of sources) {
      const filename = path.join(destination, source.stagedPath); directories(path.dirname(filename), destination);
      if (sha(readBoundedRegular(filename, 1024 * 1024)) !== source.sha256) throw new Error('Staged source changed');
    }
  }
  verifyStagedSources();
  const makeArgs = ['--no-print-directory', '-rR', '-j2', 'CC=/usr/bin/gcc', 'AR=/usr/bin/ar', 'STRIP=/usr/bin/strip',
    'PKG_CONFIG=/usr/bin/pkg-config', 'CFLAGS=-g0', 'LDFLAGS=-Wl,--build-id=none', 'KEEP_SYMBOLS=0'];
  run('make', [...makeArgs, 'release'], path.join(destination, 'sources/glibutil'), { log: path.join(destination, 'work/glibutil-make') });
  run('make', [...makeArgs, 'LIBGLIBUTIL_PATH=../glibutil', 'release'], path.join(destination, 'sources/gbinder'), { log: path.join(destination, 'work/gbinder-make') });
  const artifacts = {}, reports = {};
  for (const [name, version] of [['glibutil', '1.0.80'], ['gbinder', '1.1.45']]) {
    const filename = 'lib' + name + '.so.' + version, relative = 'libs/' + filename;
    const bytes = readBoundedRegular(path.join(destination, 'sources', name, 'build/release', filename), 8 * 1024 * 1024);
    if (!bytes.length) throw new Error('Empty shared library'); writeNew(path.join(destination, relative), bytes);
    fs.symlinkSync(filename, path.join(destination, 'libs/lib' + name + '.so.1'));
    artifacts[name] = { path: relative, size: bytes.length, sha256: sha(bytes) };
    const info = elf(relative, 'lib' + name + '.so.1', destination); writeNew(path.join(destination, 'work/' + name + '-readelf.txt'), info.report);
    reports[name] = { soname: info.soname, dependencies: info.dependencies, reportSha256: sha(info.report) };
  }
  verifyStagedSources();
  run('gcc', ['-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', 'work/test_binder_library.c', '-ldl', '-o', 'work/test_binder_library'], destination);
  const smoke = spawnSync(path.join(destination, 'work/test_binder_library'), Object.values(artifacts).map(item => path.join(destination, item.path)),
    { cwd: destination, env: { ...env, LD_LIBRARY_PATH: path.join(destination, 'libs') }, timeout: 10000, maxBuffer: 65536 });
  if (smoke.error || smoke.signal || smoke.status !== 0 || smoke.stderr.length ||
      smoke.stdout.toString().trim() !== 'Private library symbol checks passed; no Binder functions called') throw new Error('Library symbol smoke failed');
  writeNew(path.join(destination, 'work/symbol-smoke.txt'), smoke.stdout);
  directories(path.dirname(testSource)); if (sha(readBoundedRegular(testSource, 1024 * 1024)) !== ownHash) throw new Error('Own source changed');
  verifyStagedSources();
  for (const artifact of Object.values(artifacts)) if (sha(readBoundedRegular(path.join(destination, artifact.path), 8 * 1024 * 1024)) !== artifact.sha256) throw new Error('Artifact changed');
  const receipt = { format: 1, component: 'binder-shared-library-preview', installable: false, binderOperationsExecuted: false,
    gbinderRevision, glibutilRevision, compiler, glibVersion, makeArgs, targets, sources, artifacts, elf: reports, symbolSmokePassed: true,
    limitations: ['Not an installer, signature or legal clearance', 'Same compiler/GLib/sysroot only; not cross-host reproducibility',
      'No Binder device/protocol/Waydroid integration tested', 'No hostile same-UID authentication or global network sandbox'] };
  writeNew(path.join(destination, 'binder-build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); return receipt;
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2); if (args.length !== 4 || args[0] !== 'build') throw new Error('Invalid invocation');
    process.stdout.write(JSON.stringify(buildBinder({ gbinderRepo: args[1], glibutilRepo: args[2], output: args[3] })) + '\n');
  } catch { process.stderr.write('Binder build rejected; partial output is not installable\n'); process.exitCode = 1; }
}
module.exports = { binderBuildPlan, buildBinder };
