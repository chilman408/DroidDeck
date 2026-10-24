// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { createRuntimeOwnership, parseUnitState } = require("./runtime-ownership");
const { SERVICES, createRuntimeSupervisor } = require("./runtime-supervisor");
const absent = { load: "not-found", active: "inactive", description: "" };
const live = { pid: 1234, identity: "5:12:33", androidIdentity: "1006:123/391:456", width: 762, height: 720, density: 180,
  nativeLease: "a".repeat(32),
  containerAddress: "192.168.240.112", boot: ["BOOTCLASSPATH=/system/framework/framework.jar",
    "DEX2OATBOOTCLASSPATH=/apex/com.android.art/javalib/core-oj.jar"] };
function fixture(configuration) {
  const units = new Map(), calls = [], environments = []; let health = true, identity = live.identity, android = live.androidIdentity, now = 0;
  const owner = createRuntimeOwnership({ owner: "a".repeat(32), configuration,
    readUnit: async name => units.get(name) || absent,
    identity: async () => identity,
    androidIdentity: async () => android,
    now: () => now,
    ready: async name => { calls.push(["ready", name]); if (!health) throw new Error("not ready"); },
    run: async (file, args, options) => {
      calls.push([file, args]);
      environments.push(options.env);
      if (file === "/usr/bin/systemd-run") {
        const name = args.find(arg => arg.startsWith("--unit=")).slice(7);
        const description = args.find(arg => arg.startsWith("--description=")).slice(14);
        units.set(name, { load: "loaded", active: "active", description });
      } else if (args[0] === "stop") units.delete(args[1]);
    } });
  return { owner, units, calls, environments, failHealth: () => { health = false; }, stale: () => { identity = "5:12:34"; },
    fresh: () => { identity = live.identity; },
    androidRecovered: () => { android = "1007:124/391:456"; }, advance: ms => { now += ms; } };
}

test('portable ownership snapshots configuration and passes it through identity-checked launch', async () => {
  const config = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', port: 9443 };
  const f = fixture(config); config.user = 'root'; config.lanAddress = '0.0.0.0';
  await f.owner.startService('gateway', live);
  const args = f.calls[0][1];
  assert.ok(args.includes('GATEWAY_ORIGIN=https://10.20.30.40:9443'));
  assert.equal(args[args.indexOf('/usr/sbin/runuser') + 2], 'deck');
  assert.equal(f.environments[0].LD_LIBRARY_PATH, '/opt/droiddeck/libgbinder');
  await f.owner.stopService('gateway'); assert.equal(f.units.size, 0);
  const stale = fixture({ user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }); stale.stale();
  await assert.rejects(stale.owner.startService('gateway', live), /identity changed/); assert.equal(stale.calls.length, 0);
  assert.throws(() => fixture({ user: 'root', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }));
});

test("assembled supervisor retries a pre-launch identity race without failed cleanup or stopping foreign workers", async () => {
  const f = fixture();
  const supervisor = createRuntimeSupervisor({ waitReady: async () => {}, pauseTouch: async () => {},
    startService: (name, options) => f.owner.startService(name, live, options),
    stopService: name => f.owner.stopService(name) });
  f.stale();
  await assert.rejects(supervisor.start(), /identity changed before launch/);
  assert.deepEqual(supervisor.snapshot(), { phase: "stopped", recoveryRequired: false });
  assert.deepEqual(f.calls, []);
  f.fresh();
  await supervisor.start();
  assert.equal(supervisor.snapshot().phase, "ready");
  await supervisor.stop();
  assert.equal(f.units.size, 0);
});
test("preflight refuses existing preview workers before mutation, including inactive loaded units", async () => {
  for (const active of ["active", "inactive", "failed", "activating"]) {
    const f = fixture(); f.units.set("tesla-waydroid-api-preview", { load: "loaded", active, description: "manual preview" });
    await assert.rejects(f.owner.assertVacant(), /already exists/); assert.deepEqual(f.calls, []);
  }
});
test("launch validates fresh identity, records owner before acknowledgement and waits for actual readiness", async () => {
  const f = fixture(); await f.owner.assertVacant(); await f.owner.startService("api", live);
  assert.equal(f.calls[0][0], "/usr/bin/systemd-run");
  assert.ok(f.calls[0][1].includes("--description=TeslaAndroid runtime " + "a".repeat(32)));
  assert.ok(f.calls[0][1].includes("--collect")); assert.deepEqual(f.calls[1], ["ready", "api"]);
  await f.owner.stopService("api"); assert.equal(f.units.size, 0);
});
test("failed readiness still permits cleanup of the attempted owned worker", async () => {
  const f = fixture(); f.failHealth(); await assert.rejects(f.owner.startService("api", live), /not ready/);
  await f.owner.stopService("api"); assert.equal(f.units.size, 0);
});

test("owned worker stop can settle within its sixty-second systemd shutdown allowance", async () => {
  let unit = absent, stopBudget;
  const owner = createRuntimeOwnership({ owner: "a".repeat(32),
    readUnit: async () => unit, identity: async () => live.identity,
    androidIdentity: async () => live.androidIdentity, ready: async () => {},
    run: async (file, args, options) => {
      if (file === "/usr/bin/systemd-run") {
        unit = { load: "loaded", active: "active", description: "TeslaAndroid runtime " + "a".repeat(32) };
      } else if (args[0] === "stop") {
        stopBudget = options.timeoutMs ?? 10000;
        // Model systemd taking fifteen seconds to finish its acknowledged stop job.
        if (stopBudget < 15000) throw new Error("Command timed out before worker shutdown settled");
        unit = absent;
      }
    } });
  await owner.startService("api", live);
  await owner.stopService("api");
  assert.ok(stopBudget > 60000 && stopBudget <= 65000);
  assert.equal(unit.load, "not-found");
});
test("stale container identity cannot reach systemd-run", async () => {
  const f = fixture(); f.stale(); await assert.rejects(f.owner.startService("gateway", live), /identity changed/);
  assert.deepEqual(f.calls, []);
});
test("a container restart during readiness cannot acknowledge an old namespace worker", async () => {
  let identity = live.identity; const units = new Map();
  const owner = createRuntimeOwnership({ owner: "b".repeat(32), identity: async () => identity,
    androidIdentity: async () => live.androidIdentity,
    readUnit: async name => units.get(name) || absent,
    ready: async () => { identity = "5:13:44"; },
    run: async (file, args) => {
      if (file === "/usr/bin/systemd-run") units.set("tesla-waydroid-gateway-preview", {
        load: "loaded", active: "active", description: "TeslaAndroid runtime " + "b".repeat(32),
      });
      else if (args[0] === "stop") units.delete(args[1]);
    } });
  await assert.rejects(owner.startService("gateway", live), /identity changed during readiness/);
  await owner.stopService("gateway"); assert.equal(units.size, 0);
});

test("Android recovery invalidates stale workers even when container and units are healthy", async () => {
  const f = fixture(); for (const name of SERVICES) await f.owner.startService(name, live);
  assert.equal(await f.owner.healthy(live), true);
  f.androidRecovered(); assert.equal(await f.owner.healthy(live), false);
  const next = fixture(); next.androidRecovered();
  await assert.rejects(next.owner.startService("api", live), /identity changed/);
  assert.deepEqual(next.calls, []);
});
test("cleanup refuses an unowned unit or a replacement with another description", async () => {
  const f = fixture(); await assert.rejects(f.owner.stopService("api"), /not attempted/);
  await f.owner.startService("api", live); f.units.get("tesla-waydroid-api-preview").description = "another coordinator";
  const count = f.calls.length; await assert.rejects(f.owner.stopService("api"), /ownership changed/);
  assert.equal(f.calls.length, count);
});
test("all workers must retain ownership and active state before public runtime is considered healthy", async () => {
  const f = fixture(); for (const name of SERVICES) await f.owner.startService(name, live);
  assert.equal(await f.owner.healthy(live), true);
  f.units.get("tesla-waydroid-video-preview").active = "failed"; assert.equal(await f.owner.healthy(live), false);
});
test('owned capture auto-restart has a bounded grace period without tearing down other workers', async () => {
  for (const name of ['audio', 'video']) {
    const f = fixture(); for (const service of SERVICES) await f.owner.startService(service, live);
    const unit = f.units.get(`tesla-waydroid-${name}-preview`);
    unit.active = 'activating';
    assert.equal(await f.owner.healthy(live), true);
    f.advance(9999); assert.equal(await f.owner.healthy(live), true);
    f.advance(1); assert.equal(await f.owner.healthy(live), false);
    assert.equal(f.calls.filter(call => call[1]?.[0] === 'stop').length, 0);
  }
});
test('recovered capture clears its grace period; identity, ownership and permanent failures still fail closed', async () => {
  const f = fixture(); for (const service of SERVICES) await f.owner.startService(service, live);
  const audio = f.units.get('tesla-waydroid-audio-preview');
  audio.active = 'activating'; assert.equal(await f.owner.healthy(live), true);
  f.advance(9000); audio.active = 'active'; assert.equal(await f.owner.healthy(live), true);
  audio.active = 'activating'; f.advance(2000); assert.equal(await f.owner.healthy(live), true);
  audio.description = 'foreign'; assert.equal(await f.owner.healthy(live), false);
  audio.description = 'TeslaAndroid runtime ' + 'a'.repeat(32);
  audio.active = 'failed'; assert.equal(await f.owner.healthy(live), false);
  audio.active = 'inactive'; assert.equal(await f.owner.healthy(live), false);
  audio.active = 'active'; f.units.get('tesla-waydroid-gateway-preview').active = 'activating';
  assert.equal(await f.owner.healthy(live), false);
  f.units.get('tesla-waydroid-gateway-preview').active = 'active';
  audio.active = 'activating'; f.androidRecovered(); assert.equal(await f.owner.healthy(live), false);
});
test("unit state parser rejects duplicates, missing fields and unexpected keys", () => {
  assert.deepEqual(parseUnitState("Description=TeslaAndroid runtime abc\nLoadState=loaded\nActiveState=active\n"),
    { load: "loaded", active: "active", description: "TeslaAndroid runtime abc" });
  for (const text of ["LoadState=loaded", "LoadState=loaded\nLoadState=loaded", "ExecStart=kill", "bad"]) {
    assert.throws(() => parseUnitState(text), /unit state/);
  }
});
