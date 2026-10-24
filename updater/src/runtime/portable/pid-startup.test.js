// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { startPidPreparation } = require('./pid-startup');
test('portable startup gates PID rewrite on validated metadata and fixes the portable mode', async () => {
  const calls = [];
  const result = await startPidPreparation({ read: async () => { calls.push('metadata'); return { version: 1 }; },
    prepare: async options => { assert.deepEqual(options, { portable: true }); calls.push('prepare'); return 'updated'; } });
  assert.equal(result, 'updated'); assert.deepEqual(calls, ['metadata', 'prepare']);
});
test('missing or rejected metadata cannot rewrite LXC configuration', async () => {
  let writes = 0; const prepare = async () => ++writes;
  await assert.rejects(startPidPreparation({ read: async () => undefined, prepare }), /configuration/);
  await assert.rejects(startPidPreparation({ read: async () => { throw new Error('NSS rejected'); }, prepare }));
  assert.equal(writes, 0);
});
