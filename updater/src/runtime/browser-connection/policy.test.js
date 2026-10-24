// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { normalizeHostname, normalizeProfile, DEFAULT_ORIGIN } = require('./policy');
test('new installs use the requested default without inventing certificate trust', () => {
  assert.equal(DEFAULT_ORIGIN, 'https://device.teslaandroid.com');
  assert.deepEqual(normalizeProfile({ kind: 'default' }), { version: 1, kind: 'default',
    hostname: 'device.teslaandroid.com', origin: DEFAULT_ORIGIN, certificateManagement: 'local-admin', trustVerified: false });
});
test('hostname policy normalizes case and IDNA but rejects URLs, numeric addresses and delimiters', () => {
  assert.equal(normalizeHostname('DroidDeck.Example.COM'), 'droiddeck.example.com');
  assert.equal(normalizeHostname('b\u00fccher.example'), 'xn--bcher-kva.example');
  for (const name of ['', ' example.com', 'example.com ', 'https://example.com', 'example.com:443',
    'example.com/', 'example.com?x', '*.example.com', 'a_b.example', 'a..example', '-a.example',
    'a-.example', 'example.com.', '127.0.0.1', '127.1', '0x7f.1', 'localhost', '[::1]',
    'a\n.example', 'a'.repeat(64) + '.example', 'a'.repeat(60) + '.' + 'b'.repeat(30) + '.com']) {
    assert.throws(() => normalizeHostname(name), /hostname/i, name);
  }
});
test('proxy profiles never request a proxy private key and provider fields are bounded', () => {
  const value = normalizeProfile({ kind: 'reverse-proxy', hostname: 'DROIDDECK.mondriandsm.synology.me' });
  assert.equal(value.origin, 'https://droiddeck.mondriandsm.synology.me');
  assert.equal(value.certificateManagement, 'reverse-proxy');
  assert.equal(value.trustVerified, false);
  assert.equal(normalizeProfile({ kind: 'duckdns', hostname: 'deck.duckdns.org' }).certificateManagement, 'automatic-dns01');
  assert.equal(normalizeProfile({ kind: 'manual', hostname: 'deck.example.com' }).certificateManagement, 'manual-import');
  for (const value of [null, [], { kind: 'unknown', hostname: 'deck.example.com' },
    { kind: 'duckdns', hostname: 'a.b.duckdns.org' }, { kind: 'duckdns', hostname: 'duckdns.org' },
    { kind: 'duckdns', hostname: 'duckdns.org.evil.example' }, { kind: 'default', hostname: 'evil.example' },
    { kind: 'reverse-proxy', hostname: 'deck.example.com', privateKey: 'secret' },
    { kind: 'duckdns', hostname: 'deck.duckdns.org', token: 'secret' }]) assert.throws(() => normalizeProfile(value));
});
