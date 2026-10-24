// SPDX-License-Identifier: GPL-3.0-only
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { navigationButtons, requireNavigationButtons } = require("./navigation-visibility");

const node = (name, bounds, options = {}) => ({ package: "com.android.launcher3",
  "resource-id": `com.android.launcher3:id/${name}`, bounds, clickable: "true", enabled: "true", ...options });
const bottom = [node("back", "[280,584][334,638]"), node("home", "[375,584][429,638]"),
  node("recent_apps", "[470,584][524,638]")];

test("three real bottom buttons are visible at the user's failing geometry", () => {
  assert.deepEqual(requireNavigationButtons(bottom, 804, 638).home.center, [402, 611]);
});
test("clipped landscape phone buttons cannot pass because keyevents work", () => {
  assert.throws(() => requireNavigationButtons([node("back", "[0,72][54,126]"),
    node("home", "[0,270][54,324]"), node("recent_apps", "[0,510][54,564]")], 804, 54),
  /No visible in-frame back/);
});
test("hidden, disabled, empty, partial and app-imposter buttons do not count", () => {
  for (const item of [node("back", "[0,0][54,54]", { "visible-to-user": "false" }),
    node("back", "[0,0][54,54]", { enabled: "false" }), node("back", "[0,0][0,54]"),
    node("back", "[-1,0][54,54]"), node("back", "[0,0][54,54]", { package: "com.google.android.youtube" })]) {
    assert.deepEqual(navigationButtons([item], 804, 638), {});
  }
});
test("standard SystemUI buttons and distinct touch positions are required", () => {
  const system = bottom.map(item => ({ ...item, package: "com.android.systemui" }));
  assert.ok(requireNavigationButtons(system, 804, 638).recents);
  assert.throws(() => requireNavigationButtons(bottom.map(item => ({ ...item, bounds: "[0,0][54,54]" })),
    804, 638), /overlap/);
});
