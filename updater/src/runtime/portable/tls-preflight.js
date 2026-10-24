// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs');
const { createSecureContext } = require('node:tls');
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('./runtime-config');
const { readProtectedRuntimeConfig } = require('./protected-config');
const { readBoundedRegular } = require('./read-bounded');
const { parseCertificates, validateKey, verifyCertificateImport } = require('../browser-connection/tls-import');

function identity(stat) {
  const fields = ['dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtimeMs', 'ctimeMs'];
  if (fields.some(key => !Number.isFinite(stat[key]) || stat[key] < 0)) throw new Error('Unsafe TLS metadata');
  return JSON.stringify(fields.map(key => stat[key]));
}
function metadata(stat, { uid, directory = false, privateMode = false, limit = 0 } = {}) {
  if (!stat || stat.uid !== uid || stat.mode & (privateMode ? 0o077 : 0o022) ||
      !(directory ? stat.isDirectory() && stat.mode & 0o100 :
        stat.isFile() && stat.nlink === 1 && stat.size > 0 && stat.size <= limit && stat.mode & 0o400)) {
    throw new Error('Unsafe TLS metadata');
  }
  return identity(stat);
}
function missing(io, filename) {
  try { return io.lstatSync(filename); }
  catch (error) { if (error.code === 'ENOENT') return null; throw new Error('TLS metadata unavailable'); }
}
async function inspectTlsReadiness(configuration, { io = fs, read = readBoundedRegular,
  verify = verifyCertificateImport, now = Date.now() } = {}) {
  const config = normalizeRuntimeConfig(configuration), layout = deriveRuntimeLayout(config);
  if (!Number.isFinite(now)) throw new Error('Invalid TLS preflight clock');
  const checked = [];
  const result = { kind: 'portable-tls-preflight', installable: false, localTransportReady: false,
    certificateHostnameMatches: false, certificateLanAddressMatches: false,
    certificateTrustVerified: false, proxyRouteVerified: false, browserReady: false, status: 'missing' };
  function remember(filename, options, stat) {
    const expected = metadata(stat, options); checked.push({ filename, options, expected });
  }
  function unchanged() {
    for (const { filename, options, expected } of checked) {
      if (metadata(missing(io, filename), options) !== expected) throw new Error('TLS namespace changed');
    }
  }
  for (const [filename, uid, privateMode] of [['/', 0, false], ['/home', 0, false],
    [config.home, config.uid, false], [config.home + '/.config', config.uid, false],
    [layout.preferences, config.uid, true]]) {
    const stat = missing(io, filename);
    if (!stat) { unchanged(); return Object.freeze(result); }
    remember(filename, { uid, directory: true, privateMode }, stat);
    const access = stat.uid === config.uid ? stat.mode >> 6 : stat.gid === config.gid ? stat.mode >> 3 : stat.mode;
    if (!(access & 1)) throw new Error('Unsafe TLS directory access');
  }
  const files = [[layout.keyFile, true, 16384], [layout.certificateFile, false, 128 * 1024]];
  let absent = false;
  for (const [filename, privateMode, limit] of files) {
    const stat = missing(io, filename);
    if (!stat) { absent = true; continue; }
    remember(filename, { uid: config.uid, privateMode, limit }, stat);
  }
  if (absent) { unchanged(); return Object.freeze(result); }
  let keyBuffer, certificateBuffer;
  try {
    keyBuffer = read(layout.keyFile, 16384); certificateBuffer = read(layout.certificateFile, 128 * 1024);
    unchanged();
    const decode = value => new TextDecoder('utf-8', { fatal: true }).decode(value);
    const privateKeyPem = decode(keyBuffer), certificatePem = decode(certificateBuffer);
    const { certs } = parseCertificates(certificatePem), leaf = certs[0];
    validateKey(leaf, privateKeyPem);
    createSecureContext({ key: privateKeyPem, cert: certificatePem, minVersion: 'TLSv1.2' });
    if (leaf.ca || leaf.keyUsage && !leaf.keyUsage.includes('1.3.6.1.5.5.7.3.1')) throw new Error('Invalid server certificate');
    const starts = Date.parse(leaf.validFrom), expires = Date.parse(leaf.validTo);
    if (!Number.isFinite(starts) || !Number.isFinite(expires) || starts > now || expires <= now) result.status = 'not-currently-valid';
    else if (expires - now < 72 * 3600000) result.status = 'near-expiry';
    else {
      result.localTransportReady = true; result.status = 'local-pair-ready';
      result.certificateHostnameMatches = Boolean(leaf.checkHost(config.hostname, { subject: 'never', partialWildcards: false }));
      result.certificateLanAddressMatches = Boolean(leaf.checkIP(config.lanAddress));
      // A usable backend pair is not evidence of browser trust or a proxy route.
      if (result.certificateHostnameMatches) {
        try {
          const trust = await verify({ hostname: config.hostname, certificatePem, privateKeyPem }, { now });
          result.certificateTrustVerified = trust.trustVerified === true && trust.trustScope === 'host-trust-store';
        } catch (_) { /* A proxy may own the separately trusted public certificate. */ }
      }
    }
  } catch (_) { result.status = 'invalid-pair'; result.localTransportReady = false; }
  finally { keyBuffer?.fill(0); certificateBuffer?.fill(0); }
  unchanged();
  return Object.freeze(result);
}
async function main(args) {
  if (process.platform !== 'linux' || process.getuid() !== 0 || args.length !== 1 || args[0] !== 'inspect') {
    throw new Error('Fixed root Linux TLS inspection required');
  }
  console.log(JSON.stringify(await inspectTlsReadiness(readProtectedRuntimeConfig()), null, 2));
}
if (require.main === module) main(process.argv.slice(2)).catch(() => {
  console.error('Portable TLS preflight rejected'); process.exitCode = 1;
});
module.exports = { inspectTlsReadiness, main };
