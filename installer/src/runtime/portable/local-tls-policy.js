// SPDX-License-Identifier: GPL-3.0-only
const { createSecureContext } = require('node:tls');
const { normalizeRuntimeConfig } = require('./runtime-config');
const { readProtectedRuntimeConfig } = require('./protected-config');
const { parseCertificates, validateKey } = require('../browser-connection/tls-import');

function validateLocalPair(request) {
  if (!request || Object.keys(request).sort().join(',') !== 'certificate,configuration,key') throw Error('Exact local pair required');
  const config = normalizeRuntimeConfig(request.configuration);
  const { certs } = parseCertificates(request.certificate);
  if (certs.length !== 1) throw Error('One local certificate required');
  const leaf = certs[0], now = Date.now();
  const starts = Date.parse(leaf.validFrom), expires = Date.parse(leaf.validTo);
  validateKey(leaf, request.key);
  createSecureContext({ key: request.key, cert: request.certificate, minVersion: 'TLSv1.2' });
  if (leaf.ca || !leaf.checkHost(config.hostname, { subject: 'never', partialWildcards: false }) ||
      !leaf.checkIP(config.lanAddress) || leaf.issuer !== leaf.subject || !leaf.verify(leaf.publicKey) ||
      !leaf.keyUsage?.includes('1.3.6.1.5.5.7.3.1') || !Number.isFinite(starts) ||
      !Number.isFinite(expires) || starts > now || expires - now < 72 * 3600000) {
    throw Error('Current matching self-signed server SAN pair required');
  }
  return { localTransportReady: true, publicFingerprint256: leaf.fingerprint256,
    hostname: config.hostname, lanAddress: config.lanAddress, certificateTrustVerified: false,
    browserTrustVerified: false, trustSetup: 'Explicitly trust this server certificate on each client, or provision a trusted reverse proxy.' };
}

async function main(args) {
  if (args.join(',') === '--configuration') {
    console.log(JSON.stringify(readProtectedRuntimeConfig())); return;
  }
  if (args.join(',') !== '--validate') throw Error('Exact local TLS policy operation required');
  const chunks = []; let size = 0;
  try {
    for await (const chunk of process.stdin) {
      size += chunk.length; if (size > 160 * 1024) throw Error('Bounded local pair required');
      chunks.push(chunk);
    }
    const raw = Buffer.concat(chunks);
    try { console.log(JSON.stringify(validateLocalPair(JSON.parse(raw.toString('utf8'))))); }
    finally { raw.fill(0); }
  } finally { for (const chunk of chunks) chunk.fill(0); }
}
if (require.main === module) main(process.argv.slice(2)).catch(() => {
  console.error('Local TLS policy refused; private key material redacted'); process.exitCode = 1;
});
module.exports = { validateLocalPair };
