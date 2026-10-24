// SPDX-License-Identifier: GPL-3.0-only
// Local-admin import validation only; never expose key material through browser APIs.
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path');
const { X509Certificate, createPrivateKey } = require('node:crypto');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);
const { normalizeHostname } = require('./policy');
function decodePem(block, label) {
  const match = new RegExp('^\\s*-----BEGIN ' + label + '-----([A-Za-z0-9+/=\\r\\n]+)-----END ' + label + '-----\\s*$').exec(block);
  if (!match) throw new Error('Unexpected PEM content');
  const base64 = match[1].replace(/[\r\n]/g, ''), data = Buffer.from(base64, 'base64');
  if (!data.length || data.toString('base64') !== base64) throw new Error('Invalid PEM encoding');
  return data;
}
function parseCertificates(pem) {
  if (typeof pem !== 'string' || Buffer.byteLength(pem) > 128 * 1024) throw new Error('Invalid certificate chain');
  const blocks = pem.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g) || [];
  if (!blocks.length || blocks.length > 8 || pem.replace(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g, '').trim()) {
    throw new Error('Invalid certificate chain');
  }
  let certs;
  try { certs = blocks.map(block => {
    const data = decodePem(block, 'CERTIFICATE'), cert = new X509Certificate(data);
    if (!cert.raw.equals(data)) throw new Error('Trailing certificate data');
    return cert;
  }); }
  catch (_) { throw new Error('Invalid certificate chain'); }
  if (new Set(certs.map(cert => cert.fingerprint256)).size !== certs.length) throw new Error('Duplicate certificate');
  return { blocks, certs };
}
function validateKey(leaf, pem) {
  if (typeof pem !== 'string' || Buffer.byteLength(pem) > 16384 ||
      !/^\s*-----BEGIN PRIVATE KEY-----[A-Za-z0-9+/=\r\n]+-----END PRIVATE KEY-----\s*$/.test(pem)) {
    throw new Error('One unencrypted PKCS8 private key required');
  }
  let key;
  try {
    const data = decodePem(pem, 'PRIVATE KEY');
    key = createPrivateKey({ key: data, format: 'der', type: 'pkcs8' });
    if (!key.export({ format: 'der', type: 'pkcs8' }).equals(data)) throw new Error('Noncanonical PKCS8');
  }
  catch (_) { throw new Error('Invalid private key'); }
  if (!leaf.checkPrivateKey(key)) throw new Error('Certificate key mismatch');
  const details = key.asymmetricKeyDetails;
  if (key.asymmetricKeyType === 'rsa' && ![2048, 3072, 4096].includes(details.modulusLength) ||
      key.asymmetricKeyType === 'ec' && !['prime256v1', 'secp384r1'].includes(details.namedCurve) ||
      !['rsa', 'ec'].includes(key.asymmetricKeyType)) throw new Error('Unsupported private key strength');
}
async function verifyCertificateImport(request, { openssl = '/usr/bin/openssl', caFile, now = Date.now() } = {}) {
  if (!request || typeof request !== 'object' || Array.isArray(request) ||
      Object.keys(request).some(key => !['hostname', 'certificatePem', 'privateKeyPem'].includes(key))) throw new Error('Invalid TLS import');
  const hostname = normalizeHostname(request.hostname), { blocks, certs } = parseCertificates(request.certificatePem), leaf = certs[0];
  if (!Number.isFinite(now)) throw new Error('Invalid certificate clock');
  if (leaf.ca || !leaf.checkHost(hostname, { subject: 'never', partialWildcards: false })) throw new Error('Certificate SAN mismatch');
  if (leaf.keyUsage && !leaf.keyUsage.includes('1.3.6.1.5.5.7.3.1')) throw new Error('Certificate does not permit server authentication');
  const starts = Date.parse(leaf.validFrom), expires = Date.parse(leaf.validTo);
  if (!Number.isFinite(starts) || !Number.isFinite(expires) || starts > now + 300000 || expires <= now) throw new Error('Certificate not currently valid');
  if (expires - now < 72 * 3600000) throw new Error('Certificate needs at least 72 hours remaining');
  validateKey(leaf, request.privateKeyPem);
  for (let index = 0; index + 1 < certs.length; index++) {
    if (!certs[index + 1].ca || !certs[index].checkIssued(certs[index + 1]) || !certs[index].verify(certs[index + 1].publicKey)) {
      throw new Error('Invalid certificate chain order');
    }
  }
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'droiddeck-tls-verify-'));
  try {
    await fs.chmod(dir, 0o700);
    const leafFile = path.join(dir, 'leaf.pem'), chainFile = path.join(dir, 'chain.pem');
    await fs.writeFile(leafFile, blocks[0] + '\n', { flag: 'wx', mode: 0o600 });
    const args = ['verify', '-auth_level', '2', '-purpose', 'sslserver', '-verify_hostname', hostname,
      '-attime', String(Math.floor(now / 1000))];
    // caFile is a local-admin/test adapter option, never part of a browser profile.
    if (caFile) args.push('-CAfile', caFile, '-no-CApath', '-no-CAstore');
    else args.push('-CApath', '/etc/ssl/certs', '-no-CAfile', '-no-CAstore');
    if (blocks.length > 1) {
      await fs.writeFile(chainFile, blocks.slice(1).join('\n') + '\n', { flag: 'wx', mode: 0o600 });
      args.push('-untrusted', chainFile);
    }
    args.push(leafFile);
    try { await execFile(openssl, args, { timeout: 10000, maxBuffer: 16384,
      env: { ...process.env, OPENSSL_CONF: os.devNull } }); }
    catch (_) { throw new Error('Certificate trust verification failed'); }
    return { hostname, fingerprint256: leaf.fingerprint256, issuer: leaf.issuer,
      startsAt: starts, expiresAt: expires, trustVerified: true, trustScope: 'host-trust-store', renewal: 'manual' };
  } finally {
    // Only the newly created verification directory is removed; private keys never enter it.
    if (!dir.startsWith(path.resolve(os.tmpdir()) + path.sep)) throw new Error('Unsafe TLS verification directory');
    await fs.rm(dir, { recursive: true });
  }
}
module.exports = { parseCertificates, validateKey, verifyCertificateImport };
