// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), c = require('node:crypto');
const { operation, backup, verifyBackup, sign, canon } = require('./release-signer-crypto');
const pair = c.generateKeyPairSync('ed25519');
const raw = pair.privateKey.export({ format: 'der', type: 'pkcs8' });
const pub = pair.publicKey.export({ format: 'der', type: 'spki' });
const sha = raw => c.createHash('sha256').update(raw).digest('hex'), pin = sha(pub);
const passphrase = 'fixture passphrase only, not used for real custody';
const value = { schema: 1, kind: 'droiddeck-runtime-release', channel: 'pilot', keySha256: pin,
  bundleSha256: 'a'.repeat(64), manifestSha256: 'b'.repeat(64),
  base: { version: '0.1.0-rc.2', sourceSha256: 'c'.repeat(64) },
  target: { version: '0.1.0-rc.3', sourceRevision: 'd'.repeat(40), sourceSha256: 'e'.repeat(64) },
  platform: { distribution: 'ubuntu', version: '26.04', architecture: 'x86_64' }, dataPolicy: 'preserve' };
const statement = Buffer.from(canon(value));
let encrypted;
test('real fixture Ed25519 signature binds exact canonical statement', () => {
  const result = sign(raw, statement, sha(statement)), signature = Buffer.from(result.signature, 'base64');
  assert.equal(result.releaseSigned, true); assert.equal(result.keySha256, pin); assert.equal(signature.length, 64);
  assert.equal(c.verify(null, statement, pair.publicKey, signature), true);
  assert.equal(c.verify(null, Buffer.concat([statement, Buffer.from(' ')]), pair.publicKey, signature), false);
  assert.equal(Object.hasOwn(result, 'private'), false);
});
test('signing refuses wrong domain/key/hash, duplicate/noncanonical JSON and unknown fields', () => {
  for (const changes of [{ kind: 'other' }, { channel: 'stable' }, { keySha256: 'f'.repeat(64) },
    { dataPolicy: 'erase' }, { extra: true }, { schema: true }]) {
    const changed = Buffer.from(canon({ ...value, ...changes }));
    assert.throws(() => sign(raw, changed, sha(changed)));
  }
  for (const changed of [statement.subarray(0, -1), Buffer.from(JSON.stringify(value)),
    Buffer.from(statement.toString().replace('"schema":1', '"schema":1,"schema":1'))]) {
    assert.throws(() => sign(raw, changed, sha(changed)));
  }
  assert.throws(() => sign(raw, statement, 'f'.repeat(64)));
});
test('portable backup decrypts and signs without Windows DPAPI or plaintext private export', () => {
  encrypted = backup(raw, passphrase);
  const result = verifyBackup(encrypted, passphrase, pin);
  assert.equal(result.backupRecoveryVerified, true); assert.equal(result.publicKeySpkiHex, pub.toString('hex'));
  assert.equal(Object.hasOwn(result, 'private'), false);
  assert.equal(JSON.stringify(encrypted).includes(raw.toString('base64')), false);
  assert.notEqual(encrypted.ciphertextHex, raw.toString('hex'));
});
test('each backup has an independent random salt and nonce', () => {
  const next = backup(raw, passphrase);
  assert.notEqual(next.ivHex, encrypted.ivHex); assert.notEqual(next.kdf.saltHex, encrypted.kdf.saltHex);
  assert.notEqual(next.ciphertextHex, encrypted.ciphertextHex);
});
test('wrong password, ciphertext, nonce, tag, salt and identity fail authentication', () => {
  assert.throws(() => verifyBackup(encrypted, passphrase + 'wrong', pin));
  for (const field of ['ivHex', 'ciphertextHex', 'tagHex', 'publicKeySpkiHex']) {
    const changed = structuredClone(encrypted); changed[field] = (changed[field][0] === '0' ? '1' : '0') + changed[field].slice(1);
    assert.throws(() => verifyBackup(changed, passphrase, pin));
  }
  const changed = structuredClone(encrypted); changed.kdf.saltHex = '00'.repeat(32);
  assert.throws(() => verifyBackup(changed, passphrase, pin));
  assert.throws(() => verifyBackup(encrypted, passphrase, 'f'.repeat(64)));
});
test('downgraded/excessive KDF, malformed bounds, unknown fields and short passwords refuse', () => {
  for (const changes of [{ schema: true }, { extra: true }, { algorithm: 'aes-128-cbc' },
    { tagHex: '00' }, { ciphertextHex: '00'.repeat(49) }, { ivHex: 'FF'.repeat(12) },
    { kdf: { ...encrypted.kdf, N: 1024 } }, { kdf: { ...encrypted.kdf, N: 2 ** 30 } },
    { kdf: { ...encrypted.kdf, extra: true } }]) {
    assert.throws(() => verifyBackup({ ...encrypted, ...changes }, passphrase, pin));
  }
  for (const password of ['', 'short', 'x'.repeat(1025)]) assert.throws(() => backup(raw, password));
});
test('private helper has fixed modes and does not export existing or recovered private keys', () => {
  const verified = operation({ mode: 'verify', private: raw.toString('base64') });
  assert.equal(verified.selfTest, true); assert.equal(verified.keySha256, pin); assert.equal(Object.hasOwn(verified, 'private'), false);
  assert.throws(() => operation({ mode: 'shell', private: raw.toString('base64') }));
  assert.throws(() => operation({ mode: 'verify', private: '' }));
  const recovered = operation({ mode: 'verifyBackup', backup: encrypted, passphrase, expectedKeySha256: pin });
  assert.equal(recovered.backupRecoveryVerified, true); assert.equal(Object.hasOwn(recovered, 'private'), false);
});
test.after(() => raw.fill(0));
