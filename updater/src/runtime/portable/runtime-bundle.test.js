// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { execFileSync } = require('node:child_process');
const { FILES, REVISION, buildRuntimeBundle, verifyRuntimeBundle } = require('./runtime-bundle');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'runtime-bundle-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo'); fs.mkdirSync(repo);
  const git = (...args) => execFileSync('git', ['-C', repo, ...args], { windowsHide: true }).toString().trim();
  git('init', '-q'); git('config', 'user.name', 'Generated Fixture'); git('config', 'user.email', 'fixture@example.invalid');
  git('config', 'core.autocrlf', 'false');
  for (const f of FILES) {
    const p = path.join(repo, 'runtime', f); fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, 'generated non-executed bytes for ' + f + '\n');
  }
  fs.writeFileSync(path.join(repo, 'runtime', 'secret.pem'), 'generated private extra sentinel');
  git('add', '.'); git('commit', '-qm', 'Generated source');
  return { root, repo, revision: git('rev-parse', 'HEAD'), git };
}
test('production source selection excludes private state, tests and experimental installers', () => {
  assert.equal(REVISION, '76c89fbb8de9a672a9ec843808f9eca6fd8c9528');
  assert(FILES.includes('portable/worker-entry.js'));
  assert(FILES.includes('waydroid-display/session-probe.js'));
  assert(FILES.includes('waydroid-display/probe.js'));
  assert(!FILES.some(f => /\.test\.|\.pem$|deploy-preview|live-|generated-|promote-images/.test(f)));
  assert.equal(new Set(FILES).size, FILES.length);
});
test('builds identical bytes from pinned objects despite dirty checkout and private extras', t => {
  const f = fixture(t), a = path.join(f.root, 'a'), b = path.join(f.root, 'b');
  const first = buildRuntimeBundle({ ...f, output: a });
  fs.writeFileSync(path.join(f.repo, 'runtime', FILES[0]), 'dirty sentinel');
  const second = buildRuntimeBundle({ ...f, output: b });
  assert.deepEqual(first, second);
  assert.equal(first.installable, false);
  assert.equal(fs.existsSync(path.join(a, 'secret.pem')), false);
  assert.throws(() => buildRuntimeBundle({ ...f, output: a }));
});
test('rejects missing selected object and invalid revision before output', t => {
  const f = fixture(t), output = path.join(f.root, 'output');
  assert.throws(() => buildRuntimeBundle({ ...f, output, revision: '--all' }));
  fs.unlinkSync(path.join(f.repo, 'runtime', FILES[0])); f.git('add', '-u'); f.git('commit', '-qm', 'Generated missing object');
  assert.throws(() => buildRuntimeBundle({ ...f, output, revision: f.git('rev-parse', 'HEAD') }));
  assert.equal(fs.existsSync(output), false);
});
test('verification rejects changed bytes and additional private files', t => {
  const f = fixture(t), output = path.join(f.root, 'output');
  buildRuntimeBundle({ ...f, output });
  const p = path.join(output, FILES[0]), original = fs.readFileSync(p);
  fs.writeFileSync(p, 'changed'); assert.throws(() => verifyRuntimeBundle(output));
  fs.writeFileSync(p, original);
  fs.writeFileSync(path.join(output, 'key.pem'), 'generated sentinel');
  assert.throws(() => verifyRuntimeBundle(output));
});
test('verification rejects hardlinked selected files', t => {
  const f = fixture(t), output = path.join(f.root, 'output'); buildRuntimeBundle({ ...f, output });
  fs.linkSync(path.join(output, FILES[0]), path.join(f.root, 'hardlink'));
  assert.throws(() => verifyRuntimeBundle(output));
});

test('pinned isolated bundle loads compositor and touch imports without checkout resolution', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'runtime-loadability-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(root, 'component');
  buildRuntimeBundle({ repo: path.resolve(__dirname, '../..'), output });
  // Only stub the decoder's import; an attempted capture must fail this import-only fixture.
  const dependency = path.join(output, 'waydroid-display/node_modules/pngjs');
  fs.mkdirSync(dependency, { recursive: true });
  fs.writeFileSync(path.join(dependency, 'index.js'),
    'exports.PNG={sync:{read(){throw new Error("No capture in import-only fixture")}}};\n');
  const script = 'for(const p of process.argv.slice(1)) require(p);';
  const entrypoints = ['preview-gateway/compositor-video-launcher.js', 'waydroid-display/touch-adapter.js']
    .map(p => path.join(output, p));
  const options = { windowsHide: true, timeout: 10000, stdio: 'pipe',
    env: { ...process.env, NODE_PATH: '', NODE_OPTIONS: '' } };
  const missing = path.join(output, 'waydroid-display/probe.js');
  const original = fs.readFileSync(missing); fs.unlinkSync(missing);
  assert.throws(() => execFileSync(process.execPath, ['-e', script, ...entrypoints], options), error =>
    error.status !== 0 && /Cannot find module '\.\/probe'/.test(error.stderr.toString()));
  fs.writeFileSync(missing, original);
  assert.doesNotThrow(() => execFileSync(process.execPath, ['-e', script, ...entrypoints], options));
});
