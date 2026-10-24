// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { captureLayout } = require('./capture-layout');
const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', home: '/home/display' };
test('portable capture layout shares configured identity, library and preference roots', () => {
  const result = captureLayout(configuration);
  assert.equal(result.root, '/opt/droiddeck'); assert.equal(result.hostLibraries, '/opt/droiddeck/libgbinder');
  assert.equal(result.user, 'deck'); assert.equal(result.uid, 1200);
  assert.equal(result.videoSettingsFile, '/home/display/.config/droiddeck/browser-video.json');
  assert.equal(result.audioSettingsFile, '/home/display/.config/droiddeck/browser-audio.json');
  assert.deepEqual(result.userEnvironment, ['XDG_RUNTIME_DIR=/run/user/1200', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1200/bus']);
  assert.equal(Object.isFrozen(result), true); assert.equal(Object.isFrozen(result.userEnvironment), true);
  assert.throws(() => captureLayout({ ...configuration, password: 'secret' }));
});
test('absent configuration preserves the original capture paths and desktop identity', () => {
  const result = captureLayout();
  assert.equal(result.root, '/opt/teslaandroid-waydroid'); assert.equal(result.user, 'hilman'); assert.equal(result.uid, 1000);
  assert.equal(result.videoSettingsFile, '/home/hilman/.config/teslaandroid-waydroid/browser-video.json');
  assert.equal(result.audioSettingsFile, '/home/hilman/.config/teslaandroid-waydroid/browser-audio.json');
});
