// SPDX-License-Identifier: GPL-3.0-only
// Read-only: no resize, keyevent, touch, app launch, or preference write.
const assert = require("node:assert/strict");
const { createHostAdapter } = require("../runtime/waydroid-display/host-adapter");
const { readFrame } = require("../runtime/waydroid-display/frame-check");
const { inspectUi } = require("./android-ui");
const { navigationButtons, requireNavigationButtons } = require("./navigation-visibility");

async function smoke() {
  const adapter = createHostAdapter({ captureMethod: "logical", hostLibs: "/opt/teslaandroid-waydroid/libgbinder" });
  const guest = async args => (await adapter.guest(args)).toString();
  const before = await adapter.inspect();
  const mode = (await guest(["/system/bin/cmd", "settings", "get", "secure", "navigation_mode"])).trim();
  assert.equal(mode, "0", "Three-button navigation not selected");
  const nodes = await inspectUi(guest);
  const frame = readFrame(await adapter.capture());
  const after = await adapter.inspect();
  assert.equal(after.width, before.width, "Display changed during observation");
  assert.equal(after.height, before.height, "Display changed during observation");
  assert.equal(frame.width, before.width);
  assert.equal(frame.height, before.height);
  assert.equal(frame.nonblack, true);
  console.log(JSON.stringify({ width: frame.width, height: frame.height, density: before.density,
    selectedThreeButton: true, inFrameButtons: navigationButtons(nodes, frame.width, frame.height) }));
  return requireNavigationButtons(nodes, frame.width, frame.height);
}
if (require.main === module) smoke().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { smoke };
