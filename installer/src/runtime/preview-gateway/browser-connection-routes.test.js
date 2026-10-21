// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { createGateway } = require('./server');
const { createDisplayControl } = require('./display-control');
const { createProxyStore } = require('../browser-connection/proxy-store');
test('browser connection changes require owner, exact Origin, CSRF and bounded profile', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-connection-route-')); fs.chmodSync(directory, 0o700);
  fs.writeFileSync(path.join(directory, 'index.html'), 'test');
  const store = createProxyStore(path.join(directory, 'profile.json'), { initialHostname: 'old.example' });
  const display = createDisplayControl({ helper: { inspect: async () => ({ width: 976, height: 720, density: 180, resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } });
  await display.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: display, connectionControl: store });
  await new Promise(resolve => gateway.listen(0, '127.0.0.1', resolve));
  t.after(async () => { await new Promise(resolve => gateway.close(resolve)); fs.rmSync(directory, { recursive: true, force: true }); });
  const base = 'http://127.0.0.1:' + gateway.address().port;
  const owner = display.createSession(), viewer = display.createSession(); display.connect(owner.token, 'display');
  const post = (payload, session = owner, extra = {}, suffix = '') => fetch(base + '/api/browserConnection' + suffix, { method: 'POST',
    headers: { Origin: base, 'Content-Type': 'application/json', 'X-Display-Session': session.token, 'X-Display-CSRF': session.csrfToken, ...extra }, body: typeof payload === 'string' ? payload : JSON.stringify(payload) });
  const get = await fetch(base + '/api/browserConnection', { headers: { 'X-Display-Session': viewer.token } });
  assert.equal(get.status, 200); assert.equal((await get.json()).origin, 'https://old.example');
  const payload = { revision: 0, hostname: 'new.example' };
  assert.equal((await post(payload, viewer)).status, 403);
  assert.equal((await post(payload, owner, { Origin: 'https://evil.example' })).status, 403);
  assert.equal((await post(payload, owner, { 'X-Display-CSRF': 'wrong' })).status, 403);
  assert.equal((await post(payload, owner, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await post('x'.repeat(513))).status, 413);
  assert.equal((await post(payload, owner, {}, '?x=1')).status, 400);
  assert.equal((await post({ ...payload, privateKey: 'secret' })).status, 400);
  assert.equal(store.snapshot().revision, 0);
  assert.equal((await post(payload)).status, 200);
  assert.equal(store.snapshot().previousOrigin, 'https://old.example');
  assert.equal((await post(payload)).status, 409);
  assert.equal((await fetch(base + '/api/browserConnection', { headers: { 'X-Display-Session': viewer.token, Origin: 'https://evil.example' } })).status, 403);
  display.revokeAll(); assert.equal((await post({ ...payload, revision: 1 })).status, 403);
});

test('new proxy hostname becomes accepted without dropping old hostname or allowing forged Origin', async t => {
  const { execFileSync } = require('node:child_process'), https = require('node:https');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-alias-route-')); fs.chmodSync(directory, 0o700);
  fs.writeFileSync(path.join(directory, 'index.html'), 'test');
  const openssl = process.platform === 'win32' ? 'C:/Strawberry/c/bin/openssl.exe' : '/usr/bin/openssl';
  execFileSync(openssl, ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
    '-keyout', path.join(directory, 'key.pem'), '-out', path.join(directory, 'cert.pem')], { stdio: 'ignore', env: { ...process.env, OPENSSL_CONF: os.devNull } });
  const store = createProxyStore(path.join(directory, 'profile.json'), { initialHostname: 'old.example' });
  const display = createDisplayControl({ helper: { inspect: async () => ({ width: 976, height: 720, density: 180, resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } }); await display.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: display, connectionControl: store,
    publicOrigin: 'https://192.168.2.90:8443', proxyOrigin: 'https://old.example',
    tls: { key: fs.readFileSync(path.join(directory, 'key.pem')), cert: fs.readFileSync(path.join(directory, 'cert.pem')) } });
  await new Promise(resolve => gateway.listen(0, '127.0.0.1', resolve));
  t.after(async () => { await new Promise(resolve => gateway.close(resolve)); fs.rmSync(directory, { recursive: true, force: true }); });
  const viewer = display.createSession();
  const get = (host, origin = 'https://' + host) => new Promise((resolve, reject) => {
    https.get({ host: '127.0.0.1', port: gateway.address().port, path: '/api/browserConnection', rejectUnauthorized: false,
      headers: { Host: host, Origin: origin, 'X-Display-Session': viewer.token } }, response => { response.resume(); response.on('end', () => resolve(response.statusCode)); }).on('error', reject);
  });
  assert.equal(await get('new.example'), 403);
  assert.equal(await get('old.example'), 200);
  store.update({ revision: 0, hostname: 'new.example' });
  assert.equal(await get('new.example'), 200); assert.equal(await get('old.example'), 200);
  assert.equal(await get('new.example', 'https://evil.example'), 403);
  assert.equal(await get('evil.example', 'https://new.example'), 403);
});
