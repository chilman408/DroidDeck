// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { generatedVideoPixels, generatedMotion } = require('./generated-video-pixels');
function frame(red, green, blue) {
  const data = Buffer.alloc(96 * 96 * 4);
  for (let i = 0; i < data.length; i += 4) data.set([red, green, blue, 255], i);
  return { width: 96, height: 96, data };
}
test('black and gray screens are not generated colorful video', () => {
  for (const value of [0, 100, 255]) assert.equal(generatedVideoPixels(frame(value, value, value)).colorfulRatio, 0);
});
test('different generated colors have distinct signatures without storing pixels', () => {
  const red = generatedVideoPixels(frame(200, 10, 10)), green = generatedVideoPixels(frame(10, 200, 10));
  assert.equal(red.colorfulRatio, 1); assert.equal(green.colorfulRatio, 1);
  assert.notEqual(red.signature, green.signature);
  assert.deepEqual(Object.keys(red), ['colorfulRatio', 'signature']);
});
test('invalid pixel sizes are rejected', () => {
  assert.throws(() => generatedVideoPixels({ width: 96, height: 96, data: Buffer.alloc(1) }));
});
test('compression noise does not count as meaningful video motion', () => {
  const before = frame(200, 10, 10), after = frame(202, 12, 12);
  assert.notEqual(generatedVideoPixels(before).signature, generatedVideoPixels(after).signature);
  assert.equal(generatedMotion(before, after).moving, false);
});
test('moving generated colors pass without exporting pixels', () => {
  const facts = generatedMotion(frame(200, 10, 10), frame(10, 200, 10));
  assert.equal(facts.moving, true);
  assert.equal(facts.changedRatio, 1);
  assert.deepEqual(Object.keys(facts), ['changedRatio', 'meanDifference', 'moving']);
});
test('a sparse changed pixel or geometry transition is not a video-motion pass', () => {
  const before = frame(200, 10, 10), after = frame(200, 10, 10);
  after.data.set([0, 255, 0, 255], (30 * 96 + 30) * 4);
  assert.equal(generatedMotion(before, after).moving, false);
  assert.throws(() => generatedMotion(before, { width: 100, height: 96, data: Buffer.alloc(100 * 96 * 4) }));
});
