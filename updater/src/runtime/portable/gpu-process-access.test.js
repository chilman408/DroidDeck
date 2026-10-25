// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process'), path = require('node:path');
const api = require('./gpu-access');
const config = { user: 'hilman', uid: 1000, gid: 1000, lanAddress: '192.168.2.215' };
const status = ({ uids = '1000 1000 1000 1000', gids = '1000 1000 1000 1000', groups = '44 990', capability = '0' } = {}) =>
  `Name:\tgenerated\nUid:\t${uids}\nGid:\t${gids}\nGroups:\t${groups}\nCapInh:\t0\nCapPrm:\t0\nCapEff:\t${capability}\nCapAmb:\t0\n`;
function fixture() {
  const calls = [], nodes = { '/dev': { dev: 1, ino: 1, uid: 0, gid: 0, mode: 0o40755, isDirectory: () => true },
    '/dev/dri': { dev: 1, ino: 2, uid: 0, gid: 0, mode: 0o40755, isDirectory: () => true } };
  for (const [name, minor, gid] of [['card1', 1, 44], ['renderD128', 128, 990]]) nodes['/dev/dri/' + name] =
    { dev: 1, ino: minor + 10, uid: 0, gid, mode: 0o20660, nlink: 1, rdev: 226 * 256 + minor, isCharacterDevice: () => true };
  const options = { readStatus: async () => { calls.push('status'); return Buffer.from(status()); },
    lookup: async () => { calls.push('passwd'); return 'hilman:x:1000:1000::/home/hilman:/bin/bash'; },
    io: { lstat: async filename => { calls.push(filename); return { ...nodes[filename] }; },
      readdir: async () => { calls.push('listing'); return ['card1', 'renderD128']; } } };
  return { calls, options, nodes, inspect: () => api.assertGpuProcessAccess(config, options) };
}

test('current credentials use actual groups plus primary GID, without predicting NSS membership', async () => {
  const f = fixture(), result = await f.inspect();
  assert.equal(result.kind, 'gpu-process-access-preflight'); assert.equal(result.credentialBasis, 'current-process');
  assert.deepEqual(result.groups, [44, 990, 1000]); assert.equal(result.processCredentialsMatched, true);
  assert.equal(result.actualProcessGpuModeAccess, true); assert.equal(result.actualServiceMainProcessVerified, false);
  for (const key of ['gpuAccelerationVerified', 'deviceAccessTested', 'installable', 'readyForActivation',
    'servicesActivated', 'androidOperationsExecuted']) assert.equal(result[key], false, key);
  assert.equal(f.calls.filter(x => x === 'status').length, 4);
  assert.doesNotMatch(JSON.stringify(result), /generated|CapEff|uids/); assert.ok(Object.isFrozen(result));
});

test('root, saved/filesystem IDs, wrong primary GID and capabilities refuse before account or DRM reads', async () => {
  for (const patch of [{ uids: '0 0 0 0' }, { uids: '1000 1000 0 1000' }, { uids: '1000 1000 1000 0' },
    { gids: '1000 1000 1000 44' }, { gids: '44 44 44 44' }, { capability: '0000000000000001' }]) {
    const f = fixture(); f.options.readStatus = async () => status(patch);
    await assert.rejects(f.inspect(), /credentials/); assert.deepEqual(f.calls, []);
  }
});

test('missing actual GPU groups, missing nodes or blocked search refuses regardless of inspector privileges', async () => {
  const f = fixture(); f.options.readStatus = async () => status({ groups: '' });
  await assert.rejects(f.inspect(), /DRM mode access/);
  const g = fixture(); g.options.io.readdir = async () => []; await assert.rejects(g.inspect(), /DRM mode access/);
  const h = fixture(); h.nodes['/dev/dri'].mode = 0o40700; await assert.rejects(h.inspect(), /DRM mode access/);
});

test('bounded proc parser requires unambiguous complete IDs, groups and all four capability masks', () => {
  assert.equal(typeof api.parseGpuProcessCredentials, 'function'); const parse = api.parseGpuProcessCredentials;
  assert.deepEqual(parse(status()).groups, [44, 990]);
  for (const value of ['', status() + 'Uid:\t1000 1000 1000 1000\n', status({ groups: '44 44' }),
    status({ uids: '1000 1000 1000' }), status({ gids: '1000 1000 1000 2147483648' }),
    status({ groups: '01' }), status({ groups: Array.from({ length: 65 }, (_, i) => i).join(' ') }),
    status({ capability: 'xyz' }), status({ capability: '1'.repeat(17) }), status().replace('CapPrm:', 'Other:'),
    status() + '\0', status().replace('CapEff:\t0', 'CapEff:\v0'),
    Buffer.from([255]), Buffer.alloc(65537), {}, null]) assert.throws(() => parse(value));
});

test('credential drift cannot survive the mode inspection or be masked by primary-GID union', async () => {
  for (const at of [2, 3, 4]) {
    const f = fixture(); let reads = 0;
    f.options.readStatus = async () => status({ groups: ++reads < at ? '44 990' : '44 990 1000' });
    await assert.rejects(f.inspect(), /credentials changed/);
  }
});

test('existing NSS/device-generation validation, immutable config and cancellation remain effective', async () => {
  const f = fixture(); f.options.lookup = async () => 'wrong'; await assert.rejects(f.inspect());
  const g = fixture(), original = g.options.io.lstat;
  g.options.io.lstat = async name => { const result = await original(name); if (name.endsWith('renderD128')) g.nodes[name].ino++; return result; };
  await assert.rejects(g.inspect(), /device changed/);
  const h = fixture(), mutable = { ...config };
  h.options.readStatus = async () => { mutable.uid = 2000; return status(); };
  assert.equal((await api.assertGpuProcessAccess(mutable, h.options)).uid, 1000);
  const i = fixture(), controller = new AbortController();
  i.options.readStatus = async () => { controller.abort(); return status(); };
  await assert.rejects(api.assertGpuProcessAccess(config, { ...i.options, signal: controller.signal }));
  assert.deepEqual(i.calls, []);
});

test('native fixed self reader matches real/effective kernel IDs without exporting unselected status',
  { skip: process.platform !== 'linux' }, async () => {
    const record = api.parseGpuProcessCredentials(await api.readGpuProcessStatus());
    assert.equal(record.uids[0], process.getuid()); assert.equal(record.uids[1], process.geteuid());
    assert.equal(record.gids[0], process.getgid()); assert.equal(record.gids[1], process.getegid());
  });

test('configured compositor and headless main refuse at GPU guard before environment/socket/graph work',
  { skip: process.platform !== 'linux' }, async () => {
    for (const filename of ['./desktop-preflight', '../waydroid-display/headless-session-preflight']) {
      let called = 0;
      await assert.rejects(require(filename).main({ configuration: config, args: [],
        load: async () => config, checkGpu: async value => { called++; assert.deepEqual(value, config); throw Error('generated GPU refusal'); } }),
      /generated GPU refusal/);
      assert.equal(called, 1);
    }
  });

test('GPU process CLI retains fixed redacted errors for invalid actions', () => {
  const result = spawnSync(process.execPath, [path.join(__dirname, 'gpu-access.js'), 'arbitrary', 'private-value'],
    { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), 'GPU-access preflight rejected');
});
