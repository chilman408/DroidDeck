// SPDX-License-Identifier: GPL-3.0-only
const os = require("node:os");
const net = require("node:net");
const text = value => typeof value === "string" ? value.replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, 160) || null : null;
const bytes = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
function networkAddresses(interfaces) {
  if (!interfaces || typeof interfaces !== "object" || Array.isArray(interfaces)) return Object.freeze([]);
  const result = [];
  for (const [name, addresses] of Object.entries(interfaces).slice(0, 32)) {
    if (!/^[A-Za-z0-9_.:-]{1,32}$/.test(name) || !Array.isArray(addresses)) continue;
    for (const value of addresses.slice(0, 32)) {
      if (!value || value.internal || !["IPv4", 4].includes(value.family) || net.isIP(value.address) !== 4 ||
          /^(?:0|127|22[4-9]|23\d|24\d|25[0-5])\./.test(value.address)) continue;
      if (!result.some(item => item.interface === name && item.address === value.address)) {
        result.push(Object.freeze({ interface: name, address: value.address }));
      }
      if (result.length === 16) return Object.freeze(result);
    }
  }
  return Object.freeze(result);
}
function createDeviceInfoProvider({ system = os, clock = () => performance.now(), lanOrigin = null } = {}) {
  if (lanOrigin !== null) {
    const parsed = new URL(lanOrigin);
    if (parsed.protocol !== "https:" || net.isIP(parsed.hostname) !== 4 || parsed.username || parsed.password ||
        parsed.pathname !== "/" || parsed.search || parsed.hash || parsed.origin !== lanOrigin) throw new Error("Invalid LAN streaming origin");
  }
  let previous, sampledAt;
  return () => {
    const now = clock();
    if (previous && now >= sampledAt && now - sampledAt < 2000) return previous;
    const cpus = system.cpus(), total = bytes(system.totalmem()), free = bytes(system.freemem()), uptime = system.uptime();
    const validCpus = Array.isArray(cpus) && cpus.length > 0 && cpus.length <= 4096;
    previous = Object.freeze({ deviceInfoVersion: 1, device_model: "Waydroid VM", release_type: "development",
      host_name: text(system.hostname()), kernel_release: text(system.release()),
      cpu_model: validCpus ? text(cpus[0]?.model) : null, cpu_cores: validCpus ? cpus.length : null,
      memory_total_bytes: total, memory_free_bytes: total !== null && free !== null && free <= total ? free : null,
      uptime_seconds: Number.isFinite(uptime) && uptime >= 0 && uptime <= Number.MAX_SAFE_INTEGER ? Math.floor(uptime) : null,
      cpu_temperature: null, serial_number: null, is_modem_detected: null, is_carplay_detected: null, ota_url: "",
      networkInfoVersion: 1, network_mode: "Waydroid NAT", lan_stream_origin: lanOrigin,
      android_addresses: networkAddresses(system.networkInterfaces?.()),
      network_controls: Object.freeze({ hotspot: false, offlineRouting: false, telemetryProxy: false, firmwareProxy: false }) });
    sampledAt = now; return previous;
  };
}
module.exports = { createDeviceInfoProvider, networkAddresses };
