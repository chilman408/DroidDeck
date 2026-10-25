// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { createHostAdapter, parseBootClasspaths } = require("./host-adapter");

test("logical capture selects a fixed staged Java helper, not physical screencap", async () => {
  let command;
  const adapter = createHostAdapter({ captureMethod: "logical", run: async (file, args) => {
    if (file.endsWith("lxc-info")) return Buffer.from("RUNNING\n");
    if (args.at(-1) === "/data/system/environ/classpath") return Buffer.from(
      "export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /apex/com.android.art/javalib/core-oj.jar\n");
    command = args.slice(args.indexOf("--") + 1);
    return Buffer.from("frame");
  } });
  assert.equal((await adapter.capture()).toString(), "frame");
  assert.deepEqual(command, ["/system/bin/env",
    "CLASSPATH=/data/local/tmp/tesla-waydroid-test-20261003/logical-capture.jar",
    "ANDROID_ART_ROOT=/apex/com.android.art", "ANDROID_I18N_ROOT=/apex/com.android.i18n",
    "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata",
    "BOOTCLASSPATH=/system/framework/framework.jar",
    "DEX2OATBOOTCLASSPATH=/apex/com.android.art/javalib/core-oj.jar",
    "/system/bin/app_process", "/system/bin", "TeslaLogicalCapture"]);
});

test("boot classpaths accept only image jar paths and reject ambiguous or shell-valued exports", () => {
  const valid = "export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /apex/com.android.art/javalib/core-oj.jar\n";
  assert.deepEqual(parseBootClasspaths(valid), ["BOOTCLASSPATH=/system/framework/framework.jar",
    "DEX2OATBOOTCLASSPATH=/apex/com.android.art/javalib/core-oj.jar"]);
  for (const text of ["", valid + valid, valid.replace("framework.jar", "$(id).jar"),
    valid.replace("/system/framework/framework.jar", "/data/untrusted.jar"),
    valid.replace("framework.jar", "../framework.jar")]) {
    assert.throws(() => parseBootClasspaths(text), /Invalid Android boot classpath/);
  }
});

test("unknown capture methods are rejected before running guest commands", () => {
  assert.throws(() => createHostAdapter({ captureMethod: "arbitrary" }), /Invalid capture method/);
});

test("calibration accepts only bounded axis-aligned synthetic transforms", async () => {
  const commands = [];
  const adapter = createHostAdapter({ run: async (file, args) => {
    if (file.endsWith("lxc-info")) return Buffer.from("RUNNING\n");
    if (args.at(-1) === "/data/system/environ/classpath") return Buffer.from(
      "export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /apex/com.android.art/javalib/core-oj.jar\n");
    commands.push(args);
    return Buffer.from("OK\n");
  } });
  await adapter.calibrate([0.9, 0, 48, 0, 1, 0]);
  assert.deepEqual(commands[0].slice(-7), ["--calibrate", "0.9", "0", "48", "0", "1", "0"]);
  for (const matrix of [[-1, 0, 0, 0, 1, 0], [1, 1, 0, 0, 1, 0], [1, 0, Infinity, 0, 1, 0]]) {
    await assert.rejects(adapter.calibrate(matrix));
  }
  assert.equal(commands.length, 1);
});
