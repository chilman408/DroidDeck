// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync, spawn } = require('node:child_process');
let builder;
try { builder = require('./display-build'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
function available() { assert.ok(builder, 'Diagnostic display builder is not implemented'); }
const linux = process.platform === 'linux' && Boolean(process.env.DROIDDECK_LIBWS_TEST_REPO);
test('display recipe is fixed and diagnostic-only without Android/server operations', () => {
  available(); const plan = builder.displayBuildPlan();
  assert.equal(Object.isFrozen(plan), true); assert.equal(plan.installable, false);
  assert.equal(plan.serverExecuted, false); assert.equal(plan.androidOperationsExecuted, false); assert.equal(plan.browserPngFallback, false);
  assert.equal(plan.upstreamRevision, '8d2e2f2923d15baba6101d879fc9593ef4a0ef78');
  assert.deepEqual(plan.patches, ['libws-loopback.patch', 'libws-case-insensitive-handshake.patch']);
  assert.equal(Object.isFrozen(plan.flags), true); assert.ok(plan.flags.includes('-static'));
});
test('display CLI rejects extra args without echoing private input', () => {
  available(); const result = spawnSync(process.execPath, [path.join(__dirname, 'display-build.js'), 'invalid', 'private-value'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), 'Display build rejected; partial output is not installable');
});
test('display build leaves an existing output untouched', () => {
  available(); const output = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-display-existing-'));
  try {
    fs.writeFileSync(path.join(output, 'keep'), 'unchanged'); assert.throws(() => builder.buildDisplay({ output }));
    assert.equal(fs.readFileSync(path.join(output, 'keep'), 'utf8'), 'unchanged');
  } finally { fs.rmSync(output, { recursive: true }); }
});
function fixture(label) {
  const stem = path.join(os.homedir(), 'droiddeck-display-' + label + '-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  for (const relative of ['runtime/portable/display-build.js', 'runtime/portable/read-bounded.js', 'runtime/portable/touch-build.js',
    'runtime/display/tesla-android-x86-display.c', 'tests/native/test_display_capture.c', 'tests/native/display_capture_fixture.c',
    'runtime/patches/libws-loopback.patch', 'runtime/patches/libws-case-insensitive-handshake.patch']) {
    fs.mkdirSync(path.dirname(path.join(stem, relative)), { recursive: true, mode: 0o700 });
    fs.copyFileSync(path.join(__dirname, '../..', relative), path.join(stem, relative), fs.constants.COPYFILE_EXCL);
  }
  return { stem, entry: path.join(stem, 'runtime/portable/display-build.js'), output: stem + '-output' };
}
test('Linux missing pin and linked own source reject before output', { skip: !linux }, () => {
  available(); const f = fixture('pin');
  assert.equal(spawnSync('/usr/bin/git', ['init', '--bare', path.join(f.stem, 'empty')], { timeout: 10000 }).status, 0);
  assert.throws(() => builder.buildDisplay({ libwsRepo: path.join(f.stem, 'empty'), output: f.output })); assert.equal(fs.existsSync(f.output), false);
  fs.renameSync(path.join(f.stem, 'runtime/display'), path.join(f.stem, 'runtime/display-original'));
  fs.symlinkSync(path.join(f.stem, 'runtime/display-original'), path.join(f.stem, 'runtime/display'));
  const result = spawnSync(process.execPath, [f.entry, 'build', process.env.DROIDDECK_LIBWS_TEST_REPO, f.output], { encoding: 'utf8', timeout: 60000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(fs.existsSync(f.output), false);
});
test('Linux missing libws object cannot fetch or mutate source cache', { skip: !linux, timeout: 60000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-display-promisor-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  const cache = require('../../tests/fixtures/git-promisor').missingPublicObject({ source: process.env.DROIDDECK_LIBWS_TEST_REPO,
    revision: builder.displayBuildPlan().upstreamRevision, missingPath: 'ws.c', root: stem });
  assert.equal(cache.hasObject(), false); const before = cache.packNames();
  assert.throws(() => builder.buildDisplay({ libwsRepo: cache.repository, output: stem + '-output' }));
  assert.equal(cache.hasObject(), false); assert.deepEqual(cache.packNames(), before); assert.equal(fs.existsSync(cache.fetchHead), false);
  assert.equal(fs.existsSync(stem + '-output'), false);
});
test('Linux native diagnostic build repeats and checks six synthetic capture cases', { skip: !linux, timeout: 120000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-display-build-' + process.pid + '-' + Date.now());
  const first = builder.buildDisplay({ libwsRepo: process.env.DROIDDECK_LIBWS_TEST_REPO, output: stem + '-one' });
  const second = builder.buildDisplay({ libwsRepo: process.env.DROIDDECK_LIBWS_TEST_REPO, output: stem + '-two' });
  assert.deepEqual(first.artifact, second.artifact); assert.equal(first.nativeTestsPassed, true); assert.equal(first.elfStaticX86_64, true);
  assert.equal(first.serverExecuted, false); assert.equal(first.androidOperationsExecuted, false); assert.equal(first.browserPngFallback, false);
  assert.equal(first.syntheticCaptureCases, 6); assert.equal(first.installable, false);
  for (const source of first.stagedSources) assert.equal(require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(stem + '-one', source.path))).digest('hex'), source.sha256);
  assert.equal(fs.statSync(stem + '-one').mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(stem + '-one', first.artifact.path)).mode & 0o777, 0o700);
  console.log(JSON.stringify({ first: stem + '-one', second: stem + '-two', artifact: first.artifact }));
});
test('Linux staged display source edit rejects receipt', { skip: !linux, timeout: 60000 }, async () => {
  available(); const f = fixture('staged');
  const child = spawn(process.execPath, [f.entry, 'build', process.env.DROIDDECK_LIBWS_TEST_REPO, f.output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let changed = false, stdout = '', stderr = '';
  child.stdout.on('data', bytes => { stdout += bytes; }); child.stderr.on('data', bytes => { stderr += bytes; });
  const timer = setInterval(() => {
    const source = path.join(f.output, 'sources/runtime/display/tesla-android-x86-display.c');
    if (!changed && fs.existsSync(source)) { fs.appendFileSync(source, '\n/* staged edit */\n'); changed = true; }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 50000); let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  assert.equal(changed, true); assert.equal(code, 1); assert.equal(stdout, ''); assert.equal(stderr.trim(), 'Display build rejected; partial output is not installable');
  assert.equal(fs.existsSync(path.join(f.output, 'display-build-receipt.json')), false);
});
