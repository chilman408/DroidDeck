// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { startGateway } = require('./aggregate-gateway');
const { startWorker } = require('./worker-entry');
const { loadWorkerConfiguration } = require('./worker-config');
const { configurationDigest } = require('./configuration-generation');
const frontendDir = '/var/lib/droidmate-browser/' + 'a'.repeat(64) + '/frontend';
const legacyArgs = ['/opt/teslaandroid-waydroid/frontend', '8443'];
const config = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
function fixture() {
  const calls = [], answer = Symbol('server');
  const original = Object.freeze({ frontendDir: '/opt/droiddeck/frontend', tls: { key: Buffer.from('fixture'), cert: Buffer.from('fixture') },
    auth: {}, targets: {}, accessFile: '/home/deck/.config/droiddeck/browser-access.json', displayControl: {}, audioSettings: {},
    gpsControl: {}, videoSettings: {}, connectionControl: {}, updateControl: {}, retireTouch: () => {}, publicOrigin: 'https://10.20.30.40:8443' });
  return { calls, answer, original, server: {
    main: async options => { calls.push(['main', options]); return options.create(original); },
    createGateway: options => { calls.push(['create', options]); return answer; }
  } };
}
test('legacy uses original admission and changes only frontendDir, preserving TLS and every control object', async () => {
  const f = fixture(); let admitted = false;
  const result = await startGateway({ mode: 'legacy', frontendDir, args: legacyArgs, environment: {}, uid: 1000, server: f.server,
    loadLegacy: async options => { const value = await loadWorkerConfiguration(options); assert.equal(value, undefined); admitted = true; } });
  assert.equal(admitted, true); assert.equal(result, f.answer);
  assert.deepEqual(f.calls.map(x => x[0]), ['main', 'create']);
  assert.deepEqual(f.calls[0][1].args, legacyArgs);
  const delivered = f.calls[1][1]; assert.equal(delivered.frontendDir, frontendDir);
  assert.deepEqual(Object.keys(delivered).sort(), Object.keys(f.original).sort());
  for (const name of Object.keys(f.original).filter(x => x !== 'frontendDir')) assert.equal(delivered[name], f.original[name], name);
  assert.equal(f.original.frontendDir, '/opt/droiddeck/frontend');
});
test('portable keeps real worker UID/account/configuration-generation admission and immutable no-args entry', async () => {
  const f = fixture(); let checked = false;
  const environment = { DROIDDECK_PORTABLE_RUNTIME: '1', DROIDDECK_CONFIGURATION_SHA256: configurationDigest(config) };
  const result = await startGateway({ mode: 'portable', frontendDir, args: [], environment, uid: 1200, server: f.server,
    portable: (name, options) => startWorker(name, { ...options, read: () => config,
      validateAccount: async c => { assert.equal(c.uid, 1200); checked = true; } }) });
  assert.equal(checked, true); assert.equal(result, f.answer);
  assert.equal(f.calls[0][1].configuration.uid, 1200); assert.equal(Object.isFrozen(f.calls[0][1].configuration), true);
  assert.deepEqual(f.calls[0][1].args, []); assert.equal(Object.isFrozen(f.calls[0][1].args), true);
  assert.equal(f.calls[1][1].frontendDir, frontendDir);
});
test('portable account and digest failures never dispatch server or silently try legacy', async () => {
  for (const failure of ['account', 'digest', 'uid']) {
    const f = fixture(); let downgraded = false;
    const environment = { DROIDDECK_PORTABLE_RUNTIME: '1', DROIDDECK_CONFIGURATION_SHA256: failure === 'digest' ? '0'.repeat(64) : configurationDigest(config) };
    await assert.rejects(startGateway({ mode: 'portable', frontendDir, args: [], environment, uid: failure === 'uid' ? 0 : 1200,
      server: f.server, loadLegacy: () => { downgraded = true; throw Error('No downgrade'); }, portable: (name, options) => startWorker(name, {
        ...options, read: () => config, validateAccount: async () => { if (failure === 'account') throw Error('account mismatch'); }
      }) }));
    assert.deepEqual(f.calls, []); assert.equal(downgraded, false);
  }
});
test('malformed frontend paths and mode/argument confusion refuse before any admission or dispatch', async () => {
  let calls = 0; const refused = () => { calls++; throw Error('No dispatch'); };
  for (const bad of [undefined, '', '/', '/tmp/frontend', frontendDir + '/', frontendDir + '/..', frontendDir.replace('/frontend', '/../frontend'),
    frontendDir.replace('a'.repeat(64), 'A'.repeat(64)), frontendDir.replace('/var/', '//var/'), frontendDir + '\n']) {
    await assert.rejects(startGateway({ mode: 'legacy', frontendDir: bad, args: legacyArgs, uid: 1000, environment: {}, portable: refused, loadLegacy: refused, server: {} }));
  }
  assert.equal(calls, 0);
  for (const override of [{ mode: 'other' }, { mode: 'portable', args: legacyArgs }, { args: [] }, { args: [legacyArgs[0], '0'] },
    { args: [legacyArgs[0], '08443'] }, { args: ['../frontend', '8443'] }, { args: [legacyArgs[0], '8443', 'extra'] }, { uid: 0 }]) {
    await assert.rejects(startGateway({ mode: 'legacy', frontendDir, args: legacyArgs, uid: 1000, environment: {}, ...override, portable: refused, loadLegacy: refused, server: {} }));
  }
  assert.equal(calls, 0);
});
test('legacy refuses every portable opt-in or digest, portable requires both exact opt-in and a digest', async () => {
  let calls = 0; const refused = () => { calls++; throw Error('No dispatch'); };
  for (const mode of ['legacy', 'portable']) for (const environment of [{ DROIDDECK_PORTABLE_RUNTIME: '0' },
    { DROIDDECK_PORTABLE_RUNTIME: '' }, { DROIDDECK_CONFIGURATION_SHA256: 'a'.repeat(64) },
    { DROIDDECK_PORTABLE_RUNTIME: '1' }, { DROIDDECK_PORTABLE_RUNTIME: '1', DROIDDECK_CONFIGURATION_SHA256: 'invalid' }]) {
    await assert.rejects(startGateway({ mode, frontendDir, args: mode === 'legacy' ? legacyArgs : [], uid: 1000, environment, portable: refused, loadLegacy: refused, server: {} }));
  }
  await assert.rejects(startGateway({ mode: 'portable', frontendDir, args: [], uid: 1000, environment: {}, portable: refused, server: {} }));
  assert.equal(calls, 0);
});
test('abort and dependency errors propagate unchanged without retry or fallback', async () => {
  const error = Error('cancelled');
  for (const mode of ['portable', 'legacy']) {
    const f = fixture(), signal = AbortSignal.abort(error);
    await assert.rejects(startGateway({ mode, frontendDir, args: mode === 'legacy' ? legacyArgs : [], uid: 1000, signal,
      environment: mode === 'legacy' ? {} : { DROIDDECK_PORTABLE_RUNTIME: '1', DROIDDECK_CONFIGURATION_SHA256: 'a'.repeat(64) },
      server: f.server, portable: () => assert.fail('No dispatch'), loadLegacy: () => assert.fail('No dispatch') }), x => x === error);
    assert.deepEqual(f.calls, []);
  }
  const f = fixture(); f.server.main = async () => { throw error; };
  await assert.rejects(startGateway({ mode: 'legacy', frontendDir, args: legacyArgs, uid: 1000, environment: {}, server: f.server,
    loadLegacy: loadWorkerConfiguration }), x => x === error);
});
test('legacy snapshots original arguments before asynchronous admission can mutate the caller array', async () => {
  const f = fixture(), args = [...legacyArgs];
  await startGateway({ mode: 'legacy', frontendDir, args, uid: 1000, environment: {}, server: f.server,
    loadLegacy: async options => { args[0] = '/foreign'; await loadWorkerConfiguration(options); } });
  assert.deepEqual(f.calls[0][1].args, legacyArgs); assert.equal(Object.isFrozen(f.calls[0][1].args), true);
});

test('abort during asynchronous server initialization prevents factory creation and propagates its exact reason', async () => {
  const f = fixture(), controller = new AbortController(), error = Error('stopped during initialization');
  let entered, release;
  const ready = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  f.server.main = async options => { entered(); await gate; return options.create(f.original); };
  const pending = startGateway({ mode: 'legacy', frontendDir, args: legacyArgs, environment: {}, uid: 1000,
    signal: controller.signal, server: f.server, loadLegacy: loadWorkerConfiguration });
  await ready; controller.abort(error); release();
  await assert.rejects(pending, reason => reason === error); assert.deepEqual(f.calls, []);
});
