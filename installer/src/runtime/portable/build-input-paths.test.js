// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const specs = [
  { name: 'GPS', entry: 'gps-build', env: 'DROIDDECK_D8_TEST_JAR', directory: 'runtime/waydroid-display',
    source: 'TeslaLocationBridge.java', staged: 'sources/TeslaLocationBridge.java', receipt: 'gps-build-receipt.json',
    message: 'GPS build rejected; partial output is not installable', files: ['tests/java/GpsBridgeReflectionTest.java'] },
  { name: 'API', entry: 'api-build', env: 'DROIDDECK_API_TEST_REPO', directory: 'runtime/api',
    source: 'tesla-android-x86-api.cpp', staged: 'sources/runtime/api/tesla-android-x86-api.cpp', receipt: 'api-build-receipt.json',
    message: 'Native API build rejected; partial output is not installable', files: ['runtime/portable/touch-build.js', 'tests/native/test_api.cpp'] },
  { name: 'Touch', entry: 'touch-build', env: 'DROIDDECK_LIBWS_TEST_REPO', directory: 'runtime/touch',
    source: 'tesla-android-x86-touch.c', staged: 'sources/runtime/touch/touch_protocol.c', receipt: 'touch-build-receipt.json',
    message: 'Native touch build rejected; partial output is not installable', files: [
      ...require('./touch-build').touchBuildPlan().ownFiles.filter(name => name !== 'tesla-android-x86-touch.c').map(name => 'runtime/touch/' + name),
      ...require('./touch-build').touchBuildPlan().tests.map(name => 'tests/native/' + name + '.c'),
      ...require('./touch-build').touchBuildPlan().patches.map(name => 'runtime/patches/' + name) ] },
];
const enabled = spec => process.platform === 'linux' && Boolean(process.env[spec.env]);
function fixture(spec, label) {
  const stem = path.join(os.homedir(), 'droiddeck-input-path-' + label + '-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 });
  for (const relative of ['runtime/portable/' + spec.entry + '.js', 'runtime/portable/read-bounded.js',
    spec.directory + '/' + spec.source, ...spec.files]) {
    const destination = path.join(stem, relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true, mode: 0o700 });
    fs.copyFileSync(path.join(root, relative), destination, fs.constants.COPYFILE_EXCL);
  }
  return { stem, entry: path.join(stem, 'runtime/portable/' + spec.entry + '.js'), output: stem + '-output' };
}
function linkExisting(directory) {
  fs.renameSync(directory, directory + '-original');
  fs.symlinkSync(directory + '-original', directory);
}
async function runWhile(entry, input, output, staged, mutate) {
  const child = spawn(process.execPath, [entry, 'build', input, output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let changed = false, stdout = '', stderr = '';
  child.stdout.on('data', bytes => { stdout += bytes; }); child.stderr.on('data', bytes => { stderr += bytes; });
  const timer = setInterval(() => {
    if (!changed && fs.existsSync(path.join(output, staged))) { mutate(); changed = true; }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 100000);
  let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  return { changed, code, stdout, stderr };
}
for (const spec of specs) {
  test(spec.name + ' linked source ancestry rejects before output', { skip: !enabled(spec), timeout: 120000 }, () => {
    const f = fixture(spec, spec.entry + '-initial'); linkExisting(path.join(f.stem, spec.directory));
    const result = spawnSync(process.execPath, [f.entry, 'build', process.env[spec.env], f.output], { encoding: 'utf8', timeout: 100000 });
    assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), spec.message);
    assert.equal(fs.existsSync(f.output), false);
  });
  test(spec.name + ' same-byte source ancestry replacement rejects receipt', { skip: !enabled(spec), timeout: 120000 }, async () => {
    const f = fixture(spec, spec.entry + '-final');
    const result = await runWhile(f.entry, process.env[spec.env], f.output, spec.staged, () => linkExisting(path.join(f.stem, spec.directory)));
    assert.equal(result.changed, true); assert.equal(result.code, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), spec.message);
    assert.equal(fs.existsSync(path.join(f.output, spec.receipt)), false);
  });
}

test('GPS linked dot-dot tool input rejects before output', { skip: !enabled(specs[0]), timeout: 120000 }, () => {
  const f = fixture(specs[0], 'gps-tool-initial');
  fs.mkdirSync(path.join(f.stem, 'real/sub'), { recursive: true, mode: 0o700 });
  fs.copyFileSync(process.env.DROIDDECK_D8_TEST_JAR, path.join(f.stem, 'real/r8.jar'), fs.constants.COPYFILE_EXCL);
  fs.symlinkSync(path.join(f.stem, 'real/sub'), path.join(f.stem, 'link'));
  const result = spawnSync(process.execPath, [f.entry, 'build', f.stem + '/link/../r8.jar', f.output], { encoding: 'utf8', timeout: 100000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), specs[0].message);
  assert.equal(fs.existsSync(f.output), false);
});

test('GPS same-byte tool ancestry replacement rejects receipt', { skip: !enabled(specs[0]), timeout: 120000 }, async () => {
  const f = fixture(specs[0], 'gps-tool-final'), directory = path.join(f.stem, 'input');
  fs.mkdirSync(directory, { mode: 0o700 }); const input = path.join(directory, 'r8.jar');
  fs.copyFileSync(process.env.DROIDDECK_D8_TEST_JAR, input, fs.constants.COPYFILE_EXCL);
  const result = await runWhile(f.entry, input, f.output, 'tools/r8.jar', () => linkExisting(directory));
  assert.equal(result.changed, true); assert.equal(result.code, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), specs[0].message);
  assert.equal(fs.existsSync(path.join(f.output, specs[0].receipt)), false);
});
