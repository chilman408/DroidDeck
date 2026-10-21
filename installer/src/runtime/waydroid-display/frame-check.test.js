// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { PNG } = require("pngjs");
const { readFrame } = require("./frame-check");

function image(width, height, color = [0, 0, 0, 255]) {
  const png = new PNG({ width, height });
  for (let i = 0; i < png.data.length; i += 4) png.data.set(color, i);
  return PNG.sync.write(png);
}

test("reports actual colored capture dimensions", () => {
  assert.deepEqual(readFrame(image(640, 480, [32, 120, 200, 255])),
    { width: 640, height: 480, nonblack: true });
});
test("black and transparent captures do not pass verification", () => {
  assert.equal(readFrame(image(640, 480)).nonblack, false);
  assert.equal(readFrame(image(32, 32, [255, 255, 255, 0])).nonblack, false);
});
test("rejects damaged, oversized and over-dimension captures", () => {
  assert.throws(() => readFrame(image(640, 480).subarray(0, 60)));
  assert.throws(() => readFrame(Buffer.alloc(16 * 1024 * 1024 + 1)), /size/i);
  assert.throws(() => readFrame(image(1921, 1)), /dimensions/i);
});

module.exports = { image };
