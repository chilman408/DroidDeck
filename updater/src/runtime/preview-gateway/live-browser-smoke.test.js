// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

async function browserOptions(address, env = { GATEWAY_AUTH_FILE: "test-password" }) {
  let options;
  let finish;
  const stopped = new Promise((resolve) => { finish = resolve; });
  const sentinel = new Error("stop before navigation");
  const processStub = { argv: ["node", "live-browser-smoke.js", address],
    env };
  // Intercept browser creation so this credential-scope test needs no live VM.
  const dependencies = {
    "playwright-core": { chromium: { launch: async () => ({
      newPage: async (value) => { options = value; throw sentinel; },
      close: async () => {},
    }) } },
    "node:fs": { readFileSync: () => "test-only-password\n" },
    "node:net": net,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "live-browser-smoke.js"), "utf8"), {
    require: (name) => dependencies[name], URL, process: processStub,
    console: { error: (error) => { finish(error); } },
  });
  assert.equal(await stopped, sentinel);
  return options;
}

test("LAN smoke credentials are limited to the exact HTTPS origin", async () => {
  const options = await browserOptions("https://192.168.2.90:8443/");
  assert.equal(options.httpCredentials.origin, "https://192.168.2.90:8443");
  assert.equal(options.httpCredentials.username, "tesla");
  assert.equal(options.httpCredentials.password, "test-only-password");
});

test("localhost smoke does not supply credentials or bypass TLS verification", async () => {
  const options = await browserOptions("http://127.0.0.1:18080/");
  assert.equal(options.httpCredentials, undefined);
  assert.equal(options.ignoreHTTPSErrors, false);
});

test("LAN smoke can view default-off login without a password file", async () => {
  const options = await browserOptions("https://192.168.2.90:8443/", {});
  assert.equal(options.httpCredentials, undefined);
});

test("LAN smoke uses the selected username when login is enabled", async () => {
  const options = await browserOptions("https://192.168.2.90:8443/", {
    GATEWAY_AUTH_FILE: "test-password", GATEWAY_AUTH_USER: "driver",
  });
  assert.equal(options.httpCredentials.username, "driver");
});
