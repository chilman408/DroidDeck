// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
test("GPS opt-in deployment passes the fixed private socket before Node and leaves rollback unchanged", async () => {
  const calls = [], module = { exports: {} };
  const context = vm.createContext({ module, process, console, require(name) {
    if (name === "./host-adapter") return { createHostAdapter: () => ({}), execute: async (file, args) => {
      calls.push({ file, args }); return Buffer.from(file.endsWith("lxc-info") ? "2" : ""); } };
    if (["./touch-adapter", "./session-probe", "../preview-gateway/display-helper-client"].includes(name)) return {};
    return require(name);
  } });
  vm.runInContext(fs.readFileSync(__dirname + "/deploy-preview.js", "utf8"), context);
  await module.exports.startGateway(true, true, true, true);
  const args = calls.find(c => c.file.endsWith("systemd-run")).args;
  const gps = args.indexOf("GATEWAY_GPS_SOCKET=/run/teslaandroid-waydroid/gps.sock");
  assert.ok(gps > 0 && gps < args.indexOf("/usr/bin/node"));
  assert.ok(args.includes("GATEWAY_AUDIO_STREAM=1")); assert.ok(args.includes("GATEWAY_H264_STREAM=1"));
  calls.length = 0; await module.exports.startGateway(true, true, true);
  assert.equal(calls.find(c => c.file.endsWith("systemd-run")).args.some(value => value.startsWith("GATEWAY_GPS_SOCKET=")), false);
});
