// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const test = require("node:test");
const { playerState } = require("./youtube-player-state");
const player = bounds => ({ package: "com.google.android.youtube",
  "resource-id": "com.google.android.youtube:id/player_overlays", bounds });
const navigation = ["back", "home", "recent_apps"].map((name, i) => ({
  package: "com.android.launcher3", "resource-id": `com.android.launcher3:id/${name}`,
  bounds: `[${403 + i * 76},619][${453 + i * 76},669]`, clickable: "true" }));

test("full-frame player is fullscreen, not generic fullscreen windowing mode", () => {
  assert.equal(playerState([player("[0,0][852,676]")], 852, 676).layout, "fullscreen");
  assert.equal(playerState([player("[0,0][852,676]"), ...navigation], 852, 676).layout, "unknown");
});
test("watch screen requires a smaller player and all three visible navigation buttons", () => {
  assert.equal(playerState([player("[0,32][553,343]"), ...navigation], 852, 676).layout, "watch");
  assert.equal(playerState([player("[0,32][553,343]")], 852, 676).layout, "unknown");
});
test("ambiguous and out-of-frame observations fail instead of accepting a transition", () => {
  assert.throws(() => playerState([], 852, 676));
  assert.throws(() => playerState([player("[0,0][852,676]"), player("[0,32][553,343]")], 852, 676));
  assert.throws(() => playerState([player("[0,0][960,720]")], 852, 676));
});
