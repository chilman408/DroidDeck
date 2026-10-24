// SPDX-License-Identifier: GPL-3.0-only
// Read-only, emits only player controls and geometry, never account/video titles.
const { createHostAdapter } = require("../runtime/waydroid-display/host-adapter");
const { inspectUi } = require("./android-ui");
const assert = require("node:assert/strict");
const { playerState } = require("./youtube-player-state");
async function main() {
  const args = process.argv.slice(2);
  assert.ok(args.length === 0 || (args.length === 2 && args[0] === "--expect" &&
    ["fullscreen", "watch"].includes(args[1])), "Use --expect fullscreen or --expect watch");
  const adapter = createHostAdapter({ captureMethod: "logical", hostLibs: "/opt/teslaandroid-waydroid/libgbinder" });
  const guest = async args => (await adapter.guest(args)).toString();
  const geometry = await adapter.inspect();
  const nodes = await inspectUi(guest);
  const after = await adapter.inspect();
  assert.equal(after.width, geometry.width, "Display changed during observation");
  assert.equal(after.height, geometry.height, "Display changed during observation");
  const state = playerState(nodes, geometry.width, geometry.height);
  if (args.length) assert.equal(state.layout, args[1], "Unexpected player layout");
  const controls = nodes.filter(node => node.package === "com.google.android.youtube" &&
    (/fullscreen|full_screen|play_pause|player_controls/.test(node["resource-id"] || "") ||
      /^(?:Enter|Exit) full screen$|^(?:Pause|Play|Play video)$/.test(node["content-desc"] || "")))
    .map(node => ({ id: node["resource-id"], bounds: node.bounds, clickable: node.clickable,
      description: /^(?:Enter|Exit) full screen$|^(?:Pause|Play|Play video)$/.test(node["content-desc"] || "") ?
        node["content-desc"] : null }));
  console.log(JSON.stringify({ width: geometry.width, height: geometry.height, density: geometry.density, state, controls,
    packages: [...new Set(nodes.map(node => node.package))],
    playerButtons: nodes.filter(node => node.package === "com.google.android.youtube" && node.clickable === "true")
      .map(node => ({ id: node["resource-id"], bounds: node.bounds })) }));
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { main };
