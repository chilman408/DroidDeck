// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const source = process.argv[2];
if (!source) {
  throw new Error("usage: node tests/frontend-origin.test.js FRONTEND_CHECKOUT");
}

const patch = path.resolve(__dirname, "../runtime/patches/frontend-same-origin.patch");
const fixedDisplayPatch = path.resolve(__dirname, "../runtime/patches/frontend-fixed-display.patch");
const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-frontend-"));
try {
  const target = path.join(directory, "js/core/shared.js");
  const controllerTarget = path.join(directory, "js/display/display-controller.js");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.mkdirSync(path.dirname(controllerTarget), { recursive: true });
  fs.writeFileSync(
    target,
    execFileSync("git", ["-C", source, "show", "HEAD:js/core/shared.js"]),
  );
  fs.writeFileSync(
    controllerTarget,
    execFileSync("git", ["-C", source, "show", "HEAD:js/display/display-controller.js"]),
  );
  execFileSync("git", ["apply", "--check", patch], { cwd: directory });
  execFileSync("git", ["apply", patch], { cwd: directory });
  execFileSync("git", ["apply", "--check", fixedDisplayPatch], { cwd: directory });
  execFileSync("git", ["apply", fixedDisplayPatch], { cwd: directory });
  const script = fs.readFileSync(target, "utf8");

  for (const url of ["http://192.168.2.125:8080/", "https://vm.example.test/"]) {
    const location = new URL(url);
    const window = { location };
    vm.runInNewContext(script, { window });
    const flavor = window.TAHtml.createFlavor();
    const socketScheme = location.protocol === "https:" ? "wss:" : "ws:";
    assert.equal(flavor.domain, location.hostname);
    assert.equal(flavor.apiBaseUrl, location.origin + "/api");
    for (const channel of ["audio", "display", "gps", "touchscreen"]) {
      assert.equal(
        flavor[channel + "WebSocket"],
        socketScheme + "//" + location.host + "/sockets/" + channel,
      );
    }
  }

  const window = {
    TAHtml: {
      utils: {
        normalizeSize: (size) => size,
        toInt: (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback,
      },
      constants: {},
    },
  };
  vm.runInNewContext(fs.readFileSync(controllerTarget, "utf8"), { window });
  const controller = Object.create(window.TAHtml.DisplayController.prototype);
  controller.isPaused = false;
  controller.flavor = {};
  controller.resize = { mode: "normal", token: 0 };
  controller.apiClient = {
    fetchDisplayState: async () => ({ width: 1280, height: 720, fixedDisplay: 1 }),
    postDisplayState: () => { throw new Error("fixed display must not resize"); },
  };
  let applied = null;
  controller._setNormalState = (value) => { applied = value; };
  controller._startResize({ width: 800, height: 600 }).then(() => {
    assert.equal(applied.adjustedSize.width, 1280);
    assert.equal(applied.adjustedSize.height, 720);
    console.log("frontend origin and fixed-display tests passed");
  }).catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
} finally {
  fs.rmSync(directory, { recursive: true, force: true });
}
