// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync } = require('node:child_process');
const { X509Certificate } = require('node:crypto');
const { inspectTlsReadiness, main } = require('./tls-preflight');
const config = { user: 'deck', uid: 1200, gid: 1200, lanAddress: '192.168.2.215', hostname: 'deck.example.com' };
const parent = '/home/deck/.config/droiddeck', keyPath = parent + '/key.pem', certPath = parent + '/cert.pem';
function stat(directory, uid, mode, size = 100) {
  return { dev: 1, ino: 1, mode: (directory ? 0o040000 : 0o100000) | mode,
    uid, gid: uid, nlink: 1, size, mtimeMs: 1, ctimeMs: 1,
    isDirectory: () => directory, isFile: () => !directory };
}
function namespace({ certificate, key } = {}) {
  const nodes = new Map([['/', stat(true, 0, 0o755)], ['/home', stat(true, 0, 0o755)],
    ['/home/deck', stat(true, 1200, 0o755)], ['/home/deck/.config', stat(true, 1200, 0o700)],
    [parent, stat(true, 1200, 0o700)]]);
  if (key !== undefined) nodes.set(keyPath, stat(false, 1200, 0o600, Buffer.byteLength(key)));
  if (certificate !== undefined) nodes.set(certPath, stat(false, 1200, 0o644, Buffer.byteLength(certificate)));
  const reads = [];
  return { nodes, reads, io: { lstatSync(filename) {
    if (!nodes.has(filename)) throw Object.assign(new Error('Missing'), { code: 'ENOENT' });
    return nodes.get(filename);
  } }, read(filename, limit) {
    reads.push(filename); const value = filename === keyPath ? key : certificate;
    assert.ok(Buffer.byteLength(value) <= limit); return Buffer.from(value);
  } };
}
const inspect = f => inspectTlsReadiness(config, f);
test('missing preferences or either TLS file reports missing without reading secret bytes', async () => {
  for (const absent of [parent, keyPath, certPath]) {
    const f = namespace({ key: 'not-read-key', certificate: 'not-read-cert' }); f.nodes.delete(absent);
    const result = await inspect(f);
    assert.equal(result.status, 'missing'); assert.equal(result.localTransportReady, false);
    assert.equal(result.browserReady, false); assert.equal(result.installable, false);
    assert.deepEqual(f.reads, []);
  }
});
test('unsafe directory owners, permissions or symlinks fail before reading', async () => {
  for (const [filename, patch] of [['/', { uid: 1200 }], ['/home', { mode: 0o040777 }],
    ['/home/deck', { uid: 0 }], [parent, { mode: 0o040750 }],
    ['/home/deck/.config', { isDirectory: () => false }], [parent, { mode: 0o040600 }],
    ['/home', { mode: 0o040750 }]]) {
    const f = namespace({ key: 'secret', certificate: 'cert' }); Object.assign(f.nodes.get(filename), patch);
    await assert.rejects(inspect(f), /Unsafe TLS/); assert.deepEqual(f.reads, []);
  }
});
test('unsafe key/certificate owners, links, modes, empty or oversize files fail closed', async () => {
  for (const [filename, patch] of [[keyPath, { uid: 0 }], [keyPath, { mode: 0o100644 }],
    [keyPath, { size: 16385 }], [certPath, { size: 131073 }], [certPath, { mode: 0o100666 }],
    [certPath, { nlink: 2 }], [keyPath, { size: 0 }], [certPath, { isFile: () => false }]]) {
    const f = namespace({ key: 'secret', certificate: 'cert' }); Object.assign(f.nodes.get(filename), patch);
    await assert.rejects(inspect(f), /Unsafe TLS/); assert.deepEqual(f.reads, []);
  }
});
test('permission errors are not reported as missing', async () => {
  const f = namespace(); f.io.lstatSync = () => { throw Object.assign(new Error('private-path'), { code: 'EACCES' }); };
  await assert.rejects(inspect(f), /^Error: TLS metadata unavailable$/);
});
test('directory races on missing-file path are rejected', async () => {
  const f = namespace(), original = f.io.lstatSync; let passes = 0;
  f.io.lstatSync = filename => {
    const info = original(filename);
    if (filename === '/' && ++passes > 1) return { ...info, ino: 2 };
    return info;
  };
  await assert.rejects(inspect(f), /namespace changed/);
});
test('invalid material produces only closed status and no raw errors or key bytes', async () => {
  const f = namespace({ key: 'sensitive-private-key-text', certificate: 'bad-certificate' });
  const result = await inspect(f);
  assert.equal(result.status, 'invalid-pair'); assert.equal(result.localTransportReady, false);
  assert.doesNotMatch(JSON.stringify(result), /sensitive|BEGIN|bad-certificate|\/home|Error/);
  const broken = namespace({ key: 'secret', certificate: 'cert' });
  broken.read = () => { throw new Error('sensitive-content'); };
  assert.equal((await inspect(broken)).status, 'invalid-pair');
});
test('bad configuration and nonfinite clocks fail before IO', async () => {
  for (const now of [NaN, Infinity, -Infinity]) await assert.rejects(inspectTlsReadiness(config, { now }), /clock/);
  await assert.rejects(inspectTlsReadiness({ ...config, keyFile: '/other' }), /schema/);
});
test('CLI rejects override arguments without reading configuration', async () => {
  for (const args of [[], ['inspect', '/override'], ['activate']]) await assert.rejects(main(args), /inspection required/);
});

const openssl = process.platform === 'win32' ? 'C:/Strawberry/c/bin/openssl.exe' : '/usr/bin/openssl';
const available = spawnSync(openssl, ['version']).status === 0;
test('generated local TLS pairs distinguish transport, SAN, expiry and browser trust', { skip: !available }, async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-tls-preflight-'));
  fs.chmodSync(directory, 0o700);
  t.after(() => { assert.ok(directory.startsWith(path.resolve(os.tmpdir()) + path.sep)); fs.rmSync(directory, { recursive: true, force: true }); });
  const run = args => assert.equal(spawnSync(openssl, args, { cwd: directory, encoding: 'utf8',
    timeout: 15000, env: { ...process.env, OPENSSL_CONF: os.devNull } }).status, 0, 'Generated TLS fixture failed');
  run(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'key.pem', '-out', 'cert.pem',
    '-days', '7', '-subj', '/CN=Generated Test Only', '-addext', 'basicConstraints=critical,CA:FALSE',
    '-addext', 'extendedKeyUsage=serverAuth', '-addext', 'subjectAltName=DNS:deck.example.com,IP:192.168.2.215']);
  const material = { key: fs.readFileSync(path.join(directory, 'key.pem'), 'utf8'),
    certificate: fs.readFileSync(path.join(directory, 'cert.pem'), 'utf8') };
  await t.test('self-signed pair can be local transport but never trusted browser acceptance', async () => {
    const result = await inspect(namespace(material));
    assert.equal(result.localTransportReady, true); assert.equal(result.status, 'local-pair-ready');
    assert.equal(result.certificateHostnameMatches, true); assert.equal(result.certificateLanAddressMatches, true);
    assert.equal(result.certificateTrustVerified, false); assert.equal(result.browserReady, false);
    assert.equal(result.proxyRouteVerified, false); assert.equal(result.installable, false);
    assert.doesNotMatch(JSON.stringify(result), /BEGIN|PRIVATE|Generated Test|fingerprint|issuer/);
  });
  await t.test('wrong configured name is not claimed trusted', async () => {
    const f = namespace(material); f.verify = () => { throw new Error('Must not be called'); };
    const result = await inspectTlsReadiness({ ...config, hostname: 'other.example.com' }, f);
    assert.equal(result.localTransportReady, true); assert.equal(result.certificateHostnameMatches, false);
    assert.equal(result.certificateTrustVerified, false);
  });
  await t.test('host-store trust remains distinct from routing/browser acceptance', async () => {
    const f = namespace(material); f.verify = async request => {
      assert.equal(request.hostname, config.hostname);
      return { trustVerified: true, trustScope: 'host-trust-store' };
    };
    const result = await inspect(f);
    assert.equal(result.certificateTrustVerified, true); assert.equal(result.proxyRouteVerified, false);
    assert.equal(result.browserReady, false);
  });
  await t.test('near-expired, expired and not-yet-valid certificates cannot start transport', async () => {
    const cert = new X509Certificate(material.certificate), expiry = Date.parse(cert.validTo);
    for (const [now, status] of [[expiry - 1000, 'near-expiry'], [expiry + 1, 'not-currently-valid'],
      [Date.parse(cert.validFrom) - 1, 'not-currently-valid']]) {
      const result = await inspectTlsReadiness(config, { ...namespace(material), now });
      assert.equal(result.localTransportReady, false); assert.equal(result.status, status);
    }
  });
  await t.test('changed namespace across asynchronous trust verification fails closed', async () => {
    const f = namespace(material); f.verify = async () => { f.nodes.get(keyPath).ino++; return { trustVerified: true }; };
    await assert.rejects(inspect(f), /namespace changed/);
  });
  await t.test('secret buffers are wiped after verification', async () => {
    const f = namespace(material), buffers = [], read = f.read;
    f.read = (...args) => { const buffer = read(...args); buffers.push(buffer); return buffer; };
    await inspect(f); assert.equal(buffers.length, 2);
    assert.ok(buffers.every(buffer => buffer.every(byte => byte === 0)));
  });
  await t.test('mismatched private key is rejected without changing filesystem', async () => {
    run(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-out', 'other.pem']);
    const result = await inspect(namespace({ ...material, key: fs.readFileSync(path.join(directory, 'other.pem'), 'utf8') }));
    assert.equal(result.status, 'invalid-pair'); assert.equal(result.localTransportReady, false);
  });
});
