// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { loadWorkerConfiguration } = require('./worker-config');
const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
test('worker bootstrap verifies actual role UID and immutable configured account before returning metadata', async () => {
  for (const [role, uid] of [['privileged', 0], ['user', 1200]]) {
    let checked = 0;
    const result = await loadWorkerConfiguration({ role, uid, read: () => configuration,
      validateAccount: async config => { assert.equal(config.uid, 1200); assert.equal(Object.isFrozen(config), true); checked++; } });
    assert.equal(result.uid, 1200); assert.equal(checked, 1);
  }
});
test('wrong UID, roles and malformed configuration cannot reach account lookup', async () => {
  for (const input of [{ role: 'privileged', uid: 1200 }, { role: 'user', uid: 0 }, { role: 'user', uid: 1000 },
    { role: 'other', uid: 0 }, { role: 'user', uid: '1200' }]) {
    await assert.rejects(loadWorkerConfiguration({ ...input, read: () => configuration, validateAccount: async () => assert.fail('Account lookup must not run') }));
  }
  await assert.rejects(loadWorkerConfiguration({ role: 'privileged', uid: 0, read: () => ({ ...configuration, password: 'secret' }), validateAccount: async () => assert.fail() }));
});
test('disabled worker configuration preserves legacy UID gates and cancellation prevents reads', async () => {
  for (const [role, uid] of [['privileged', 0], ['user', 1000]]) {
    assert.equal(await loadWorkerConfiguration({ role, uid, read: () => undefined, validateAccount: async () => assert.fail() }), undefined);
  }
  await assert.rejects(loadWorkerConfiguration({ role: 'user', uid: 1200, read: () => undefined }));
  await assert.rejects(loadWorkerConfiguration({ role: 'privileged', uid: 0, signal: AbortSignal.abort(new Error('cancelled')), read: () => assert.fail() }), /cancelled/);
});
