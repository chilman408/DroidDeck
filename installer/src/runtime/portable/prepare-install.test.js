// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { prepare, privatePath, pinnedNpmMetadata, defaults, main } = require('./prepare-install');
const { createHash } = require('node:crypto');

test('all composed production builder adapters are callable', () => {
  const operations = defaults();
  assert.deepEqual(Object.keys(operations).sort(), ['candidate', 'dependency', 'frontend', 'npm', 'reproduce']);
  for (const operation of Object.values(operations)) assert.equal(typeof operation, 'function');
});

test('preparation rejects unsafe paths and invalid command shapes', () => {
  for (const value of ['relative', '/tmp/shared', '/home/../tmp/new', '/']) {
    assert.throws(() => privatePath(value, true));
  }
  for (const args of [[], ['--force'], ['/input', '/output', '--force']]) assert.throws(() => main(args));
});

test('preparation refuses root/non-Linux before reading publisher inputs', () => {
  if (process.platform !== 'linux' || process.getuid?.() < 1000) {
    assert.throws(() => prepare('/absent/input', '/absent/output'), /Unprivileged Linux/);
  }
});

test('npm metadata keeps the exact accepted CRLF receipt, never accepts unknown bytes', () => {
  const expected = Buffer.from('{\r\n  "fixture": true\r\n}\r\n');
  const pin = createHash('sha256').update(expected).digest('hex');
  assert.deepEqual(pinnedNpmMetadata(Buffer.from('{\n  "fixture": true\n}\n'), pin), expected);
  assert.deepEqual(pinnedNpmMetadata(expected, pin), expected);
  assert.throws(() => pinnedNpmMetadata(Buffer.from('{"fixture": false}\n'), pin));
  assert.throws(() => pinnedNpmMetadata(Buffer.from([0xff]), pin));
});
