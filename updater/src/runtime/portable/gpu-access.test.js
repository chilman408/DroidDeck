// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
let implementation;
try { implementation = require('./gpu-access'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
const config = { user: 'hilman', uid: 1000, gid: 1000, lanAddress: '192.168.2.215' };
const directory = () => ({ uid: 0, gid: 0, mode: 0o40755, dev: 1, ino: 10,
  isDirectory: () => true, isCharacterDevice: () => false });
const node = (kind) => ({ uid: 0, gid: kind === 'render' ? 990 : 44, mode: 0o20660,
  nlink: 1, dev: 1, ino: kind === 'render' ? 12 : 11, rdev: 226 * 256 + (kind === 'render' ? 128 : 1),
  isDirectory: () => false, isCharacterDevice: () => true });
function fixture(groups = '1000 44 990\n') {
  assert.ok(implementation, 'GPU-access inspector is not implemented');
  const reads = [], nodes = { '/dev': directory(), '/dev/dri': directory(),
    '/dev/dri/card1': node('card'), '/dev/dri/renderD128': node('render') };
  let names = ['renderD128', 'card1', 'by-path'];
  const io = { lstat: async filename => { reads.push(filename); return { ...nodes[filename] }; },
    readdir: async filename => { reads.push(filename); return [...names]; } };
  const lookup = async () => 'hilman:x:1000:1000::/home/hilman:/bin/bash\n';
  const options = { io, lookup, lookupGroups: async () => groups };
  return { reads, nodes, options, setNames: value => { names = value; }, inspect: () => implementation.inspectGpuAccess(config, options) };
}
test('eligible account reports card/render modes without claiming GPU acceleration', async () => {
  const f = fixture(), receipt = await f.inspect();
  assert.equal(receipt.unattendedRenderModeAccess, true);
  assert.equal(receipt.unattendedCardModeAccess, true);
  assert.equal(receipt.installable, false); assert.equal(receipt.gpuAccelerationVerified, false);
  assert.equal(receipt.temporaryDesktopAclsIgnored, true);
  assert.deepEqual(receipt.nodes.map(item => item.path), ['/dev/dri/card1', '/dev/dri/renderD128']);
  assert.ok(receipt.nodes.every(item => item.readWriteByMode));
  assert.ok(Object.isFrozen(receipt)); assert.ok(Object.isFrozen(receipt.nodes[0]));
});
test('missing persistent group access is not hidden by root or desktop ACLs', async () => {
  const receipt = await fixture('1000 4\n').inspect();
  assert.equal(receipt.unattendedRenderModeAccess, false);
  assert.equal(receipt.unattendedCardModeAccess, false);
  assert.deepEqual(receipt.requiredDeviceGroups, [44, 990]);
});
test('directory search eligibility gates device access with group-class precedence', async () => {
  for (const [mode, gid, groups] of [[0o40700, 0, '1000 44 990'],
    [0o40750, 45, '1000 44 990'], [0o40701, 44, '1000 44 990']]) {
    const f = fixture(groups); Object.assign(f.nodes['/dev/dri'], { mode, gid });
    const receipt = await f.inspect();
    assert.equal(receipt.unattendedRenderModeAccess, false);
    assert.equal(receipt.unattendedCardModeAccess, false);
    assert.equal(receipt.namespaceSearchByMode, false);
    assert.ok(receipt.nodes.every(node => node.deviceReadWriteByMode && !node.readWriteByMode));
  }
  const f = fixture(); Object.assign(f.nodes['/dev'], { mode: 0o40750, gid: 45 });
  const receipt = await f.inspect();
  assert.equal(receipt.unattendedRenderModeAccess, false);
  assert.deepEqual(receipt.requiredDirectoryGroups, [45]);
  assert.deepEqual(receipt.requiredDeviceGroups, []);
});
test('strict bounded groups include primary group and reject malformed/duplicate records', () => {
  fixture();
  assert.deepEqual(implementation.parseSupplementalGroups(Buffer.from('990 1000 44\n'), 1000), [44, 990, 1000]);
  for (const value of ['', '990', '1000 1000', '1000\n44', '1000 -1', '1000 01', '1000 2147483648',
    '1000\0', Buffer.from([255]), '1'.repeat(4097), Array.from({length: 65}, (_, i) => i + 1000).join(' ')]) {
    assert.throws(() => implementation.parseSupplementalGroups(value, 1000));
  }
});
test('invalid config and mismatched NSS identity fail before device IO', async () => {
  const f = fixture();
  await assert.rejects(implementation.inspectGpuAccess({ ...config, user: '../root' }, f.options));
  await assert.rejects(implementation.inspectGpuAccess(config, { ...f.options, lookup: async () => 'wrong' }));
  assert.deepEqual(f.reads, []);
});
test('unsafe namespace and fake or insecure DRM devices are rejected', async () => {
  for (const [name, changes] of [['/dev/dri', { uid: 1000 }], ['/dev', { mode: 0o40777 }],
    ['/dev/dri', { isDirectory: () => false }], ['/dev/dri/renderD128', { uid: 1000 }],
    ['/dev/dri/renderD128', { mode: 0o20666 }], ['/dev/dri/renderD128', { nlink: 2 }],
    ['/dev/dri/renderD128', { isCharacterDevice: () => false }], ['/dev/dri/renderD128', { rdev: 1 }],
    ['/dev/dri/renderD128', { rdev: 226 * 256 + 129 }]]) {
    const f = fixture(); Object.assign(f.nodes[name], changes); await assert.rejects(f.inspect());
  }
});
test('bounded namespace and unsupported named DRM layouts fail closed', async () => {
  for (const names of [Array.from({ length: 129 }, (_, i) => 'other' + i), ['renderD999'], ['card999'], ['card1', 'card1']]) {
    const f = fixture(); f.setNames(names); await assert.rejects(f.inspect());
  }
  const f = fixture(); f.setNames(['by-path']); const receipt = await f.inspect();
  assert.deepEqual(receipt.nodes, []); assert.equal(receipt.unattendedRenderModeAccess, false);
});
test('metadata or listing changes during inspection invalidate the receipt', async () => {
  for (const kind of ['inode', 'mode', 'directory', 'listing']) {
    const f = fixture(), original = f.options.io.readdir; let count = 0;
    f.options.io.readdir = async filename => {
      const result = await original(filename);
      if (++count === 1) {
        if (kind === 'inode') f.nodes['/dev/dri/renderD128'].ino++;
        if (kind === 'mode') f.nodes['/dev/dri/renderD128'].mode = 0o20600;
        if (kind === 'directory') f.nodes['/dev/dri'].ino++;
        if (kind === 'listing') f.setNames(['card1']);
      }
      return result;
    };
    // Node changes must occur after that node's first observation.
    if (kind === 'inode' || kind === 'mode') {
      f.options.io.readdir = original;
      const stat = f.options.io.lstat;
      f.options.io.lstat = async filename => {
        const result = await stat(filename);
        if (filename === '/dev/dri/renderD128') {
          if (kind === 'inode') f.nodes[filename].ino++;
          else f.nodes[filename].mode = 0o20600;
        }
        return result;
      };
    }
    await assert.rejects(f.inspect());
  }
});
test('configuration snapshots and cancellation cannot change inspected identity', async () => {
  const f = fixture(), mutable = { ...config }; let inspected;
  const receipt = await implementation.inspectGpuAccess(mutable, { ...f.options,
    lookup: async user => { mutable.uid = 2000; inspected = user; return f.options.lookup(); } });
  assert.equal(inspected, 'hilman'); assert.equal(receipt.uid, 1000);
  const controller = new AbortController(); controller.abort();
  const cancelled = fixture();
  await assert.rejects(implementation.inspectGpuAccess(config, { ...cancelled.options, signal: controller.signal }));
  assert.deepEqual(cancelled.reads, []);
});
test('NSS group changes and cancellation during lookup refuse stale eligibility', async () => {
  const f = fixture(); let calls = 0;
  await assert.rejects(implementation.inspectGpuAccess(config, { ...f.options,
    lookupGroups: async () => ++calls === 1 ? '1000 44 990' : '1000 44' }), /groups changed/);
  assert.equal(calls, 2);
  const controller = new AbortController(), cancelled = fixture();
  await assert.rejects(implementation.inspectGpuAccess(config, { ...cancelled.options, signal: controller.signal,
    lookupGroups: async () => { controller.abort(); return '1000 44 990'; } }));
  assert.deepEqual(cancelled.reads, []);
});
test('cancellation during directory stat starts no subsequent listing operation', async () => {
  const f = fixture(), original = f.options.io.lstat, controller = new AbortController();
  f.options.io.lstat = async filename => {
    const result = await original(filename);
    if (filename === '/dev/dri') controller.abort();
    return result;
  };
  await assert.rejects(implementation.inspectGpuAccess(config, { ...f.options, signal: controller.signal }));
  assert.deepEqual(f.reads, ['/dev', '/dev/dri']);
});
test('production CLI rejects invalid commands without printing input data', () => {
  fixture();
  const { spawnSync } = require('node:child_process'), path = require('node:path');
  const result = spawnSync(process.execPath, [path.join(__dirname, 'gpu-access.js'), 'unexpected', 'private-value'],
    { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'GPU-access preflight rejected');
});
