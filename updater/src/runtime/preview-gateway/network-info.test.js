// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { networkAddresses, createDeviceInfoProvider } = require("./device-info");
const item = (address, patch = {}) => ({ address, family: "IPv4", internal: false, ...patch });
const system = { hostname: () => "vm", release: () => "kernel", uptime: () => 1,
  cpus: () => [], totalmem: () => 100, freemem: () => 10 };
test("network metadata uses the current namespace IPv4 without MACs or physical host identities", () => {
  const info = createDeviceInfoProvider({ system: { ...system, networkInterfaces: () => ({
    lo: [item("127.0.0.1", { internal: true })], eth0: [item("192.168.240.112", { mac: "private" })] }) },
    lanOrigin: "https://192.168.2.90:8443" })();
  assert.equal(info.networkInfoVersion, 1); assert.equal(info.network_mode, "Waydroid NAT");
  assert.equal(info.lan_stream_origin, "https://192.168.2.90:8443");
  assert.deepEqual(info.android_addresses, [{ interface: "eth0", address: "192.168.240.112" }]);
  assert.equal(JSON.stringify(info).includes("private"), false);
  assert.deepEqual(info.network_controls, { hotspot: false, offlineRouting: false, telemetryProxy: false, firmwareProxy: false });
  assert.ok(Object.isFrozen(info.android_addresses) && Object.isFrozen(info.android_addresses[0]));
});
test("address metadata rejects malformed entries, loopback, multicast, IPv6 and duplicate aliases", () => {
  const interfaces = { "invalid\nname": [item("192.168.2.8")], bad: null,
    eth0: [null, item("192.168.240.112"), item("192.168.240.112"), item("::1", { family: "IPv6" }),
      item("0.0.0.0"), item("127.0.0.2"), item("224.0.0.1"), item("255.255.255.255"), item("injected")],
    eth1: [item("10.0.0.2", { family: 4 })] };
  assert.deepEqual(networkAddresses(interfaces), [{ interface: "eth0", address: "192.168.240.112" }, { interface: "eth1", address: "10.0.0.2" }]);
  assert.deepEqual(networkAddresses(null), []); assert.deepEqual(networkAddresses([]), []);
});
test("diagnostic interface and address counts are bounded", () => {
  const entries = Array.from({ length: 100 }, (_, i) => ["eth" + i, Array.from({ length: 100 }, (_, n) => item("10.0." + i + "." + (n + 1)))]);
  assert.equal(networkAddresses(Object.fromEntries(entries)).length, 16);
});
test("LAN endpoint metadata accepts only an exact fixed HTTPS IPv4 origin", () => {
  for (const lanOrigin of ["http://192.168.2.90", "https://example.com", "https://192.168.2.90/path",
    "https://user:password@192.168.2.90", "https://192.168.2.90?bad", "https://192.168.2.90/"]) {
    assert.throws(() => createDeviceInfoProvider({ system, lanOrigin }), /origin/);
  }
  assert.equal(createDeviceInfoProvider({ system })().lan_stream_origin, null);
});
test("network samples refresh with the existing two-second diagnostics cache", () => {
  let now = 0, calls = 0;
  const provider = createDeviceInfoProvider({ clock: () => now, system: { ...system,
    networkInterfaces: () => { calls++; return { eth0: [item(calls === 1 ? "192.168.240.112" : "192.168.240.113")] }; } } });
  assert.equal(provider().android_addresses[0].address, "192.168.240.112");
  now = 1999; assert.equal(provider().android_addresses[0].address, "192.168.240.112");
  now = 2000; assert.equal(provider().android_addresses[0].address, "192.168.240.113");
});
