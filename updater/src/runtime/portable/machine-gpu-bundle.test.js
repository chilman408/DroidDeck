// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { createHash } = require('node:crypto'), { spawnSync } = require('node:child_process');
const file = path.join(__dirname, 'machine-gpu-bundle.js');
function api() { assert(fs.existsSync(file), 'Pinned source bundle implementation required'); return require(file); }
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-machine-bundle-'));
  t.after(() => {
    if (path.dirname(path.resolve(root)) !== path.resolve(os.tmpdir()) || !path.basename(root).startsWith('droiddeck-machine-bundle-')) throw Error('Unsafe fixture cleanup');
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { root, output: path.join(root, 'payload') };
}
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
test('eleven pinned source payloads form the fixed launcher layout with no units or activation claim', t => {
  const b = api(), f = fixture(t), m = b.buildMachineGpuBundle({ output: f.output });
  assert.equal(m.kind, 'machine-gpu-source-component'); assert.equal(m.files.length, 11);
  assert.deepEqual(b.verifyMachineGpuBundle(f.output), m);
  assert.equal(sha(fs.readFileSync(path.join(f.output, 'machine-gpu-entry.py'))), '534bcdd629dca1c48f92663be42d5708d313ba62f7b37dc9155ace18db50ca70');
  assert(fs.existsSync(path.join(f.output, 'machine-gpu-runtime/runtime/portable/machine-gpu-lifecycle-state.py')));
  assert(!fs.existsSync(path.join(f.output, 'systemd')));
  for (const key of b.FALSE_FLAGS) assert.equal(m[key], false);
  assert(!JSON.stringify(m).includes(f.root));
});
test('two independent new outputs have identical bytes and manifest', t => {
  const b = api(), f = fixture(t), second = path.join(f.root, 'second');
  const m = b.buildMachineGpuBundle({ output: f.output });
  assert.deepEqual(b.buildMachineGpuBundle({ output: second }), m);
  for (const relative of [...m.files.map(r => r.path), 'bundle-manifest.json']) {
    assert(fs.readFileSync(path.join(f.output, relative)).equals(fs.readFileSync(path.join(second, relative))));
  }
});
test('rewritten receipt hashes cannot adopt changed source or readiness flags', t => {
  const b = api();
  for (const relative of ['machine-gpu-entry.py', 'machine-gpu-bootstrap.py', 'machine-gpu-runtime/runtime/portable/machine-gpu-lifecycle-state.py']) {
    const f = fixture(t), m = b.buildMachineGpuBundle({ output: f.output });
    const target = path.join(f.output, relative); fs.appendFileSync(target, '\n# generated foreign\n');
    const r = m.files.find(r => r.path === relative), bytes = fs.readFileSync(target); r.size = bytes.length; r.sha256 = sha(bytes);
    fs.writeFileSync(path.join(f.output, 'bundle-manifest.json'), JSON.stringify(m, null, 2) + '\n');
    assert.throws(() => b.verifyMachineGpuBundle(f.output));
  }
  const f = fixture(t), m = b.buildMachineGpuBundle({ output: f.output }); m.installable = true;
  fs.writeFileSync(path.join(f.output, 'bundle-manifest.json'), JSON.stringify(m, null, 2) + '\n');
  assert.throws(() => b.verifyMachineGpuBundle(f.output));
});
test('existing outputs and unknown private-looking entries remain untouched', t => {
  const b = api(), f = fixture(t); b.buildMachineGpuBundle({ output: f.output });
  const marker = path.join(f.output, 'bundle-manifest.json'), original = fs.readFileSync(marker);
  assert.throws(() => b.buildMachineGpuBundle({ output: f.output })); assert(fs.readFileSync(marker).equals(original));
  const foreign = path.join(f.output, 'generated-private-sentinel'); fs.writeFileSync(foreign, 'generated only');
  assert.throws(() => b.verifyMachineGpuBundle(f.output)); assert.equal(fs.readFileSync(foreign, 'utf8'), 'generated only');
});
test('bad or same-size foreign build input refuses before output exists', t => {
  const b = api(), f = fixture(t), sourceRoot = path.join(f.root, 'source'); fs.mkdirSync(sourceRoot);
  for (const record of Object.values(b.SOURCES)) {
    const target = path.join(sourceRoot, record.source); fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, fs.readFileSync(path.join(__dirname, '../..', record.source)));
  }
  const target = path.join(sourceRoot, 'runtime/portable/machine-gpu-entry.py');
  const raw = fs.readFileSync(target); raw[0] ^= 1; fs.writeFileSync(target, raw);
  assert.throws(() => b.buildMachineGpuBundle({ output: f.output, sourceRoot })); assert(!fs.existsSync(f.output));
});
test('partial write preserves an unready directory without a final manifest', t => {
  const b = api(), f = fixture(t), original = fs.fsyncSync;
  fs.fsyncSync = () => { throw Error('generated flush fault'); };
  try { assert.throws(() => b.buildMachineGpuBundle({ output: f.output })); } finally { fs.fsyncSync = original; }
  assert(fs.existsSync(f.output)); assert(!fs.existsSync(path.join(f.output, 'bundle-manifest.json')));
  assert.throws(() => b.verifyMachineGpuBundle(f.output));
});
test('payload modes and hardlink or directory aliases are refused on Linux', { skip: process.platform !== 'linux' }, t => {
  const b = api(), f = fixture(t), m = b.buildMachineGpuBundle({ output: f.output });
  assert.equal(fs.statSync(f.output).mode & 0o777, 0o700);
  for (const r of m.files) assert.equal(fs.statSync(path.join(f.output, r.path)).mode & 0o777, 0o600);
  const entry = path.join(f.output, 'machine-gpu-entry.py');
  for (const mode of [0o644, 0o4600]) {
    fs.chmodSync(entry, mode); assert.throws(() => b.verifyMachineGpuBundle(f.output)); fs.chmodSync(entry, 0o600);
  }
  fs.linkSync(entry, path.join(f.root, 'linked')); assert.throws(() => b.verifyMachineGpuBundle(f.output));
  fs.unlinkSync(path.join(f.root, 'linked'));
  const alias = path.join(f.root, 'alias'); fs.symlinkSync(f.output, alias, 'dir');
  assert.throws(() => b.verifyMachineGpuBundle(alias));
});
test('verify CLI reports only inert summary and rejects install or root build', t => {
  const b = api(), f = fixture(t); b.buildMachineGpuBundle({ output: f.output });
  const cli = args => spawnSync(process.execPath, [file, ...args], { encoding: 'utf8', timeout: 10000 });
  const result = cli(['verify', f.output]); assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).files, 11); assert(!result.stdout.includes(f.root));
  for (const mode of ['--install', '--activate', '--force']) assert.equal(cli([mode, f.output]).status, 1);
  if (process.platform !== 'linux' || process.getuid() === 0) {
    const output = path.join(f.root, 'forbidden'); assert.equal(cli(['build', output]).status, 1); assert(!fs.existsSync(output));
  }
});
