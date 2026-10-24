// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), { createHash } = require('node:crypto');
const { buildInstallProfile, buildOwnedInstallProfile } = require('./install-profile');
const config = { user: 'alice', uid: 1200, gid: 1300, lanAddress: '192.168.10.8' };
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const cmd = args => hash([[args[0], args, []]]), empty = hash([]);
const pre = '/opt/droiddeck/runtime/portable/worker-entry.js', prefix = '/etc/systemd/system/';
function fixture() {
  const paths = ['NODE_PATH=/opt/droiddeck/runtime/preview-gateway/node_modules:/opt/droiddeck/runtime/waydroid-display/node_modules',
    'LD_LIBRARY_PATH=/opt/droiddeck/libgbinder', 'DROIDDECK_CONFIGURATION_SHA256=3153f0912667bec225d11a8727cb11ecc3b19aaf6d4323b1429c0ff799aafca9'];
  const desktop = ['HOME=/home/alice', 'XDG_RUNTIME_DIR=/run/user/1200',
    'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1200/bus', 'WAYLAND_DISPLAY=wayland-teslaandroid', ...paths];
  const record = (name, user, group, env, start, check, dropins = []) => ({ LoadState: 'loaded', NeedDaemonReload: false,
    Transient: false, FragmentPath: prefix + name, DropInPaths: dropins, Type: 'simple', NotifyAccess: 'none',
    User: user, Group: group, SupplementaryGroups: [], EnvironmentSha256: hash([...env].sort()),
    EnvironmentFilesSha256: empty, PassEnvironment: [], UnsetEnvironment: [],
    WorkingDirectory: '', ExecConditionSha256: empty, ExecStartPreSha256: check ? cmd(['/usr/bin/node', pre, check]) : empty,
    ExecStartSha256: cmd(start), ExecStartPostSha256: empty, ExecReloadSha256: empty, ExecReloadPostSha256: empty,
    ExecStopSha256: empty, ExecStopPostSha256: empty, KillMode: 'control-group', KillSignal: 15,
    Restart: 'on-failure', TimeoutStopUSec: 20000000, TimeoutStartUSec: 90000000,
    Requires: [], Wants: [], After: [], PartOf: [] });
  const compositor = 'tesla-waydroid-compositor.service', headless = 'tesla-waydroid-headless-session.service';
  const c = record(compositor, 'alice', '1300', desktop, ['/usr/bin/weston', '--backend=pipewire', '--renderer=gl',
    '--socket=wayland-teslaandroid', '--width=1280', '--height=720', '--idle-time=0', '--no-config', '--shell=kiosk-shell.so'], 'compositor-check');
  c.SupplementaryGroups = ['render', 'video']; c.Requires = ['user@1200.service']; c.After = ['user@1200.service'];
  const h = record(headless, 'alice', '1300', desktop, ['/usr/bin/python3', '-I', '-B',
    '/opt/droiddeck/headless-runtime/headless-runtime.py'], 'headless-check', [prefix + headless + '.d/60-droiddeck-headless.conf']);
  Object.assign(h, { Type: 'notify', NotifyAccess: 'main', KillSignal: 10, TimeoutStartUSec: 60000000,
    TimeoutStopUSec: 45000000, SupplementaryGroups: ['render', 'video'], Requires: ['user@1200.service', compositor],
    Wants: ['waydroid-container.service'], After: ['user@1200.service', compositor, 'waydroid-container.service'],
    PartOf: ['waydroid-container.service', compositor] });
  const r = record('tesla-waydroid-runtime.service', 'root', '', [...paths, 'TESLA_WAYDROID_CAPTURE_BACKEND=compositor'], ['/usr/bin/node', pre, 'runtime']);
  Object.assign(r, { WorkingDirectory: '/opt/droiddeck/runtime/waydroid-display', TimeoutStopUSec: 900000000,
    Wants: ['network-online.target', 'waydroid-container.service', headless, 'droiddeck-browser-connection.service'],
    After: ['network-online.target', 'waydroid-container.service', headless, 'droiddeck-browser-connection.service'] });
  const b = record('droiddeck-browser-connection.service', 'root', '1300', paths, ['/usr/bin/node', pre, 'connection']);
  b.After = ['local-fs.target', 'systemd-tmpfiles-setup.service'];
  const container = record('waydroid-container.service', '', '',
    ['DROIDDECK_CONFIGURATION_SHA256=3153f0912667bec225d11a8727cb11ecc3b19aaf6d4323b1429c0ff799aafca9'],
    ['/usr/bin/waydroid', 'container', 'start']);
  Object.assign(container, { Type: 'dbus', Restart: 'no', TimeoutStopUSec: 90000000,
    ExecStartPreSha256: cmd(['/usr/bin/node', '/opt/droiddeck/pid-compat/portable/pid-startup.js']),
    FragmentPath: '/usr/lib/systemd/system/waydroid-container.service',
    DropInPaths: [prefix + 'waydroid-container.service.d/40-droiddeck-pid-compat.conf'] });
  return { [compositor]: c, [headless]: h, 'tesla-waydroid-runtime.service': r,
    'droiddeck-browser-connection.service': b, 'waydroid-container.service': container };
}
function api() { return require('./unit-admission'); }
test('effective base/session graph admits intended loaded settings and implicit dependency ordering', () => {
  const snapshot = fixture(); snapshot['tesla-waydroid-compositor.service'].After.push('system.slice');
  assert.deepEqual(api().compareUnits(config, snapshot), []);
});
test('wrong generation, graphics, headless callback, navigation worker or command flags cannot match', () => {
  for (const [unit, field, value] of [
    ['tesla-waydroid-compositor.service', 'User', 'root'],
    ['tesla-waydroid-compositor.service', 'SupplementaryGroups', ['video']],
    ['tesla-waydroid-compositor.service', 'EnvironmentSha256', '0'.repeat(64)],
    ['tesla-waydroid-compositor.service', 'EnvironmentFilesSha256', hash([['/generated/private.env', false]])],
    ['tesla-waydroid-headless-session.service', 'PassEnvironment', ['DROIDDECK_CONFIGURATION_SHA256']],
    ['tesla-waydroid-headless-session.service', 'UnsetEnvironment', ['DROIDDECK_CONFIGURATION_SHA256']],
    ['tesla-waydroid-compositor.service', 'ExecStartSha256', cmd(['/usr/bin/weston', '--renderer=pixman'])],
    ['tesla-waydroid-headless-session.service', 'Type', 'simple'],
    ['tesla-waydroid-headless-session.service', 'NotifyAccess', 'none'],
    ['tesla-waydroid-headless-session.service', 'ExecStartSha256', hash([['/usr/bin/python3', ['/usr/bin/python3'], ['privileged']]])],
    ['waydroid-container.service', 'ExecStartPreSha256', empty],
    ['tesla-waydroid-runtime.service', 'ExecStopPostSha256', cmd(['/bin/false'])],
  ]) {
    const snapshot = fixture(); snapshot[unit][field] = value;
    assert(api().compareUnits(config, snapshot).some(r => r.unit === unit && r.fields.includes(field)));
  }
});
test('stale metadata, foreign dropins, lost dependencies and masked units refuse without exposing values', () => {
  for (const [field, value] of [['NeedDaemonReload', true], ['Transient', true], ['LoadState', 'masked'],
    ['DropInPaths', ['/run/systemd/system/secret-generated.conf']], ['Requires', []], ['PartOf', []]]) {
    const snapshot = fixture(); snapshot['tesla-waydroid-headless-session.service'][field] = value;
    const differences = api().compareUnits(config, snapshot); assert(differences.length > 0);
    assert(!JSON.stringify(differences).includes('secret-generated'));
  }
});
test('unknown units/fields, incomplete snapshots, duplicate dependencies and unbounded values refuse', () => {
  const admission = api();
  for (const change of [s => { s.foreign = s['waydroid-container.service']; }, s => { delete s['waydroid-container.service']; },
    s => { s['waydroid-container.service'].Foreign = true; }, s => { s['waydroid-container.service'].After = ['a', 'a']; },
    s => { s['waydroid-container.service'].User = 'a'.repeat(5000); }]) {
    const snapshot = fixture(); change(snapshot); assert.throws(() => admission.compareUnits(config, snapshot));
  }
});
test('a different machine identity or private home cannot inherit admission from this snapshot', () => {
  for (const different of [{ ...config, user: 'bob' }, { ...config, uid: 1500 }, { ...config, gid: 1600 },
    { ...config, home: '/home/another' }, { ...config, lanAddress: '10.1.2.3' }]) {
    assert(api().compareUnits(different, fixture()).length > 0);
  }
});
test('inspection binds a v2 source profile and exact NSS account without claiming GPU/startup readiness', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'unit-admission-')); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(root, 'profile'); buildInstallProfile(config, output, 'a'.repeat(64));
  const before = fs.readFileSync(path.join(output, 'bundle-manifest.json'));
  const result = await api().inspectProfileUnits(output, { snapshot: async () => fixture(),
    lookup: async () => 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n' });
  assert.equal(result.baseSessionUnitsMatched, true); assert.equal(result.accountValidated, true);
  for (const key of ['gpuRecoveryVerified', 'readyForActivation', 'installable', 'servicesActivated', 'androidOperationsExecuted']) assert.equal(result[key], false);
  assert.deepEqual(fs.readFileSync(path.join(output, 'bundle-manifest.json')), before);
  await assert.rejects(api().inspectProfileUnits(output, { snapshot: async () => fixture(),
    lookup: async () => 'alice:x:1201:1300:Generated:/home/alice:/bin/bash\n' }));
});
test('base-only profile and profile mutation during inspection cannot produce a success receipt', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'unit-admission-')); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const base = path.join(root, 'base'); buildInstallProfile(config, base);
  await assert.rejects(api().inspectProfileUnits(base, { snapshot: async () => fixture() }));
  const output = path.join(root, 'profile'); buildInstallProfile(config, output, 'a'.repeat(64));
  await assert.rejects(api().inspectProfileUnits(output, { lookup: async () => 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n',
    snapshot: async () => { fs.appendFileSync(path.join(output, 'runtime.json'), ' '); return fixture(); } }));
});
test('changed loaded settings between snapshots cannot pass stable unit admission', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'unit-admission-')); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(root, 'profile'); buildInstallProfile(config, output, 'a'.repeat(64)); let calls = 0;
  const result = await api().inspectProfileUnits(output, { lookup: async () => 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n',
    snapshot: async () => { const s = fixture(); if (++calls === 2) s['waydroid-container.service'].NeedDaemonReload = true; return s; } });
  assert.equal(result.baseSessionUnitsMatched, false); assert.equal(result.snapshotStable, false);
});

function ownedFixture() {
  const snapshot = fixture();
  snapshot['tesla-waydroid-headless-session.service'].ExecStartSha256 = cmd(['/usr/bin/python3', '-I', '-B',
    '/opt/droiddeck/headless-owned-runtime/headless-owned-runtime.py']);
  return snapshot;
}
function ownedProfile(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'owned-unit-admission-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const base = path.join(root, 'base'), m = buildInstallProfile(config, base);
  const sha = value => createHash('sha256').update(value).digest('hex');
  const receipt = { kind: 'protected-machine-gpu-profile-policy', configurationDigest: m.configurationDigest,
    metadataSha256: sha(fs.readFileSync(path.join(base, 'runtime.json'))),
    serviceManifestSha256: sha(fs.readFileSync(path.join(base, 'services/bundle-manifest.json'))),
    pidManifestSha256: sha(fs.readFileSync(path.join(base, 'pid-compat/bundle-manifest.json'))),
    originalConfigurationSha256: sha('generated original'), softwareConfigurationSha256: sha('generated software'),
    gpuConfigurationSha256: sha('generated GPU'), accountValidated: true, servicesActivated: false, installable: false,
    readyForActivation: false, androidOperationsExecuted: false, gpuProfilePolicyRegenerated: true, codecsVerified: true, independentRecoveryArmed: false };
  const output = path.join(root, 'owned'); buildOwnedInstallProfile(config, receipt, output);
  return output;
}
const lookup = async () => 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n';

test('v3 owned command is opt-in and cannot satisfy legacy admission or accept the legacy command', () => {
  assert.deepEqual(api().compareUnits(config, ownedFixture(), 3), []);
  assert(api().compareUnits(config, ownedFixture()).some(r => r.fields.includes('ExecStartSha256')));
  assert(api().compareUnits(config, fixture(), 3).some(r => r.fields.includes('ExecStartSha256')));
});

test('unknown profile versions cannot silently select a command or become an arbitrary path override', () => {
  for (const version of [1, 4, null, true, '3', { version: 3 }, '/generated/override']) {
    assert.throws(() => api().compareUnits(config, fixture(), version));
  }
});

test('owned graph retains exact interpreter flags, notify, private environment and closed dropin paths', () => {
  const unit = 'tesla-waydroid-headless-session.service';
  for (const [key, value] of [
    ['ExecStartSha256', cmd(['/usr/bin/python3', '/opt/droiddeck/headless-owned-runtime/headless-owned-runtime.py'])],
    ['ExecStartSha256', hash([['/usr/bin/python3', ['/usr/bin/python3', '-I', '-B', '/opt/droiddeck/headless-owned-runtime/headless-owned-runtime.py'], ['privileged']]])],
    ['NotifyAccess', 'all'], ['Type', 'simple'], ['User', 'root'], ['EnvironmentSha256', 'a'.repeat(64)],
    ['DropInPaths', [prefix + unit + '.d/60-droiddeck-headless.conf', '/run/systemd/system/generated-private.conf']],
    ['PassEnvironment', ['HOME']], ['UnsetEnvironment', ['DROIDDECK_CONFIGURATION_SHA256']], ['PartOf', []]
  ]) {
    const s = ownedFixture(); s[unit][key] = value;
    const difference = api().compareUnits(config, s, 3); assert(difference.some(r => r.unit === unit && r.fields.includes(key)));
    assert(!JSON.stringify(difference).includes('generated-private'));
  }
});

test('v3 inspection binds owned recipe, two snapshots and NSS without claiming recovery or root provenance', async t => {
  const output = ownedProfile(t), before = fs.readFileSync(path.join(output, 'bundle-manifest.json'));
  const result = await api().inspectProfileUnits(output, { snapshot: async () => ownedFixture(), lookup });
  assert.equal(result.profileVersion, 3); assert.equal(result.baseSessionUnitsMatched, true);
  assert.equal(result.snapshotStable, true); assert.equal(result.accountValidated, true);
  assert.notEqual(result.ownedProfilePolicyMatched, true);
  for (const k of ['gpuRecoveryVerified', 'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted']) assert.equal(result[k], false);
  assert.deepEqual(fs.readFileSync(path.join(output, 'bundle-manifest.json')), before);
});

test('v3 inspection reports a stable old command as mismatch rather than coercing graph success', async t => {
  const output = ownedProfile(t);
  const r = await api().inspectProfileUnits(output, { snapshot: async () => fixture(), lookup });
  assert.equal(r.profileVersion, 3); assert.equal(r.snapshotStable, true); assert.equal(r.baseSessionUnitsMatched, false);
  assert.deepEqual(r.mismatches, [{ unit: 'tesla-waydroid-headless-session.service', fields: ['ExecStartSha256'] }]);
});

test('changing owned command between snapshots refuses stable admission and merges mismatch fields', async t => {
  const output = ownedProfile(t); let calls = 0;
  const r = await api().inspectProfileUnits(output, { lookup, snapshot: async () => ++calls === 1 ? ownedFixture() : fixture() });
  assert.equal(r.baseSessionUnitsMatched, false); assert.equal(r.snapshotStable, false);
  assert(r.mismatches.some(r => r.fields.includes('ExecStartSha256')));
});

test('wrong NSS or owned policy mutation during inspection cannot return successful admission', async t => {
  const output = ownedProfile(t); let lookups = 0, snapshots = 0;
  await assert.rejects(api().inspectProfileUnits(output, { snapshot: async () => ownedFixture(),
    lookup: async () => { lookups++; return 'alice:x:1201:1300:Generated:/home/alice:/bin/bash\n'; } }));
  assert.equal(lookups, 1);
  await assert.rejects(api().inspectProfileUnits(output, { lookup, snapshot: async () => {
    snapshots++; fs.appendFileSync(path.join(output, 'headless-owned-runtime/policy.json'), ' '); return ownedFixture();
  } }));
  assert.equal(snapshots, 1);
});
