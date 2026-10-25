// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error("Set TA_FRONTEND_DIR to the patched frontend");
function fixture() {
  const window = { location: { origin: "https://teslavm.mondriandsm.synology.me" } };
  const context = vm.createContext({ window, console });
  for (const file of ["js/core/shared.js", "js/settings/settings-controller.js"]) vm.runInContext(fs.readFileSync(path.join(frontend, file), "utf8"), context);
  const tiles = [], errors = [], settings = Object.assign(Object.create(window.TAHtml.SettingsController.prototype), {
    _renderToken: 1, activeSectionId: "network", _appendLoadingState() {}, _appendTile: tile => tiles.push(tile),
    _createTrailingText: text => text, _renderErrorState: error => errors.push(error),
    displayController: { remoteDisplayState: { displayControlSupported: 1 } },
    content: { innerHTML: "", dataset: {} }, banner: {}, bannerText: {},
    apiClient: { fetchSystemConfiguration: async () => ({}), fetchDeviceInfo: async () => ({
      networkInfoVersion: 1, lan_stream_origin: "https://192.168.2.90:8443", network_mode: "Waydroid NAT",
      android_addresses: [{ interface: "eth0", address: "192.168.240.112" }] }) } });
  return { settings, tiles, errors };
}
test("VM Network settings show actual browser/LAN/NAT facts without nonfunctional Pi controls", async () => {
  const f = fixture(); await f.settings._renderNetworkSection(1);
  assert.equal(f.tiles.find(row => row.title === "Browser connection").trailing, "https://teslavm.mondriandsm.synology.me");
  assert.equal(f.tiles.find(row => row.title === "LAN streaming").trailing, "https://192.168.2.90:8443");
  assert.equal(f.tiles.find(row => row.title === "Android IPv4 (eth0)").trailing, "192.168.240.112");
  assert.equal(f.tiles.some(row => /hotspot|routing|proxy/i.test(row.title)), false);
  assert.equal(f.tiles.length, 4);
  assert.equal(f.settings.content.dataset.vmNetworkInfo, "1"); assert.equal(f.settings.banner.hidden, true);
  assert.equal(f.settings.networkPendingConfig, null); assert.deepEqual(f.errors, []);
  assert.equal(f.tiles.every(row => row.dense === false), true);
});

test("VM Network facts do not depend on legacy Wi-Fi configuration", async () => {
  const f = fixture(); let requests = 0;
  f.settings.apiClient.fetchSystemConfiguration = async () => { requests++; throw new Error('Pi Wi-Fi unavailable'); };
  await f.settings._renderNetworkSection(1);
  assert.equal(requests, 0); assert.equal(f.tiles.length, 4); assert.deepEqual(f.errors, []);
});
test("late Network details cannot repaint a different Settings section", async () => {
  const f = fixture(); f.settings.apiClient.fetchDeviceInfo = async () => { f.settings.activeSectionId = "audio"; return { networkInfoVersion: 1 }; };
  await f.settings._renderNetworkSection(1); assert.equal(f.tiles.length, 0); assert.deepEqual(f.errors, []);
});
test("missing capability or failed diagnostics stays unavailable rather than exposing dead write controls", async () => {
  for (const fetchDeviceInfo of [async () => ({}), async () => { throw new Error("failed"); }]) {
    const f = fixture(); f.settings.apiClient.fetchDeviceInfo = fetchDeviceInfo;
    await f.settings._renderNetworkSection(1); assert.equal(f.tiles.length, 0);
    assert.deepEqual(f.errors, ["Network details unavailable"]);
  }
});
