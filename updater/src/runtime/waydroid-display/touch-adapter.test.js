// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { verifyInputReader, createTouchAdapter, displayViewport } = require("./touch-adapter");

const dump = `  Device 8: TeslaAndroid x86 Touchscreen
    Sources: TOUCHSCREEN
    Motion Ranges:
      X: source=TOUCHSCREEN, min=0.000, max=959.000
      Y: source=TOUCHSCREEN, min=0.000, max=639.000
    Touch Input Mapper (mode - DIRECT):
      AssociatedDisplay: isExternal=false, displayId=''
      EnableForInactiveViewport: false
      Raw Touch Axes:
        X: min=0, max=959, flat=0
        Y: min=0, max=639, flat=0
      Affine Transformation:
        X scale: 1.000
        X ymix: 0.000
        X offset: 0.000
        Y xmix: 0.000
        Y scale: 1.000
        Y offset: 0.000
      Viewport INTERNAL: displayId=0, orientation=0, logicalFrame=[0, 0, 960, 640], physicalFrame=[0, 0, 960, 640], deviceSize=[960, 640], isActive=[1]
  Device 9: Other device
`;
test("InputReader verification requires named direct touchscreen and matching raw/logical axes", () => {
  assert.equal(verifyInputReader(dump, { width: 960, height: 640 }), true);
  for (const bad of [dump.replace("959.000", "1279.000"), dump.replace("max=639,", "max=767,"),
    dump.replace("mode - DIRECT", "mode - POINTER"), dump.replace("displayId=0", "displayId=2"), dump + dump]) {
    assert.throws(() => verifyInputReader(bad, { width: 960, height: 640 }), /InputReader/);
  }
});
test("letterboxed InputReader uses identity calibration after native pre-mapping", () => {
  const letterbox = dump.replace("max=959.000", "max=1278.667").replace("max=639.000", "max=766.800")
    .replace("physicalFrame=[0, 0, 960, 640], deviceSize=[960, 640]", "physicalFrame=[64, 0, 1216, 768], deviceSize=[1280, 768]");
  assert.equal(verifyInputReader(letterbox, { width: 960, height: 640 }), true);
  for (const coefficient of ["X scale", "Y scale"]) {
    assert.throws(() => verifyInputReader(letterbox.replace(`${coefficient}: 1.000`, `${coefficient}: 1.001`),
      { width: 960, height: 640 }), /calibration/i);
  }
  assert.throws(() => verifyInputReader(letterbox.replace("X ymix: 0.000", "X ymix: 0.001"),
    { width: 960, height: 640 }), /calibration/i);
  assert.throws(() => verifyInputReader(letterbox.replace("X offset: 0.000", "X offset: 48.000"), { width: 960, height: 640 }), /calibration/i);
});
test("pre-mapping takes one consistent active display viewport and rejects ambiguity", () => {
  const size = { width: 960, height: 640 };
  assert.deepEqual(displayViewport(dump + dump, size).device, [960, 640]);
  for (const bad of [dump.replace("isActive=[1]", "isActive=[0]"),
    dump.replace("orientation=0", "orientation=1"), dump.replace("displayId=0", "displayId=2"),
    dump + dump.replace("deviceSize=[960, 640]", "deviceSize=[1280, 768]")]) {
    assert.throws(() => displayViewport(bad, size), /viewport/i);
  }
});
test("touch pause refuses a mapping to a physical device before removing or killing anything", async () => {
  const commands = [];
  const adapter = createTouchAdapter({
    guest: async args => {
      commands.push(args);
      if (args.includes("/proc/bus/input/devices")) return Buffer.from('I: Bus=0006 Vendor=5441 Product=0001 Version=0000\nN: Name="TeslaAndroid x86 Touchscreen"\nH: Handlers=event9\n');
      if (args.includes("stat")) return Buffer.from("d:40\n");
      throw new Error("Unexpected mutation");
    },
    deviceNumber: async () => "13:73", run: async () => { throw new Error("Unexpected mutation"); },
  });
  await assert.rejects(adapter.pause(), /not the synthetic device/);
  assert.equal(commands.some(args => args.includes("rm") || args.includes("kill")), false);
});
for (const supervised of [false, true]) test(`touch remap preserves calibrated identity and coordinator lifetime (supervised: ${supervised})`, async () => {
  const commands = [];
  let calibrated = false;
  const adapter = createTouchAdapter({
    supervised,
    guest: async args => { commands.push(args); return Buffer.from(args.includes("/system/bin/dumpsys") ? dump :
      'I: Bus=0006 Vendor=5441 Product=0001 Version=0000\nN: Name="TeslaAndroid x86 Touchscreen"\nH: Handlers=event12\n'); },
    deviceNumber: async event => { assert.equal(event, "event12"); return "13:76"; },
    run: async (file, args) => { commands.push([file, ...args]); return Buffer.alloc(0); },
    calibrate: async matrix => { calibrated = true; assert.deepEqual(matrix, [1, 0, 0, 0, 1, 0]); },
  });
  await adapter.remap({ width: 960, height: 640, density: 180 });
  const mappings = commands.filter(args => args.some(arg => ["mknod", "chown", "chmod", "ln"].includes(arg)));
  assert.deepEqual(mappings.map(args => args[1]), ["mknod", "chown", "chmod", "ln"]);
  assert.deepEqual(mappings[0].slice(-3), ["c", "13", "76"]);
  const launch = commands.find(args => args.includes("--unit=tesla-waydroid-touch-preview"));
  assert.ok(launch.includes("LD_LIBRARY_PATH=/data/local/tmp/tesla-waydroid-test-20261003/lib64"));
  assert.equal(launch.includes("--property=BindsTo=tesla-waydroid-runtime.service"), supervised);
  assert.equal(launch.includes("--property=After=tesla-waydroid-runtime.service"), supervised);
  assert.deepEqual(launch.slice(-8), ["960", "640", "960", "640", "0", "0", "960", "640"]);
  assert.equal(calibrated, true);
});

test("verification rejects a native service without the matching pre-mapping arguments", async () => {
  const binary = "/data/local/tmp/tesla-waydroid-test-20261003/bin/tesla-android-x86-touch";
  let argv = [binary, "960", "640"];
  const adapter = createTouchAdapter({ guest: async args => {
    if (args.includes("/system/bin/dumpsys")) return Buffer.from(dump);
    if (args.includes("pidof")) return Buffer.from("43");
    if (args.includes("readlink")) return Buffer.from(binary);
    if (args.includes("/proc/43/cmdline")) return Buffer.from(argv.join("\0") + "\0");
    throw new Error("Unexpected guest operation");
  } });
  await assert.rejects(adapter.verifyTouch({ width: 960, height: 640 }), /pre-mapping/);
  argv = [binary, "960", "640", "960", "640", "0", "0", "960", "640"];
  assert.equal(await adapter.verifyTouch({ width: 960, height: 640 }), true);
  argv[5] = "1";
  await assert.rejects(adapter.verifyTouch({ width: 960, height: 640 }), /pre-mapping/);
});
