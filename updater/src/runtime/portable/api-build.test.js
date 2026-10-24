// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto'), { spawnSync, spawn } = require('node:child_process');
let builder;
try { builder = require('./api-build'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
function available() { assert.ok(builder, 'Native API builder is not implemented'); }
const linux = process.platform === 'linux' && Boolean(process.env.DROIDDECK_API_TEST_REPO);
test('API fixed recipe pins the header/license and includes no server execution', () => {
  available(); const plan = builder.apiBuildPlan();
  assert.equal(plan.upstreamRevision, '6e3707cbeafa4a9a29d1e77ab3ad0e793836ace2');
  assert.deepEqual(plan.upstreamFiles, ['LICENSE', 'httplib.h']);
  assert.equal(plan.installable, false); assert.equal(plan.serverExecuted, false);
  assert.equal(Object.isFrozen(plan), true); assert.equal(Object.isFrozen(plan.flags), true);
  assert.deepEqual(plan.tests, ['test_api']);
  for (const flag of ['-DCPPHTTPLIB_NO_EXCEPTIONS', '-std=c++17', '-static', '-no-pie', '-Wl,--build-id=none']) assert.ok(plan.flags.includes(flag));
});
test('API builder rejects invalid CLI without echoing private input', () => {
  available(); const result = spawnSync(process.execPath, [path.join(__dirname, 'api-build.js'), 'invalid', 'private-value'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'Native API build rejected; partial output is not installable');
});
test('API builder leaves an existing output untouched', () => {
  available(); const output = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-api-existing-'));
  try {
    fs.writeFileSync(path.join(output, 'keep'), 'unchanged');
    assert.throws(() => builder.buildApi({ upstreamRepo: 'invalid', output }));
    assert.equal(fs.readFileSync(path.join(output, 'keep'), 'utf8'), 'unchanged');
  } finally { fs.rmSync(output, { recursive: true }); }
});
test('real API builds are static/repeatable and receipts match staged inputs', { skip: !linux, timeout: 120000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-api-build-test-' + process.pid + '-' + Date.now());
  const first = builder.buildApi({ upstreamRepo: process.env.DROIDDECK_API_TEST_REPO, output: stem + '-one' });
  const second = builder.buildApi({ upstreamRepo: process.env.DROIDDECK_API_TEST_REPO, output: stem + '-two' });
  assert.equal(first.artifact.sha256, second.artifact.sha256);
  assert.deepEqual(first, second);
  for (const [field, filename] of [['warningSha256', 'link-warnings.txt'], ['testWarningSha256', 'test-link-warnings.txt'],
    ['compileWarningSha256', 'compile-warnings.txt'], ['testCompileWarningSha256', 'test-compile-warnings.txt']]) {
    assert.equal(first[field], crypto.createHash('sha256').update(fs.readFileSync(path.join(stem + '-one/work', filename))).digest('hex'));
  }
  assert.equal(first.nativeTestsPassed, true); assert.equal(first.elfStaticX86_64, true);
  assert.equal(first.installable, false); assert.equal(first.serverExecuted, false);
  assert.equal(fs.statSync(stem + '-one').mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(stem + '-one', first.artifact.name)).mode & 0o777, 0o700);
  for (const item of first.sources) {
    const bytes = fs.readFileSync(path.join(stem + '-one', 'sources', item.path));
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), item.sha256);
  }
  console.log(JSON.stringify({ first: stem + '-one', second: stem + '-two', artifactSha256: first.artifact.sha256, bytes: first.artifact.size }));
});
test('Linux missing fixed revision rejects before output publication', { skip: !linux }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-api-pin-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 });
  assert.equal(spawnSync('/usr/bin/git', ['init', '--bare', stem], { timeout: 10000 }).status, 0);
  assert.throws(() => builder.buildApi({ upstreamRepo: stem, output: stem + '-output' }));
  assert.equal(fs.existsSync(stem + '-output'), false);
});
test('Linux missing public blob cannot lazily fetch or mutate the source cache', { skip: !linux, timeout: 60000 }, () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-api-promisor-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 });
  const origin = path.join(stem, 'public-origin'), repository = path.join(stem, 'missing-blob'), output = stem + '-output';
  const gitEnv = { PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0' };
  function git(args, input) {
    const result = spawnSync('/usr/bin/git', ['--no-pager', '--no-replace-objects', ...args], { env: gitEnv, input, timeout: 10000, maxBuffer: 1024 * 1024 });
    assert.equal(result.status, 0, result.stderr?.toString()); return result.stdout;
  }
  // Only public Git objects enter this generated fixture; no source-cache files are copied.
  git(['clone', '--bare', '--no-hardlinks', process.env.DROIDDECK_API_TEST_REPO, origin]);
  git(['-C', origin, 'config', 'uploadpack.allowAnySHA1InWant', 'true']);
  fs.mkdirSync(repository, { mode: 0o700 }); git(['init', '--bare', repository]);
  const revision = builder.apiBuildPlan().upstreamRevision;
  const tree = git(['-C', origin, 'rev-parse', revision + '^{tree}']).toString().trim();
  const license = git(['-C', origin, 'rev-parse', revision + ':LICENSE']).toString().trim();
  const header = git(['-C', origin, 'rev-parse', revision + ':httplib.h']).toString().trim();
  for (const [kind, oid] of [['commit', revision], ['tree', tree], ['blob', license]]) {
    const bytes = git(['-C', origin, 'cat-file', kind, oid]);
    assert.equal(git(['-C', repository, 'hash-object', '-t', kind, '-w', '--stdin'], bytes).toString().trim(), oid);
  }
  for (const [key, value] of [['core.repositoryformatversion', '1'], ['extensions.partialClone', 'origin'],
    ['remote.origin.url', origin], ['remote.origin.promisor', 'true'], ['remote.origin.partialclonefilter', 'blob:none'], ['protocol.file.allow', 'always']]) git(['-C', repository, 'config', key, value]);
  const present = () => spawnSync('/usr/bin/git', ['--no-lazy-fetch', '-C', repository, 'cat-file', '-e', header], { env: gitEnv, timeout: 10000 }).status;
  assert.notEqual(present(), 0);
  const before = fs.readdirSync(path.join(repository, 'objects/pack')).sort();
  assert.throws(() => builder.buildApi({ upstreamRepo: repository, output }));
  assert.notEqual(present(), 0);
  assert.deepEqual(fs.readdirSync(path.join(repository, 'objects/pack')).sort(), before);
  assert.equal(fs.existsSync(path.join(repository, 'FETCH_HEAD')), false);
  assert.equal(fs.existsSync(output), false);
});
test('Linux ordinary source edit after capture cannot publish success receipt', { skip: !linux, timeout: 60000 }, async () => {
  available(); const stem = path.join(os.homedir(), 'droiddeck-api-edit-test-' + process.pid + '-' + Date.now());
  fs.mkdirSync(stem, { mode: 0o700 });
  for (const part of ['runtime', 'runtime/portable', 'runtime/api', 'tests', 'tests/native']) fs.mkdirSync(path.join(stem, part), { mode: 0o700 });
  const repo = path.resolve(__dirname, '../..');
  for (const name of ['runtime/portable/api-build.js', 'runtime/portable/read-bounded.js', 'runtime/portable/touch-build.js',
    'runtime/api/tesla-android-x86-api.cpp', 'tests/native/test_api.cpp']) fs.copyFileSync(path.join(repo, name), path.join(stem, name), fs.constants.COPYFILE_EXCL);
  const output = stem + '-output', entry = path.join(stem, 'runtime/portable/api-build.js');
  const child = spawn(process.execPath, [entry, 'build', process.env.DROIDDECK_API_TEST_REPO, output], { stdio: ['ignore', 'pipe', 'pipe'] });
  let edited = false, stdout = '', stderr = '';
  child.stdout.on('data', data => { stdout += data; }); child.stderr.on('data', data => { stderr += data; });
  const timer = setInterval(() => {
    if (!edited && fs.existsSync(path.join(output, 'sources/runtime/api/tesla-android-x86-api.cpp'))) {
      fs.appendFileSync(path.join(stem, 'runtime/api/tesla-android-x86-api.cpp'), '\n/* ordinary edit */\n'); edited = true;
    }
  }, 5);
  const killTimer = setTimeout(() => child.kill('SIGKILL'), 50000);
  let code;
  try { code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); }
  finally { clearInterval(timer); clearTimeout(killTimer); }
  assert.equal(edited, true); assert.equal(code, 1); assert.equal(stdout, '');
  assert.equal(stderr.trim(), 'Native API build rejected; partial output is not installable');
  assert.equal(fs.existsSync(path.join(output, 'api-build-receipt.json')), false);
});
