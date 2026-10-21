// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { waitForPublicBoot, uptime, readPublicInfo } = require("./live-public-boot-smoke");
const info = value => ({ deviceInfoVersion: 1, uptime_seconds: value });
function fixture(values) {
  let clock = 0;
  return { readInfo: async () => { const value = values.shift(); if (value instanceof Error) throw value; return value || info(1000); },
    sleep: async () => { clock += 2000; }, now: () => clock, timeoutMs: 12000 };
}
test("public boot probe requires downtime and lower fresh host uptime without using SSH", async () => {
  const result = await waitForPublicBoot(fixture([info(1000), info(1002), new Error("down"), info(25)]));
  assert.deepEqual(result, { publicBootRecovered: true, beforeUptime: 1000, afterUptime: 25, sshUsedByProbe: false });
});
test("old/cached host uptime or no observed downtime cannot establish reboot acceptance", async () => {
  for (const values of [[info(1000), info(25)], [info(1000), new Error("down"), info(1001)],
    [info(1000), new Error("down"), info(500)]]) await assert.rejects(waitForPublicBoot(fixture(values)), /did not recover/);
});
test("public boot probe rejects malformed uptime and unbounded deadlines", async () => {
  for (const value of [null, info(-1), info(Infinity), info("2"), { deviceInfoVersion: 2, uptime_seconds: 5 }]) {
    assert.throws(() => uptime(value));
  }
  for (const timeoutMs of [0, -1, 240001, Infinity]) await assert.rejects(waitForPublicBoot({ ...fixture([]), timeoutMs }));
});
test("public probe uses actual Fetch Response semantics, fixed TLS origin and deadline", async () => {
  const request = async (url, options) => {
    assert.equal(url, "https://teslavm.mondriandsm.synology.me/api/deviceInfo");
    assert.equal(options.redirect, "error"); assert.ok(options.signal instanceof AbortSignal);
    return new Response(JSON.stringify(info(20)), { status: 200 });
  };
  assert.deepEqual(await readPublicInfo({ request }), info(20));
  await assert.rejects(readPublicInfo({ request: async () => new Response("Unavailable", { status: 503 }) }));
});
