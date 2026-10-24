// SPDX-License-Identifier: GPL-3.0-only
// Private helper protocol: stdin/stdout are captured by release-signer.ps1.
// Never invoke its generate mode in a recorded console or export its response.
const c = require('node:crypto');
const sha = raw => c.createHash('sha256').update(raw).digest('hex');
const canon = value => JSON.stringify(value && typeof value === 'object' && !Array.isArray(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, JSON.parse(canon(value[key]))])) : value) + '\n';
const AAD = Buffer.from('DroidDeck release key backup v1\n');
function exact(value, names) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join(',') !== [...names].sort().join(',')) throw Error('Closed crypto record required');
}
function hex(value, bytes) {
  if (typeof value !== 'string' || !new RegExp(`^[a-f0-9]{${bytes * 2}}$`).test(value)) throw Error('Bounded canonical hexadecimal required');
  return Buffer.from(value, 'hex');
}
function publicKey(key) {
  const raw = c.createPublicKey(key).export({ format: 'der', type: 'spki' });
  if (raw.length !== 44 || raw.subarray(0, 12).toString('hex') !== '302a300506032b6570032100') throw Error('Ed25519 only');
  return { publicKeySpkiHex: raw.toString('hex'), keySha256: sha(raw) };
}
function open(raw) {
  if (!Buffer.isBuffer(raw) || raw.length !== 48) throw Error('Bounded Ed25519 PKCS8 required');
  return c.createPrivateKey({ key: raw, format: 'der', type: 'pkcs8' });
}
function selfTest(key) {
  const message = Buffer.from('DroidDeck signing key initialization self-test\n');
  const signature = c.sign(null, message, key);
  if (signature.length !== 64 || !c.verify(null, message, c.createPublicKey(key), signature)) throw Error('Signing self-test failed');
}
function derive(passphrase, salt) {
  if (typeof passphrase !== 'string' || passphrase.length < 20 || passphrase.length > 1024) throw Error('Backup passphrase must be 20 to 1024 characters');
  const raw = Buffer.from(passphrase);
  try { return c.scryptSync(raw, salt, 32, { N: 131072, r: 8, p: 1, maxmem: 256 * 1024 * 1024 }); }
  finally { raw.fill(0); }
}
function backup(raw, passphrase) {
  const key = open(raw), identity = publicKey(key), salt = c.randomBytes(32), iv = c.randomBytes(12);
  const wrapping = derive(passphrase, salt);
  try {
    const cipher = c.createCipheriv('aes-256-gcm', wrapping, iv);
    cipher.setAAD(Buffer.concat([AAD, Buffer.from(identity.keySha256)]));
    const encrypted = Buffer.concat([cipher.update(raw), cipher.final()]);
    return { schema: 1, kind: 'droiddeck-release-key-backup', algorithm: 'aes-256-gcm',
      kdf: { name: 'scrypt', N: 131072, r: 8, p: 1, saltHex: salt.toString('hex') },
      ivHex: iv.toString('hex'), ciphertextHex: encrypted.toString('hex'), tagHex: cipher.getAuthTag().toString('hex'), ...identity };
  } finally { wrapping.fill(0); }
}
function verifyBackup(value, passphrase, expected) {
  exact(value, ['schema', 'kind', 'algorithm', 'kdf', 'ivHex', 'ciphertextHex', 'tagHex', 'publicKeySpkiHex', 'keySha256']);
  exact(value.kdf, ['name', 'N', 'r', 'p', 'saltHex']);
  if (value.schema !== 1 || value.kind !== 'droiddeck-release-key-backup' || value.algorithm !== 'aes-256-gcm' ||
      value.kdf.name !== 'scrypt' || value.kdf.N !== 131072 || value.kdf.r !== 8 || value.kdf.p !== 1 ||
      value.keySha256 !== expected || sha(hex(value.publicKeySpkiHex, 44)) !== value.keySha256) throw Error('Original fixed backup policy required');
  const salt = hex(value.kdf.saltHex, 32), iv = hex(value.ivHex, 12), tag = hex(value.tagHex, 16), encrypted = hex(value.ciphertextHex, 48);
  const wrapping = derive(passphrase, salt); let raw;
  try {
    const decipher = c.createDecipheriv('aes-256-gcm', wrapping, iv);
    decipher.setAAD(Buffer.concat([AAD, Buffer.from(value.keySha256)])); decipher.setAuthTag(tag);
    raw = Buffer.concat([decipher.update(encrypted), decipher.final()]);
    const key = open(raw), identity = publicKey(key);
    if (identity.publicKeySpkiHex !== value.publicKeySpkiHex) throw Error('Backup public key differs');
    selfTest(key); return { ...identity, backupRecoveryVerified: true };
  } finally { wrapping.fill(0); raw?.fill(0); }
}
function sign(raw, statement, expected) {
  const key = open(raw), identity = publicKey(key);
  if (!Buffer.isBuffer(statement) || statement.length < 1 || statement.length > 8192 || sha(statement) !== expected) throw Error('Exact reviewed statement required');
  const value = JSON.parse(statement);
  exact(value, ['schema', 'kind', 'channel', 'keySha256', 'bundleSha256', 'manifestSha256', 'base', 'target', 'platform', 'dataPolicy']);
  if (value.schema !== 1 || value.kind !== 'droiddeck-runtime-release' || value.channel !== 'pilot' ||
      value.dataPolicy !== 'preserve' || value.keySha256 !== identity.keySha256 || !Buffer.from(canon(value)).equals(statement)) throw Error('Closed canonical release domain required');
  const signature = c.sign(null, statement, key);
  if (!c.verify(null, statement, c.createPublicKey(key), signature)) throw Error('Release signature failed verification');
  return { ...identity, signature: signature.toString('base64'), statementSha256: expected, releaseSigned: true };
}
function operation(request) {
  if (!request || !['generate', 'verify', 'sign', 'backup', 'verifyBackup'].includes(request.mode)) throw Error('Fixed signing operation required');
  if (request.mode === 'verifyBackup') return verifyBackup(request.backup, request.passphrase, request.expectedKeySha256);
  const pair = request.mode === 'generate' ? c.generateKeyPairSync('ed25519') : null;
  const raw = pair ? pair.privateKey.export({ format: 'der', type: 'pkcs8' }) : Buffer.from(request.private, 'base64');
  try {
    const key = open(raw); selfTest(key);
    const result = { ...publicKey(key), selfTest: true };
    if (pair) result.private = raw.toString('base64');
    if (request.mode === 'sign') return { ...result, ...sign(raw, Buffer.from(request.statement, 'base64'), request.expectedStatementSha256) };
    if (request.mode === 'backup') {
      const encrypted = backup(raw, request.passphrase);
      return { ...result, backup: encrypted, ...verifyBackup(encrypted, request.passphrase, result.keySha256) };
    }
    return result;
  } finally { raw.fill(0); }
}
if (require.main === module) {
  let chunks = [], size = 0;
  process.stdin.on('data', chunk => { size += chunk.length; if (size > 16384) process.exit(1); chunks.push(chunk); });
  process.stdin.on('end', () => {
    try { const raw = Buffer.concat(chunks), result = operation(JSON.parse(raw)); raw.fill(0); chunks = []; process.stdout.write(JSON.stringify(result)); }
    catch { process.stderr.write('Signing/backup operation refused'); process.exitCode = 1; }
  });
}
module.exports = { operation, backup, verifyBackup, sign, canon };
