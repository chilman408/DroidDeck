// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { startWorker, WORKERS } = require('./worker-entry');
const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
test('closed worker registry passes one immutable checked configuration and no CLI arguments to launchers', async () => {
  for (const [name, worker] of Object.entries(WORKERS)) {
    let checked = false, launched = false;
    const input = { ...configuration };
    const pending = startWorker(name, { uid: worker.role === 'privileged' ? 0 : 1200, read: () => input,
      validateAccount: async value => { assert.equal(value.user, 'deck'); checked = true; },
      launch: async (selected, options) => { assert.equal(selected, name); assert.equal(checked, true);
        assert.equal(Object.isFrozen(options.configuration), true); assert.deepEqual(options.args, []);
        assert.equal(options.configuration.uid, 1200); launched = true; } });
    input.uid = 0; await pending; assert.equal(launched, true);
  }
});
test('unknown worker, absent config, wrong UID, rejected account and cancellation cannot launch anything', async () => {
  const launch = async () => assert.fail('Launch must not run');
  await assert.rejects(startWorker('../server', { uid: 0, read: () => assert.fail(), launch }));
  await assert.rejects(startWorker('video', { uid: 0, read: () => undefined, launch }), /configuration/);
  await assert.rejects(startWorker('video', { uid: 1200, read: () => configuration, launch }));
  await assert.rejects(startWorker('gateway', { uid: 1000, read: () => configuration, launch }));
  await assert.rejects(startWorker('gateway', { uid: 1200, read: () => configuration,
    validateAccount: async () => { throw new Error('account mismatch'); }, launch }), /account mismatch/);
  await assert.rejects(startWorker('video', { uid: 0, signal: AbortSignal.abort(new Error('cancelled')), read: () => assert.fail(), launch }), /cancelled/);
});

test('Linux standalone entry rejects an unknown name without exposing configuration details', { skip: process.platform !== 'linux' }, () => {
  const result = require('node:child_process').spawnSync(process.execPath, [require.resolve('./worker-entry'), '../gateway'], { encoding: 'utf8', timeout: 5000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, '');
  assert.equal(result.stderr.trim(), 'Portable worker startup rejected');
});
