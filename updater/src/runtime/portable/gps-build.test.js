// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync, spawn } = require('node:child_process');
let builder;
try { builder = require('./gps-build'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
function available() { assert.ok(builder, 'GPS Java/DEX builder is not implemented'); }
const linux = process.platform === 'linux' && Boolean(process.env.DROIDDECK_D8_TEST_JAR);
test('GPS recipe fixes publisher/toolchain/source without Android operations', () => {
  available(); const plan = builder.gpsBuildPlan();
  assert.equal(Object.isFrozen(plan), true); assert.equal(plan.installable, false);
  assert.equal(plan.androidOperationsExecuted, false); assert.equal(plan.javaRelease, 8); assert.equal(plan.minApi, 26);
  assert.equal(plan.d8Version, '8.13.17'); assert.equal(plan.d8Sha256, 'd31fd0dc751d48740009cdd9a485126acb1d0d14c59b9f05579479940f4adf74');
  assert.deepEqual(plan.sourceFiles, ['runtime/waydroid-display/TeslaLocationBridge.java']);
  assert.equal(Object.isFrozen(plan.sourceFiles), true);
});
test('GPS CLI rejects extra arguments without echoing private input', () => {
  available(); const result = spawnSync(process.execPath, [path.join(__dirname, 'gps-build.js'), 'invalid', 'private-value'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'GPS build rejected; partial output is not installable');
});
test('GPS build never overwrites an existing output', () => {
  available(); const output = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-gps-existing-'));
  try {
    fs.writeFileSync(path.join(output, 'keep'), 'unchanged');
    assert.throws(() => builder.buildGps({ d8Jar: 'invalid', output }));
    assert.equal(fs.readFileSync(path.join(output, 'keep'), 'utf8'), 'unchanged');
  } finally { fs.rmSync(output, { recursive: true }); }
});
test('Linux wrong D8 bytes reject before tools execute or output is created', { skip: !linux }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-gps-tool-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 }); const jar = path.join(stem, 'bad.jar'); fs.writeFileSync(jar, 'not the publisher tool');
  assert.throws(() => builder.buildGps({ d8Jar: jar, output: stem + '-output' }));
  assert.equal(fs.existsSync(stem + '-output'), false);
});
test('Linux pinned GPS class/DEX/JAR builds are repeatable and never call Android', { skip: !linux, timeout: 120000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-gps-build-test-' + process.pid + '-' + Date.now());
  const first = builder.buildGps({ d8Jar: process.env.DROIDDECK_D8_TEST_JAR, output: stem + '-one' });
  const second = builder.buildGps({ d8Jar: process.env.DROIDDECK_D8_TEST_JAR, output: stem + '-two' });
  for (const name of ['class', 'dex', 'jar']) assert.equal(first.artifacts[name].sha256, second.artifacts[name].sha256);
  assert.equal(first.installable, false); assert.equal(first.androidOperationsExecuted, false);
  assert.equal(first.hostTestsPassed, true); assert.equal(first.dexReparsePassed, true); assert.deepEqual(first.jarEntries, ['classes.dex']);
  for (const item of first.sourceFiles) {
    const bytes = fs.readFileSync(path.join(stem + '-one', item.stagedPath));
    assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'), item.sha256);
  }
  assert.equal(fs.statSync(stem + '-one').mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(stem + '-one', 'location-bridge.jar')).mode & 0o777, 0o600);
  console.log(JSON.stringify({ first: stem + '-one', second: stem + '-two', artifacts: first.artifacts }));
});
test('Linux source edit after capture rejects a success receipt', { skip: !linux, timeout: 120000 }, async () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-gps-edit-test-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  for (const name of ['runtime', 'runtime/portable', 'runtime/waydroid-display', 'tests', 'tests/java']) fs.mkdirSync(path.join(stem, name), { mode: 0o700 });
  for (const name of ['runtime/portable/gps-build.js', 'runtime/portable/read-bounded.js', 'runtime/waydroid-display/TeslaLocationBridge.java', 'tests/java/GpsBridgeReflectionTest.java']) fs.copyFileSync(path.join(__dirname, '../..', name), path.join(stem, name), fs.constants.COPYFILE_EXCL);
  const output = stem + '-output', child = spawn(process.execPath, [path.join(stem, 'runtime/portable/gps-build.js'), 'build', process.env.DROIDDECK_D8_TEST_JAR, output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let edited = false, stdout = '', stderr = '';
  child.stdout.on('data', data => { stdout += data; }); child.stderr.on('data', data => { stderr += data; });
  const timer = setInterval(() => {
    if (!edited && fs.existsSync(path.join(output, 'sources/TeslaLocationBridge.java'))) {
      fs.appendFileSync(path.join(stem, 'runtime/waydroid-display/TeslaLocationBridge.java'), '\n// ordinary edit\n'); edited = true;
    }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 100000);
  let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  assert.equal(edited, true); assert.equal(code, 1); assert.equal(stdout, '');
  assert.equal(stderr.trim(), 'GPS build rejected; partial output is not installable');
  assert.equal(fs.existsSync(path.join(output, 'gps-build-receipt.json')), false);
});
