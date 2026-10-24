// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const { setTimeout: delay } = require("node:timers/promises");
const { execute, validateSize, HOST_LIBS, GUEST_LIBS } = require("./host-adapter");
const { parseSyntheticDevice, verifyProcess, stopTransient } = require("./session-probe");
const { calculateCalibration } = require("./touch-geometry");
const MAPPINGS = ["/dev/input/tesla-touch-preview", "/dev/tesla-touch-preview"];
const BINARY = "/data/local/tmp/tesla-waydroid-test-20261003/bin/tesla-android-x86-touch";
const IDENTITY = [1, 0, 0, 0, 1, 0];

function parseViewport(line, size) {
  const list = label => new RegExp(`${label}=\\[([0-9, ]+)\\]`).exec(line)?.[1].split(",").map(Number);
  const viewport = { logical: list("logicalFrame"), physical: list("physicalFrame"), device: list("deviceSize"),
    rotation: Number(/\borientation=(\d+)/.exec(line)?.[1]) };
  if (!line.includes("isActive=[1]")) throw new Error("InputReader viewport is inactive");
  calculateCalibration(size, viewport);
  return viewport;
}
function displayViewport(text, size) {
  const lines = text.split("\n").filter(line => /Viewport(?: INTERNAL)?:.*\bdisplayId=0\b/.test(line));
  const viewports = new Map(lines.map(line => {
    const viewport = parseViewport(line, size);
    return [JSON.stringify(viewport), viewport];
  }));
  if (viewports.size !== 1) throw new Error("InputReader display viewport missing or ambiguous");
  return [...viewports.values()][0];
}
const mappingArgs = (size, viewport) => [size.width, size.height, ...viewport.device, ...viewport.physical].map(String);

function touchViewport(text, { width, height }) {
  const blocks = text.split(/(?=^  Device \d+:)/m).filter(block =>
    /^  Device \d+: TeslaAndroid x86 Touchscreen\s*$/m.test(block));
  if (blocks.length !== 1) throw new Error("InputReader synthetic identity missing or ambiguous");
  const block = blocks[0];
  const raw = block.split("Raw Touch Axes:")[1]?.split("Calibration:")[0];
  if (!block.includes("Touch Input Mapper (mode - DIRECT)") ||
      !/Viewport(?: INTERNAL)?:.*\bdisplayId=0\b/.test(block) ||
      !new RegExp(`X: min=0, max=${width - 1},`).test(raw || "") ||
      !new RegExp(`Y: min=0, max=${height - 1},`).test(raw || "")) {
    throw new Error("InputReader display association or axes mismatch");
  }
  const viewportLine = /^\s+Viewport(?: INTERNAL)?:[^\n]+/m.exec(block)?.[0] || "";
  const viewport = parseViewport(viewportLine, { width, height });
  const matrix = calculateCalibration({ width, height }, viewport);
  const range = axis => Number(new RegExp(`${axis}: source=TOUCHSCREEN, min=0\\.000, max=([0-9.]+)`).exec(block)?.[1]);
  const expectedX = (width - 1) * viewport.device[0] / width;
  const expectedY = (height - 1) * viewport.device[1] / height;
  if (Math.abs(range("X") - expectedX) > 0.002 || Math.abs(range("Y") - expectedY) > 0.002 ||
      !Number.isFinite(range("X")) || !Number.isFinite(range("Y"))) throw new Error("InputReader motion axes mismatch");
  return { block, matrix };
}
function verifyInputReader(text, size) {
  const { block } = touchViewport(text, size);
  const names = ["X scale", "X ymix", "X offset", "Y xmix", "Y scale", "Y offset"];
  const actual = names.map(name => Number(new RegExp(`${name}: (-?[0-9.]+)`).exec(block)?.[1]));
  if (!actual.every((value, i) => Number.isFinite(value) && value === IDENTITY[i])) {
    throw new Error("InputReader synthetic calibration mismatch");
  }
  return true;
}

function createTouchAdapter({ guest, calibrate, supervised = false, hostLibs = HOST_LIBS, run = (file, args, options = {}) => execute(file, args,
  { ...options, env: { ...process.env, LD_LIBRARY_PATH: hostLibs } }),
  deviceNumber = event => fs.readFile(`/sys/class/input/${event}/dev`, "utf8") } = {}) {
  const discover = async options => parseSyntheticDevice((await guest(
    ["/system/bin/toybox", "cat", "/proc/bus/input/devices"], options)).toString());
  const number = async event => {
    const value = (await deviceNumber(event)).trim();
    if (!/^13:\d{1,5}$/.test(value)) throw new Error("Unexpected evdev device number");
    return value.split(":");
  };
  async function pause(options) {
    const event = await discover(options);
    const current = event ? await number(event) : null;
    const remove = [];
    for (const mapping of MAPPINGS) {
      let mapped;
      try { mapped = (await guest(["/system/bin/toybox", "stat", "-c", "%t:%T", mapping], options))
        .toString().trim().split(":").map(v => parseInt(v, 16)); }
      catch (error) { if (error.code === 1) continue; throw error; }
      if (!current || mapped[0] !== Number(current[0]) || mapped[1] !== Number(current[1])) {
        throw new Error("Mapping is not the synthetic device");
      }
      remove.push(mapping);
    }
    if (remove.length) await guest(["/system/bin/toybox", "rm", ...remove], options);
    let pids;
    try { pids = (await guest(["/system/bin/toybox", "pidof", "tesla-android-x86-touch"], options)).toString().trim().split(/\s+/).filter(Boolean); }
    catch (error) { if (error.code === 1) pids = []; else throw error; }
    for (const pid of pids) {
      await verifyProcess("touch", pid, args => guest(args, options));
      await guest(["/system/bin/toybox", "kill", "-TERM", pid], options);
    }
    await stopTransient(() => run("/usr/bin/systemctl", ["stop", "tesla-waydroid-touch-preview"], options));
    for (let i = 0; i < 30; i++) {
      if (!(await discover(options))) return;
      await delay(50, undefined, options);
    }
    throw new Error("Old synthetic device has not closed");
  }
  async function remap(size, options) {
    validateSize(size);
    const viewport = displayViewport((await guest(["/system/bin/dumpsys", "input"], options)).toString(), size);
    await run("/usr/bin/systemd-run", ["--unit=tesla-waydroid-touch-preview", `--setenv=LD_LIBRARY_PATH=${hostLibs}`,
      ...(supervised ? ["--collect", "--property=BindsTo=tesla-waydroid-runtime.service", "--property=After=tesla-waydroid-runtime.service"] : []),
      "/usr/bin/waydroid", "shell", "--", "/system/bin/env", `LD_LIBRARY_PATH=${GUEST_LIBS}`,
      BINARY, ...mappingArgs(size, viewport)], options);
    let event;
    for (let i = 0; i < 30; i++) {
      event = await discover(options);
      if (event) break;
      await delay(50, undefined, options);
    }
    if (!event) throw new Error("New synthetic device not found");
    const [major, minor] = await number(event);
    await guest(["/system/bin/toybox", "mknod", MAPPINGS[1], "c", major, minor], options);
    await guest(["/system/bin/toybox", "chown", "system:input", MAPPINGS[1]], options);
    await guest(["/system/bin/toybox", "chmod", "0660", MAPPINGS[1]], options);
    await guest(["/system/bin/toybox", "ln", MAPPINGS[1], MAPPINGS[0]], options);
    let geometry;
    for (let i = 0; i < 20; i++) {
      try { geometry = touchViewport((await guest(["/system/bin/dumpsys", "input"], options)).toString(), size); break; }
      catch (error) { if (options?.signal?.aborted || i === 19) throw error; }
      await delay(100, undefined, options);
    }
    if (typeof calibrate !== "function") throw new Error("Synthetic calibration helper is unavailable");
    if (geometry.matrix.some((value, i) => Math.abs(value - calculateCalibration(size, viewport)[i]) > 0.001)) {
      throw new Error("InputReader viewport changed during synthetic remap");
    }
    await calibrate(IDENTITY, options);
  }
  async function verifyTouch(size, options) {
    let failure;
    for (let i = 0; i < 20; i++) {
      try {
        const text = (await guest(["/system/bin/dumpsys", "input"], options)).toString();
        verifyInputReader(text, size);
        const viewport = displayViewport(text, size);
        const pid = (await guest(["/system/bin/toybox", "pidof", "tesla-android-x86-touch"], options)).toString().trim();
        await verifyProcess("touch", pid, args => guest(args, options));
        const argv = (await guest(["/system/bin/toybox", "cat", `/proc/${pid}/cmdline`], options)).toString().split("\0").filter(Boolean);
        if (JSON.stringify(argv) !== JSON.stringify([BINARY, ...mappingArgs(size, viewport)])) {
          throw new Error("Synthetic native pre-mapping does not match viewport");
        }
        return true;
      }
      catch (error) { failure = error; if (options?.signal?.aborted) throw error; }
      await delay(100, undefined, options);
    }
    throw failure;
  }
  return { pause, remap, verifyTouch, resume: async () => {} };
}
module.exports = { createTouchAdapter, verifyInputReader, touchViewport, displayViewport };
