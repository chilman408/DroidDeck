// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict"), path = require("node:path");
const { chromium } = require("playwright-core");
const { PNG } = require("../waydroid-display/node_modules/pngjs");
const origin = "https://teslavm.mondriandsm.synology.me";
const desiredFields = ["width", "height", "density", "resolutionPreset", "isResponsive"];

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const context = await browser.newContext();
  const sessionHeaders = { Origin: origin, "Content-Type": "application/json" };
  let original, session, page;
  const errors = [], connections = [];
  try {
    const probe = await context.request.post(origin + "/api/displaySession", { headers: sessionHeaders, data: {} });
    assert.equal(probe.status(), 200);
    const initialSession = await probe.json();
    const initial = await context.request.get(origin + "/api/displayState", {
      headers: { "X-Display-Session": initialSession.token },
    });
    assert.equal(initial.status(), 200); original = await initial.json();
    page = await context.newPage();
    await page.addInitScript(() => {
      for (const key of ["DisplayRepository_isPrimaryDisplaySharedPreferencesKey", "flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey"]) {
        localStorage.setItem(key, "true");
      }
    });
    await page.setViewportSize({ width: original.width, height: original.height });
    page.on("pageerror", error => errors.push(error.message));
    page.on("websocket", socket => connections.push(new URL(socket.url()).pathname));
    page.on("response", async response => {
      if (response.url().endsWith("/api/displaySession") && response.ok()) session = await response.json();
    });
    await page.goto(origin + "/", { waitUntil: "domcontentloaded", timeout: 20000 });
    async function check(label) {
      await page.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoFrames) > 0,
        undefined, { timeout: 40000 });
      assert.equal(new URL(page.url()).searchParams.get("video"), null, "PNG fallback is forbidden");
      const pixels = PNG.sync.read(await page.locator("#canvas").screenshot());
      let nonblack = 0;
      for (let i = 0; i < pixels.data.length; i += 4) {
        if (pixels.data[i] > 8 || pixels.data[i + 1] > 8 || pixels.data[i + 2] > 8) nonblack++;
      }
      assert.ok(nonblack > pixels.width * pixels.height * 0.01, "Black streamed picture: " + label);
      if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({
        path: path.join(process.env.SMOKE_SCREENSHOT_DIR, label + ".png"),
      });
      console.log(JSON.stringify({ label, width: pixels.width, height: pixels.height, nonblack,
        video: await page.locator("#canvas").evaluate(canvas => ({ ...canvas.dataset })) }));
    }
    await check("public-initial");
    if (process.argv.includes("--navigation")) {
      assert.ok(session, "Browser display session missing");
      const stateResponse = await context.request.get(origin + "/api/displayState", {
        headers: { "X-Display-Session": session.token },
      });
      const state = await stateResponse.json();
      assert.equal(state.canControl, true, "Another browser owns input; do not steal control");
      assert.ok(state.width >= 676 && state.width <= 1920); assert.equal(state.height, 720);
      assert.equal(state.density, 180);
      // Measured right-aligned tablet navigation at both 740x720 and 980x720/density180.
      for (const [label, x] of [["recents", state.width - 272], ["home", state.width - 348]]) {
        const before = PNG.sync.read(await page.locator("#canvas").screenshot());
        const frames = Number(await page.locator("#canvas").getAttribute("data-video-frames"));
        const box = await page.locator("#touch-layer").boundingBox();
        const start = performance.now();
        await page.mouse.click(box.x + box.width * x / state.width, box.y + box.height * 689 / 720);
        await page.waitForFunction(previous => Number(document.querySelector("#canvas").dataset.videoFrames) > previous,
          frames, { polling: 50, timeout: 10000 });
        let changed = 0;
        const deadline = performance.now() + 8000;
        while (changed <= 1000 && performance.now() < deadline) {
          const after = PNG.sync.read(await page.locator("#canvas").screenshot());
          changed = 0;
          // Ignore navigation-button press highlights; require changed Android content.
          for (let i = 0; i < before.width * (before.height - 80) * 4; i += 4) {
            if (Math.abs(before.data[i] - after.data[i]) + Math.abs(before.data[i + 1] - after.data[i + 1]) +
                Math.abs(before.data[i + 2] - after.data[i + 2]) > 75) changed++;
          }
          if (changed <= 1000) await page.waitForTimeout(80);
        }
        assert.ok(changed > 1000, "Input did not visibly change Android content: " + label);
        console.log(JSON.stringify({ button: label, changedPixels: changed, browserToPictureMs: Math.round(performance.now() - start) }));
        await page.waitForTimeout(700);
        await check("public-button-" + label);
      }
    }
    if (process.argv.includes("--resize") || process.argv.includes("--supported-aspects")) {
      assert.equal(original.isResponsive, 1, "Do not enable a user's disabled dynamic sizing for this probe");
      assert.ok(session, "Browser display session missing");
      const currentResponse = await context.request.get(origin + "/api/displayState", {
        headers: { "X-Display-Session": session.token },
      });
      assert.equal(currentResponse.status(), 200);
      const current = await currentResponse.json();
      assert.equal(current.canControl, true, "Another browser owns input; do not steal control");
      if (current.canResize === false && current.displayRole === null && current.isRearDisplayEnabled === 1) {
        const selector = current.isRearDisplayPrioritised === 1 ? "#rear-display-button" : "#main-display-button";
        await page.locator(selector).click();
      } else assert.notEqual(current.canResize, false, "Only the preferred display may run resize tests");
      await page.waitForResponse(async response => {
        if (new URL(response.url()).pathname !== "/api/displayState" || !response.ok()) return false;
        const state = await response.json(); return state.canControl && state.canResize !== false && state.transactionStatus === "idle";
      }, { timeout: 30000 });
      const cases = process.argv.includes("--supported-aspects") ? [
        ["public-4-3", 1200, 900, 4, 3], ["public-1-1", 700, 700, 1, 1],
        ["public-16-9", 1440, 810, 16, 9], ["public-9-16", 390, 844, 9, 16],
        ["public-3-4", 768, 1024, 3, 4],
      ] : [["public-portrait", 390, 844], ["public-restored", original.width, original.height]];
      for (const [label, width, height, a, b] of cases) {
        await page.setViewportSize({ width, height });
        const expected = await page.evaluate(async token => {
          const response = await fetch("/api/displayState", { headers: { "X-Display-Session": token } });
          if (!response.ok) throw new Error("Display state unavailable");
          const state = await response.json(), rect = document.querySelector("#home-stage").getBoundingClientRect();
          if (!state.canControl) throw new Error("Another browser owns input; do not steal control");
          return TAHtml.calculateDynamicSize({ width: rect.width, height: rect.height }, state.resolutionPreset,
            state, state.isResponsive === 1);
        }, session.token);
        assert.ok(expected);
        if (a && b) assert.equal(expected.width * b, expected.height * a);
        try { await page.waitForFunction(size => {
          const canvas = document.querySelector("#canvas"), layer = document.querySelector("#touch-layer");
          if (canvas?.dataset.videoState !== "ready" || Number(canvas.dataset.videoWidth) !== size.width ||
              Number(canvas.dataset.videoHeight) !== size.height || !layer) return false;
          const picture = canvas.getBoundingClientRect(), touch = layer.getBoundingClientRect();
          return ["x", "y", "width", "height"].every(key => Math.abs(picture[key] - touch[key]) < 1);
        }, expected, { timeout: 40000 }); } catch (error) {
          const facts = await page.evaluate(async token => {
            const response = await fetch("/api/displayState", { headers: { "X-Display-Session": token } });
            const state = response.ok ? await response.json() : {};
            const canvas = document.querySelector("#canvas"), layer = document.querySelector("#touch-layer");
            const box = element => {
              const rect = element?.getBoundingClientRect();
              return rect && [rect.x, rect.y, rect.width, rect.height];
            };
            return { apiStatus: response.status,
              state: Object.fromEntries(["width", "height", "resolutionPreset", "isResponsive", "generation",
                "canControl", "canResize", "transactionStatus"].map(key => [key, state[key]])),
              canvas: canvas && { ...canvas.dataset }, picture: box(canvas), touch: box(layer),
              focusedTag: document.activeElement?.tagName, hidden: document.hidden };
          }, session.token);
          console.log(JSON.stringify({ label, expected, ...facts }));
          throw error;
        }
        await check(label);
      }
    }
    for (let index = 0; index < 3; index++) {
      await page.locator("#home-settings-button").click();
      await page.locator('[data-page="settings"]').click();
      await page.waitForTimeout(1500);
      await page.locator('[data-page="home"]').click();
      await page.waitForTimeout(500);
      await check("public-settings-return-" + index);
    }
    await page.waitForTimeout(32000);
    await check("public-idle-watchdog");
    assert.deepEqual(errors, []);
    assert.ok(connections.includes("/sockets/display"));
    console.log(JSON.stringify({ pageErrors: errors, streaming: "H264", anonymous: true }));
  } finally {
    try {
      if (original && session && page) {
        // Stop viewport writes before restoring the user's starting display preferences.
        await page.route("**/api/displayState", route => route.request().method() === "POST" ? route.abort() : route.continue());
        for (let attempt = 0; attempt < 30; attempt++) {
          const response = await context.request.get(origin + "/api/displayState", {
            headers: { "X-Display-Session": session.token },
          });
          if (!response.ok()) break;
          const current = await response.json();
          if (current.transactionStatus !== "idle") { await page.waitForTimeout(250); continue; }
          if (desiredFields.every(key => original[key] === current[key])) break;
          assert.equal(current.canControl, true, "Cannot restore test display without ownership");
          const restored = await context.request.post(origin + "/api/displayState", { timeout: 25000,
            headers: { ...sessionHeaders, "X-Display-Session": session.token, "X-Display-CSRF": session.csrfToken },
            data: { ...Object.fromEntries(desiredFields.map(key => [key, original[key]])), generation: current.generation },
          });
          assert.equal(restored.status(), 200, "Original display preference restore failed");
          const result = await restored.json();
          for (const key of desiredFields) assert.equal(result[key], original[key]);
          console.log("Original display preferences restored");
          break;
        }
      }
    } finally { await browser.close(); }
  }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
