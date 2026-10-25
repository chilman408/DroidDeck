// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const { applyGuestPidLimit } = require('./pid-compat-hook');

function fixture(overrides = {}) {
  let value = '4194304\n'; const writes = [];
  const options = {
    pid: 2, args: ['waydroid', 'lxc', 'pre-mount'],
    environment: { LXC_NAME: 'waydroid', LXC_CONFIG_FILE: '/var/lib/waydroid/lxc/waydroid/config' },
    io: {
      async readlink(name) { return name === '/proc/self/ns/pid' ? 'pid:[22]' : 'pid:[1]'; },
      async readFile(name) { return name === '/proc/self/status' ? 'Name:\tnode\nNSpid:\t20202\t2\n' : value; },
      async statfs() { return { type: 0x9fa0 }; },
      async writeFile(name, text) { writes.push([name, text]); value = text; },
    }, ...overrides,
  };
  return { options, writes };
}

test('early PID hook applies only the guest cap, reads it back and is idempotent', async () => {
  const { options, writes } = fixture();
  assert.equal(await applyGuestPidLimit(options), 'updated');
  assert.deepEqual(writes, [['/proc/sys/kernel/pid_max', '65535\n']]);
  assert.equal(await applyGuestPidLimit(options), 'unchanged');
  assert.equal(writes.length, 1);
});

test('early PID hook rejects host namespace, wrong hook and mismatched PID before writing', async () => {
  for (const change of [options => { options.args = []; },
    options => { options.environment.LXC_NAME = 'other'; },
    options => { options.environment.LXC_CONFIG_FILE = '/other/config'; },
    options => { options.pid = 9; },
    options => { options.io.readlink = async () => 'pid:[1]'; },
    options => { options.io.readFile = async () => 'NSpid:\t2\n'; },
    options => { options.io.statfs = async () => ({ type: 0 }); }]) {
    const { options, writes } = fixture(); change(options);
    await assert.rejects(applyGuestPidLimit(options)); assert.equal(writes.length, 0);
  }
});

test('early PID hook refuses a failed write/readback instead of acknowledging success', async () => {
  const { options } = fixture();
  options.io.writeFile = async () => {};
  await assert.rejects(applyGuestPidLimit(options), /verification/);
});

test('early PID hook preserves a lower safe guest limit', async () => {
  const { options, writes } = fixture();
  options.io.readFile = async name => name === '/proc/self/status' ? 'NSpid:\t20202\t2\n' : '32768\n';
  assert.equal(await applyGuestPidLimit(options), 'unchanged'); assert.equal(writes.length, 0);
});
