// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const { parseBootClasspaths } = require("../runtime/waydroid-display/host-adapter");
const { execute } = require("../runtime/waydroid-display/host-adapter");

async function inspectUi(guest) {
  const boot = parseBootClasspaths(await guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"]));
  const file = "/data/local/tmp/tesla-taskbar-smoke-ui.xml";
  await guest(["/system/bin/env", "PATH=/system/bin:/apex/com.android.runtime/bin", "USER_ID=0",
    "ANDROID_ROOT=/system", "ANDROID_DATA=/data", "ANDROID_STORAGE=/storage", "EXTERNAL_STORAGE=/sdcard",
    "ANDROID_ART_ROOT=/apex/com.android.art",
    "ANDROID_I18N_ROOT=/apex/com.android.i18n", "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...boot,
    "CLASSPATH=/system/framework/android.test.runner.jar:/system/framework/uiautomator.jar",
    "/system/bin/app_process", "/system/bin", "com.android.commands.uiautomator.Launcher", "dump", "--windows", file]);
  const xml = await guest(["/system/bin/toybox", "cat", file]);
  assert.ok(Buffer.byteLength(xml) < 1024 * 1024, "Unexpected UI hierarchy size");
  const parsed = spawnSync("python3", ["-c",
    "import sys,json,xml.etree.ElementTree as ET; print(json.dumps([dict(n.attrib) for n in ET.fromstring(sys.stdin.read()).iter('node')]))"],
  { input: xml, encoding: "utf8", shell: false, timeout: 10000, maxBuffer: 1024 * 1024 });
  assert.equal(parsed.status, 0, parsed.stderr);
  return JSON.parse(parsed.stdout);
}
function center(node) {
  const bounds = /^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$/.exec(node.bounds);
  assert.ok(bounds, "Invalid accessible button bounds");
  const [left, top, right, bottom] = bounds.slice(1).map(Number);
  assert.ok(right > left && bottom > top, "Empty accessible button");
  return [Math.floor((left + right) / 2), Math.floor((top + bottom) / 2)];
}
function tapPackets(x, y, width, height) {
  assert.ok([x, y, width, height].every(Number.isInteger) && x >= 0 && y >= 0 &&
    x < width && y < height && width <= 1920 && height <= 1920, "Tap outside logical display");
  return [`s 0\nT 1\nX ${x}\nY ${y}\nS 0\n`, "s 0\nT -1\nS 0\n"];
}
async function tapStream(x, y, width, height) {
  const WebSocket = require("ws");
  const packets = tapPackets(x, y, width, height);
  const socket = new WebSocket("ws://127.0.0.1:9999");
  let timer;
  try {
    await new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error("Native touch test connection timed out")), 5000);
      socket.once("open", resolve); socket.once("error", reject);
    });
    clearTimeout(timer);
    const send = packet => new Promise((resolve, reject) => socket.send(packet, error => error ? reject(error) : resolve()));
    await send(packets[0]);
    await new Promise(resolve => setTimeout(resolve, 80));
    await send(packets[1]);
    await new Promise(resolve => setTimeout(resolve, 100));
  } finally {
    clearTimeout(timer); socket.terminate();
  }
}
async function tapNative(x, y, width, height) {
  tapPackets(x, y, width, height);
  const options = { env: { ...process.env, LD_LIBRARY_PATH: "/opt/teslaandroid-waydroid/libgbinder" } };
  const pid = (await execute("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH"], options)).toString().trim();
  assert.match(pid, /^[1-9]\d{1,7}$/, "Guest namespace unavailable");
  await execute("/usr/bin/nsenter", ["-t", pid, "-n", "/usr/bin/node", __filename,
    "--tap", ...[x, y, width, height].map(String)], options);
}
if (require.main === module) {
  assert.equal(process.argv.length, 7);
  assert.equal(process.argv[2], "--tap");
  tapStream(...process.argv.slice(3).map(Number)).catch(error => { console.error(error.message); process.exitCode = 1; });
}
module.exports = { inspectUi, center, tapPackets, tapStream, tapNative };
