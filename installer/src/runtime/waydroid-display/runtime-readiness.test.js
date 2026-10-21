// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { createRuntimeReadiness } = require("./runtime-readiness");
test("readiness probes enter the fresh namespace as the unprivileged user with fixed protected code", async () => {
  const calls = [], ready = createRuntimeReadiness({ run: async (file, args) => { calls.push({ file, args }); return Buffer.from("READY\n"); } });
  const state = { pid: 1234, containerAddress: "192.168.240.112" };
  await ready("api", state); await ready("gateway", state); await ready("lan-relay", state);
  assert.equal(calls[0].file, "/usr/bin/nsenter"); assert.deepEqual(calls[0].args.slice(0, 7), ["-t", "1234", "-n", "/usr/sbin/runuser", "-u", "hilman", "--"]);
  assert.equal(calls[1].args.at(-1), "192.168.240.112");
  assert.equal(calls[2].file, "/usr/sbin/runuser");
  assert.ok(calls.every(call => call.args.includes("/opt/teslaandroid-waydroid/runtime/preview-gateway/runtime-probe.js")));
});
test("helper readiness only inspects capabilities and does not resize or enable GPS", async () => {
  let display = 0, gps = 0;
  const ready = createRuntimeReadiness({ display: { inspect: async () => { display++; return { recoveryRequired: false }; } },
    gps: { inspect: async () => { gps++; return { supported: true, recoveryRequired: false }; } }, run: async () => assert.fail() });
  await ready("display-helper", {}); await ready("gps", {}); assert.equal(display, 1); assert.equal(gps, 1);
});
test("failed health retries are bounded and cancelled callers cannot open native probes", async () => {
  const controller = new AbortController(); let attempts = 0;
  const ready = createRuntimeReadiness({ run: async () => { attempts++; throw new Error("not ready"); },
    sleep: async () => { controller.abort(new Error("cancelled")); } });
  await assert.rejects(ready("api", { pid: 1234 }, { signal: controller.signal }), /cancelled/); assert.equal(attempts, 1);
  await assert.rejects(ready("api", {}, { signal: controller.signal }), /cancelled/); assert.equal(attempts, 1);
  await assert.rejects(ready("sshd", {}), /Unknown/);
});
test('portable readiness uses immutable worker identity and secret-free probe metadata', async () => {
  const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', port: 9443 }, calls = [];
  const ready = createRuntimeReadiness({ configuration, run: async (file, args, options) => { calls.push({ file, args, options }); return Buffer.from('READY\n'); } });
  configuration.user = 'root'; configuration.port = 1;
  await ready('gateway', { pid: 1234, containerAddress: '192.168.240.112' });
  await ready('lan-relay', { pid: 1234, containerAddress: '192.168.240.112' });
  for (const call of calls) {
    assert.ok(call.args.includes('/opt/droiddeck/runtime/preview-gateway/runtime-probe.js'));
    assert.ok(call.args.includes('deck'));
    const record = call.args.find(arg => arg.startsWith('TESLA_WAYDROID_PORTABLE_PROBE_CONFIG=')); assert.ok(record);
    const config = JSON.parse(record.slice(record.indexOf('=') + 1)); assert.equal(config.port, 9443); assert.equal(config.user, 'deck');
    assert.equal(call.options.env.LD_LIBRARY_PATH, '/opt/droiddeck/libgbinder');
    assert.doesNotMatch(JSON.stringify({ args: call.args, libraries: call.options.env.LD_LIBRARY_PATH }), /key\.pem|password|certificatePem|hilman|192\.168\.2\.90/);
  }
  assert.throws(() => createRuntimeReadiness({ configuration }));
});
