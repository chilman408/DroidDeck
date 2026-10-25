// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { deploymentProfile } = require("../tools/deploy-rear-display");
test("deployment profiles select only fixed reviewed code lists and private backup locations", () => {
  const rear = deploymentProfile(["node", "script"]), network = deploymentProfile(["node", "script", "--network"]);
  assert.equal(rear.runtime.length + rear.web.length, 12);
  assert.deepEqual(network.runtime, ["preview-gateway/device-info.js", "preview-gateway/server.js"]);
  assert.equal(network.runtime.length + network.web.length, 4);
  assert.equal(network.stage, "/home/hilman/teslaandroid-waydroid/network-deploy-stage-20261009");
  assert.equal(network.backup, "/var/lib/teslaandroid-waydroid/backups/network-info-20261009");
  for (const profile of [rear, network]) for (const filename of [...profile.runtime, ...profile.web]) {
    assert.equal(filename.includes(".."), false); assert.ok(!filename.endsWith(".json"));
  }
});
test("arbitrary deployment paths, extra arguments and malformed selectors cannot select targets", () => {
  for (const argv of [[], ["node"], ["node", "script", "--network", "/tmp"], ["node", "script", "/tmp"],
    ["node", "script", "--rear"], ["node", "script", "--network;shutdown"], ["node", "script", null]]) {
    assert.throws(() => deploymentProfile(argv), /fixed/);
  }
});
