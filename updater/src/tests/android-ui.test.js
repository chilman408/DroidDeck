// SPDX-License-Identifier: GPL-3.0-only
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { center, tapPackets } = require("./android-ui");
test("Android drawer test taps measured bounds, not a fixed screen coordinate", () => {
  assert.deepEqual(center({ bounds: "[225,661][279,715]" }), [252, 688]);
  assert.deepEqual(center({ bounds: "[20,30][75,81]" }), [47, 55]);
  for (const bounds of ["", "[1,2][1,2]", "[20,30][10,25]", "[-1,0][20,30]", "[0,0][10,20]x"]) {
    assert.throws(() => center({ bounds }));
  }
});
test("Native drawer tap uses logical touchscreen protocol and releases contact", () => {
  assert.deepEqual(tapPackets(188, 608, 676, 640), [
    "s 0\nT 1\nX 188\nY 608\nS 0\n", "s 0\nT -1\nS 0\n",
  ]);
  for (const args of [[676, 1, 676, 640], [1, 640, 676, 640], [-1, 0, 676, 640], [1.5, 1, 676, 640]]) {
    assert.throws(() => tapPackets(...args));
  }
});
