// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const { windowFacts, compositionFacts, inspectCaptureBoundary } = require('./capture-boundary-probe');
const window = (app, flags, active, extra = '') => `  Window #7 Window{123 u0 ${app}/.MainActivity}:\n    mDisplayId=0\n    mAttrs={(0,0)(fillxfill) sim={adjust=resize}\n      fl=${flags}\n      pfl=NONE}\n    mHasSurface=${active}\n    isOnScreen=${active}\n    isVisible=${active}\n${extra}`;
test('hidden secure windows do not prove the visible app is protected', () => {
  const result = windowFacts(window('com.netflix.mediaclient', 'SECURE HARDWARE_ACCELERATED', false) +
    window('com.amazon.avod.thirdpartyclient', 'HARDWARE_ACCELERATED', true));
  assert.equal(result[0].secure, true); assert.equal(result[0].active, false);
  assert.equal(result[1].secure, false); assert.equal(result[1].active, true);
});
test('flags are limited to attributes, numeric SECURE supported, missing metadata stays unknown', () => {
  assert.equal(windowFacts(window('com.hulu.plus', '0x2000', true))[0].secure, true);
  assert.equal(windowFacts(window('com.hulu.plus', '0x1000', true, '    historical fl=SECURE\n'))[0].secure, false);
  const incomplete = windowFacts('  Window #0 Window{abc u0 com.disney.disneyplus/.Main}:\n    mHasSurface=true\n')[0];
  assert.equal(incomplete.active, false); assert.equal(incomplete.secure, null); assert.equal(incomplete.visible, null);
});
test('protected child layer evidence is separate from window flags and duplicate mirrors', () => {
  const result = compositionFacts('  Layer [42] (Secure) SurfaceView[com.hulu.plus/.Main]#42\n' +
    '  Layer [42 mirrored from 40,] (Secure) SurfaceView[com.hulu.plus/.Main]#42\n' +
    '  Layer [44] com.disney.disneyplus/.Main#44\n' +
    '  Layer [45] (Secure) unrelated.secret/.Account#45\n');
  assert.deepEqual(result.layers, [{ app: 'com.hulu.plus', id: '42', secure: true },
    { app: 'com.disney.disneyplus', id: '44', secure: false }]);
});
test('physical secure output is not mistaken for a secure scrcpy mirror', () => {
  const result = compositionFacts('Display 0 (physical, "Primary display")\n   Composition Display State:\n   isEnabled=true isSecure=true usesDeviceComposition=false\n' +
    'Display 11529215047174513140 (virtual, "scrcpy")\n   Composition Display State:\n   isEnabled=true isSecure=false usesDeviceComposition=false\n');
  assert.deepEqual(result.displays.map(v => [v.capture, v.secure]), [[false, true], [true, false]]);
  assert.equal(result.displays[1].id, '11529215047174513140');
});
test('diagnostic redacts unrelated app titles and bounds dumps', () => {
  assert.deepEqual(windowFacts(window('unrelated.secret', 'SECURE', true)), []);
  assert.throws(() => windowFacts('x'.repeat(4 * 1024 * 1024 + 1)));
  assert.throws(() => compositionFacts(null));
});
test('probe has only fixed read-only commands and fences container identity', async () => {
  const calls = [], run = async (file, args, options) => {
    calls.push({ file, args }); assert.equal(options.timeoutMs, 10000);
    return Buffer.from(file.endsWith('lxc-info') ? '2036\n' : args.includes('windows') ? window('com.netflix.mediaclient', 'SECURE', true) : '');
  };
  const result = await inspectCaptureBoundary(run);
  assert.equal(result.windows[0].active, true); assert.equal(result.protectedBufferStatus, 'unknown');
  assert.equal(calls.length, 4); assert.equal(calls[1].file, '/usr/bin/nsenter');
  assert.deepEqual(calls[1].args.slice(-3), ['dumpsys', 'window', 'windows'].map((v, i) => i === 0 ? '/system/bin/' + v : v));
  let reads = 0;
  await assert.rejects(inspectCaptureBoundary(async file => Buffer.from(file.endsWith('lxc-info') ? String(++reads === 1 ? 2036 : 2037) : '')), /Container changed/);
  await assert.rejects(inspectCaptureBoundary(async () => Buffer.from('1')), /Invalid container/);
});
