// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { parseRuntimeAccount, validateRuntimeAccount } = require('./runtime-account');
const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
const row = 'deck:private-placeholder:1200:1201:Personal Info:/home/deck:/bin/bash\n';
test('NSS account parser compares exact UID/group/home without retaining private fields', () => {
  const account = parseRuntimeAccount(row, configuration);
  assert.deepEqual(account, { user: 'deck', uid: 1200, gid: 1201, home: '/home/deck' });
  assert.equal(Object.isFrozen(account), true);
  assert.doesNotMatch(JSON.stringify(account), /private-placeholder|Personal Info|bash/);
});
test('wrong, aliased, noncanonical, duplicate and oversized account records are rejected generically', () => {
  for (const text of [row.replace(':1200:', ':0:'), row.replace(':1200:', ':1202:'), row.replace(':1201:', ':1202:'),
    row.replace(':1200:', ':01200:'), row.replace('/home/deck', '/root'), row.replace('deck:', 'root:'), row + row,
    'deck:secret-token', 'secret-token'.repeat(1000), null, { toString: () => row }]) {
    assert.throws(() => parseRuntimeAccount(text, configuration), error => !/private-placeholder|secret-token|Personal Info/.test(error.message));
  }
});
test('read-only account lookup receives normalized snapshot and cancellation signal', async () => {
  const signal = new AbortController().signal;
  const result = await validateRuntimeAccount(configuration, { signal, lookup: async (user, options) => {
    assert.equal(user, 'deck'); assert.equal(options.signal, signal); return Buffer.from(row);
  } });
  assert.equal(result.uid, 1200);
  await assert.rejects(validateRuntimeAccount(configuration, { lookup: async () => { throw new Error('backend-secret'); } }), error => !/backend-secret/.test(error.message));
  let calls = 0; await assert.rejects(validateRuntimeAccount(configuration, { signal: AbortSignal.abort(new Error('cancelled')),
    lookup: async () => { calls++; return row; } }), /cancelled/); assert.equal(calls, 0);
});
