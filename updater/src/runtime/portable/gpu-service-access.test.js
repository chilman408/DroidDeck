// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process'), path = require('node:path');
const { createServicePlan, GPU_SERVICE_GROUPS } = require('./service-plan');
let implementation;
try { implementation = require('./gpu-service-access'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
const config = { user: 'hilman', uid: 1000, gid: 1000, lanAddress: '192.168.2.215' };
function fixture() {
  assert.ok(implementation, 'Service GPU preflight is missing');
  const calls = [], records = { render: 'render:x:990:\n', video: 'video:x:44:\n' };
  const directory = { dev: 1, ino: 10, uid: 0, gid: 0, mode: 0o40755, isDirectory: () => true };
  const nodes = Object.fromEntries(['card1', 'renderD128'].map((name, i) => ['/dev/dri/' + name,
    { dev: 1, ino: 11 + i, uid: 0, gid: i ? 990 : 44, mode: 0o20660, nlink: 1,
      rdev: 226 * 256 + (i ? 128 : 1), isCharacterDevice: () => true }]));
  nodes['/dev'] = { ...directory }; nodes['/dev/dri'] = { ...directory };
  const options = { lookup: async user => { calls.push('passwd:' + user); return 'hilman:x:1000:1000::/home/hilman:/bin/bash\n'; },
    lookupGroups: async user => { calls.push('groups:' + user); return '1000\n'; },
    lookupGroup: async name => { calls.push('group:' + name); return records[name]; },
    io: { readdir: async () => { calls.push('listing'); return ['card1', 'renderD128']; },
      lstat: async filename => { calls.push(filename); return { ...nodes[filename] }; } } };
  return { options, calls, records, nodes, inspect: () => implementation.inspectGpuServiceAccess(config, options) };
}

test('proposed GPU service groups resolve independently without modifying account membership', async () => {
  const f = fixture(); f.records.render = 'render:private-password:990:private-member\n';
  const result = await f.inspect();
  assert.equal(result.kind, 'gpu-service-access-preflight');
  assert.deepEqual(result.accountGroups, [1000]); assert.deepEqual(result.groups, [44, 990, 1000]);
  assert.deepEqual(result.serviceSupplementaryGroups, [{ name: 'render', gid: 990 }, { name: 'video', gid: 44 }]);
  assert.equal(result.renderGroupMatchesDevice, true); assert.equal(result.videoGroupMatchesDevice, true);
  assert.equal(result.proposedServiceGpuModeAccess, true);
  for (const flag of ['installable', 'readyForActivation', 'servicesActivated', 'accountMembershipChanged',
    'gpuAccelerationVerified', 'effectiveServicePolicyVerified', 'actualServiceCredentialsVerified',
    'androidOperationsExecuted']) assert.equal(result[flag], false, flag);
  assert.doesNotMatch(JSON.stringify(result), /private-password|private-member/);
  assert.equal(result.temporaryDesktopAclsIgnored, true);
  assert.match(result.configurationDigest, /^[a-f0-9]{64}$/);
  assert.ok(Object.isFrozen(result)); assert.ok(Object.isFrozen(result.accountGroups));
  assert.ok(Object.isFrozen(result.serviceSupplementaryGroups[0]));
  assert.deepEqual(f.calls.filter(value => value.startsWith('group:')), ['group:render', 'group:video', 'group:render', 'group:video']);
});

test('preflight and both generated GPU units share exactly the same fixed group names', () => {
  fixture(); assert.deepEqual(GPU_SERVICE_GROUPS, ['render', 'video']); assert.ok(Object.isFrozen(GPU_SERVICE_GROUPS));
  const plan = createServicePlan(config);
  for (const [name, bytes] of Object.entries(plan.files)) {
    const matches = bytes.split('\n').filter(line => line.startsWith('SupplementaryGroups='));
    assert.deepEqual(matches, /(?:compositor|headless-session)\.service$/.test(name)
      ? ['SupplementaryGroups=' + GPU_SERVICE_GROUPS.join(' ')] : [], name);
  }
});

test('named groups are bounded, exact, nonroot and never include member or password data in receipts', () => {
  fixture(); const parse = implementation.parseServiceGpuGroup;
  assert.deepEqual(parse(Buffer.from('render:x:990:private-member\n'), 'render'), { name: 'render', gid: 990 });
  for (const row of ['', 'video:x:990:', 'render:x:0:', 'render:x:0990:', 'render:x:2147483648:',
    'render:x:-1:', 'render:x:990::', 'render:x:990:\nother:x:44:', 'render:x:990:\0',
    Buffer.from([255]), 'x'.repeat(4097), {}, null]) assert.throws(() => parse(row, 'render'));
  assert.throws(() => parse('other:x:990:', 'other'));
});

test('missing, root or aliased group mappings refuse before reading DRM metadata', async () => {
  for (const value of [undefined, '', 'render:x:0:', 'render:x:44:']) {
    const f = fixture(); f.records.render = value; await assert.rejects(f.inspect());
    assert.ok(!f.calls.includes('/dev'));
  }
  const f = fixture(); f.options.lookupGroup = async () => { throw Error('secret-member'); };
  await assert.rejects(f.inspect()); assert.ok(!f.calls.includes('/dev'));
});

test('permissions and named device mapping are distinct from inherited access', async () => {
  const f = fixture(); f.records.render = 'render:x:991:';
  f.options.lookupGroups = async () => '1000 990';
  const result = await f.inspect();
  assert.equal(result.unattendedRenderModeAccess, true); assert.equal(result.renderGroupMatchesDevice, false);
  assert.equal(result.proposedServiceGpuModeAccess, false);
  const g = fixture(); g.nodes['/dev/dri/renderD128'].mode = 0o20600;
  assert.equal((await g.inspect()).proposedServiceGpuModeAccess, false);
  const h = fixture(); h.nodes['/dev/dri'].mode = 0o40700;
  assert.equal((await h.inspect()).proposedServiceGpuModeAccess, false);
});

test('group mapping drift and hidden inherited-group drift invalidate proposed service eligibility', async () => {
  const f = fixture(), original = f.options.lookupGroup; let calls = 0;
  f.options.lookupGroup = async name => { const row = await original(name); return ++calls === 3 ? 'render:x:991:' : row; };
  await assert.rejects(f.inspect(), /mapping changed/);
  const g = fixture(); let memberships = 0;
  g.options.lookupGroups = async () => ++memberships === 1 ? '1000 990' : '1000';
  await assert.rejects(g.inspect(), /account groups changed/);
});

test('underlying NSS identity and DRM namespace generations still fail closed', async () => {
  const f = fixture(); let accountReads = 0;
  f.options.lookup = async () => ++accountReads === 1 ? 'hilman:x:1000:1000::/home/hilman:/bin/bash' : 'wrong';
  await assert.rejects(f.inspect());
  const g = fixture(), original = g.options.io.lstat;
  g.options.io.lstat = async filename => { const result = await original(filename); if (filename.endsWith('renderD128')) g.nodes[filename].ino++; return result; };
  await assert.rejects(g.inspect(), /device changed/);
});

test('invalid configuration is refused before any lookup and absent nodes never imply usable GPU services', async () => {
  const f = fixture(); await assert.rejects(implementation.inspectGpuServiceAccess({ ...config, uid: 0 }, f.options));
  assert.deepEqual(f.calls, []);
  const g = fixture(); g.options.io.readdir = async () => [];
  const result = await g.inspect(); assert.equal(result.proposedServiceGpuModeAccess, false);
  assert.equal(result.renderGroupMatchesDevice, false); assert.equal(result.videoGroupMatchesDevice, false);
});

test('cancellation and mutable caller configuration cannot widen the inspected identity', async () => {
  const f = fixture(), mutable = { ...config }, original = f.options.lookupGroup;
  f.options.lookupGroup = async name => { mutable.uid = 2000; return original(name); };
  assert.equal((await implementation.inspectGpuServiceAccess(mutable, f.options)).uid, 1000);
  const g = fixture(), controller = new AbortController();
  g.options.lookupGroup = async name => { g.calls.push(name); controller.abort(); return g.records[name]; };
  await assert.rejects(implementation.inspectGpuServiceAccess(config, { ...g.options, signal: controller.signal }));
  assert.deepEqual(g.calls, ['render']);
  const h = fixture(); await assert.rejects(implementation.inspectGpuServiceAccess(config, { ...h.options, signal: controller.signal }));
  assert.deepEqual(h.calls, []);
});

test('production command rejects arbitrary actions with fixed redacted diagnostics', () => {
  fixture(); const result = spawnSync(process.execPath, [path.join(__dirname, 'gpu-service-access.js'), 'activate', 'private-value'],
    { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'GPU-service preflight rejected');
});
