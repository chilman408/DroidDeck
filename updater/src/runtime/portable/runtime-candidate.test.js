// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');
const legacy = require('./runtime-bundle');
let api, prepared;
try { api = require('./runtime-candidate'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
test.after(() => { if (prepared?.temporary) fs.rmSync(prepared.root, { recursive: true, force: true }); });
function inputs() {
  assert.ok(api, 'Runtime candidate builder missing');
  if (prepared) return prepared;
  if (process.env.DROIDDECK_CANDIDATE_TEST_INPUTS) {
    const root = path.resolve(process.env.DROIDDECK_CANDIDATE_TEST_INPUTS);
    return prepared = { root, base: path.join(root, 'base'), guard: path.join(root, 'guard') };
  }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-candidate-inputs-'));
  const base = path.join(root, 'base'), guard = path.join(root, 'guard');
  legacy.buildRuntimeBundle({ repo: path.resolve(__dirname, '../..'), output: base });
  fs.mkdirSync(guard, { mode: 0o700 });
  for (const file of Object.keys(api.OVERLAYS)) {
    const filename = path.join(guard, file); fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
    fs.writeFileSync(filename, execFileSync('git', ['show', api.GUARD_REVISION + ':runtime/' + file],
      { cwd: path.resolve(__dirname, '../..'), windowsHide: true }), { mode: 0o600, flag: 'wx' });
  }
  return prepared = { root, base, guard, temporary: true };
}
function fixture(t) {
  const source = inputs(), root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-candidate-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const base = path.join(root, 'base'), guard = path.join(root, 'guard'), output = path.join(root, 'output');
  fs.cpSync(source.base, base, { recursive: true }); fs.cpSync(source.guard, guard, { recursive: true });
  return { root, base, guard, output, build: () => api.buildRuntimeCandidate({ base, guard, output }) };
}

test('closed candidate recipe pins only three tested overlays and keeps legacy generation unchanged', () => {
  inputs(); assert.equal(api.BASE_MANIFEST, '9de6dc880c7608aa3f959b94f3b72537eeae9597dc02002038015195ad224f71');
  assert.equal(api.MANIFEST_SHA256, '6d136e676dccc2a2b520fe8731d52d700e957a1b99f6a9de033915ad275f89c8');
  assert.equal(api.GUARD_REVISION, '5301fea5f07cf34e4bdd46ffc477693f0778b39d');
  assert.equal(legacy.REVISION, '76c89fbb8de9a672a9ec843808f9eca6fd8c9528');
  assert.equal(Object.keys(api.OVERLAYS).length, 3); assert.ok(Object.isFrozen(api.OVERLAYS));
});

test('two fresh outputs reproduce the pinned candidate and preserve all other base bytes', t => {
  const f = fixture(t), first = f.build(), second = api.buildRuntimeCandidate({ ...f, output: path.join(f.root, 'second') });
  assert.deepEqual(first, second); assert.equal(first.kind, 'runtime-source-candidate');
  for (const flag of ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted']) assert.equal(first[flag], false);
  assert.equal(first.files.length, 76); assert.equal(api.verifyRuntimeCandidate(f.output).files.length, 76);
  for (const name of legacy.FILES) assert.deepEqual(fs.readFileSync(path.join(f.output, name)),
    fs.readFileSync(path.join(Object.hasOwn(api.OVERLAYS, name) ? f.guard : f.base, name)));
  assert.throws(() => legacy.verifyRuntimeBundle(f.output));
});

test('existing output, source aliases and source corruption refuse before output writes', t => {
  const f = fixture(t); fs.mkdirSync(f.output); assert.throws(f.build); assert.deepEqual(fs.readdirSync(f.output), []);
  assert.throws(() => api.buildRuntimeCandidate({ ...f, output: path.join(f.base, 'nested') }));
  fs.appendFileSync(path.join(f.guard, 'portable/gpu-access.js'), 'changed');
  assert.throws(() => api.buildRuntimeCandidate({ ...f, output: path.join(f.root, 'absent') }));
  assert.equal(fs.existsSync(path.join(f.root, 'absent')), false);
});

test('unreviewed base bytes, extra overlay files and self-consistent manifest edits are not accepted', t => {
  const f = fixture(t); fs.writeFileSync(path.join(f.base, legacy.FILES[0]), 'unreviewed'); assert.throws(f.build);
  const g = fixture(t); fs.writeFileSync(path.join(g.guard, 'extra.js'), 'extra'); assert.throws(g.build);
  const h = fixture(t); h.build(); const file = path.join(h.output, 'bundle-manifest.json');
  const manifest = JSON.parse(fs.readFileSync(file)); manifest.guardSourceRevision = '0'.repeat(40);
  fs.writeFileSync(file, JSON.stringify(manifest, null, 2) + '\n'); assert.throws(() => api.verifyRuntimeCandidate(h.output));
});

test('hardlinked input and output payloads never pass byte-only verification', t => {
  const f = fixture(t); fs.linkSync(path.join(f.guard, 'portable/gpu-access.js'), path.join(f.root, 'linked'));
  assert.throws(f.build); assert.equal(fs.existsSync(f.output), false);
  const g = fixture(t); g.build(); fs.linkSync(path.join(g.output, legacy.FILES[0]), path.join(g.root, 'linked'));
  assert.throws(() => api.verifyRuntimeCandidate(g.output));
});

test('write uncertainty leaves a non-reusable output and does not publish a manifest', t => {
  const f = fixture(t), original = fs.writeFileSync; let writes = 0;
  fs.writeFileSync = (...args) => { if (++writes === 2) throw Error('generated write failure'); return original(...args); };
  try { assert.throws(f.build, api.RuntimeCandidateUncertain); } finally { fs.writeFileSync = original; }
  assert.equal(fs.existsSync(f.output), true); assert.equal(fs.existsSync(path.join(f.output, 'bundle-manifest.json')), false);
  assert.throws(f.build); assert.ok(fs.readdirSync(f.output).length);
});

test('input generation drift after output creation is uncertain, never silently copied', t => {
  const f = fixture(t), original = fs.writeFileSync; let changed = false;
  fs.writeFileSync = (...args) => {
    const result = original(...args);
    if (!changed) { changed = true; fs.appendFileSync(path.join(f.guard, 'portable/gpu-access.js'), 'drift'); }
    return result;
  };
  try { assert.throws(f.build, api.RuntimeCandidateUncertain); } finally { fs.writeFileSync = original; }
  assert.equal(fs.existsSync(path.join(f.output, 'bundle-manifest.json')), false);
});

test('symlinked input namespace and linked output roots are refused', { skip: process.platform !== 'linux' }, t => {
  const f = fixture(t), alias = path.join(f.root, 'alias'); fs.symlinkSync(f.guard, alias);
  assert.throws(() => api.buildRuntimeCandidate({ ...f, guard: alias }));
  f.build(); fs.symlinkSync(f.output, path.join(f.root, 'output-link'));
  assert.throws(() => api.verifyRuntimeCandidate(path.join(f.root, 'output-link')));
});

test('replacement of the originally created output generation is preserved as uncertainty', { skip: process.platform !== 'linux' }, t => {
  const f = fixture(t), original = fs.writeFileSync; let changed = false;
  fs.writeFileSync = (...args) => {
    const result = original(...args);
    if (!changed) { changed = true; fs.renameSync(f.output, f.output + '.retained'); fs.mkdirSync(f.output, { mode: 0o700 }); }
    return result;
  };
  try { assert.throws(f.build, api.RuntimeCandidateUncertain); } finally { fs.writeFileSync = original; }
  assert.equal(fs.existsSync(f.output + '.retained'), true); assert.deepEqual(fs.readdirSync(f.output), []);
});

test('production CLI rejects arbitrary commands with fixed diagnostics', () => {
  inputs(); const result = spawnSync(process.execPath, [path.join(__dirname, 'runtime-candidate.js'), 'activate', 'private-value'],
    { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), 'Runtime candidate rejected');
});

test('isolated candidate loads both guarded startup modules without checkout fallback', t => {
  const f = fixture(t); f.build();
  const deps = path.join(f.root, 'generated-dependencies'), png = path.join(deps, 'pngjs');
  fs.mkdirSync(png, { recursive: true });
  fs.writeFileSync(path.join(png, 'index.js'), 'exports.PNG={sync:{read(){throw Error("No capture in import-only fixture")}}};\n');
  const script = 'const a=require("node:assert/strict");for(const f of process.argv.slice(1)){const m=require(f);a.equal(typeof m.main,"function");}' +
    'a.equal(typeof require(process.argv[1].replace("desktop-preflight.js","gpu-access.js")).assertGpuProcessAccess,"function");';
  const entries = ['portable/desktop-preflight.js', 'waydroid-display/headless-session-preflight.js'].map(file => path.join(f.output, file));
  const options = { encoding: 'utf8', timeout: 10000, windowsHide: true, cwd: f.root,
    env: { ...process.env, NODE_PATH: deps, NODE_OPTIONS: '' } };
  const filename = path.join(f.output, 'portable/gpu-access.js'), bytes = fs.readFileSync(filename); fs.unlinkSync(filename);
  const missing = spawnSync(process.execPath, ['-e', script, ...entries], options);
  assert.notEqual(missing.status, 0); assert.match(missing.stderr, /Cannot find module/);
  fs.writeFileSync(filename, bytes, { mode: 0o600, flag: 'wx' });
  const result = spawnSync(process.execPath, ['-e', script, ...entries], options);
  assert.equal(result.error, undefined); assert.equal(result.status, 0, result.stderr); assert.equal(result.stdout, '');
  assert.equal(api.verifyRuntimeCandidate(f.output).files.length, 76);
});
