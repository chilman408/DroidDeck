// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const { navigationButtons } = require("./navigation-visibility");

function playerState(nodes, width, height) {
  const player = nodes.filter(node => node.package === "com.google.android.youtube" &&
    node["resource-id"] === "com.google.android.youtube:id/player_overlays" &&
    node["visible-to-user"] !== "false");
  assert.equal(player.length, 1, "Expected one visible YouTube player");
  const match = /^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$/.exec(player[0].bounds || "");
  assert.ok(match, "Invalid player bounds");
  const bounds = match.slice(1).map(Number);
  const [left, top, right, bottom] = bounds;
  assert.ok(left < right && top < bottom && right <= width && bottom <= height,
    "Player lies outside the current logical frame");
  const navigation = navigationButtons(nodes, width, height);
  const fullFrame = left === 0 && top === 0 && right === width && bottom === height &&
    Object.keys(navigation).length === 0;
  const watch = right - left < width && ["back", "home", "recents"].every(key => navigation[key]);
  return { layout: fullFrame ? "fullscreen" : watch ? "watch" : "unknown", bounds, navigation };
}

module.exports = { playerState };
