// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync, spawn } = require('node:child_process');
let builder;
try { builder = require('./binder-build'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
function available() { assert.ok(builder, 'Binder library builder is not implemented'); }
const linux = process.platform === 'linux' && Boolean(process.env.DROIDDECK_GBINDER_TEST_REPO && process.env.DROIDDECK_GLIBUTIL_TEST_REPO);
const inputs = () => ({ gbinderRepo: process.env.DROIDDECK_GBINDER_TEST_REPO, glibutilRepo: process.env.DROIDDECK_GLIBUTIL_TEST_REPO });
test('library recipe pins publisher commits and excludes Binder runtime operations', () => {
  available(); const plan = builder.binderBuildPlan();
  assert.equal(Object.isFrozen(plan), true); assert.equal(plan.installable, false); assert.equal(plan.binderOperationsExecuted, false);
  assert.equal(plan.gbinderRevision, '08252c411e63f89c2a7b056ed5bdd0b9728704ec');
  assert.equal(plan.glibutilRevision, 'e2fe548f8cce21551fd59a461618ea586b5f98fb');
  assert.deepEqual(plan.targets, ['release']); assert.equal(Object.isFrozen(plan.targets), true);
});
test('library CLI rejects extra arguments without echoing input', () => {
  available(); const result = spawnSync(process.execPath, [path.join(__dirname, 'binder-build.js'), 'invalid', 'private-value'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), 'Binder build rejected; partial output is not installable');
});
test('library builder never overwrites existing output', () => {
  available(); const output = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-binder-existing-'));
  try {
    fs.writeFileSync(path.join(output, 'keep'), 'unchanged'); assert.throws(() => builder.buildBinder({ output }));
    assert.equal(fs.readFileSync(path.join(output, 'keep'), 'utf8'), 'unchanged');
  } finally { fs.rmSync(output, { recursive: true }); }
});
test('Linux missing library pin and linked source repo reject before output', { skip: !linux }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-binder-pin-test-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  assert.equal(spawnSync('/usr/bin/git', ['init', '--bare', stem], { timeout: 10000 }).status, 0);
  assert.throws(() => builder.buildBinder({ ...inputs(), gbinderRepo: stem, output: stem + '-output' }));
  assert.equal(fs.existsSync(stem + '-output'), false);
  fs.symlinkSync(inputs().gbinderRepo, stem + '-link');
  assert.throws(() => builder.buildBinder({ ...inputs(), gbinderRepo: stem + '-link', output: stem + '-linked-output' }));
  assert.equal(fs.existsSync(stem + '-linked-output'), false);
});
test('Linux missing publisher object cannot fetch or mutate cache', { skip: !linux, timeout: 120000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-binder-promisor-test-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  const cache = require('../../tests/fixtures/git-promisor').missingPublicObject({ source: inputs().glibutilRepo,
    revision: builder.binderBuildPlan().glibutilRevision, missingPath: 'Makefile', root: stem });
  assert.equal(cache.hasObject(), false); const before = cache.packNames();
  assert.throws(() => builder.buildBinder({ ...inputs(), glibutilRepo: cache.repository, output: stem + '-output' }));
  assert.equal(cache.hasObject(), false); assert.deepEqual(cache.packNames(), before); assert.equal(fs.existsSync(cache.fetchHead), false);
  assert.equal(fs.existsSync(stem + '-output'), false);
});
test('Linux private library pair builds repeatably and passes ELF/symbol checks', { skip: !linux, timeout: 240000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-binder-build-test-' + process.pid + '-' + Date.now());
  const first = builder.buildBinder({ ...inputs(), output: stem + '-one' });
  const second = builder.buildBinder({ ...inputs(), output: stem + '-two' });
  assert.deepEqual(first.artifacts, second.artifacts); assert.equal(first.installable, false);
  assert.equal(first.binderOperationsExecuted, false); assert.equal(first.symbolSmokePassed, true);
  for (const artifact of Object.values(first.artifacts)) assert.equal(fs.statSync(path.join(stem + '-one', artifact.path)).mode & 0o777, 0o600);
  assert.equal(fs.statSync(stem + '-one').mode & 0o777, 0o700);
  assert.equal(fs.readlinkSync(path.join(stem + '-one', 'libs/libglibutil.so.1')), 'libglibutil.so.1.0.80');
  assert.equal(fs.readlinkSync(path.join(stem + '-one', 'libs/libgbinder.so.1')), 'libgbinder.so.1.1.45');
  for (const item of first.sources) {
    assert.equal(require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(stem + '-one', item.stagedPath))).digest('hex'), item.sha256);
    assert.equal(/(^|\/)(test|unit)\//.test(item.stagedPath), false);
  }
  console.log(JSON.stringify({ first: stem + '-one', second: stem + '-two', artifacts: first.artifacts }));
});
test('Linux same-byte own test-source ancestry replacement rejects receipt', { skip: !linux, timeout: 180000 }, async () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-binder-edit-test-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  for (const relative of ['runtime/portable/binder-build.js', 'runtime/portable/read-bounded.js', 'tests/native/test_binder_library.c']) {
    fs.mkdirSync(path.dirname(path.join(stem, relative)), { recursive: true, mode: 0o700 });
    fs.copyFileSync(path.join(__dirname, '../..', relative), path.join(stem, relative), fs.constants.COPYFILE_EXCL);
  }
  const output = stem + '-output', child = spawn(process.execPath, [path.join(stem, 'runtime/portable/binder-build.js'), 'build', inputs().gbinderRepo, inputs().glibutilRepo, output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let changed = false, stdout = '', stderr = '';
  child.stdout.on('data', bytes => { stdout += bytes; }); child.stderr.on('data', bytes => { stderr += bytes; });
  const timer = setInterval(() => {
    if (!changed && fs.existsSync(path.join(output, 'work/test_binder_library.c'))) {
      fs.renameSync(path.join(stem, 'tests/native'), path.join(stem, 'tests/native-original'));
      fs.symlinkSync(path.join(stem, 'tests/native-original'), path.join(stem, 'tests/native')); changed = true;
    }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 150000); let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  assert.equal(changed, true); assert.equal(code, 1); assert.equal(stdout, ''); assert.equal(stderr.trim(), 'Binder build rejected; partial output is not installable');
  assert.equal(fs.existsSync(path.join(output, 'binder-build-receipt.json')), false);
});

test('Linux staged smoke-source edit rejects a success receipt', { skip: !linux, timeout: 180000 }, async () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-binder-staged-test-' + process.pid + '-' + Date.now()); fs.mkdirSync(stem, { mode: 0o700 });
  for (const relative of ['runtime/portable/binder-build.js', 'runtime/portable/read-bounded.js', 'tests/native/test_binder_library.c']) {
    fs.mkdirSync(path.dirname(path.join(stem, relative)), { recursive: true, mode: 0o700 });
    fs.copyFileSync(path.join(__dirname, '../..', relative), path.join(stem, relative), fs.constants.COPYFILE_EXCL);
  }
  const output = stem + '-output', child = spawn(process.execPath, [path.join(stem, 'runtime/portable/binder-build.js'), 'build', inputs().gbinderRepo, inputs().glibutilRepo, output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let changed = false, stdout = '', stderr = '';
  child.stdout.on('data', bytes => { stdout += bytes; }); child.stderr.on('data', bytes => { stderr += bytes; });
  const timer = setInterval(() => {
    const source = path.join(output, 'work/test_binder_library.c');
    if (!changed && fs.existsSync(source)) { fs.appendFileSync(source, '\n/* ordinary staged edit */\n'); changed = true; }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 150000); let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  assert.equal(changed, true); assert.equal(code, 1); assert.equal(stdout, ''); assert.equal(stderr.trim(), 'Binder build rejected; partial output is not installable');
  assert.equal(fs.existsSync(path.join(output, 'binder-build-receipt.json')), false);
});
