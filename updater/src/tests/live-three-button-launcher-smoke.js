// SPDX-License-Identifier: GPL-3.0-only
// Run as root on the VM with the browser gateway paused to avoid resize races.
const assert = require("node:assert/strict");
const { createHostAdapter } = require("../runtime/waydroid-display/host-adapter");
const { readFrame } = require("../runtime/waydroid-display/frame-check");
const { createHelperClient } = require("../runtime/preview-gateway/display-helper-client");
const { inspectUi, center, tapNative } = require("./android-ui");

async function smoke() {
  const adapter = createHostAdapter({ captureMethod: "logical", hostLibs: "/opt/teslaandroid-waydroid/libgbinder" });
  const helper = createHelperClient("/run/teslaandroid-waydroid/display.sock");
  const guest = async args => (await adapter.guest(args)).toString();
  const wait = () => new Promise(resolve => setTimeout(resolve, 1000));
  const crashMarker = async () => (await guest(["/system/bin/logcat", "-b", "crash", "-d", "-v", "threadtime"]))
    .split(/\r?\n/).filter(line => /Process: (?:com\.android\.launcher3|app\.lawnchair),/.test(line)).at(-1) || "none";
  const resumed = () => guest(["/system/bin/dumpsys", "activity", "activities"]);
  const snapshot = await helper.inspect();
  assert.equal(snapshot.recoveryRequired, false);
  assert.equal((await guest(["/system/bin/settings", "get", "secure", "navigation_mode"])).trim(), "0");
  assert.match(await guest(["/system/bin/cmd", "package", "resolve-activity", "--brief", "-a",
    "android.intent.action.MAIN", "-c", "android.intent.category.HOME"]), /app\.lawnchair\//);
  const marker = await crashMarker();
  const results = [];
  try {
    const sizes = process.argv.includes("--compact-only") ? [[676, 640]] :
      [[740, 720], [762, 720], [676, 640], [390, 844], [640, 960], [1440, 832], [980, 720]];
    for (const [width, height] of sizes) {
      await helper.resize({ width, height, density: snapshot.density,
        resolutionPreset: snapshot.resolutionPreset, isResponsive: snapshot.isResponsive });
      await guest(["/system/bin/input", "keyevent", "3"]);
      await wait();
      assert.match(await resumed(), /topResumedActivity=.*app\.lawnchair\//);
      const frame = readFrame(await adapter.capture());
      assert.equal(frame.width, width);
      assert.equal(frame.height, height);
      assert.equal(frame.nonblack, true);
      let drawer;
      if (process.argv.includes("--taskbar")) {
        const nodes = await inspectUi(guest);
        const button = nodes.find(node => node.package === "com.android.launcher3" &&
          ["All apps", "Apps list"].includes(node["content-desc"]) && node.clickable === "true");
        if (button) {
          const coordinates = center(button);
          await tapNative(...coordinates, width, height);
          await wait();
          assert.equal(await crashMarker(), marker, "Taskbar All Apps crashed");
          const opened = await inspectUi(guest);
          const didOpen = opened.some(node => node.package === "com.android.launcher3" &&
            /all_apps|apps_list|search_container/.test(node["resource-id"]));
          if (!didOpen) {
            const profile = await guest(["/system/bin/dumpsys", "activity", "service",
              "com.android.launcher3/com.android.quickstep.TouchInteractionService"]);
            assert.equal(/\bisTablet:\s*(true|false)/.exec(profile)?.[1], "false",
              "Tablet drawer did not open at " + width + "x" + height);
          }
          drawer = { opened: didOpen, phoneLayoutNoOp: !didOpen, coordinates };
          if (didOpen) {
            await guest(["/system/bin/input", "keyevent", "4"]);
            await wait();
          }
        } else {
          const profile = await guest(["/system/bin/dumpsys", "activity", "service",
            "com.android.launcher3/com.android.quickstep.TouchInteractionService"]);
          assert.match(profile, /isTaskbarPresent(?:=|:)\s*false/, "Expected taskbar All Apps icon missing");
          drawer = { absentOnPhoneLayout: true };
        }
      }
      await guest(["/system/bin/am", "start", "-a", "android.settings.SETTINGS"]);
      await wait();
      assert.match(await resumed(), /topResumedActivity=.*com\.android\.settings\//);
      await guest(["/system/bin/input", "keyevent", "187"]);
      await wait();
      assert.match(await resumed(), /topResumedActivity=.*com\.android\.launcher3\/com\.android\.quickstep\.RecentsActivity/);
      assert.equal(readFrame(await adapter.capture()).nonblack, true);
      await guest(["/system/bin/input", "keyevent", "4"]);
      await wait();
      assert.match(await resumed(), /topResumedActivity=.*com\.android\.settings\//);
      await guest(["/system/bin/input", "keyevent", "3"]);
      await wait();
      assert.match(await resumed(), /topResumedActivity=.*app\.lawnchair\//);
      assert.equal(await crashMarker(), marker, "A new launcher crash occurred");
      assert.equal((await guest(["/system/bin/settings", "get", "secure", "navigation_mode"])).trim(), "0");
      results.push({ width, height, home: true, recents: true, back: true, nonblack: true, threeButton: true, drawer });
      console.log(JSON.stringify(results.at(-1)));
    }
  } finally {
    await helper.resize({ width: snapshot.width, height: snapshot.height, density: snapshot.density,
      resolutionPreset: snapshot.resolutionPreset, isResponsive: snapshot.isResponsive });
    await guest(["/system/bin/input", "keyevent", "3"]);
  }
  return results;
}

if (require.main === module) smoke().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { smoke };
