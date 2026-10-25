// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { startupFacts } = require('./browser-startup-metadata');

test('startup facts retain only bounded numeric timings and authority flags', () => {
  const value = startupFacts({ displayOpen: 123.456, touchOpen: 987.654, rendered: 150,
    controlGranted: 800, pageErrors: 0, blockedWrites: 2, token: 'private', url: 'private' });
  assert.deepEqual(value, { displayOpenMs: 123, touchOpenMs: 988, firstRenderedMs: 150,
    controlGrantedMs: 800, pictureToTouchOpenMs: 838, pageErrors: 0, blockedWrites: 2 });
  assert.equal(JSON.stringify(value).includes('private'), false);
});

test('absent or invalid readiness never reports responsive input', () => {
  const value = startupFacts({ displayOpen: 10, rendered: 25, touchOpen: undefined, controlGranted: null,
    pageErrors: 0, blockedWrites: 0 });
  assert.equal(value.touchOpenMs, null); assert.equal(value.pictureToTouchOpenMs, null);
  assert.equal(value.controlGrantedMs, null);
  for (const bad of [NaN, Infinity, -1, 60001, '1']) {
    assert.equal(startupFacts({ touchOpen: bad }).touchOpenMs, null);
  }
});
