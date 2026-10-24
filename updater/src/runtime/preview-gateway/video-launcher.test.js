// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { serverArgs, SERVER_SHA256 } = require("./video-launcher");

test('configured settings identity does not consult the legacy user command', async () => {
  const { settingsUserUid } = require('./video-launcher');
  const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
  assert.equal(await settingsUserUid(async () => assert.fail('Legacy lookup must not run'), configuration), 1200);
  await assert.rejects(settingsUserUid(async () => assert.fail(), { ...configuration, uid: 0 }));
});
test("settings UID lookup always supplies the required command execution options", async () => {
  const { settingsUserUid } = require("./video-launcher");
  const run = async (file, args, options) => {
    assert.equal(file, "/usr/bin/id"); assert.deepEqual(args, ["-u", "hilman"]);
    assert.equal(options.env, process.env); assert.equal(options.signal, undefined);
    return Buffer.from("1000\n");
  };
  assert.equal(await settingsUserUid(run), 1000);
  for (const value of ["0", "bad", "1000;sh", "1.5"]) await assert.rejects(settingsUserUid(async () => Buffer.from(value)), /settings user/);
});
test("video server has pinned version, fixed root-protected artifact and no input/audio/cleanup", () => {
  const args = serverArgs(["BOOTCLASSPATH=/system/framework/framework.jar", "DEX2OATBOOTCLASSPATH=/system/framework/framework.jar"]);
  for (const value of ["4.0", "control=false", "audio=false", "cleanup=false", "tunnel_forward=true",
    "send_device_meta=false", "send_dummy_byte=false", "video_encoder=c2.android.avc.encoder", "max_fps=30",
    "CLASSPATH=/data/local/tmp/tesla-waydroid-test-20261003/scrcpy-server-v4.0.jar"]) assert.ok(args.includes(value));
  assert.match(SERVER_SHA256, /^[a-f0-9]{64}$/);
  assert.throws(() => serverArgs(["BOOTCLASSPATH=$(evil)"]), /classpath/i);
});

test("supervised video carries only a validated fixed-role lease into Android", () => {
  const boot = ["BOOTCLASSPATH=/system/framework/framework.jar", "DEX2OATBOOTCLASSPATH=/system/framework/framework.jar"];
  const owner = "a".repeat(32), args = serverArgs(boot, owner);
  assert.ok(args.includes("TESLA_WAYDROID_RUNTIME_OWNER=" + owner));
  assert.ok(args.includes("TESLA_WAYDROID_RUNTIME_ROLE=video"));
  assert.equal(serverArgs(boot).some(value => value.startsWith("TESLA_WAYDROID_RUNTIME_")), false);
  for (const value of ["", "A".repeat(32), "../owner", [owner]]) assert.throws(() => serverArgs(boot, value), /lease/);
});
test("encoder profiles change only bounded bitrate and frame rate arguments", () => {
  const boot = ["BOOTCLASSPATH=/system/framework/framework.jar", "DEX2OATBOOTCLASSPATH=/system/framework/framework.jar"];
  const args = serverArgs(boot, "a".repeat(32), { version: 1, revision: 1, bitRate: 8000000, maxFps: 45 });
  assert.ok(args.includes("max_fps=45")); assert.ok(args.includes("video_bit_rate=8000000"));
  assert.ok(args.includes("control=false")); assert.ok(args.includes("audio=false"));
  assert.throws(() => serverArgs(boot, undefined, { version: 1, revision: 0, bitRate: "4000000;sh", maxFps: 30 }));
});
