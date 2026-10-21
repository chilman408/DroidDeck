// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync } = require('node:child_process');
const { verifyCertificateImport } = require('./tls-import');
const openssl = process.platform === 'win32' ? 'C:/Strawberry/c/bin/openssl.exe' : '/usr/bin/openssl';
const available = spawnSync(openssl, ['version']).status === 0;
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-generated-tls-'));
  fs.chmodSync(dir, 0o700);
  t.after(() => { assert.ok(dir.startsWith(path.resolve(os.tmpdir()) + path.sep)); fs.rmSync(dir, { recursive: true, force: true }); });
  const run = args => {
    const result = spawnSync(openssl, args, { cwd: dir, encoding: 'utf8', timeout: 15000,
      env: { ...process.env, OPENSSL_CONF: os.devNull } });
    assert.equal(result.status, 0, 'Generated TLS fixture command failed');
  };
  run(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.crt', '-days', '30', '-subj', '/CN=DroidDeck Generated Test CA', '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign']);
  run(['req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'leaf.key', '-out', 'leaf.csr', '-subj', '/CN=deck.example.com']);
  // Generated test fixture, never real account or production certificate material.
  fs.writeFileSync(path.join(dir, 'extensions.conf'), 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:deck.example.com\n', { mode: 0o600 });
  run(['x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial', '-days', '7', '-sha256', '-extfile', 'extensions.conf', '-out', 'leaf.crt']);
  run(['pkcs8', '-topk8', '-nocrypt', '-in', 'leaf.key', '-out', 'pkcs8.key']);
  return { dir, run, read: file => fs.readFileSync(path.join(dir, file), 'utf8'),
    options: { openssl, caFile: path.join(dir, 'ca.crt') } };
}
test('manual TLS validates generated SAN/key/chain without returning private material', { skip: !available }, async t => {
  const f = fixture(t), key = f.read('pkcs8.key'), certificate = f.read('leaf.crt');
  const result = await verifyCertificateImport({ hostname: 'deck.example.com', certificatePem: certificate, privateKeyPem: key }, f.options);
  assert.equal(result.hostname, 'deck.example.com'); assert.equal(result.trustVerified, true);
  assert.equal(result.renewal, 'manual'); assert.match(result.fingerprint256, /^[A-F0-9:]+$/);
  assert.equal(result.trustScope, 'host-trust-store');
  assert.doesNotMatch(JSON.stringify(result), /BEGIN|PRIVATE|pkcs8|\.key/);
  assert.ok(result.expiresAt > Date.now() + 72 * 3600000);
});
test('wrong hostname, wrong key, untrusted chain and near expiry never activate', { skip: !available }, async t => {
  const f = fixture(t), request = { hostname: 'deck.example.com', certificatePem: f.read('leaf.crt'), privateKeyPem: f.read('pkcs8.key') };
  await assert.rejects(verifyCertificateImport({ ...request, hostname: 'other.example.com' }, f.options), /SAN/);
  await assert.rejects(verifyCertificateImport({ ...request, privateKeyPem: f.read('ca.key') }, f.options), /key mismatch/);
  f.run(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'other-ca.key', '-out', 'other-ca.crt', '-days', '30', '-subj', '/CN=Unrelated Generated CA']);
  await assert.rejects(verifyCertificateImport(request, { openssl, caFile: path.join(f.dir, 'other-ca.crt') }), /trust verification/);
  const { X509Certificate } = require('node:crypto');
  const expiry = Date.parse(new X509Certificate(request.certificatePem).validTo);
  await assert.rejects(verifyCertificateImport(request, { ...f.options, now: expiry - 3600000 }), /72 hours/);
  for (const extra of [{ certificatePem: request.certificatePem + '\nsecret' }, { privateKeyPem: request.privateKeyPem + '\nsecret' }, { certificatePem: request.certificatePem.repeat(9) }]) {
    await assert.rejects(verifyCertificateImport({ ...request, ...extra }, f.options));
  }
});

test('common-name-only, client-only, self-signed and duplicate chains are rejected', { skip: !available }, async t => {
  const f = fixture(t), key = f.read('pkcs8.key');
  const request = certificatePem => ({ hostname: 'deck.example.com', certificatePem, privateKeyPem: key });
  f.run(['x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-days', '7', '-sha256', '-out', 'cn-only.crt']);
  await assert.rejects(verifyCertificateImport(request(f.read('cn-only.crt')), f.options), /SAN/);
  fs.writeFileSync(path.join(f.dir, 'client.conf'), 'basicConstraints=critical,CA:FALSE\nextendedKeyUsage=clientAuth\nsubjectAltName=DNS:deck.example.com\n', { mode: 0o600 });
  f.run(['x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-days', '7', '-sha256', '-extfile', 'client.conf', '-out', 'client.crt']);
  await assert.rejects(verifyCertificateImport(request(f.read('client.crt')), f.options), /server authentication/);
  f.run(['x509', '-req', '-in', 'leaf.csr', '-signkey', 'pkcs8.key', '-days', '7', '-sha256', '-extfile', 'extensions.conf', '-out', 'self.crt']);
  await assert.rejects(verifyCertificateImport(request(f.read('self.crt')), f.options), /trust verification/);
  await assert.rejects(verifyCertificateImport(request(f.read('leaf.crt').repeat(2)), f.options), /Duplicate/);
  const result = await verifyCertificateImport(request(f.read('leaf.crt') + f.read('ca.crt')), f.options);
  assert.equal(result.trustVerified, true);
});

test('hidden PEM headers and trailing decoded bytes are not valid certificate/key imports', { skip: !available }, async t => {
  const f = fixture(t), request = { hostname: 'deck.example.com', certificatePem: f.read('leaf.crt'), privateKeyPem: f.read('pkcs8.key') };
  await assert.rejects(verifyCertificateImport({ ...request,
    certificatePem: request.certificatePem.replace('-----BEGIN CERTIFICATE-----', '-----BEGIN CERTIFICATE-----\nComment: extra-content\n') }, f.options));
  const append = (pem, label) => {
    const data = pem.replace(new RegExp('-----[^-]+-----', 'g'), '').replace(/\s/g, '');
    const encoded = Buffer.concat([Buffer.from(data, 'base64'), Buffer.from('extra-content')]).toString('base64');
    return '-----BEGIN ' + label + '-----\n' + encoded + '\n-----END ' + label + '-----\n';
  };
  await assert.rejects(verifyCertificateImport({ ...request, privateKeyPem: append(request.privateKeyPem, 'PRIVATE KEY') }, f.options));
  await assert.rejects(verifyCertificateImport({ ...request, certificatePem: append(request.certificatePem, 'CERTIFICATE') }, f.options));
  for (const extra of [{ caFile: 'untrusted' }, { openssl: 'evil' }, { now: 0 }]) await assert.rejects(verifyCertificateImport({ ...request, ...extra }, f.options), /Invalid TLS import/);
});

test('supported EC keys validate while weak RSA, unsupported curves and invalid dates fail', { skip: !available }, async t => {
  const f = fixture(t);
  for (const curve of ['prime256v1', 'secp384r1', 'secp521r1']) {
    f.run(['req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:' + curve, '-nodes', '-keyout', curve + '.key', '-out', curve + '.csr', '-subj', '/CN=deck.example.com']);
    f.run(['x509', '-req', '-in', curve + '.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-days', '7', '-sha256', '-extfile', 'extensions.conf', '-out', curve + '.crt']);
    const request = { hostname: 'deck.example.com', certificatePem: f.read(curve + '.crt'), privateKeyPem: f.read(curve + '.key') };
    if (curve === 'secp521r1') await assert.rejects(verifyCertificateImport(request, f.options), /Unsupported private key strength/);
    else assert.equal((await verifyCertificateImport(request, f.options)).trustVerified, true);
  }
  f.run(['req', '-new', '-newkey', 'rsa:1024', '-nodes', '-keyout', 'weak.key', '-out', 'weak.csr', '-subj', '/CN=deck.example.com']);
  f.run(['x509', '-req', '-in', 'weak.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-days', '7', '-sha256', '-extfile', 'extensions.conf', '-out', 'weak.crt']);
  await assert.rejects(verifyCertificateImport({ hostname: 'deck.example.com', certificatePem: f.read('weak.crt'), privateKeyPem: f.read('weak.key') }, f.options), /Unsupported private key strength/);
  const request = { hostname: 'deck.example.com', certificatePem: f.read('leaf.crt'), privateKeyPem: f.read('pkcs8.key') };
  const cert = new (require('node:crypto').X509Certificate)(request.certificatePem);
  for (const now of [Date.parse(cert.validFrom) - 600000, Date.parse(cert.validTo) + 1000]) {
    await assert.rejects(verifyCertificateImport(request, { ...f.options, now }), /not currently valid/);
  }
});
