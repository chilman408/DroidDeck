// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
test("audio preview env precedes Node, while video-only rollback omits audio", async () => {
  const calls = [], module = { exports: {} };
  const context = vm.createContext({ module, process, console, require(name) {
    if (name === "./host-adapter") return { createHostAdapter: () => ({}), execute: async (file, args) => {
      calls.push({ file, args }); return Buffer.from(file.endsWith("lxc-info") ? "2" : ""); } };
    if (["./touch-adapter", "./session-probe", "../preview-gateway/display-helper-client"].includes(name)) return {};
    return require(name);
  } });
  vm.runInContext(fs.readFileSync(__dirname + "/deploy-preview.js", "utf8"), context);
  await module.exports.startGateway(true, true, true);
  const audio = calls.find(c => c.file.endsWith("systemd-run")).args;
  assert.ok(audio.indexOf("GATEWAY_AUDIO_STREAM=1") > 0 && audio.indexOf("GATEWAY_AUDIO_STREAM=1") < audio.indexOf("/usr/bin/node"));
  calls.length = 0; await module.exports.startGateway(true, true);
  assert.equal(calls.find(c => c.file.endsWith("systemd-run")).args.includes("GATEWAY_AUDIO_STREAM=1"), false);
});
