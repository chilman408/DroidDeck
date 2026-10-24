// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRearDisplaySettings, readRearDisplaySettings, canDriveSize, preferredRole } = require('./rear-display-settings');
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ta-rear-'));
  fs.chmodSync(directory, 0o700);
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const filename = path.join(directory, 'browser-rear-display.json');
  return { filename, settings: createRearDisplaySettings(filename) };
}
test('rear support defaults off and does not create a preference file on read', t => {
  const { filename, settings } = fixture(t);
  assert.deepEqual(settings.snapshot(), { version: 1, revision: 0, enabled: false, prioritised: false });
  assert.equal(fs.existsSync(filename), false);
});
test('rear flags persist atomically, no-op stays at same revision and stale saves fail', t => {
  const { filename, settings } = fixture(t);
  const state = settings.update({ revision: 0, enabled: true, prioritised: false });
  assert.equal(state.revision, 1);
  assert.deepEqual(createRearDisplaySettings(filename).snapshot(), state);
  assert.deepEqual(settings.update({ revision: 1, enabled: true, prioritised: false }), state);
  assert.throws(() => settings.update({ revision: 0, enabled: false, prioritised: false }), error => error.status === 409);
  assert.deepEqual(settings.snapshot(), state);
  assert.equal(fs.readdirSync(path.dirname(filename)).length, 1);
  if (process.platform !== 'win32') assert.equal(fs.statSync(filename).mode & 0o777, 0o600);
});
test('only explicit boolean preferences and an exact bounded revision are accepted', t => {
  const { settings } = fixture(t);
  for (const patch of [{ enabled: 1 }, { prioritised: 'true' }, { revision: -1 },
    { revision: Number.MAX_SAFE_INTEGER }, { role: 'rear' }, { command: 'reboot' }]) {
    assert.throws(() => settings.update({ revision: 0, enabled: false, prioritised: false, ...patch }), error => error.status === 400);
  }
  assert.throws(() => settings.update(null), error => error.status === 400);
});
test('a corrupt persisted profile fails closed without changing bytes', t => {
  const { filename } = fixture(t);
  const invalid = JSON.stringify({ version: 1, revision: 0, enabled: true, prioritised: 'yes' });
  fs.writeFileSync(filename, invalid, { mode: 0o600 });
  assert.throws(() => createRearDisplaySettings(filename), /profile/);
  assert.equal(fs.readFileSync(filename, 'utf8'), invalid);
});
test('a private regular single-link file is required', { skip: process.platform === 'win32' }, t => {
  const { filename, settings } = fixture(t);
  settings.update({ revision: 0, enabled: true, prioritised: true });
  fs.chmodSync(filename, 0o644); assert.throws(() => readRearDisplaySettings(filename), /Unsafe/);
  fs.chmodSync(filename, 0o600);
  fs.linkSync(filename, filename + '.link'); assert.throws(() => readRearDisplaySettings(filename), /Unsafe/);
  fs.unlinkSync(filename + '.link');
  fs.renameSync(filename, filename + '.real'); fs.symlinkSync(filename + '.real', filename);
  assert.throws(() => readRearDisplaySettings(filename));
});
test('oversized state and a symlinked or shared directory are refused', { skip: process.platform === 'win32' }, t => {
  const { filename } = fixture(t);
  fs.writeFileSync(filename, ' '.repeat(1025), { mode: 0o600 });
  assert.throws(() => readRearDisplaySettings(filename), /Unsafe/);
  fs.chmodSync(path.dirname(filename), 0o755);
  assert.throws(() => readRearDisplaySettings(filename), /directory/);
  fs.chmodSync(path.dirname(filename), 0o700);
  const linked = path.dirname(filename) + '-link';
  fs.symlinkSync(path.dirname(filename), linked); t.after(() => fs.unlinkSync(linked));
  assert.throws(() => readRearDisplaySettings(path.join(linked, 'browser-rear-display.json')), /directory/);
});
test('rear priority selects shared sizing role, not a separate Android screen', () => {
  const disabled = { enabled: false, prioritised: true };
  assert.equal(preferredRole(disabled), 'primary');
  assert.equal(canDriveSize(disabled, null), true);
  for (const prioritised of [false, true]) {
    const state = { enabled: true, prioritised };
    assert.equal(preferredRole(state), prioritised ? 'rear' : 'primary');
    assert.equal(canDriveSize(state, null), false);
    assert.equal(canDriveSize(state, 'primary'), !prioritised);
    assert.equal(canDriveSize(state, 'rear'), prioritised);
    assert.throws(() => canDriveSize(state, 'admin'), /role/);
  }
});
