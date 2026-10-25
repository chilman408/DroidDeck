// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync } = require('node:child_process');
let verifier;
try { verifier = require('./scrcpy-input'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
function available() { assert.ok(verifier, 'Scrcpy publisher verifier is not implemented'); }
test('scrcpy input metadata fixes exact publisher server and runtime name', () => {
  available(); const plan = verifier.scrcpyInputPlan();
  assert.equal(Object.isFrozen(plan), true); assert.equal(plan.version, '4.0'); assert.equal(plan.assetId, 418648068);
  assert.equal(plan.filename, 'scrcpy-server-v4.0'); assert.equal(plan.runtimeFilename, 'scrcpy-server-v4.0.jar');
  assert.equal(plan.size, 732226); assert.equal(plan.sha256, '84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a');
  assert.equal(plan.url, 'https://github.com/Genymobile/scrcpy/releases/download/v4.0/scrcpy-server-v4.0');
});
test('scrcpy CLI rejects extra private arguments without echo', () => {
  available(); const result = spawnSync(process.execPath, [path.join(__dirname, 'scrcpy-input.js'), 'verify', 'private-value', 'extra'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.equal(result.stderr.trim(), 'External scrcpy input rejected');
});
test('scrcpy wrong size, correct-sized ZIP-looking bytes and hardlinks reject without modification', () => {
  available(); const stem = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-scrcpy-input-')), filename = path.join(stem, 'server');
  try {
    fs.writeFileSync(filename, 'wrong bytes'); assert.throws(() => verifier.verifyScrcpyInput(filename));
    const bytes = Buffer.alloc(732226); bytes.set([0x50, 0x4b, 3, 4]); fs.writeFileSync(filename, bytes);
    assert.throws(() => verifier.verifyScrcpyInput(filename)); assert.deepEqual(fs.readFileSync(filename), bytes);
    fs.linkSync(filename, path.join(stem, 'alias')); assert.throws(() => verifier.verifyScrcpyInput(filename));
  } finally { fs.rmSync(stem, { recursive: true }); }
});
test('scrcpy verifier rejects missing, relative and noncanonical paths', () => {
  available(); assert.throws(() => verifier.verifyScrcpyInput('relative-server'));
  const stem = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-scrcpy-path-'));
  try {
    const filename = path.join(stem, 'missing'); assert.throws(() => verifier.verifyScrcpyInput(filename));
    assert.throws(() => verifier.verifyScrcpyInput(stem + path.sep + 'sub' + path.sep + '..' + path.sep + 'server'));
  } finally { fs.rmSync(stem, { recursive: true }); }
});
test('Linux scrcpy linked file and parent ancestry reject', { skip: process.platform !== 'linux' }, () => {
  available(); const stem = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-scrcpy-link-')); fs.writeFileSync(path.join(stem, 'server'), 'wrong bytes');
  try {
    fs.symlinkSync(path.join(stem, 'server'), path.join(stem, 'linked')); assert.throws(() => verifier.verifyScrcpyInput(path.join(stem, 'linked')));
    fs.symlinkSync(stem, stem + '-linked'); assert.throws(() => verifier.verifyScrcpyInput(path.join(stem + '-linked', 'server')));
  } finally { fs.unlinkSync(stem + '-linked'); fs.rmSync(stem, { recursive: true }); }
});
test('actual publisher scrcpy asset validates with no deployment or runtime claim', { skip: !process.env.DROIDDECK_SCRCPY_TEST_SERVER }, () => {
  available(); const filename = process.env.DROIDDECK_SCRCPY_TEST_SERVER, before = fs.statSync(filename);
  const receipt = verifier.verifyScrcpyInput(filename); assert.equal(receipt.kind, 'external-scrcpy-server-receipt');
  assert.equal(receipt.installable, false); assert.equal(receipt.installed, false); assert.equal(receipt.downloaded, false);
  assert.equal(receipt.androidOperationsExecuted, false); assert.equal(receipt.signatureVerified, false); assert.equal(receipt.zipFormatVerified, false);
  assert.equal(receipt.publisherPinMatched, true); assert.equal(receipt.size, 732226); assert.equal(receipt.sha256, verifier.scrcpyInputPlan().sha256);
  const after = fs.statSync(filename); assert.equal(after.size, before.size); assert.equal(after.mtimeMs, before.mtimeMs); assert.equal(after.mode, before.mode);
  assert.equal(JSON.stringify(receipt).includes(filename), false);
  const cli = spawnSync(process.execPath, [path.join(__dirname, 'scrcpy-input.js'), 'verify', filename], { encoding: 'utf8', timeout: 10000 });
  assert.equal(cli.status, 0); assert.deepEqual(JSON.parse(cli.stdout), receipt); assert.equal(cli.stderr, '');
});

test('Linux file replacement during bounded read rejects generation before digest mismatch', { skip: process.platform !== 'linux' }, () => {
  available(); const stem = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-scrcpy-replace-')), filename = path.join(stem, 'server');
  const bytes = Buffer.alloc(732226); bytes.set([0x50, 0x4b, 3, 4]); fs.writeFileSync(filename, bytes);
  const originalRead = fs.readSync; let changed = false;
  try {
    fs.readSync = (...args) => {
      const count = originalRead(...args);
      if (!changed) { changed = true; fs.renameSync(filename, filename + '-original'); fs.writeFileSync(filename, bytes); }
      return count;
    };
    assert.throws(() => verifier.verifyScrcpyInput(filename), /File changed|Input generation changed/);
    assert.equal(changed, true);
  } finally { fs.readSync = originalRead; fs.rmSync(stem, { recursive: true }); }
});
