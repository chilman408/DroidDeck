// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { calculateCalibration } = require("./touch-geometry");
const size = { width: 960, height: 640 };
const viewport = { logical: [0, 0, 960, 640], physical: [64, 0, 1216, 768], device: [1280, 768], rotation: 0 };
test("native pre-mapping maps logical capture corners through the letterboxed physical viewport", () => {
  const matrix = calculateCalibration(size, viewport);
  assert.deepEqual(matrix, [0.9, 0, 48, 0, 1, 0]);
  // Logical wire -> pre-mapped raw -> InputReader physical -> WindowManager logical.
  for (const [x, y] of [[0, 0], [959, 639], [480, 320]]) {
    const physicalX = (matrix[0] * x + matrix[2]) * 1280 / 960;
    const physicalY = (matrix[4] * y + matrix[5]) * 768 / 640;
    assert.ok(Math.abs((physicalX - 64) * 960 / 1152 - x) < 0.0001);
    assert.ok(Math.abs(physicalY * 640 / 768 - y) < 0.0001);
  }
});
test("portrait viewport compensation preserves center and corners", () => {
  assert.deepEqual(calculateCalibration({ width: 640, height: 960 }, {
    logical: [0, 0, 640, 960], physical: [384, 0, 896, 768], device: [1280, 768], rotation: 0,
  }), [0.4, 0, 192, 0, 1, 0]);
});
test("mismatching logical, inactive, rotated or invalid physical viewport is rejected", () => {
  for (const change of [{ rotation: 1 }, { logical: [0, 0, 1280, 768] }, { physical: [0, 0, 1281, 768] },
    { physical: [64, 0, 64, 768] }, { device: [0, 768] }]) {
    assert.throws(() => calculateCalibration(size, { ...viewport, ...change }), /viewport/i);
  }
});
