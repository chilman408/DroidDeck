// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { buildServiceBundle, verifyServiceBundle, FILES } = require('./service-bundle');
const config = { user: 'deck', uid: 1200, gid: 1200, lanAddress: '10.20.30.40' };
function fixture(t) { const root = fs.mkdtempSync(path.join(os.tmpdir(), 'service-bundle-')); t.after(() => fs.rmSync(root, { recursive: true, force: true })); return root; }
test('builds exact deterministic service plan, never overwrites or starts units', t => {
  const root = fixture(t), a = path.join(root, 'a'), b = path.join(root, 'b');
  const first = buildServiceBundle({ configuration: config, output: a });
  assert.deepEqual(buildServiceBundle({ configuration: config, output: b }), first);
  assert.equal(first.files.length, 7); assert.equal(first.installable, false);
  assert.deepEqual(verifyServiceBundle(a), first);
  assert.throws(() => buildServiceBundle({ configuration: config, output: a }));
});
test('invalid or secret-bearing metadata refuses before output', t => {
  const output = path.join(fixture(t), 'out');
  assert.throws(() => buildServiceBundle({ configuration: { ...config, password: 'generated sentinel' }, output }));
  assert.equal(fs.existsSync(output), false);
});
test('changed unit and extra private file fail verification', t => {
  const output = path.join(fixture(t), 'out'); buildServiceBundle({ configuration: config, output });
  const name = path.join(output, FILES[0]), bytes = fs.readFileSync(name);
  fs.writeFileSync(name, 'wrong'); assert.throws(() => verifyServiceBundle(output)); fs.writeFileSync(name, bytes);
  fs.writeFileSync(path.join(output, 'key.pem'), 'generated sentinel'); assert.throws(() => verifyServiceBundle(output));
});
