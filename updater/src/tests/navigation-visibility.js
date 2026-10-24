// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");

function navigationButtons(nodes, width, height) {
  assert.ok(Number.isInteger(width) && width > 0 && Number.isInteger(height) && height > 0);
  const names = { back: "back", home: "home", recent_apps: "recents", recents: "recents" };
  const result = {};
  for (const node of nodes) {
    if (!["com.android.launcher3", "com.android.systemui"].includes(node.package) ||
        node.clickable !== "true" || node.enabled === "false" || node["visible-to-user"] === "false") continue;
    const name = names[node["resource-id"]?.split("/").at(-1)];
    if (!name) continue;
    const bounds = /^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$/.exec(node.bounds || "");
    if (!bounds) continue;
    const [left, top, right, bottom] = bounds.slice(1).map(Number);
    if (left < 0 || top < 0 || right > width || bottom > height || right <= left || bottom <= top) continue;
    // A successful keyevent is not evidence that a real on-screen button exists.
    result[name] = { bounds: [left, top, right, bottom],
      center: [Math.floor((left + right) / 2), Math.floor((top + bottom) / 2)] };
  }
  return result;
}

function requireNavigationButtons(nodes, width, height) {
  const buttons = navigationButtons(nodes, width, height);
  for (const name of ["back", "home", "recents"]) {
    assert.ok(buttons[name], `No visible in-frame ${name} button at ${width}x${height}`);
  }
  assert.equal(new Set(Object.values(buttons).map(button => button.center.join(","))).size, 3,
    "Navigation buttons overlap at the same touch position");
  return buttons;
}

module.exports = { navigationButtons, requireNavigationButtons };
