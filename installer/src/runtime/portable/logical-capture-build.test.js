// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync, spawn } = require('node:child_process');
let builder;
try { builder = require('./logical-capture-build'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
function available() { assert.ok(builder, 'Logical capture builder is not implemented'); }
const linux = process.platform === 'linux' && Boolean(process.env.DROIDDECK_D8_TEST_JAR && process.env.DROIDDECK_SDK_TEST_JAR);
const inputs = () => ({ d8Jar: process.env.DROIDDECK_D8_TEST_JAR, androidJar: process.env.DROIDDECK_SDK_TEST_JAR });

test('logical recipe fixes Android SDK, D8 and unchanged source without Android operations', () => {
  available(); const plan = builder.logicalCaptureBuildPlan();
  assert.equal(Object.isFrozen(plan), true); assert.equal(plan.installable, false);
  assert.equal(plan.androidOperationsExecuted, false); assert.equal(plan.javaRelease, 8); assert.equal(plan.minApi, 26);
  assert.equal(plan.sdkApi, 36); assert.equal(plan.sdkRevision, 2);
  assert.equal(plan.androidSha256, 'd9eb9da824d9e247a352f570f01e1169e725b2954bca9e283a71786c59b59f9a');
  assert.equal(plan.d8Sha256, 'd31fd0dc751d48740009cdd9a485126acb1d0d14c59b9f05579479940f4adf74');
  assert.deepEqual(plan.sourceFiles, ['runtime/waydroid-display/TeslaLogicalCapture.java']);
  assert.equal(Object.isFrozen(plan.sourceFiles), true);
});

test('logical CLI rejects extra arguments without echoing inputs', () => {
  available(); const result = spawnSync(process.execPath, [path.join(__dirname, 'logical-capture-build.js'), 'invalid', 'private-value'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'Logical build rejected; partial output is not installable');
});

test('logical build never overwrites an existing output', () => {
  available(); const output = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-logical-existing-'));
  try {
    fs.writeFileSync(path.join(output, 'keep'), 'unchanged');
    assert.throws(() => builder.buildLogicalCapture({ output }));
    assert.equal(fs.readFileSync(path.join(output, 'keep'), 'utf8'), 'unchanged');
  } finally { fs.rmSync(output, { recursive: true }); }
});

test('Linux wrong SDK and D8 pins reject before output creation', { skip: !linux }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-logical-tool-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 }); const bad = path.join(stem, 'bad.jar'); fs.writeFileSync(bad, 'not the publisher input');
  for (const key of ['d8Jar', 'androidJar']) {
    const output = stem + '-' + key;
    assert.throws(() => builder.buildLogicalCapture({ ...inputs(), [key]: bad, output }));
    assert.equal(fs.existsSync(output), false);
  }
});

test('Linux linked output ancestry rejects without following', { skip: !linux }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-logical-link-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 }); fs.symlinkSync(stem, stem + '-link');
  assert.throws(() => builder.buildLogicalCapture({ ...inputs(), output: path.join(stem + '-link', 'output') }));
  assert.equal(fs.existsSync(path.join(stem, 'output')), false);
});

test('Linux SDK-pinned capture artifacts repeat with invalid-matrix tests only', { skip: !linux, timeout: 120000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-logical-build-test-' + process.pid + '-' + Date.now());
  const first = builder.buildLogicalCapture({ ...inputs(), output: stem + '-one' });
  const second = builder.buildLogicalCapture({ ...inputs(), output: stem + '-two' });
  for (const name of ['class', 'dex', 'jar']) assert.equal(first.artifacts[name].sha256, second.artifacts[name].sha256);
  assert.equal(first.installable, false); assert.equal(first.androidOperationsExecuted, false);
  assert.equal(first.hostTestsPassed, true); assert.equal(first.dexReparsePassed, true); assert.deepEqual(first.jarEntries, ['classes.dex']);
  assert.deepEqual(fs.readdirSync(path.join(stem + '-one', 'classes')), ['TeslaLogicalCapture.class']);
  for (const item of first.sourceFiles) {
    const bytes = fs.readFileSync(path.join(stem + '-one', item.stagedPath));
    assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'), item.sha256);
  }
  assert.equal(fs.statSync(stem + '-one').mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(stem + '-one', 'logical-capture.jar')).mode & 0o777, 0o600);
  console.log(JSON.stringify({ first: stem + '-one', second: stem + '-two', artifacts: first.artifacts }));
});

test('Linux tool path containing linked dot-dot traversal rejects before output', { skip: !linux, timeout: 120000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-logical-traversal-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 }); fs.mkdirSync(path.join(stem, 'real'), { mode: 0o700 });
  fs.mkdirSync(path.join(stem, 'real/sub'), { mode: 0o700 }); fs.symlinkSync(path.join(stem, 'real/sub'), path.join(stem, 'link'));
  fs.copyFileSync(inputs().d8Jar, path.join(stem, 'real/r8.jar'), fs.constants.COPYFILE_EXCL);
  const output = stem + '-output';
  assert.throws(() => builder.buildLogicalCapture({ ...inputs(), d8Jar: stem + '/link/../r8.jar', output }));
  assert.equal(fs.existsSync(output), false);
});

test('Linux linked production-source ancestry rejects before output', { skip: !linux, timeout: 120000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-logical-source-link-test-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  for (const name of ['runtime', 'runtime/portable', 'source-real', 'tests', 'tests/java']) fs.mkdirSync(path.join(stem, name), { mode: 0o700 });
  for (const name of ['runtime/portable/logical-capture-build.js', 'runtime/portable/read-bounded.js', 'tests/java/LogicalCaptureValidationTest.java']) fs.copyFileSync(path.join(__dirname, '../..', name), path.join(stem, name), fs.constants.COPYFILE_EXCL);
  fs.copyFileSync(path.join(__dirname, '../waydroid-display/TeslaLogicalCapture.java'), path.join(stem, 'source-real/TeslaLogicalCapture.java'), fs.constants.COPYFILE_EXCL);
  fs.symlinkSync(path.join(stem, 'source-real'), path.join(stem, 'runtime/waydroid-display'));
  const output = stem + '-output';
  const result = spawnSync(process.execPath, [path.join(stem, 'runtime/portable/logical-capture-build.js'), 'build', inputs().d8Jar, inputs().androidJar, output], { encoding: 'utf8', timeout: 100000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'Logical build rejected; partial output is not installable');
  assert.equal(fs.existsSync(output), false);
});

test('Linux source change rejects final receipt', { skip: !linux, timeout: 120000 }, async () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-logical-edit-test-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  for (const name of ['runtime', 'runtime/portable', 'runtime/waydroid-display', 'tests', 'tests/java']) fs.mkdirSync(path.join(stem, name), { mode: 0o700 });
  for (const name of ['runtime/portable/logical-capture-build.js', 'runtime/portable/read-bounded.js', 'runtime/waydroid-display/TeslaLogicalCapture.java', 'tests/java/LogicalCaptureValidationTest.java']) fs.copyFileSync(path.join(__dirname, '../..', name), path.join(stem, name), fs.constants.COPYFILE_EXCL);
  const output = stem + '-output', child = spawn(process.execPath, [path.join(stem, 'runtime/portable/logical-capture-build.js'), 'build', inputs().d8Jar, inputs().androidJar, output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let edited = false, stdout = '', stderr = '';
  child.stdout.on('data', data => { stdout += data; }); child.stderr.on('data', data => { stderr += data; });
  const timer = setInterval(() => {
    if (!edited && fs.existsSync(path.join(output, 'sources/TeslaLogicalCapture.java'))) {
      fs.appendFileSync(path.join(stem, 'runtime/waydroid-display/TeslaLogicalCapture.java'), '\n// ordinary edit\n'); edited = true;
    }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 100000);
  let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  assert.equal(edited, true); assert.equal(code, 1); assert.equal(stdout, '');
  assert.equal(stderr.trim(), 'Logical build rejected; partial output is not installable');
  assert.equal(fs.existsSync(path.join(output, 'logical-build-receipt.json')), false);
});
