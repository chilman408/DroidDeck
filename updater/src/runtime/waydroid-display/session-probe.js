// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs");
const path = require("node:path");
const { setTimeout: delay } = require("node:timers/promises");
const { createHostAdapter, execute, validateSize, HOST_LIBS, GUEST_LIBS, GUEST_ARGS } = require("./host-adapter");
const { readFrame } = require("./frame-check");
const { runProbe } = require("./probe");
const { waitForSession } = require("./session-lifecycle");
const { restartGraphics } = require("./graphics-restart");

const BASE = "/data/local/tmp/tesla-waydroid-test-20261003/bin/";
const BINARIES = { api: BASE + "tesla-android-x86-api", touch: BASE + "tesla-android-x86-touch",
  display: BASE + "tesla-android-x86-display-latest" };
const HOME = "/home/hilman/teslaandroid-waydroid";
const MAPPINGS = ["/dev/input/tesla-touch-preview", "/dev/tesla-touch-preview"];

function parseSyntheticDevice(text) {
  const devices = text.split(/\r?\n\s*\r?\n/).filter((block) =>
    /^N: Name="TeslaAndroid x86 Touchscreen"$/m.test(block));
  if (devices.length > 1) throw new Error("Synthetic device identity is ambiguous");
  if (!devices.length) return null;
  if (!/^I: Bus=0006 Vendor=5441 Product=0001 Version=0000$/m.test(devices[0])) {
    throw new Error("Synthetic device identity mismatch");
  }
  const event = /^H: Handlers=.*\b(event\d+)\b/m.exec(devices[0])?.[1];
  if (!event) throw new Error("Synthetic device has no evdev handler");
  return event;
}

async function verifyProcess(kind, pid, guest) {
  if (!Number.isInteger(Number(pid)) || !/^\d+$/.test(String(pid)) || Number(pid) < 2) throw new Error("Invalid guest PID");
  if (!BINARIES[kind] || (await guest(["/system/bin/toybox", "readlink", `/proc/${pid}/exe`])).toString().trim() !== BINARIES[kind]) {
    throw new Error("Guest executable identity mismatch");
  }
  return Number(pid);
}

async function runSessionProbe({ pause, probe, resume }) {
  const start = Date.now();
  let pausedAt, resumeAt, result, failure;
  try {
    await pause();
    pausedAt = Date.now();
    result = await probe();
    return result;
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    resumeAt = Date.now();
    try { await resume(); }
    catch (error) {
      if (failure) throw new AggregateError([failure, error], "Display operation and preview recovery both failed");
      throw error;
    }
    if (result?.timings) {
      const overhead = (pausedAt - start) + (Date.now() - resumeAt);
      for (const timing of result.timings) timing.totalMs += overhead;
    }
  }
}

function readOriginalSnapshot(filename, directory = HOME) {
  if (typeof filename !== "string" || !/^session-resize-evidence-\d+\.json$/.test(filename)) {
    throw new Error("An explicit original evidence filename is required for recovery");
  }
  const fd = fs.openSync(path.join(directory, filename), fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > 4096 ||
        (process.platform !== "win32" && ((stat.mode & 0o077) || stat.uid !== process.getuid()))) {
      throw new Error("Original evidence must be a private owned regular file");
    }
    const snapshot = JSON.parse(fs.readFileSync(fd, "utf8")).snapshot;
    validateSize(snapshot);
    if (!snapshot.properties || ![snapshot.properties.width, snapshot.properties.height].every((v) =>
      typeof v === "string" && /^(?:\d{1,4})?$/.test(v)) ||
        !(snapshot.sizeOverride === null || /^\d+x\d+$/.test(snapshot.sizeOverride))) {
      throw new Error("Invalid original evidence snapshot");
    }
    return snapshot;
  } finally { fs.closeSync(fd); }
}

async function verifyRecovered(adapter, snapshot, options) {
  const actual = await adapter.inspect(options);
  const frame = readFrame(await adapter.capture(options));
  if (!frame.nonblack) throw new Error("Restored capture is black; control remains disabled");
  if (actual.width !== snapshot.width || actual.height !== snapshot.height ||
      frame.width !== snapshot.width || frame.height !== snapshot.height || actual.density !== snapshot.density) {
    throw new Error("Original display state has not been restored");
  }
  return actual;
}

async function recoverOriginal(adapter, snapshot, options) {
  await adapter.restore(snapshot, options);
  return verifyRecovered(adapter, snapshot, options);
}

async function makeRunnable({ state, unfreeze, showUI }, signal) {
  signal?.throwIfAborted();
  if (await state() === "FROZEN") await unfreeze();
  await showUI();
}

function withinBudget(timings) {
  return Array.isArray(timings) && timings.length > 0 && timings.every(({ forwardMs, restoreMs, totalMs }) =>
    Number.isFinite(forwardMs) && Number.isFinite(restoreMs) && Number.isFinite(totalMs) &&
    forwardMs >= 0 && restoreMs >= 0 && totalMs >= forwardMs + restoreMs &&
    forwardMs <= 12000 && restoreMs <= 8000 && totalMs <= 20000);
}

async function stopTransient(stop) {
  try { await stop(); }
  catch (error) { if (error.code !== 5) throw error; }
}

async function main() {
  const mode = process.argv[2];
  if (process.getuid?.() !== 0 || !["--verify-session", "--verify-graphics", "--verify-logical", "--verify-layer", "--verify-remap", "--recover", "--recover-layer"].includes(mode)) {
    throw new Error("Run as root with --verify-session, --verify-graphics, --verify-logical or --recover ORIGINAL_EVIDENCE_FILE on the disposable Ubuntu VM only");
  }
  const run = (file, args, signal) => execute(file, args, { env: process.env, shell: false, signal });
  const containerState = (signal) => run("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-sH"], signal)
    .then((buffer) => buffer.toString().trim());
  const unfreeze = (signal) => run("/usr/bin/lxc-unfreeze", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid"], signal);
  const guest = async (args, signal) => {
    if (await containerState(signal) === "FROZEN") await unfreeze(signal);
    return execute("/usr/bin/lxc-attach", [...GUEST_ARGS, ...args], {
      env: { ...process.env, LD_LIBRARY_PATH: HOST_LIBS }, shell: false, signal,
    });
  };
  const user = (args, signal) => run("/usr/sbin/runuser", ["-u", "hilman", "--", "/usr/bin/env",
    "XDG_RUNTIME_DIR=/run/user/1000", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
    `LD_LIBRARY_PATH=${HOST_LIBS}`, "WAYLAND_DISPLAY=wayland-0", ...args], signal);
  const logical = createHostAdapter();
  const layer = createHostAdapter({ captureMethod: "logical" });
  // Load after module exports are available: the touch adapter reuses identity checks above.
  const touch = require("./touch-adapter").createTouchAdapter({ guest: layer.guest, calibrate: layer.calibrate });
  const remappedLayer = { ...layer,
    applySize: async (desired, options) => {
      await touch.pause(options);
      await layer.applySize(desired, options);
      await touch.remap(desired, options);
      try { await touch.verifyTouch(desired, options); }
      catch (error) {
        fs.writeFileSync(HOME + "/input-remap-evidence-" + Date.now() + ".txt",
          await layer.guest(["/system/bin/dumpsys", "input"], options), { mode: 0o600, flag: "wx" });
        throw error;
      }
    },
    restore: async (original, options) => {
      await touch.pause(options);
      await layer.restore(original, options);
      await touch.remap(original, options);
      await touch.verifyTouch(original, options);
    },
  };
  const recovering = ["--recover", "--recover-layer"].includes(mode);
  const snapshot = recovering ? readOriginalSnapshot(process.argv[3]) : await logical.inspect();
  const evidenceFile = HOME + "/session-resize-evidence-" + Date.now() + ".json";
  fs.writeFileSync(evidenceFile, JSON.stringify({ snapshot }) + "\n", { mode: 0o600, flag: "wx" });
  let result;
  let longestRestartMs = 0;
  const restartSession = async (signal) => {
    const start = Date.now();
    await stopTransient(() => user(["/usr/bin/systemctl", "--user", "stop", "waydroid-session-software-final"], signal));
    await user(["/usr/bin/systemd-run", "--user", "--unit=waydroid-session-software-final",
      `--setenv=LD_LIBRARY_PATH=${HOST_LIBS}`, "--setenv=WAYLAND_DISPLAY=wayland-0",
      "/usr/bin/waydroid", "session", "start"], signal);
    await waitForSession({
      registered: async () => (await user(["/usr/bin/dbus-send", "--session", "--print-reply",
        "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.NameHasOwner",
        "string:id.waydro.Session"], signal)).toString().includes("boolean true"),
      state: async () => {
        try { return await containerState(signal); }
        catch (error) { if (error.code === 1) return "STOPPED"; throw error; }
      },
      unfreeze: () => unfreeze(signal),
      booted: async () => (await guest(["/system/bin/getprop", "sys.boot_completed"], signal)).toString().trim() === "1",
      bootUnavailable: (error) => error.code === 127,
      showUI: () => user(["/usr/bin/waydroid", "show-full-ui"], signal),
      sleep: () => delay(250, undefined, { signal }),
    }, signal);
    // Container creation is asynchronous; don't confuse a starting session with a ready capture.
    for (;;) {
      signal?.throwIfAborted();
      try {
        const frame = readFrame(await logical.capture({ signal }));
        if (frame.nonblack) break;
      } catch (error) { if (signal?.aborted) throw error; }
      await delay(250, undefined, { signal });
    }
    longestRestartMs = Math.max(longestRestartMs, Date.now() - start);
  };
  const propertyAdapter = createHostAdapter({ method: "properties", restartSession });
  const graphicsAdapter = createHostAdapter({ method: "properties", restartSession: (signal, desired) =>
    restartGraphics({ guest, capture: async () => readFrame(await logical.capture({ signal })),
      showUI: () => user(["/usr/bin/waydroid", "show-full-ui"], signal) }, desired, signal) });
  const stopPreview = async () => {
    await stopTransient(() => run("/usr/bin/systemctl", ["stop", "tesla-waydroid-gateway-preview"]));
    const devices = (await guest(["/system/bin/toybox", "cat", "/proc/bus/input/devices"])).toString();
    const event = parseSyntheticDevice(devices);
    const device = event ? fs.readFileSync(`/sys/class/input/${event}/dev`, "utf8").trim().split(":").map(Number) : null;
    const existing = [];
    for (const filename of MAPPINGS) {
      let mapped;
      try { mapped = (await guest(["/system/bin/toybox", "stat", "-c", "%t:%T", filename])).toString().trim().split(":"); }
      catch (error) { if (error.code === 1) continue; throw error; }
      if (!device || parseInt(mapped[0], 16) !== device[0] || parseInt(mapped[1], 16) !== device[1]) {
        throw new Error("Mapping is not the synthetic device");
      }
      existing.push(filename);
    }
    if (existing.length) await guest(["/system/bin/toybox", "rm", ...existing]);
    for (const kind of ["touch", "display", "api"]) {
      const name = BINARIES[kind].split("/").at(-1);
      let pids;
      try { pids = (await guest(["/system/bin/toybox", "pidof", name])).toString().trim().split(/\s+/); }
      catch (error) { if (error.code === 1) pids = []; else throw error; }
      for (const pid of pids) {
        await verifyProcess(kind, pid, guest);
        await guest(["/system/bin/toybox", "kill", "-TERM", pid]);
      }
      await stopTransient(() => run("/usr/bin/systemctl", ["stop", `tesla-waydroid-${kind}-preview`]));
    }
  };
  const resumePreview = async () => {
    // Recovery gets its own bounded deadline even if the diagnostic transaction timed out.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new Error("Preview recovery timed out")), 90000);
    try {
      if (result?.quiescenceRequired) throw new Error("A display operation has not stopped; leave forwarding disabled until explicit recovery");
      if (result?.recoveryRequired) await recoverOriginal(mode === "--verify-remap" ? remappedLayer :
        ["--verify-layer", "--verify-logical"].includes(mode) ? logical : propertyAdapter,
        snapshot, { signal: controller.signal });
      const actual = await verifyRecovered(logical, snapshot, { signal: controller.signal });
      for (const kind of ["api", "display", "touch"]) {
        try {
          await run("/usr/bin/systemctl", ["is-active", "--quiet", `tesla-waydroid-${kind}-preview`], controller.signal);
          continue;
        } catch (error) { if (![3, 4].includes(error.code)) throw error; }
        const args = kind === "display" ? [] : [String(actual.width), String(actual.height)];
        await run("/usr/bin/systemd-run", [`--unit=tesla-waydroid-${kind}-preview`,
          `--setenv=LD_LIBRARY_PATH=${HOST_LIBS}`, "/usr/bin/waydroid", "shell", "--",
          "/system/bin/env", `LD_LIBRARY_PATH=${GUEST_LIBS}`, BINARIES[kind], ...args], controller.signal);
      }
      let event;
      for (let i = 0; i < 40; i++) {
        event = parseSyntheticDevice((await guest(["/system/bin/toybox", "cat", "/proc/bus/input/devices"], controller.signal)).toString());
        if (event) break;
        await delay(100, undefined, { signal: controller.signal });
      }
      if (!event) throw new Error("Restored synthetic device not found");
      const [major, minor] = fs.readFileSync(`/sys/class/input/${event}/dev`, "utf8").trim().split(":");
      const mappingExists = async (filename) => {
        let value;
        try { value = (await guest(["/system/bin/toybox", "stat", "-c", "%t:%T", filename], controller.signal)).toString().trim().split(":"); }
        catch (error) { if (error.code === 1) return false; throw error; }
        if (parseInt(value[0], 16) !== Number(major) || parseInt(value[1], 16) !== Number(minor)) {
          throw new Error("Existing mapping is not the current synthetic device");
        }
        return true;
      };
      if (!(await mappingExists(MAPPINGS[1]))) {
        await guest(["/system/bin/toybox", "mknod", MAPPINGS[1], "c", major, minor], controller.signal);
      }
      await guest(["/system/bin/toybox", "chown", "system:input", MAPPINGS[1]], controller.signal);
      await guest(["/system/bin/toybox", "chmod", "0660", MAPPINGS[1]], controller.signal);
      if (!(await mappingExists(MAPPINGS[0]))) await guest(["/system/bin/toybox", "ln", MAPPINGS[1], MAPPINGS[0]], controller.signal);
      const pid = (await run("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH"], controller.signal)).toString().trim();
      if (!/^\d+$/.test(pid)) throw new Error("Invalid current container PID");
      let gatewayActive = false;
      try { await run("/usr/bin/systemctl", ["is-active", "--quiet", "tesla-waydroid-gateway-preview"], controller.signal); gatewayActive = true; }
      catch (error) { if (![3, 4].includes(error.code)) throw error; }
      if (!gatewayActive) await run("/usr/bin/systemd-run", ["--unit=tesla-waydroid-gateway-preview", "/usr/bin/nsenter", "-t", pid, "-n",
        "/usr/sbin/runuser", "-u", "hilman", "--", "/usr/bin/env", "GATEWAY_ORIGIN=https://192.168.2.90:8443",
        "GATEWAY_BIND=192.168.240.112", "GATEWAY_ANDROID_LOOPBACK=1",
        "GATEWAY_TLS_KEY_FILE=/home/hilman/.config/teslaandroid-waydroid/key.pem",
        "GATEWAY_TLS_CERT_FILE=/home/hilman/.config/teslaandroid-waydroid/cert.pem", "/usr/bin/node",
        HOME + "/runtime/preview-gateway/server.js", HOME + "/frontend", "8443"], controller.signal);
      console.log("Fixed preview recovered with current container/device identities");
    } finally { clearTimeout(timer); }
  };
  if (recovering) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new Error("Explicit recovery timed out")), 90000);
    try {
      await runSessionProbe({ pause: stopPreview,
        probe: () => recoverOriginal(mode === "--recover-layer" ? remappedLayer : propertyAdapter,
          snapshot, { signal: controller.signal }), resume: resumePreview });
    } finally { clearTimeout(timer); }
    return;
  }
  await runSessionProbe({ pause: stopPreview, resume: resumePreview, probe: async () => {
    // A longer diagnostic allowance permits recovery/evidence; it does not relax the shipping 20s gate.
    result = await runProbe(mode === "--verify-remap" ? remappedLayer : mode === "--verify-layer" ? layer : mode === "--verify-logical" ? logical : mode === "--verify-graphics" ? graphicsAdapter : propertyAdapter,
      [{ width: 960, height: 640 }, { width: 640, height: 960 }], { timeoutMs: ["--verify-logical", "--verify-layer", "--verify-remap"].includes(mode) ? 20000 : 120000 });
    result.longestRestartMs = longestRestartMs;
    fs.writeFileSync(evidenceFile, JSON.stringify({ snapshot, result }, null, 2) + "\n", { mode: 0o600 });
    return result;
  } });
  result.withinShippingBudget = withinBudget(result.timings);
  fs.writeFileSync(evidenceFile, JSON.stringify({ snapshot, result }, null, 2) + "\n", { mode: 0o600 });
  console.log(JSON.stringify(result));
  console.log("Evidence: " + evidenceFile);
  process.exitCode = result.verified && result.withinShippingBudget ? 0 : 1;
}

module.exports = { parseSyntheticDevice, verifyProcess, runSessionProbe, stopTransient,
  verifyRecovered, recoverOriginal, readOriginalSnapshot, withinBudget, makeRunnable };
if (require.main === module) main().catch((error) => { console.error(error); process.exitCode = 1; });
