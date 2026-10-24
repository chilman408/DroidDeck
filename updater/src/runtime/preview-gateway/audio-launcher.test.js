// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
test("audio capture is fixed playback-only, distinct from video and retains failure metadata", () => {
  assert.ok(fs.existsSync(path.join(__dirname, "audio-launcher.js")), "fixed audio launcher is not implemented");
  const { serverArgs, SOCKET } = require("./audio-launcher");
  const args = serverArgs(["BOOTCLASSPATH=/system/framework/framework.jar", "DEX2OATBOOTCLASSPATH=/system/framework/framework.jar"]);
  for (const option of ["4.0", "video=false", "audio=true", "audio_source=output", "audio_codec=raw", "control=false",
    "scid=54410002", "cleanup=false", "power_on=false", "tunnel_forward=true", "send_stream_meta=true",
    "send_frame_meta=false", "send_dummy_byte=false", "send_device_meta=false"]) assert.ok(args.includes(option), option);
  assert.equal(SOCKET, "\0scrcpy_54410002");
  assert.equal(args.some(v => v.startsWith("audio_source=mic") || v.startsWith("video_encoder=")), false);
  assert.throws(() => serverArgs(["BOOTCLASSPATH=$(evil)"]), /classpath/i);
});

test("supervised audio tags its guest capture separately from video", () => {
  const { serverArgs } = require("./audio-launcher"), owner = "b".repeat(32);
  const boot = ["BOOTCLASSPATH=/system/framework/framework.jar", "DEX2OATBOOTCLASSPATH=/system/framework/framework.jar"];
  const args = serverArgs(boot, owner);
  assert.ok(args.includes("TESLA_WAYDROID_RUNTIME_OWNER=" + owner));
  assert.ok(args.includes("TESLA_WAYDROID_RUNTIME_ROLE=audio"));
  assert.equal(serverArgs(boot).some(value => value.startsWith("TESLA_WAYDROID_RUNTIME_")), false);
  assert.throws(() => serverArgs(boot, "invalid"), /lease/);
});
