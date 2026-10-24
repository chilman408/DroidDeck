// SPDX-License-Identifier: GPL-3.0-only
// Rear authority contracts; live capability remains disabled until channel retirement is integrated.
const test = require('node:test');
const assert = require('node:assert/strict');
const { createDisplayControl } = require('./display-control');
const baseline = { width: 1280, height: 768, density: 180, resolutionPreset: 0, isResponsive: 1, recoveryRequired: false };
async function fixture(t, { retireAuthority = async () => {}, resize, retireTimeoutMs } = {}) {
  let policy = { version: 1, revision: 0, enabled: false, prioritised: false }, actual = { ...baseline };
  const control = createDisplayControl({ retireAuthority, retireTimeoutMs, rearSettings: {
    snapshot: () => ({ ...policy }), update(payload) {
      if (payload.revision !== policy.revision) throw Object.assign(new Error('Stale rear revision'), { status: 409 });
      policy = { version: 1, ...payload, revision: policy.revision + 1 }; return { ...policy };
    },
  }, helper: { inspect: async () => actual, resize: async value => {
    actual = { ...(resize ? await resize(value) : value), recoveryRequired: false }; return actual;
  } } });
  await control.initialize(); t.after(() => control.close());
  const connect = async role => {
    const value = control.createSession(); control.connect(value.token, 'display');
    if (role) await control.registerRole(value.token, role, value.csrfToken);
    return value;
  };
  const settings = (value, patch) => control.updateRearSettings(value.token,
    { revision: policy.revision, enabled: policy.enabled, prioritised: policy.prioritised, ...patch }, value.csrfToken);
  const change = value => control.resize(value.token, { ...baseline, width: 960, height: 640,
    generation: control.snapshot(value.token).generation }, value.csrfToken);
  return { control, connect, settings, change };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('new connected browsers receive primary and secondary roles without making a viewer an input owner', async t => {
  const f = await fixture(t), a = await f.connect(), b = await f.connect();
  assert.equal(f.control.snapshot(a.token).displayRole, 'primary');
  assert.equal(f.control.snapshot(b.token).displayRole, 'rear');
  assert.equal(f.control.snapshot(b.token).canControl, false);
  assert.equal(f.control.snapshot(b.token).displayRoleClaimSupported, 1);
});

test('explicit preferred-role claim retires old input before swapping authoritative roles without resizing', async t => {
  let finish, retired;
  const f = await fixture(t, { retireAuthority: token => { retired = token; return new Promise(resolve => { finish = resolve; }); } });
  const a = await f.connect('primary'), b = await f.connect('rear'); await f.settings(a, { enabled: true });
  const pending = f.control.registerRole(b.token, 'primary', b.csrfToken, { claim: true, generation: f.control.snapshot(b.token).generation });
  await flush(); assert.equal(retired, a.token);
  assert.equal(f.control.snapshot(a.token).canControl, false); assert.equal(f.control.snapshot(b.token).canControl, false);
  finish(); await pending;
  assert.equal(f.control.snapshot(b.token).canControl, true); assert.equal(f.control.snapshot(b.token).displayRole, 'primary');
  assert.equal(f.control.snapshot(a.token).displayRole, 'rear'); assert.equal(f.control.snapshot(a.token).canControl, false);
  assert.equal(f.control.snapshot(b.token).width, baseline.width);
});

test('role claims reject stale generation, wrong CSRF, unconnected sessions and nonpreferred roles before retirement', async t => {
  let retired = 0; const f = await fixture(t, { retireAuthority: async () => { retired++; } });
  const a = await f.connect('primary'), b = await f.connect('rear'), c = f.control.createSession();
  const generation = f.control.snapshot(b.token).generation;
  for (const [session, role, csrf, options] of [[b, 'primary', 'wrong', { claim: true, generation }],
    [b, 'primary', b.csrfToken, { claim: true, generation: generation + 1 }],
    [b, 'rear', b.csrfToken, { claim: true, generation }], [c, 'primary', c.csrfToken, { claim: true, generation }]]) {
    await assert.rejects(f.control.registerRole(session.token, role, csrf, options));
  }
  assert.equal(retired, 0); assert.equal(f.control.snapshot(a.token).canControl, true); assert.equal(f.control.snapshot(b.token).displayRole, 'rear');
});

test('disconnected claimant cannot be promoted after retirement and roles are not swapped on failure', async t => {
  let finish;
  const f = await fixture(t, { retireAuthority: () => new Promise(resolve => { finish = resolve; }) });
  const a = await f.connect('primary'), b = await f.connect('rear');
  const pending = f.control.registerRole(b.token, 'primary', b.csrfToken, { claim: true, generation: f.control.snapshot(b.token).generation });
  const rejected = assert.rejects(pending, /retirement/); await flush();
  f.control.disconnect(b.token, 'display'); finish(); await rejected;
  assert.equal(f.control.snapshot(b.token).canControl, false); assert.equal(f.control.snapshot(b.token).displayRole, 'rear');
  assert.equal(f.control.snapshot(a.token).displayRole, 'primary'); assert.equal(f.control.snapshot(a.token).transactionStatus, 'authority_failed');
});
test('support off retains display-first authority even when a viewer selects rear', async t => {
  let retired = 0;
  const f = await fixture(t, { retireAuthority: async () => { retired++; } });
  const primary = await f.connect('primary'), rear = await f.connect('rear');
  assert.equal(f.control.snapshot(primary.token).canControl, true);
  assert.equal(f.control.snapshot(rear.token).canControl, false);
  assert.equal(retired, 0);
  await assert.rejects(f.settings(rear, { enabled: true }), error => error.status === 403);
});
test('rear priority retires old authority before publishing the preferred owner', async t => {
  let finish, retired;
  const f = await fixture(t, { retireAuthority: token => {
    retired = token; return new Promise(resolve => { finish = resolve; });
  } });
  const primary = await f.connect('primary'), rear = await f.connect('rear');
  const pending = f.settings(primary, { enabled: true, prioritised: true }); await flush();
  assert.equal(retired, primary.token);
  for (const value of [primary, rear]) {
    assert.equal(f.control.snapshot(value.token).canControl, false);
    assert.equal(f.control.snapshot(value.token).transactionStatus, 'transferring');
    assert.throws(() => f.control.assertOwner(value.token, value.csrfToken));
    assert.throws(() => f.control.connect(value.token, 'touchscreen'));
  }
  finish(); await pending;
  assert.equal(f.control.snapshot(rear.token).canControl, true);
  assert.equal(f.control.snapshot(primary.token).canControl, false);
  assert.equal(f.control.snapshot(rear.token).generation, 1);
  await f.change(rear);
});
test('a non-priority owner may manage settings but cannot resize the mirrored picture', async t => {
  const f = await fixture(t), rear = await f.connect('rear');
  await f.settings(rear, { enabled: true });
  assert.equal(f.control.snapshot(rear.token).canControl, true);
  assert.equal(f.control.snapshot(rear.token).canResize, false);
  await assert.rejects(f.change(rear), error => error.status === 403);
  await f.settings(rear, { prioritised: true }); await f.change(rear);
});
test('role registration requires this session CSRF and rejects fabricated roles', async t => {
  const f = await fixture(t), owner = await f.connect('primary');
  await assert.rejects(f.control.registerRole(owner.token, 'rear', 'wrong'), error => error.status === 403);
  await assert.rejects(f.control.registerRole(owner.token, 'admin', owner.csrfToken), error => error.status === 400);
  assert.equal(f.control.snapshot(owner.token).displayRole, 'primary');
});
test('preferred browser arriving during resize cannot interrupt or invalidate its commit', async t => {
  let finish, retired = 0;
  const f = await fixture(t, { resize: value => new Promise(resolve => { finish = () => resolve(value); }),
    retireAuthority: async () => { retired++; } });
  const owner = await f.connect('rear'), viewer = f.control.createSession();
  f.control.connect(viewer.token, 'display'); await f.control.registerRole(viewer.token, 'rear', viewer.csrfToken);
  await f.settings(owner, { enabled: true, prioritised: true });
  const pending = f.change(owner); await flush();
  await assert.rejects(f.settings(owner, { prioritised: false }), error => error.status === 409);
  await f.control.registerRole(viewer.token, 'primary', viewer.csrfToken);
  assert.equal(retired, 0); finish(); await pending;
  assert.equal(f.control.snapshot(owner.token).width, 960);
  assert.equal(f.control.snapshot(owner.token).canControl, true);
});
test('failed authority retirement blocks both owners instead of promoting a stale touch peer', async t => {
  const f = await fixture(t, { retireAuthority: async () => { throw new Error('Native channel close failed'); } });
  const primary = await f.connect('primary'), rear = await f.connect('rear');
  await assert.rejects(f.settings(primary, { enabled: true, prioritised: true }), /retirement/);
  for (const value of [primary, rear]) {
    assert.equal(f.control.snapshot(value.token).canControl, false);
    assert.equal(f.control.snapshot(value.token).transactionStatus, 'authority_failed');
    assert.throws(() => f.control.assertOwner(value.token, value.csrfToken));
  }
});
test('login revocation during retirement must not promote a removed session', async t => {
  let finish;
  const f = await fixture(t, { retireAuthority: () => new Promise(resolve => { finish = resolve; }) });
  const primary = await f.connect('primary'), rear = await f.connect('rear');
  const pending = f.settings(primary, { enabled: true, prioritised: true });
  const rejected = assert.rejects(pending, /revoked/); await flush();
  f.control.revokeAll(); finish(); await rejected;
  assert.throws(() => f.control.snapshot(rear.token), /session/);
});

test('rear capability cannot be enabled without a channel retirement implementation', () => {
  assert.throws(() => createDisplayControl({ rearSettings: { snapshot: () => ({}) } }), /retirement/);
});

test('hung native retirement has a bounded fail-closed deadline', async t => {
  const f = await fixture(t, { retireAuthority: () => new Promise(() => {}), retireTimeoutMs: 10 });
  const primary = await f.connect('primary'), rear = await f.connect('rear');
  await assert.rejects(f.settings(primary, { enabled: true, prioritised: true }), /retirement/);
  assert.equal(f.control.snapshot(rear.token).transactionStatus, 'authority_failed');
  assert.equal(f.control.snapshot(rear.token).canControl, false);
  assert.throws(() => f.control.connect(primary.token, 'touchscreen'));
});

test('role changes defer a real preferred-owner transfer until the active commit', async t => {
  let finishResize, finishRetire, retired = 0;
  const f = await fixture(t, { resize: value => new Promise(resolve => { finishResize = () => resolve(value); }),
    retireAuthority: () => { retired++; return new Promise(resolve => { finishRetire = resolve; }); } });
  const owner = await f.connect('primary'), viewer = await f.connect('rear');
  await f.settings(owner, { enabled: true });
  const pending = f.change(owner); await flush();
  await f.control.registerRole(owner.token, 'rear', owner.csrfToken);
  await f.control.registerRole(viewer.token, 'primary', viewer.csrfToken);
  assert.equal(retired, 0);
  finishResize(); assert.equal((await pending).width, 960); await flush();
  assert.equal(retired, 1);
  assert.equal(f.control.snapshot(viewer.token).canControl, false);
  finishRetire(); await flush();
  assert.equal(f.control.snapshot(viewer.token).canControl, true);
  assert.equal(f.control.snapshot(viewer.token).width, 960);
});

test('a queued resize loses size authority when its owner changes role during the active resize', async t => {
  let finish, calls = 0;
  const f = await fixture(t, { resize: value => { calls++; return new Promise(resolve => { finish = () => resolve(value); }); } });
  const owner = await f.connect('primary'), viewer = await f.connect('rear');
  await f.settings(owner, { enabled: true });
  const first = f.change(owner); await flush();
  const queued = f.control.resize(owner.token, { ...baseline, width: 800, height: 600,
    generation: f.control.snapshot(owner.token).generation }, owner.csrfToken);
  const rejected = assert.rejects(queued, /revoked/);
  await f.control.registerRole(owner.token, 'rear', owner.csrfToken);
  await f.control.registerRole(viewer.token, 'primary', viewer.csrfToken);
  finish(); await first; await rejected; await flush();
  assert.equal(calls, 1);
  assert.equal(f.control.snapshot(viewer.token).canControl, true);
});

test('same-role viewers cannot take a preferred owner during its stream reconnect grace', async t => {
  let retired = 0;
  const f = await fixture(t, { retireAuthority: async () => { retired++; } });
  const owner = await f.connect('rear'), viewer = await f.connect('rear');
  await f.settings(owner, { enabled: true, prioritised: true });
  f.control.disconnect(owner.token, 'display');
  f.control.connect(viewer.token, 'display'); await flush();
  assert.equal(f.control.snapshot(owner.token).canControl, true);
  assert.equal(f.control.snapshot(viewer.token).canControl, false);
  assert.equal(retired, 0);
});
