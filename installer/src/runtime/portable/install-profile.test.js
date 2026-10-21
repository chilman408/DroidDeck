// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { buildInstallProfile, verifyInstallProfile, checkProfileAccount } = require('./install-profile');
const { configurationDigest } = require('./configuration-generation');
const config = { user: 'alice', uid: 1200, gid: 1300, lanAddress: '192.168.10.8' };
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'install-profile-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, output: path.join(root, 'profile') };
}
function rewriteRecord(root, manifestPath, relative) {
  const file = path.join(root, manifestPath), m = JSON.parse(fs.readFileSync(file));
  const bytes = fs.readFileSync(path.join(path.dirname(file), relative));
  const record = m.files.find(r => r.path === relative); record.size = bytes.length; record.sha256 = sha(bytes);
  fs.writeFileSync(file, JSON.stringify(m, null, 2) + '\n');
}
test('one profile binds canonical config, seven service files and three PID files', t => {
  const f = fixture(t), m = buildInstallProfile(config, f.output);
  assert.equal(m.configurationDigest, configurationDigest(config)); assert.equal(m.files.length, 13);
  for (const key of ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated']) assert.equal(m[key], false);
  assert.deepEqual(verifyInstallProfile(f.output), m);
  const stored = JSON.parse(fs.readFileSync(path.join(f.output, 'runtime.json')));
  assert.equal(stored.home, '/home/alice'); assert.equal(stored.hostname, 'device.teslaandroid.com');
  const unit = fs.readFileSync(path.join(f.output, 'services/systemd/system/tesla-waydroid-compositor.service'), 'utf8');
  assert(unit.includes('User=alice')); assert(unit.includes('Group=1300')); assert(unit.includes('user@1200.service'));
  const hook = fs.readFileSync(path.join(f.output, 'pid-compat/systemd/40-droiddeck-pid-compat.conf'), 'utf8');
  assert(hook.includes(m.configurationDigest));
});
test('another machine identity and hostname generate an independent coherent profile', t => {
  const f = fixture(t), a = buildInstallProfile(config, f.output);
  const other = path.join(f.root, 'other'), b = buildInstallProfile({ user: 'bob', uid: 2000, gid: 2100,
    home: '/home/bob', lanAddress: '10.1.2.3', port: 9443, hostname: 'droiddeck.example.com' }, other);
  assert.notEqual(a.configurationDigest, b.configurationDigest);
  assert.deepEqual(verifyInstallProfile(other), b);
  const unit = fs.readFileSync(path.join(other, 'services/systemd/system/tesla-waydroid-compositor.service'), 'utf8');
  assert(unit.includes('User=bob')); assert(!unit.includes('alice'));
});
test('identical inputs produce path-independent deterministic manifests', t => {
  const f = fixture(t), a = buildInstallProfile(config, f.output);
  const b = buildInstallProfile(config, path.join(f.root, 'second'));
  assert.deepEqual(a, b); assert(!JSON.stringify(a).includes(f.root));
});
test('config credentials, root identities and injection fail before output', t => {
  const f = fixture(t);
  for (const value of [{ ...config, password: 'GENERATED-SECRET' }, { ...config, user: 'root' },
    { ...config, hostname: 'bad\nExecStart=generated' }, { ...config, lanAddress: '8.8.8.8' }]) {
    assert.throws(() => buildInstallProfile(value, f.output)); assert(!fs.existsSync(f.output));
  }
});
test('service edits with rewritten child and parent hashes still fail regeneration checks', t => {
  const f = fixture(t); buildInstallProfile(config, f.output);
  const relative = 'systemd/system/tesla-waydroid-compositor.service';
  fs.appendFileSync(path.join(f.output, 'services', relative), 'ExecStart=/bin/false\n');
  rewriteRecord(f.output, 'services/bundle-manifest.json', relative);
  rewriteRecord(f.output, 'bundle-manifest.json', 'services/' + relative);
  rewriteRecord(f.output, 'bundle-manifest.json', 'services/bundle-manifest.json');
  assert.throws(() => verifyInstallProfile(f.output));
});
test('PID source edits with rewritten hashes fail against local generator sources', t => {
  const f = fixture(t); buildInstallProfile(config, f.output);
  const relative = 'portable/pid-startup.js';
  fs.appendFileSync(path.join(f.output, 'pid-compat', relative), '\n// generated changed source\n');
  rewriteRecord(f.output, 'pid-compat/bundle-manifest.json', relative);
  rewriteRecord(f.output, 'bundle-manifest.json', 'pid-compat/' + relative);
  rewriteRecord(f.output, 'bundle-manifest.json', 'pid-compat/bundle-manifest.json');
  assert.throws(() => verifyInstallProfile(f.output));
});
test('valid child component from another identity cannot be mixed into a profile', t => {
  const f = fixture(t); buildInstallProfile(config, f.output);
  const other = path.join(f.root, 'other'); buildInstallProfile({ ...config, uid: 1500 }, other);
  fs.copyFileSync(path.join(other, 'services/bundle-manifest.json'), path.join(f.output, 'services/bundle-manifest.json'));
  rewriteRecord(f.output, 'bundle-manifest.json', 'services/bundle-manifest.json');
  assert.throws(() => verifyInstallProfile(f.output));
});
test('child configuration digests cannot differ even when every file hash is valid', t => {
  for (const child of ['services', 'pid-compat']) {
    const f = fixture(t); buildInstallProfile(config, f.output);
    const filename = path.join(f.output, child, 'bundle-manifest.json'), m = JSON.parse(fs.readFileSync(filename));
    m.configurationDigest = '0'.repeat(64); fs.writeFileSync(filename, JSON.stringify(m));
    rewriteRecord(f.output, 'bundle-manifest.json', child + '/bundle-manifest.json');
    assert.throws(() => verifyInstallProfile(f.output));
  }
});
test('extra private files, hardlinks, linked ancestors and existing output are refused', t => {
  const f = fixture(t); buildInstallProfile(config, f.output);
  assert.throws(() => buildInstallProfile(config, f.output));
  fs.writeFileSync(path.join(f.output, 'services/key.pem'), 'generated');
  assert.throws(() => verifyInstallProfile(f.output)); fs.unlinkSync(path.join(f.output, 'services/key.pem'));
  fs.linkSync(path.join(f.output, 'runtime.json'), path.join(f.root, 'linked-file'));
  assert.throws(() => verifyInstallProfile(f.output));
  const link = path.join(f.root, 'linked-parent'); fs.symlinkSync(f.root, link, process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => buildInstallProfile(config, path.join(link, 'new')));
});
test('account checking is read-only and requires the exact configured NSS identity', async t => {
  const f = fixture(t), m = buildInstallProfile(config, f.output);
  const before = fs.readFileSync(path.join(f.output, 'bundle-manifest.json'));
  const result = await checkProfileAccount(f.output, { lookup: async user => {
    assert.equal(user, 'alice'); return 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n';
  } });
  assert.equal(result.configurationDigest, m.configurationDigest); assert.equal(result.accountValidated, true);
  assert.equal(result.readyForActivation, false);
  await assert.rejects(checkProfileAccount(f.output, { lookup: async () => 'alice:x:1201:1300:Generated:/home/alice:/bin/bash\n' }));
  assert.deepEqual(fs.readFileSync(path.join(f.output, 'bundle-manifest.json')), before);
});
test('manifest sync failure cannot publish a verifiable profile', t => {
  const f = fixture(t), open = fs.openSync, sync = fs.fsyncSync; let pending, injected = false;
  fs.openSync = (name, ...args) => { const fd = open(name, ...args); if (String(name).endsWith('bundle-manifest.json.partial')) pending = fd; return fd; };
  fs.fsyncSync = fd => { if (fd === pending) { injected = true; throw Object.assign(Error('generated EIO'), { code: 'EIO' }); } return sync(fd); };
  try { assert.throws(() => buildInstallProfile(config, f.output)); }
  finally { fs.openSync = open; fs.fsyncSync = sync; }
  assert.equal(injected, true);
  assert(!fs.existsSync(path.join(f.output, 'bundle-manifest.json')));
  assert.throws(() => verifyInstallProfile(f.output));
});
test('a profile change while NSS lookup is pending cannot pass account checking', async t => {
  const f = fixture(t); buildInstallProfile(config, f.output);
  await assert.rejects(checkProfileAccount(f.output, { lookup: async () => {
    fs.appendFileSync(path.join(f.output, 'runtime.json'), ' ');
    return 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n';
  } }));
});
test('forged top-level readiness and account flags are always rejected', t => {
  const f = fixture(t); buildInstallProfile(config, f.output);
  const filename = path.join(f.output, 'bundle-manifest.json'), original = JSON.parse(fs.readFileSync(filename));
  for (const flag of ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated']) {
    const m = structuredClone(original); m[flag] = true; fs.writeFileSync(filename, JSON.stringify(m));
    assert.throws(() => verifyInstallProfile(f.output));
  }
});
test('CLI rejects unsupported activation and emits no private input', t => {
  const f = fixture(t), input = path.join(f.root, 'input.json');
  fs.writeFileSync(input, JSON.stringify({ ...config, password: 'GENERATED-SECRET' }));
  for (const args of [['build', input, f.output], ['--activate'], ['build', input, f.output, '--force']]) {
    const result = spawnSync(process.execPath, [path.join(__dirname, 'install-profile.js'), ...args], { encoding: 'utf8', timeout: 10000 });
    assert.equal(result.status, 1); assert.equal(result.stdout, '');
    assert.equal(result.stderr.trim(), 'Install profile rejected; no activation performed');
  }
});
