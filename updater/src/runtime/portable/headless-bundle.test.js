// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { buildHeadlessBundle, verifyHeadlessBundle } = require('./headless-bundle');
const config = { user: 'alice', uid: 1200, gid: 1300, lanAddress: '192.168.10.8' };
const profile = 'a'.repeat(64), sha = bytes => createHash('sha256').update(bytes).digest('hex');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-headless-bundle-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, output: path.join(root, 'component') };
}
test('headless component binds five payloads to the machine and GPU profile without activation', t => {
  const f = fixture(t), m = buildHeadlessBundle(config, profile, f.output);
  assert.equal(m.files.length, 5);
  assert.equal(m.waydroidConfigurationSha256, profile);
  for (const key of ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted']) assert.equal(m[key], false);
  assert.deepEqual(verifyHeadlessBundle(config, profile, f.output), m);
  const unit = fs.readFileSync(path.join(f.output, 'systemd/60-droiddeck-headless.conf'), 'utf8');
  assert(unit.includes('Type=notify\nNotifyAccess=main\nExecStart=\n'));
  assert(unit.includes('ExecStart=/usr/bin/python3 -I -B /opt/droiddeck/headless-runtime/headless-runtime.py\n'));
  assert(!unit.includes('RuntimeMaxSec=') && !unit.includes('50-droiddeck-session'));
});
test('accepted session and host adapter source pins remain unchanged', t => {
  const f = fixture(t); buildHeadlessBundle(config, profile, f.output);
  assert.equal(sha(fs.readFileSync(path.join(f.output, 'headless-session.py'))), '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba');
  assert.equal(sha(fs.readFileSync(path.join(f.output, 'host-platform-compat.py'))), '6a1191680ed1380a1bcecbd9c6704cfa5ff9837935b24e33af9aae1fc51471b2');
});
test('different identities and profile hashes are not interchangeable', t => {
  const f = fixture(t); buildHeadlessBundle(config, profile, f.output);
  assert.throws(() => verifyHeadlessBundle({ ...config, uid: 1400 }, profile, f.output));
  assert.throws(() => verifyHeadlessBundle(config, 'b'.repeat(64), f.output));
  assert.deepEqual(buildHeadlessBundle(config, profile, path.join(f.root, 'second')), verifyHeadlessBundle(config, profile, f.output));
});
test('rewritten hashes cannot adopt modified entrypoint or notification unit', t => {
  for (const name of ['headless-runtime.py', 'systemd/60-droiddeck-headless.conf']) {
    const f = fixture(t); buildHeadlessBundle(config, profile, f.output);
    const file = path.join(f.output, name); fs.appendFileSync(file, '\n# generated changed fixture\n');
    const marker = path.join(f.output, 'bundle-manifest.json'), m = JSON.parse(fs.readFileSync(marker));
    const record = m.files.find(r => r.path === name), bytes = fs.readFileSync(file);
    record.size = bytes.length; record.sha256 = sha(bytes); fs.writeFileSync(marker, JSON.stringify(m));
    assert.throws(() => verifyHeadlessBundle(config, profile, f.output));
  }
});
test('credentials, invalid profile hashes, extra private files and existing directories refuse', t => {
  const f = fixture(t);
  for (const [c, p] of [[{ ...config, password: 'generated' }, profile], [config, '../override']]) {
    assert.throws(() => buildHeadlessBundle(c, p, f.output)); assert(!fs.existsSync(f.output));
  }
  buildHeadlessBundle(config, profile, f.output);
  assert.throws(() => buildHeadlessBundle(config, profile, f.output));
  fs.writeFileSync(path.join(f.output, 'key.pem'), 'generated fixture');
  assert.throws(() => verifyHeadlessBundle(config, profile, f.output));
});
test('hardlinked payloads and symlinked output parents are refused', t => {
  const f = fixture(t); buildHeadlessBundle(config, profile, f.output);
  fs.linkSync(path.join(f.output, 'policy.json'), path.join(f.root, 'linked-file'));
  assert.throws(() => verifyHeadlessBundle(config, profile, f.output));
  const link = path.join(f.root, 'linked-parent'); fs.symlinkSync(f.root, link, process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => buildHeadlessBundle(config, profile, path.join(link, 'new')));
});
test('real verify CLI binds provided profile bytes; build refuses root or outside-home output', t => {
  const f = fixture(t), metadata = path.join(f.root, 'config.json'), cfg = path.join(f.root, 'waydroid.cfg');
  fs.writeFileSync(metadata, JSON.stringify(config)); fs.writeFileSync(cfg, 'generated Waydroid fixture\n');
  const hash = sha(fs.readFileSync(cfg)); buildHeadlessBundle(config, hash, f.output);
  const cli = (mode, output) => spawnSync(process.execPath, [path.join(__dirname, 'headless-bundle.js'), mode, metadata, cfg, output], { encoding: 'utf8' });
  const result = cli('verify', f.output);
  assert.equal(result.status, 0, result.stderr); assert.equal(JSON.parse(result.stdout).files, 5);
  fs.appendFileSync(cfg, 'changed fixture\n'); assert.notEqual(cli('verify', f.output).status, 0);
  const refused = path.join(f.root, 'refused'); assert.notEqual(cli('build', refused).status, 0); assert(!fs.existsSync(refused));
});
test('systemd verifies the notify dropin with base services without activation', { skip: process.platform !== 'linux' }, t => {
  const f = fixture(t), units = [], directory = path.join(f.root, 'units'); fs.mkdirSync(directory);
  const { createServicePlan } = require('./service-plan');
  const current = { user: 'hilman', uid: 1000, gid: 1000, lanAddress: '192.168.2.215' };
  for (const [name, text] of Object.entries(createServicePlan(current).files)) {
    if (!name.startsWith('systemd/system/')) continue;
    const file = path.join(directory, name.slice('systemd/system/'.length));
    fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text);
    if (name.endsWith('.service')) units.push(file);
  }
  buildHeadlessBundle(current, profile, f.output);
  const dropin = path.join(directory, 'tesla-waydroid-headless-session.service.d'); fs.mkdirSync(dropin);
  fs.copyFileSync(path.join(f.output, 'systemd/60-droiddeck-headless.conf'), path.join(dropin, '60-droiddeck-headless.conf'));
  const container = path.join(directory, 'waydroid-container.service');
  fs.copyFileSync('/usr/lib/systemd/system/waydroid-container.service', container); units.push(container);
  const result = spawnSync('/usr/bin/systemd-analyze', ['verify', '--man=no', ...units], { encoding: 'utf8', timeout: 10000,
    env: { ...process.env, SYSTEMD_UNIT_PATH: directory + ':/usr/lib/systemd/system:/lib/systemd/system', SYSTEMD_COLORS: '0' } });
  assert.equal(result.error, undefined); assert.equal(result.status, 0, result.stderr); assert.equal(result.stderr.trim(), '');
});
