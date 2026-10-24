// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { createRuntimeDiscovery, parseContainerAddress, parseProcessStart, readAndroidIdentity } = require("./runtime-discovery");
const boot = "export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /apex/com.android.art/javalib/core-oj.jar\n";
const network = JSON.stringify([{ addr_info: [{}] }, { ifname: "eth0", addr_info: [
  { family: "inet", local: "192.168.240.112", prefixlen: 24, scope: "global" },
] }]);
test('portable discovery snapshots desktop identity and host libraries without weakening container checks', async () => {
  const config = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }, calls = [];
  const discovery = createRuntimeDiscovery({ configuration: config,
    run: async (file, args, options) => {
      calls.push({ file, args, options });
      if (file === '/usr/sbin/runuser') return Buffer.from(JSON.stringify({ type: 'b', data: [true] }));
      assert.equal(file, '/usr/bin/lxc-info'); return Buffer.from(args.at(-1) === '-pH' ? '1234' : 'RUNNING');
    }, readIdentity: async () => '42:99:123', readAndroid: async () => '1006:123/391:456',
    host: { inspect: async () => ({ width: 762, height: 720, density: 180 }), guest: async args => {
      if (args[0] === '/system/bin/getprop') return Buffer.from('1');
      if (args[0] === '/system/bin/ip') return Buffer.from(network);
      return Buffer.from(args.at(-1) === '/proc/sys/kernel/pid_max' ? '65535' : boot);
    } } });
  config.user = 'root'; config.uid = 0;
  const state = await discovery.inspect(); assert.equal(state.pid, 1234);
  const registration = calls[0]; assert.equal(registration.args[1], 'deck');
  assert.ok(registration.args.includes('XDG_RUNTIME_DIR=/run/user/1200'));
  assert.ok(registration.args.includes('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1200/bus'));
  assert.ok(calls.every(call => call.options.env.LD_LIBRARY_PATH === '/opt/droiddeck/libgbinder'));
  assert.throws(() => createRuntimeDiscovery({ configuration: { ...config, user: 'root' } }));
});
function fixture({ pids = ["1234", "1234"], identities = ["42:99:123", "42:99:123"], state = "RUNNING",
  androidIdentities = ["1006:123/391:456", "1006:123/391:456"],
  complete = "1", pidLimit = "65535", ip = network, geometry = { width: 762, height: 720, density: 180 }, inspect } = {}) {
  const calls = [];
  const run = async (file, args, options) => {
    calls.push({ file, args, options });
    assert.equal(file, "/usr/bin/lxc-info");
    return Buffer.from(args.at(-1) === "-pH" ? pids.shift() : state);
  };
  const host = { inspect: inspect || (async () => geometry), guest: async (args, options) => {
    calls.push({ args, options });
    if (args[0] === "/system/bin/getprop") return Buffer.from(complete);
    if (args[0] === "/system/bin/ip") return Buffer.from(ip);
    if (args.at(-1) === "/proc/sys/kernel/pid_max") return Buffer.from(pidLimit);
    return Buffer.from(boot);
  } };
  return { calls, discovery: createRuntimeDiscovery({ run, host, registered: async () => true,
    readAndroid: async () => androidIdentities.shift(),
    readIdentity: async () => identities.shift() }) };
}
test("discovers booted live geometry, classpaths and a structured guest address without mutation", async () => {
  const { discovery, calls } = fixture();
  const state = await discovery.inspect();
  assert.deepEqual(state, { pid: 1234, identity: "42:99:123", androidIdentity: "1006:123/391:456", width: 762, height: 720, density: 180,
    containerAddress: "192.168.240.112", boot: ["BOOTCLASSPATH=/system/framework/framework.jar",
      "DEX2OATBOOTCLASSPATH=/apex/com.android.art/javalib/core-oj.jar"] });
  assert.equal(calls.filter(call => call.args.at(-1) === "-pH").length, 2);
  assert.ok(calls.some(call => call.args.join(" ") === "/system/bin/ip -j -4 addr show dev eth0"));
});

test("rejects Android framework recovery within the same live container", async () => {
  await assert.rejects(fixture({ androidIdentities: ["1006:123/391:456", "1007:124/391:456"] }).discovery.inspect(), /Android identity changed/);
});
test("rejects stopped or frozen containers instead of thawing or starting them", async () => {
  for (const state of ["STOPPED", "FROZEN", "", "RUNNING\nSTOPPED"]) {
    const { discovery, calls } = fixture({ state });
    await assert.rejects(discovery.inspect(), /not running/);
    assert.equal(calls.length, 1);
  }
});
test("rejects malformed container PID before reading a namespace", async () => {
  for (const pid of ["1", "0", "1e3", "12;cmd", "123 456", "9007199254740992"]) {
    await assert.rejects(fixture({ pids: [pid] }).discovery.inspect(), /container PID/);
  }
});
test("rejects unbooted Android before display inspection", async () => {
  let inspected = false;
  const { discovery } = fixture({ complete: "0", inspect: async () => { inspected = true; } });
  await assert.rejects(discovery.inspect(), /not booted/);
  assert.equal(inspected, false);
});

test("rejects an unsafe actual guest PID limit before opening display control", async () => {
  for (const pidLimit of ['4194304', '65536', '0', '1023', '65535\n4194304', 'bad']) {
    let inspected = false;
    const { discovery } = fixture({ pidLimit, inspect: async () => { inspected = true; return { width: 762, height: 720, density: 180 }; } });
    await assert.rejects(discovery.inspect(), /Guest PID limit/);
    assert.equal(inspected, false);
  }
  const { discovery, calls } = fixture({ pidLimit: '32768' });
  await discovery.inspect();
  assert.ok(calls.some(call => call.args.join(' ') === '/system/bin/toybox cat /proc/sys/kernel/pid_max'));
});
test("an absent user session cannot inspect or mutate Android", async () => {
  const discovery = createRuntimeDiscovery({ registered: async () => false, run: async () => assert.fail() });
  await assert.rejects(discovery.inspect(), /not registered/);
});
test("rejects PID changes and same-PID namespace or process reuse during inspection", async () => {
  await assert.rejects(fixture({ pids: ["1234", "2222"] }).discovery.inspect(), /identity changed/);
  await assert.rejects(fixture({ identities: ["42:99:123", "42:99:124"] }).discovery.inspect(), /identity changed/);
});
test("propagates cancellation and passes its signal to native readiness commands", async () => {
  const controller = new AbortController();
  const { discovery, calls } = fixture({ inspect: async () => {
    controller.abort(new Error("cancelled")); return { width: 762, height: 720, density: 180 };
  } });
  await assert.rejects(discovery.inspect({ signal: controller.signal }), /cancelled/);
  assert.ok(calls.every(call => call.options.signal === controller.signal));
  assert.equal(calls.some(call => call.args[0] === "/system/bin/ip"), false);
});
test("rejects ambiguous, off-subnet or malformed addresses and unsafe geometry", async () => {
  for (const ip of ["bad-json", "{}", JSON.stringify([{ ifname: "eth0", addr_info: [
    { family: "inet", local: "192.168.2.90", scope: "global" },
  ] }]), network.replace("192.168.240.112", "192.168.240.255")]) assert.throws(() => parseContainerAddress(ip));
  const value = JSON.parse(network); value[1].addr_info.push({ ...value[1].addr_info[0], local: "192.168.240.113" });
  assert.throws(() => parseContainerAddress(JSON.stringify(value)), /ambiguous/);
  await assert.rejects(fixture({ geometry: { width: 0, height: 720, density: 180 } }).discovery.inspect(), /dimensions/);
});
test("process start identity handles parentheses in comm without accepting incomplete proc stat", () => {
  const stat = "1234 (worker (name)) S " + Array.from({ length: 19 }, (_, i) => i === 18 ? "987654" : "0").join(" ");
  assert.equal(parseProcessStart(stat, 1234), "987654");
  for (const input of ["1234", stat.replace("1234", "9999"), stat.replace("987654", "x")]) {
    assert.throws(() => parseProcessStart(input, 1234), /process identity/);
  }
});

test("Android identity reads exact services in the current guest and includes process birth", async () => {
  const calls = [], options = { signal: new AbortController().signal };
  const actual = await readAndroidIdentity(1234, options, {
    run: async (file, args, passed) => {
      calls.push(args); assert.equal(file, "/usr/bin/nsenter"); assert.equal(passed, options);
      assert.deepEqual(args.slice(0, -1), ["-t", "1234", "-m", "-p", "-r", "--", "/system/bin/toybox", "pidof"]);
      return Buffer.from(args.at(-1) === "system_server" ? "1006" : "391");
    },
    io: { readFile: async (filename, passed) => {
      assert.equal(passed.signal, options.signal);
      const pid = filename.includes("/1006/") ? 1006 : 391;
      assert.equal(filename, `/proc/1234/root/proc/${pid}/stat`);
      return `${pid} (Android worker) S ` + Array(18).fill("0").join(" ") + " 789";
    } },
  });
  assert.equal(actual, "1006:789/391:789");
  assert.equal(calls.length, 2);
});

test("Android identity refuses absent or ambiguous services and already cancelled probes", async () => {
  for (const value of ["", "1", "1006 1007", "../stat", "9007199254740992"]) {
    await assert.rejects(readAndroidIdentity(1234, {}, { run: async () => Buffer.from(value),
      io: { readFile: async () => assert.fail() } }), /identity unavailable/);
  }
  await assert.rejects(readAndroidIdentity(1234, { signal: AbortSignal.abort(new Error("cancelled")) }, {
    run: async () => assert.fail(),
  }), /cancelled/);
});
