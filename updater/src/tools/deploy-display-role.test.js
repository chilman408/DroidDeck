// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { deployRole } = require('./deploy-display-role');
function fixture(failure) {
  const calls = []; let firstStart = true;
  const dependencies = Object.fromEntries(['preflight', 'stop', 'installRuntime', 'installFrontend', 'rollbackFrontend', 'rollbackRuntime', 'start', 'waitReady'].map(name =>
    [name, async () => { calls.push(name); if (name === failure && (name !== 'start' || firstStart)) { firstStart = false; throw new Error('trial failure'); } }]));
  return { calls, dependencies };
}
test('matched display role backend and frontend install while parent is stopped, then recover readiness', async () => {
  const f = fixture(); await deployRole(f.dependencies);
  assert.deepEqual(f.calls, ['preflight', 'stop', 'installRuntime', 'installFrontend', 'start', 'waitReady']);
});
test('frontend failure rolls back only installed backend and restores the parent', async () => {
  const f = fixture('installFrontend'); await assert.rejects(deployRole(f.dependencies), /trial failure/);
  assert.deepEqual(f.calls, ['preflight', 'stop', 'installRuntime', 'installFrontend', 'stop', 'rollbackRuntime', 'start', 'waitReady']);
});
test('startup failure restores frontend before backend, but preflight failure cannot stop services', async () => {
  const f = fixture('start'); await assert.rejects(deployRole(f.dependencies), /trial failure/);
  assert.deepEqual(f.calls, ['preflight', 'stop', 'installRuntime', 'installFrontend', 'start', 'stop', 'rollbackFrontend', 'rollbackRuntime', 'start', 'waitReady']);
  const invalid = fixture('preflight'); await assert.rejects(deployRole(invalid.dependencies)); assert.deepEqual(invalid.calls, ['preflight']);
});
