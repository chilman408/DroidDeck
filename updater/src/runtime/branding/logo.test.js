// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { verifyLogo } = require('./logo');
const { PNG } = require('../waydroid-display/node_modules/pngjs');
const png = fs.readFileSync(path.join(__dirname, 'droiddeck-logo.png'));
const receipt = JSON.parse(fs.readFileSync(path.join(__dirname, 'logo-receipt.json'), 'utf8'));
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-logo-'));
  fs.writeFileSync(path.join(root, receipt.file), png);
  fs.writeFileSync(path.join(root, 'logo-receipt.json'), JSON.stringify(receipt));
  return root;
}
function cleanup(root) {
  assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
  assert.match(path.basename(root), /^droiddeck-logo-/);
  fs.rmSync(root, { recursive: true, force: true });
}
test('selected browser-cast logo is pinned and decodes as transparent RGBA', () => {
  const value = verifyLogo(), image = PNG.sync.read(png, { checkCRC: true });
  assert.equal(value.sha256, hash(png)); assert.equal(png.length, 603649);
  assert.equal(image.width, 1254); assert.equal(image.height, 1254);
  const pixel = (x, y) => Array.from(image.data.subarray((Math.floor(y * image.height) * image.width + Math.floor(x * image.width)) * 4,
    (Math.floor(y * image.height) * image.width + Math.floor(x * image.width)) * 4 + 4));
  assert.equal(pixel(0.05, 0.05)[3], 0);
  assert.equal(pixel(0.4, 0.63)[3], 0); assert.equal(pixel(0.64, 0.63)[3], 0);
  let transparent = 0, green = 0, dark = 0;
  for (let i = 0; i < image.data.length; i += 4) {
    const [r, g, b, a] = image.data.subarray(i, i + 4);
    if (a === 0) transparent++;
    if (a > 200 && g > 100 && g > r * 1.5 && g > b * 1.3) green++;
    if (a > 200 && Math.max(r, g, b) < 65) dark++;
  }
  assert.ok(transparent / (image.width * image.height) > 0.5);
  assert.ok(green > 50000); assert.ok(dark > 50000);
});
test('receipt cannot override filename, product, shape, alpha or supported version', () => {
  const root = fixture();
  try {
    for (const change of [{ file: '../other.png' }, { version: true }, { product: 'Other' }, { width: 4096 },
      { height: 100 }, { transparent: false }, { sha256: [receipt.sha256] }, { extra: true }]) {
      fs.writeFileSync(path.join(root, 'logo-receipt.json'), JSON.stringify({ ...receipt, ...change }));
      assert.throws(() => verifyLogo(root));
    }
  } finally { cleanup(root); }
});
test('changed or oversized logo is rejected without rewriting it', () => {
  const root = fixture();
  try {
    const target = path.join(root, receipt.file);
    fs.appendFileSync(target, 'changed'); assert.throws(() => verifyLogo(root));
    assert.equal(fs.readFileSync(target).subarray(-7).toString(), 'changed');
    fs.writeFileSync(target, Buffer.alloc(1024 * 1024 + 1)); assert.throws(() => verifyLogo(root));
  } finally { cleanup(root); }
});
test('a receipt digest alone cannot approve a wrong PNG signature or non-alpha format', () => {
  const root = fixture();
  try {
    for (const index of [0, 25]) {
      const bad = Buffer.from(png); bad[index] = 2;
      fs.writeFileSync(path.join(root, receipt.file), bad);
      fs.writeFileSync(path.join(root, 'logo-receipt.json'), JSON.stringify({ ...receipt, sha256: hash(bad) }));
      assert.throws(() => verifyLogo(root));
    }
  } finally { cleanup(root); }
});
test('hardlinked asset and oversized receipt fail the existing bounded reader', () => {
  const root = fixture();
  try {
    const target = path.join(root, receipt.file), alias = path.join(root, 'alias.png');
    fs.linkSync(target, alias); assert.throws(() => verifyLogo(root)); fs.unlinkSync(alias);
    fs.writeFileSync(path.join(root, 'logo-receipt.json'), 'x'.repeat(4097));
    assert.throws(() => verifyLogo(root));
  } finally { cleanup(root); }
});
test('logo verification CLI is read-only and has no caller path overrides', () => {
  const command = path.join(__dirname, 'logo.js');
  const output = JSON.parse(execFileSync(process.execPath, [command], { encoding: 'utf8' }));
  assert.equal(output.sha256, receipt.sha256); assert.equal(output.verified, true);
  assert.throws(() => execFileSync(process.execPath, [command, '--replace', 'other'], { stdio: 'pipe' }));
});
