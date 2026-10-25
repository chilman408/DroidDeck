// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
let builder;
try { builder = require('./touch-build'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
const validElf = 'Class: ELF64\nData: 2\'s complement, little endian\nType: EXEC (Executable file)\nMachine: Advanced Micro Devices X86-64\nProgram Headers:\n LOAD 0x0\n';
function available() { assert.ok(builder, 'Native touch builder is not implemented'); }
test('fixed touch recipe pins upstream source, patches and native tests', () => {
  available(); const plan = builder.touchBuildPlan();
  assert.equal(plan.upstreamRevision, '8d2e2f2923d15baba6101d879fc9593ef4a0ef78');
  assert.equal(plan.installable, false); assert.equal(Object.isFrozen(plan), true);
  assert.deepEqual(plan.patches, ['libws-loopback.patch', 'libws-case-insensitive-handshake.patch']);
  assert.ok(plan.upstreamFiles.includes('LICENSE')); assert.ok(plan.upstreamFiles.includes('ws.c'));
  assert.deepEqual(plan.tests, ['test_touch_protocol', 'test_touch_mapping', 'test_touch_keepalive']);
  assert.ok(plan.flags.includes('-static')); assert.ok(plan.flags.includes('-no-pie'));
  assert.ok(plan.flags.includes('-Wl,--build-id=none')); assert.ok(Object.isFrozen(plan.flags));
});
test('trusted readelf report validates only static ELF64 x86_64 executables', () => {
  available(); assert.equal(builder.validateElfReport(validElf), true);
  for (const value of ['', validElf.replace('ELF64', 'ELF32'), validElf.replace('little endian', 'big endian'),
    validElf.replace('EXEC', 'DYN'), validElf.replace('Advanced Micro Devices X86-64', 'AArch64'),
    validElf + '\n INTERP 0x1\n', validElf + '\n DYNAMIC 0x1\n', 'x'.repeat(65537)]) {
    assert.throws(() => builder.validateElfReport(value));
  }
});
test('production builder CLI does not print rejected input or execute custom commands', () => {
  available(); const { spawnSync } = require('node:child_process');
  const result = spawnSync(process.execPath, [path.join(__dirname, 'touch-build.js'), 'unexpected', 'private-input'],
    { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'Native touch build rejected; partial output is not installable');
});
test('existing output remains untouched before platform, source or compiler work', () => {
  available(); const root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-touch-existing-'));
  try {
    fs.writeFileSync(path.join(root, 'keep'), 'unchanged');
    assert.throws(() => builder.buildTouch({ libwsRepo: 'not-a-repo', output: root }));
    assert.equal(fs.readFileSync(path.join(root, 'keep'), 'utf8'), 'unchanged');
  } finally { fs.rmSync(root, { recursive: true }); }
});
test('Linux real pinned build is static and repeatable across fresh private roots', {
  skip: process.platform !== 'linux' || !process.env.DROIDDECK_LIBWS_TEST_REPO,
  timeout: 120000,
}, () => {
  available(); const home = os.homedir(), stem = path.join(home, 'droiddeck-touch-build-test-' + process.pid + '-' + Date.now());
  const first = builder.buildTouch({ libwsRepo: process.env.DROIDDECK_LIBWS_TEST_REPO, output: stem + '-one' });
  const second = builder.buildTouch({ libwsRepo: process.env.DROIDDECK_LIBWS_TEST_REPO, output: stem + '-two' });
  assert.equal(first.artifact.sha256, second.artifact.sha256);
  assert.equal(first.upstreamRevision, builder.touchBuildPlan().upstreamRevision);
  assert.equal(first.installable, false); assert.equal(first.nativeTestsPassed, true);
  assert.equal(first.patches[0].application, 'applied');
  assert.equal(first.patches[1].application, 'applied');
  for (const item of first.sources.filter(item => !item.path.startsWith('libws/'))) {
    assert.match(item.path, /^(runtime\/touch|tests\/native)\//);
    const bytes = fs.readFileSync(path.join(stem + '-one', 'sources', item.path));
    assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'), item.sha256);
  }
  assert.equal(first.elfStaticX86_64, true);
  assert.equal(fs.statSync(stem + '-one').mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(stem + '-one', 'tesla-android-x86-touch')).mode & 0o777, 0o700);
  console.log(JSON.stringify({ first: stem + '-one', second: stem + '-two', artifactSha256: first.artifact.sha256 }));
});

function cloneComponent(stem) {
  fs.mkdirSync(stem, { mode: 0o700 });
  for (const part of ['runtime', 'runtime/portable', 'runtime/touch', 'runtime/patches', 'tests', 'tests/native']) {
    fs.mkdirSync(path.join(stem, part), { mode: 0o700 });
  }
  const plan = builder.touchBuildPlan(), repo = path.resolve(__dirname, '../..');
  const files = ['runtime/portable/touch-build.js', 'runtime/portable/read-bounded.js',
    ...plan.ownFiles.map(name => 'runtime/touch/' + name), ...plan.patches.map(name => 'runtime/patches/' + name),
    ...plan.tests.map(name => 'tests/native/' + name + '.c')];
  for (const relative of files) fs.copyFileSync(path.join(repo, relative), path.join(stem, relative), fs.constants.COPYFILE_EXCL);
  return path.join(stem, 'runtime/portable/touch-build.js');
}
test('Linux missing touch Git object rejects without fetching or source-cache writes', {
  skip: process.platform !== 'linux' || !process.env.DROIDDECK_LIBWS_TEST_REPO, timeout: 60000,
}, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-touch-promisor-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 });
  const { missingPublicObject } = require('../../tests/fixtures/git-promisor');
  const cache = missingPublicObject({ source: process.env.DROIDDECK_LIBWS_TEST_REPO, revision: builder.touchBuildPlan().upstreamRevision, missingPath: 'ws.c', root: stem });
  assert.equal(cache.hasObject(), false); const before = cache.packNames();
  assert.throws(() => builder.buildTouch({ libwsRepo: cache.repository, output: stem + '-output' }));
  assert.equal(cache.hasObject(), false); assert.deepEqual(cache.packNames(), before);
  assert.equal(fs.existsSync(cache.fetchHead), false); assert.equal(fs.existsSync(stem + '-output'), false);
});
test('Linux rejects a changed reviewed patch before publishing an output', {
  skip: process.platform !== 'linux' || !process.env.DROIDDECK_LIBWS_TEST_REPO,
}, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-touch-patch-test-' + process.pid + '-' + Date.now());
  const entry = cloneComponent(stem);
  fs.appendFileSync(path.join(stem, 'runtime/patches/libws-loopback.patch'), '\n');
  assert.throws(() => require(entry).buildTouch({ libwsRepo: process.env.DROIDDECK_LIBWS_TEST_REPO, output: stem + '-output' }));
  assert.equal(fs.existsSync(stem + '-output'), false);
});
test('Linux source edit after capture rejects a success receipt', {
  skip: process.platform !== 'linux' || !process.env.DROIDDECK_LIBWS_TEST_REPO,
  timeout: 60000,
}, async () => {
  available(); const { spawn } = require('node:child_process');
  const stem = path.join(os.homedir(), 'droiddeck-touch-edit-test-' + process.pid + '-' + Date.now());
  const entry = cloneComponent(stem), output = stem + '-output';
  const child = spawn(process.execPath, [entry, 'build', process.env.DROIDDECK_LIBWS_TEST_REPO, output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let changed = false, stdout = '', stderr = '';
  child.stdout.on('data', data => { stdout += data; }); child.stderr.on('data', data => { stderr += data; });
  const timer = setInterval(() => {
    if (!changed && fs.existsSync(path.join(output, 'sources/runtime/touch/touch_protocol.c'))) {
      fs.appendFileSync(path.join(stem, 'runtime/touch/touch_protocol.c'), '\n/* ordinary source edit */\n'); changed = true;
    }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 50000);
  let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  assert.equal(changed, true); assert.equal(code, 1); assert.equal(stdout, '');
  assert.equal(stderr.trim(), 'Native touch build rejected; partial output is not installable');
  assert.equal(fs.existsSync(path.join(output, 'touch-build-receipt.json')), false);
});
