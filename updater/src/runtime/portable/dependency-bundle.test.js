// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { inspectDependencyStage } = require('./dependency-receipt');
const { PINS, buildDependencyBundle, verifyDependencyBundle } = require('./dependency-bundle');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dependency-bundle-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'input'), output = path.join(root, 'output');
  fs.mkdirSync(path.join(source, 'node_modules/pngjs'), { recursive: true });
  const m = { name: 'teslaandroid-waydroid-display', version: '0.1.0', private: true, license: 'GPL-3.0-only', dependencies: { pngjs: '7.0.0' } };
  const lock = { name: m.name, version: m.version, lockfileVersion: 3, packages: {
    '': { name: m.name, version: m.version, dependencies: m.dependencies },
    'node_modules/pngjs': { version: '7.0.0', integrity: 'sha512-' + Buffer.alloc(64, 1).toString('base64'),
      resolved: 'https://registry.npmjs.org/pngjs/-/pngjs-7.0.0.tgz', license: 'MIT' } } };
  fs.writeFileSync(path.join(source, 'package.json'), JSON.stringify(m));
  fs.writeFileSync(path.join(source, 'package-lock.json'), JSON.stringify(lock));
  for (const [name, bytes] of Object.entries({ 'package.json': JSON.stringify({ name: 'pngjs', version: '7.0.0' }),
    LICENSE: 'generated license fixture', 'index.js': 'generated non-executed script' })) fs.writeFileSync(path.join(source, 'node_modules/pngjs', name), bytes);
  const r = inspectDependencyStage('display', source), pins = { display: Object.fromEntries(['manifestSha256', 'lockSha256', 'filesSha256'].map(k => [k, r[k]])) };
  return { root, role: 'display', source, output, pins };
}
test('production pins retain independently rebuilt gateway/display inventories', () => {
  assert.equal(PINS.gateway.filesSha256, '36a31e5d8046a012b1ff78716102347144af36523f4463db2669e8122b554d98');
  assert.equal(PINS.display.filesSha256, 'b4a6884fea6fd759584daeefa1316d25e9ea539b0a0f6be0bd04610fa6a3be88');
});
test('copies exact production bytes and licenses without install, scripts or overwrites', t => {
  const f = fixture(t); const m = buildDependencyBundle(f);
  assert.equal(m.files.length, 3); assert.equal(m.installable, false);
  assert.deepEqual(verifyDependencyBundle(f.output, { pins: f.pins }), m);
  assert.equal(fs.readFileSync(path.join(f.output, 'pngjs/LICENSE'), 'utf8'), 'generated license fixture');
  assert.throws(() => buildDependencyBundle(f));
});
test('wrong pins and private extras refuse before creating output', t => {
  const f = fixture(t);
  assert.throws(() => buildDependencyBundle({ ...f, pins: PINS })); assert.equal(fs.existsSync(f.output), false);
  fs.writeFileSync(path.join(f.source, 'node_modules/key.pem'), 'generated sentinel');
  assert.throws(() => buildDependencyBundle(f)); assert.equal(fs.existsSync(f.output), false);
});
test('verifier rejects modified bytes, extra files and hardlinks', t => {
  const f = fixture(t); buildDependencyBundle(f);
  const filename = path.join(f.output, 'pngjs/index.js'), before = fs.readFileSync(filename);
  fs.writeFileSync(filename, 'wrong'); assert.throws(() => verifyDependencyBundle(f.output, { pins: f.pins }));
  fs.writeFileSync(filename, before); fs.linkSync(filename, path.join(f.root, 'link'));
  assert.throws(() => verifyDependencyBundle(f.output, { pins: f.pins }));
});
