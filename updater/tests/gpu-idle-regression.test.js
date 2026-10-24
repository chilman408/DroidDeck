// SPDX-License-Identifier: GPL-3.0-only
// Exercises the exact published RC5 native probe; no guest or service mutations.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const source = path.join(__dirname, '../src/runtime/portable/gpu-native.js');
assert.equal(createHash('sha256').update(fs.readFileSync(source)).digest('hex'),
  '7deaa126bbb0793211ca7bf34d14f86dd4f62ff6e343404db99e9f72d6a15c7e');
const { inspectNativeGpu, EXPECTED, LIBRARY, CONSUMERS } = require(source);

for (const touchState of ['inactive', 'failed', 'activating']) {
  test(`idle/unavailable touch (${touchState}) cannot become a GPU failure`, async () => {
    const calls = [];
    const run = async (file, args) => {
      calls.push([file, ...args]);
      // Model the legacy trigger. The actual native probe must never request it.
      if (file === '/usr/bin/systemctl') return touchState;
      if (file === '/usr/bin/lxc-info') return args.at(-1) === '-sH' ? 'RUNNING' : '700';
      assert.equal(file, '/usr/bin/nsenter');
      const guest = args.slice(6);
      if (guest[0] === '/system/bin/getprop') return EXPECTED[guest[1]];
      if (guest[0] === '/system/bin/dumpsys') return 'GLES: Mesa, virgl AMD Radeon, OpenGL ES 3.2';
      assert.deepEqual(guest, ['/system/bin/toybox', 'cat', '/proc/40/maps']);
      return [LIBRARY, CONSUMERS[0]].map(p => `1000-2000 r-xp 00001000 fd:01 500 ${p}\n`).join('');
    };
    for (let sample = 0; sample < 6; sample++) {
      const result = await inspectNativeGpu({ run, readIdentity: async () => 'ns:birth',
        readAndroid: async () => '30:400/40:500' });
      assert.equal(result.nativeGpuVerified, true);
    }
    assert.ok(calls.every(([file]) => file !== '/usr/bin/systemctl'));
    assert.ok(calls.every(call => !call.some(value => /setprop|systemctl|upgrade|kill|\/drm/.test(value))));
  });
}
