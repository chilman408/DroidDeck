// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto'), { spawnSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const root = path.resolve(__dirname, '../..'), jdk = '/usr/lib/jvm/java-17-openjdk-amd64';
const sourceFiles = Object.freeze(['runtime/waydroid-display/TeslaLocationBridge.java']);
const d8Sha256 = 'd31fd0dc751d48740009cdd9a485126acb1d0d14c59b9f05579479940f4adf74';
const plan = Object.freeze({ sourceFiles, d8Version: '8.13.17', d8Sha256, javaRelease: 8, minApi: 26,
  jarTimestamp: '2000-01-01T00:00:00Z', installable: false, androidOperationsExecuted: false });
const env = Object.freeze({ PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', TZ: 'UTC', SOURCE_DATE_EPOCH: '0' });
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function gpsBuildPlan() { return plan; }
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
function readTool(filename) {
  const before = fs.lstatSync(filename);
  if (!before.isFile() || before.nlink !== 1 || before.size < 1 || before.size > 64 * 1024 * 1024) throw new Error('Invalid tool input');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.dev !== before.dev || stat.ino !== before.ino || stat.size !== before.size) throw new Error('Tool changed');
    const bytes = Buffer.alloc(stat.size + 1); let length = 0;
    while (length < bytes.length) { const count = fs.readSync(fd, bytes, length, bytes.length - length, length); if (!count) break; length += count; }
    const after = fs.fstatSync(fd);
    if (!after.isFile() || after.nlink !== 1 || length !== stat.size || after.size !== stat.size ||
        after.mtimeMs !== stat.mtimeMs || after.ctimeMs !== stat.ctimeMs) throw new Error('Tool changed');
    return bytes.subarray(0, length);
  } finally { fs.closeSync(fd); }
}
function writeNew(filename, bytes) {
  const fd = fs.openSync(filename, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function run(tool, args, cwd, expected = 0) {
  const executable = path.join(jdk, 'bin', tool), stat = fs.lstatSync(executable);
  if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022)) throw new Error('Untrusted JDK tool');
  const result = spawnSync(executable, args, { cwd, env, timeout: 60000, maxBuffer: 1024 * 1024 });
  if (result.error || result.signal || result.status !== expected) throw new Error('GPS build command failed');
  return result;
}
function buildGps({ d8Jar, output } = {}) {
  if (typeof output !== 'string' || !path.isAbsolute(output) || fs.existsSync(output)) throw new Error('New output required');
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.getuid || process.getuid() === 0) throw new Error('Unprivileged Linux x86_64 required');
  const home = directories(os.homedir()), destination = path.resolve(output);
  if (!destination.startsWith(home + path.sep)) throw new Error('Output outside home');
  directories(path.dirname(destination), home); directories(jdk);
  if (typeof d8Jar !== 'string' || !path.isAbsolute(d8Jar) || path.resolve(d8Jar) !== d8Jar) throw new Error('Canonical absolute pinned D8 input required');
  directories(path.dirname(d8Jar)); const toolBytes = readTool(d8Jar);
  if (sha(toolBytes) !== d8Sha256) throw new Error('Publisher D8 pin mismatch');
  const own = [...sourceFiles, 'tests/java/GpsBridgeReflectionTest.java'].map(relative => {
    const filename = path.join(root, relative);
    directories(path.dirname(filename)); const bytes = readBoundedRegular(filename, 1024 * 1024);
    if (!bytes.length) throw new Error('Empty source');
    return { filename, relative, digest: sha(bytes), bytes: Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n')) };
  });
  const compilerVersion = run('javac', ['-version'], home).stdout.toString().trim();
  if (!/^javac 17\./.test(compilerVersion)) throw new Error('JDK17 required');
  fs.mkdirSync(destination, { mode: 0o700 });
  for (const name of ['sources', 'classes', 'tests-classes', 'dex', 'checked', 'tools', 'work']) fs.mkdirSync(path.join(destination, name), { mode: 0o700 });
  for (const item of own) writeNew(path.join(destination, 'sources', path.basename(item.relative)), item.bytes);
  writeNew(path.join(destination, 'tools/r8.jar'), toolBytes);
  const compile = ['-J-Xmx256m', '--release', '8', '-encoding', 'UTF-8', '-g:none', '-Xlint:all', '-Werror', '-proc:none'];
  run('javac', [...compile, '-d', 'classes', 'sources/TeslaLocationBridge.java'], destination);
  if (JSON.stringify(fs.readdirSync(path.join(destination, 'classes')).sort()) !== JSON.stringify(['TeslaLocationBridge.class'])) throw new Error('Unexpected production classes');
  run('javac', [...compile, '-classpath', 'classes', '-d', 'tests-classes', 'sources/GpsBridgeReflectionTest.java'], destination);
  const hostTests = run('java', ['-Xmx256m', '-ea', '-cp', 'classes:tests-classes', 'GpsBridgeReflectionTest'], destination);
  writeNew(path.join(destination, 'work/host-tests.txt'), hostTests.stdout);
  for (const args of [[], ['--apply', 'invalid'], ['--open']]) {
    const result = run('java', ['-Xmx256m', '-cp', 'classes', 'TeslaLocationBridge', ...args], destination, 1);
    if (result.stdout.length || result.stderr.toString().trim() !== 'Location operation failed: IllegalArgumentException') throw new Error('Invalid-argument check failed');
  }
  const classReport = run('javap', ['-verbose', '-classpath', 'classes', 'TeslaLocationBridge'], destination).stdout;
  if (!/major version:\s+52\b/.test(classReport.toString())) throw new Error('Unexpected class version');
  writeNew(path.join(destination, 'work/javap.txt'), classReport);
  const d8 = ['-Xmx256m', '-cp', 'tools/r8.jar', 'com.android.tools.r8.D8'];
  const version = run('java', [...d8, '--version'], destination).stdout.toString().trim();
  if (!/^D8 8\.13\.17\b/.test(version)) throw new Error('Unexpected D8 version');
  const converted = run('java', [...d8, '--release', '--min-api', '26', '--lib', jdk, '--output', 'dex', 'classes/TeslaLocationBridge.class'], destination);
  writeNew(path.join(destination, 'work/d8-diagnostics.txt'), converted.stderr);
  if (JSON.stringify(fs.readdirSync(path.join(destination, 'dex')).sort()) !== JSON.stringify(['classes.dex'])) throw new Error('Unexpected DEX output');
  const dexPath = path.join(destination, 'dex/classes.dex');
  fs.chmodSync(dexPath, 0o600); fs.utimesSync(dexPath, new Date(plan.jarTimestamp), new Date(plan.jarTimestamp));
  run('jar', ['--create', '--no-manifest', '--file', 'location-bridge.jar', '-C', 'dex', 'classes.dex'], destination);
  fs.chmodSync(path.join(destination, 'location-bridge.jar'), 0o600);
  const jarEntries = run('jar', ['--list', '--file', 'location-bridge.jar'], destination).stdout.toString().trim().split('\n');
  if (JSON.stringify(jarEntries) !== JSON.stringify(['classes.dex'])) throw new Error('Unexpected archive entry');
  const reparsed = run('java', [...d8, '--release', '--min-api', '26', '--output', 'checked', 'location-bridge.jar'], destination);
  writeNew(path.join(destination, 'work/d8-reparse.txt'), reparsed.stderr);
  if (JSON.stringify(fs.readdirSync(path.join(destination, 'checked')).sort()) !== JSON.stringify(['classes.dex'])) throw new Error('DEX reparse failed');
  const reparseBytes = readBoundedRegular(path.join(destination, 'checked/classes.dex'), 8 * 1024 * 1024);
  if (!reparseBytes.length) throw new Error('Empty reparsed DEX');
  for (const item of own) {
    directories(path.dirname(item.filename));
    if (sha(readBoundedRegular(item.filename, 1024 * 1024)) !== item.digest) throw new Error('Source generation changed');
  }
  directories(path.dirname(d8Jar));
  if (sha(readTool(d8Jar)) !== d8Sha256) throw new Error('Tool generation changed');
  const artifacts = {};
  for (const [name, relative] of [['class', 'classes/TeslaLocationBridge.class'], ['dex', 'dex/classes.dex'], ['jar', 'location-bridge.jar']]) {
    const bytes = readBoundedRegular(path.join(destination, relative), 8 * 1024 * 1024);
    if (!bytes.length) throw new Error('Empty artifact'); fs.chmodSync(path.join(destination, relative), 0o600);
    artifacts[name] = { path: relative, size: bytes.length, sha256: sha(bytes) };
  }
  const receipt = { format: 1, component: 'gps-java-dex-preview', installable: false, androidOperationsExecuted: false,
    compilerVersion, javaRelease: 8, minApi: 26, compileFlags: compile, d8Version: version, d8Sha256,
    sourceFiles: own.map(item => ({ path: item.relative, stagedPath: 'sources/' + path.basename(item.relative), sha256: sha(item.bytes) })), artifacts,
    hostTestsPassed: true, invalidArgumentChecks: 3, dexReparsePassed: true, reparseSha256: sha(reparseBytes), jarEntries,
    jarTimestamp: plan.jarTimestamp, diagnosticsSha256: sha(converted.stderr),
    limitations: ['Not an installer, signature, legal clearance or hostile same-UID authentication',
      'Same JDK/D8/sysroot reproducibility only', 'No Android loading, GPS delivery, Binder or mock-location runtime operation tested',
      'Host JDK standard-library D8 input for reflection-only Java8; Android platform compatibility requires fresh runtime checks'] };
  writeNew(path.join(destination, 'gps-build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); return receipt;
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 3 || args[0] !== 'build') throw new Error('Invalid invocation');
    process.stdout.write(JSON.stringify(buildGps({ d8Jar: args[1], output: args[2] })) + '\n');
  } catch { process.stderr.write('GPS build rejected; partial output is not installable\n'); process.exitCode = 1; }
}
module.exports = { gpsBuildPlan, buildGps };
