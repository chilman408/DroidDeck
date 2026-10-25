// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { waitForSession, failureSummary } = require('./prime-installation-media');

function clock() {
  let time = 0;
  return { now: () => time, sleep: async ms => { time += ms; } };
}
const refused = () => Object.assign(Error('generated refusal'), { code: 'ECONNREFUSED' });

test('listener becoming ready at 34s succeeds within parent 60s budget', async () => {
  const time = clock(); let calls = 0;
  const value = await waitForSession(async timeout => {
    calls++; assert.ok(timeout > 0 && timeout <= 15000);
    if (time.now() < 34000) throw refused();
    return { generated: true };
  }, time);
  assert.deepEqual(value, { generated: true });
  assert.equal(calls, 35); assert.equal(time.now(), 34000);
});

test('absent listener stops at 55s, without extending healthy services', async () => {
  const time = clock(); let calls = 0;
  await assert.rejects(waitForSession(async () => { calls++; throw refused(); }, time),
    { code: 'INSTALLATION_LISTENER_DEADLINE' });
  assert.equal(calls, 55); assert.equal(time.now(), 55000);
});

test('reset connection is retryable, with shrinking request timeout', async () => {
  const time = clock(), limits = [];
  await assert.rejects(waitForSession(async timeout => {
    limits.push(timeout);
    throw Object.assign(Error(), { code: 'ECONNRESET' });
  }, time), { code: 'INSTALLATION_LISTENER_DEADLINE' });
  assert.equal(limits.at(-1), 1000);
});

test('TLS, authorization, malformed replies and unavailable HTTP do not retry', async () => {
  for (const error of [Object.assign(Error(), { code: 'ERR_TLS_CERT_ALTNAME_INVALID' }),
    Object.assign(Error(), { code: 'CERT_HAS_EXPIRED' }), Object.assign(Error(), { status: 401 }),
    Object.assign(Error(), { status: 403 }), Object.assign(Error(), { status: 503 }),
    SyntaxError('generated malformed reply')]) {
    const time = clock(); let calls = 0;
    await assert.rejects(waitForSession(async () => { calls++; throw error; }, time), value => value === error);
    assert.equal(calls, 1); assert.equal(time.now(), 0);
  }
});

test('redacted diagnostics never print messages, paths, tokens or arbitrary codes', () => {
  assert.deepEqual(failureSummary(Object.assign(Error('generated private material'),
    { code: 'arbitrary-external-value', status: 401, token: 'generated' })),
  { kind: 'installation-media-route-failure', reason: 'SESSION_FAILURE', status: 401 });
  assert.deepEqual(failureSummary({ code: 'INSTALLATION_LISTENER_DEADLINE', status: 200 }),
    { kind: 'installation-media-route-failure', reason: 'INSTALLATION_LISTENER_DEADLINE' });
});
