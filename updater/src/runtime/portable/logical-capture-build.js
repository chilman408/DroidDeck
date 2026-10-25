// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto'), { spawnSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const root = path.resolve(__dirname, '../..'), jdk = '/usr/lib/jvm/java-17-openjdk-amd64';
const sourceFiles = Object.freeze(['runtime/waydroid-display/TeslaLogicalCapture.java']);
const d8Sha256 = 'd31fd0dc751d48740009cdd9a485126acb1d0d14c59b9f05579479940f4adf74';
const androidSha256 = 'd9eb9da824d9e247a352f570f01e1169e725b2954bca9e283a71786c59b59f9a';
const plan = Object.freeze({ sourceFiles, d8Version: '8.13.17', d8Sha256, sdkApi: 36, sdkRevision: 2,
  androidSha256, javaRelease: 8, minApi: 26, jarTimestamp: '2000-01-01T00:00:00Z',
  installable: false, androidOperationsExecuted: false });
const env = Object.freeze({ PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', TZ: 'UTC', SOURCE_DATE_EPOCH: '0' });
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function logicalCaptureBuildPlan() { return plan; }
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
  if (result.error || result.signal || result.status !== expected) throw new Error('Logical build command failed');
  return result;
}
function buildLogicalCapture({ d8Jar, androidJar, output } = {}) {
  if (typeof output !== 'string' || !path.isAbsolute(output) || fs.existsSync(output)) throw new Error('New output required');
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.getuid || process.getuid() === 0) throw new Error('Unprivileged Linux x86_64 required');
  const home = directories(os.homedir()), destination = path.resolve(output);
  if (!destination.startsWith(home + path.sep)) throw new Error('Output outside home');
  directories(path.dirname(destination), home); directories(jdk);
  const inputs = [[d8Jar, d8Sha256, 'r8.jar'], [androidJar, androidSha256, 'android.jar']].map(([filename, digest, name]) => {
    if (typeof filename !== 'string' || !path.isAbsolute(filename) || path.resolve(filename) !== filename) throw new Error('Canonical absolute pinned tool required');
    directories(path.dirname(filename)); const bytes = readTool(filename);
    if (sha(bytes) !== digest) throw new Error('Publisher tool pin mismatch');
    return { filename, digest, name, bytes };
  });
  const own = [...sourceFiles, 'tests/java/LogicalCaptureValidationTest.java'].map(relative => {
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
  for (const input of inputs) writeNew(path.join(destination, 'tools', input.name), input.bytes);
  const compile = ['-J-Xmx256m', '-source', '8', '-target', '8', '-bootclasspath', 'tools/android.jar',
    '-encoding', 'UTF-8', '-g:none', '-Xlint:all', '-Werror', '-proc:none'];
  run('javac', [...compile, '-d', 'classes', 'sources/TeslaLogicalCapture.java'], destination);
  if (JSON.stringify(fs.readdirSync(path.join(destination, 'classes')).sort()) !== JSON.stringify(['TeslaLogicalCapture.class'])) throw new Error('Unexpected production classes');
  run('javac', ['-J-Xmx256m', '--release', '8', '-encoding', 'UTF-8', '-g:none', '-Xlint:all', '-Werror', '-proc:none',
    '-classpath', 'classes:tools/android.jar', '-d', 'tests-classes', 'sources/LogicalCaptureValidationTest.java'], destination);
  const hostTests = run('java', ['-Xmx256m', '-ea', '-cp', 'classes:tests-classes:tools/android.jar', 'LogicalCaptureValidationTest'], destination);
  if (hostTests.stdout.toString().trim() !== 'Invalid calibration checks passed: 15' || hostTests.stderr.length) throw new Error('Invalid matrix checks failed');
  writeNew(path.join(destination, 'work/host-tests.txt'), hostTests.stdout);
  for (const args of [['invalid'], ['--calibrate'], ['--calibrate', '0', '0', '0', '0', '1', '0']]) {
    const result = run('java', ['-Xmx256m', '-cp', 'classes:tools/android.jar', 'TeslaLogicalCapture', ...args], destination, 1);
    if (result.stdout.length || !/^java\.lang\.IllegalArgumentException: (Unsupported helper operation|Invalid calibration)\r?\n/.test(result.stderr.toString())) throw new Error('Invalid-argument check failed');
  }
  const classReport = run('javap', ['-verbose', '-classpath', 'classes:tools/android.jar', 'TeslaLogicalCapture'], destination).stdout;
  if (!/major version:\s+52\b/.test(classReport.toString())) throw new Error('Unexpected class version');
  writeNew(path.join(destination, 'work/javap.txt'), classReport);
  const d8 = ['-Xmx256m', '-cp', 'tools/r8.jar', 'com.android.tools.r8.D8'];
  const version = run('java', [...d8, '--version'], destination).stdout.toString().trim();
  if (!/^D8 8\.13\.17\b/.test(version)) throw new Error('Unexpected D8 version');
  const converted = run('java', [...d8, '--release', '--min-api', '26', '--lib', 'tools/android.jar', '--output', 'dex', 'classes/TeslaLogicalCapture.class'], destination);
  writeNew(path.join(destination, 'work/d8-diagnostics.txt'), converted.stderr);
  if (JSON.stringify(fs.readdirSync(path.join(destination, 'dex')).sort()) !== JSON.stringify(['classes.dex'])) throw new Error('Unexpected DEX output');
  const dexPath = path.join(destination, 'dex/classes.dex');
  fs.chmodSync(dexPath, 0o600); fs.utimesSync(dexPath, new Date(plan.jarTimestamp), new Date(plan.jarTimestamp));
  run('jar', ['--create', '--no-manifest', '--file', 'logical-capture.jar', '-C', 'dex', 'classes.dex'], destination);
  fs.chmodSync(path.join(destination, 'logical-capture.jar'), 0o600);
  const jarEntries = run('jar', ['--list', '--file', 'logical-capture.jar'], destination).stdout.toString().trim().split('\n');
  if (JSON.stringify(jarEntries) !== JSON.stringify(['classes.dex'])) throw new Error('Unexpected archive entry');
  const reparsed = run('java', [...d8, '--release', '--min-api', '26', '--output', 'checked', 'logical-capture.jar'], destination);
  writeNew(path.join(destination, 'work/d8-reparse.txt'), reparsed.stderr);
  if (JSON.stringify(fs.readdirSync(path.join(destination, 'checked')).sort()) !== JSON.stringify(['classes.dex'])) throw new Error('DEX reparse failed');
  const reparseBytes = readBoundedRegular(path.join(destination, 'checked/classes.dex'), 8 * 1024 * 1024);
  if (!reparseBytes.length) throw new Error('Empty reparsed DEX');
  for (const item of own) {
    directories(path.dirname(item.filename));
    if (sha(readBoundedRegular(item.filename, 1024 * 1024)) !== item.digest) throw new Error('Source generation changed');
  }
  for (const input of inputs) {
    directories(path.dirname(input.filename));
    if (sha(readTool(input.filename)) !== input.digest) throw new Error('Tool generation changed');
  }
  const artifacts = {};
  for (const [name, relative] of [['class', 'classes/TeslaLogicalCapture.class'], ['dex', 'dex/classes.dex'], ['jar', 'logical-capture.jar']]) {
    const bytes = readBoundedRegular(path.join(destination, relative), 8 * 1024 * 1024);
    if (!bytes.length) throw new Error('Empty artifact'); fs.chmodSync(path.join(destination, relative), 0o600);
    artifacts[name] = { path: relative, size: bytes.length, sha256: sha(bytes) };
  }
  const receipt = { format: 1, component: 'logical-capture-java-dex-preview', installable: false, androidOperationsExecuted: false,
    compilerVersion, javaRelease: 8, minApi: 26, compileFlags: compile, d8Version: version, d8Sha256,
    sdkApi: 36, sdkRevision: 2, androidSha256,
    sourceFiles: own.map(item => ({ path: item.relative, stagedPath: 'sources/' + path.basename(item.relative), sha256: sha(item.bytes) })), artifacts,
    hostTestsPassed: true, invalidMatrixChecks: 15, invalidArgumentChecks: 3, dexReparsePassed: true,
    reparseSha256: sha(reparseBytes), jarEntries, jarTimestamp: plan.jarTimestamp, diagnosticsSha256: sha(converted.stderr),
    limitations: ['Not an installer, signature, legal clearance or hostile same-UID authentication',
      'Same JDK/D8/SDK reproducibility only', 'No Android loading, capture, Binder or valid calibration runtime operation tested',
      'Public SDK compilation does not validate reflection-only hidden Android APIs', 'Existing diagnostic PNG helper is not a browser stream fallback'] };
  writeNew(path.join(destination, 'logical-build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); return receipt;
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 4 || args[0] !== 'build') throw new Error('Invalid invocation');
    process.stdout.write(JSON.stringify(buildLogicalCapture({ d8Jar: args[1], androidJar: args[2], output: args[3] })) + '\n');
  } catch { process.stderr.write('Logical build rejected; partial output is not installable\n'); process.exitCode = 1; }
}
module.exports = { logicalCaptureBuildPlan, buildLogicalCapture };
