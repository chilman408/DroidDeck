// SPDX-License-Identifier: GPL-3.0-only
const { spawn } = require("node:child_process");
const { readFrame } = require("./frame-check");

const HOST_LIBS = "/home/hilman/libgbinder-1.1.45/build/release";
const GUEST_LIBS = "/data/local/tmp/tesla-waydroid-test-20261003/lib64";
const GUEST_ARGS = ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--clear-env",
  "--set-var", "PATH=/system/bin:/apex/com.android.runtime/bin", "--set-var", "ANDROID_ROOT=/system",
  "--set-var", "ANDROID_DATA=/data", "--set-var", "ANDROID_STORAGE=/storage", "--"];

function execute(file, args, options) {
  return new Promise((resolve, reject) => {
    options.signal?.throwIfAborted();
    const timeoutMs = options.timeoutMs === undefined ? 10000 : options.timeoutMs;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 65000) throw new Error("Invalid command timeout");
    const hasInput = options.input !== undefined;
    if (hasInput && (!(typeof options.input === "string" || Buffer.isBuffer(options.input)) ||
        Buffer.byteLength(options.input) > 4096)) throw new Error("Invalid command input");
    const grouped = process.platform !== "win32";
    const child = spawn(file, args, { env: options.env, shell: false, detached: grouped,
      stdio: [hasInput ? "pipe" : "ignore", "pipe", "pipe"], windowsHide: true });
    const output = [], errors = [];
    let outputSize = 0, errorSize = 0, failure, closed = false;
    const stop = (error) => {
      if (closed) return;
      failure ||= error;
      try {
        if (grouped && child.pid) process.kill(-child.pid, "SIGKILL");
        else child.kill("SIGKILL");
      } catch (killError) { if (killError.code !== "ESRCH") failure = killError; }
    };
    const abort = () => stop(options.signal.reason || new Error("Command cancelled"));
    const timer = setTimeout(() => stop(new Error("Command timed out")), timeoutMs);
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) abort();
    child.stdout.on("data", (chunk) => {
      outputSize += chunk.length;
      if (outputSize > 16 * 1024 * 1024) stop(new Error("Command output too large"));
      else output.push(chunk);
    });
    child.stderr.on("data", (chunk) => {
      errorSize += chunk.length;
      if (errorSize > 65536) stop(new Error("Command error output too large"));
      else errors.push(chunk);
    });
    child.on("error", (error) => { failure ||= error; });
    if (hasInput) {
      child.stdin.on("error", () => stop(new Error("Command input failed")));
      child.stdin.end(options.input);
    }
    child.on("close", (code) => {
      closed = true;
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
      if (!failure && code !== 0) failure = Object.assign(new Error(`Command failed: ${file}: ${Buffer.concat(errors).toString().slice(0, 2000)}`), { code });
      if (failure) reject(failure);
      else resolve(Buffer.concat(output));
    });
  });
}

function validateSize({ width, height, density } = {}) {
  if (![width, height].every((value) => Number.isInteger(value) && value >= 96 && value <= 1920 && value % 2 === 0) ||
      width * height > 1600000 || !Number.isInteger(density) || density < 72 || density > 640) {
    throw new Error("Invalid display dimensions or density");
  }
}

function parseWm(text, kind) {
  const physical = new RegExp(`Physical ${kind}: (\\d+(?:x\\d+)?)`).exec(text)?.[1];
  const override = new RegExp(`Override ${kind}: (\\d+(?:x\\d+)?)`).exec(text)?.[1] || null;
  if (!physical) throw new Error(`Could not inspect display ${kind}`);
  return { physical, override };
}

function parseBootClasspaths(text) {
  return ["BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH"].map((name) => {
    const values = text.split(/\r?\n/).filter((line) => line.startsWith(`export ${name} `));
    const value = values[0]?.slice(name.length + 8);
    if (values.length !== 1 || !value || value.length > 16000 || !value.split(":").every((jar) =>
      /^\/(?:system\/framework|apex\/[a-zA-Z0-9_.-]+\/javalib)\/[a-zA-Z0-9_.-]+\.jar$/.test(jar))) {
      throw new Error("Invalid Android boot classpath");
    }
    return `${name}=${value}`;
  });
}

function createHostAdapter({ run = execute, method = "logical", restartSession, captureMethod = "physical", hostLibs = HOST_LIBS } = {}) {
  if (!["physical", "logical"].includes(captureMethod)) throw new Error("Invalid capture method");
  if (!["logical", "properties"].includes(method) ||
      (method === "properties" && typeof restartSession !== "function")) throw new Error("Invalid resize method");
  const guest = async (args, { signal, input } = {}) => {
    const options = { env: { ...process.env, LD_LIBRARY_PATH: hostLibs }, shell: false, signal };
    const state = (await run("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-sH"], options)).toString().trim();
    if (state === "FROZEN") await run("/usr/bin/lxc-unfreeze", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid"], options);
    else if (state !== "RUNNING") throw new Error("Waydroid container is not running");
    return run("/usr/bin/lxc-attach", [...GUEST_ARGS, ...args], { ...options, ...(input !== undefined ? { input } : {}) });
  };
  const logicalCommand = async (args, options) => {
    const boot = parseBootClasspaths((await guest(
      ["/system/bin/toybox", "cat", "/data/system/environ/classpath"], options)).toString());
    return guest(["/system/bin/env",
    "CLASSPATH=/data/local/tmp/tesla-waydroid-test-20261003/logical-capture.jar",
    "ANDROID_ART_ROOT=/apex/com.android.art", "ANDROID_I18N_ROOT=/apex/com.android.i18n",
    "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata",
    ...boot,
    "/system/bin/app_process", "/system/bin", "TeslaLogicalCapture", ...args], options);
  };
  const capture = options => captureMethod === "logical" ? logicalCommand([], options) :
    guest(["/system/bin/env", `LD_LIBRARY_PATH=${GUEST_LIBS}`, "/system/bin/screencap", "-p"], options);
  async function calibrate(matrix, options) {
    if (!Array.isArray(matrix) || matrix.length !== 6 || !matrix.every(Number.isFinite) ||
        matrix[0] <= 0 || matrix[0] > 1 || matrix[4] <= 0 || matrix[4] > 1 || matrix[1] !== 0 ||
        matrix[3] !== 0 || matrix[2] < 0 || matrix[2] > 1920 || matrix[5] < 0 || matrix[5] > 1920) {
      throw new Error("Invalid synthetic touch calibration");
    }
    if ((await logicalCommand(["--calibrate", ...matrix.map(String)], options)).toString().trim() !== "OK") {
      throw new Error("Synthetic calibration was not acknowledged");
    }
  }
  async function inspect(options) {
    const size = parseWm((await guest(["/system/bin/wm", "size"], options)).toString(), "size");
    const density = parseWm((await guest(["/system/bin/wm", "density"], options)).toString(), "density");
    const text = (await guest(["/system/bin/getprop"], options)).toString();
    const prop = (name) => text.split(/\r?\n/).find((line) => line.startsWith(`[${name}]: [`))
      ?.slice(name.length + 5, -1) || "";
    const frame = readFrame(await capture(options));
    let touchMapping = null;
    try {
      touchMapping = (await guest(["/system/bin/toybox", "stat", "-c", "%t:%T:%a:%u:%g",
        "/dev/input/tesla-touch-preview"], options)).toString().trim();
    } catch (error) { if (options?.signal?.aborted || error.code !== 1) throw error; }
    return {
      width: frame.width, height: frame.height, density: Number(density.override || density.physical),
      physicalSize: size.physical, sizeOverride: size.override,
      physicalDensity: Number(density.physical), densityOverride: density.override,
      properties: { width: prop("persist.waydroid.width"), height: prop("persist.waydroid.height"),
        software: prop("ro.waydroid.software_rendering"), subsurface: prop("persist.waydroid.use_subsurface") },
      touchMapping,
    };
  }
  async function applySize(desired, options) {
    validateSize(desired);
    if (method === "properties") {
      await guest(["/system/bin/setprop", "persist.waydroid.width", String(desired.width)], options);
      await guest(["/system/bin/setprop", "persist.waydroid.height", String(desired.height)], options);
      try { await restartSession(options?.signal, desired); }
      catch (error) {
        if (options?.signal?.aborted) error.quiescenceRequired = true;
        throw error;
      }
      return;
    }
    await guest(["/system/bin/wm", "size", `${desired.width}x${desired.height}`], options);
  }
  async function restore(snapshot, options) {
    if (snapshot.sizeOverride !== null && !/^\d+x\d+$/.test(snapshot.sizeOverride)) {
      throw new Error("Invalid saved display override");
    }
    if (method === "properties") {
      for (const key of ["width", "height"]) {
        if (!/^(?:\d{1,4})?$/.test(snapshot.properties[key])) throw new Error("Invalid saved property");
        await guest(["/system/bin/setprop", `persist.waydroid.${key}`, snapshot.properties[key]], options);
      }
      try { await restartSession(options?.signal, snapshot); }
      catch (error) {
        if (options?.signal?.aborted) error.quiescenceRequired = true;
        throw error;
      }
    }
    await guest(["/system/bin/wm", "size", snapshot.sizeOverride || "reset"], options);
  }
  return { inspect, capture, applySize, restore, guest, calibrate };
}

module.exports = { createHostAdapter, validateSize, execute, parseBootClasspaths, HOST_LIBS, GUEST_LIBS, GUEST_ARGS };
