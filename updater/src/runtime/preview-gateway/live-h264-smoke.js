// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright-core");
const { PNG } = require("../waydroid-display/node_modules/pngjs");
async function main() {
  const base = process.argv[2];
  assert.equal(base, "http://127.0.0.1:18444/");
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const context = await browser.newContext({ viewport: { width: 976, height: 720 } });
  let session;
  const page = await context.newPage();
  page.on("pageerror", error => console.error("pageerror: " + error.message));
  page.on("console", message => { if (["error", "warning"].includes(message.type())) console.error(message.text()); });
  page.on("response", async response => {
    if (response.url().endsWith("/api/displaySession") && response.ok()) session = await response.json();
  });
  try {
    await page.goto(base);
    async function check(label) {
      console.log(JSON.stringify({ checkpoint: label, url: page.url(), video: await page.locator("#canvas").evaluate(canvas => ({ ...canvas.dataset })) }));
      await page.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoFrames) >= 10, undefined, { timeout: 40000 });
      assert.equal(new URL(page.url()).searchParams.get("video"), null, "Unexpected PNG fallback");
      const state = await page.evaluate(async token => (await fetch("/api/displayState", { headers: { "X-Display-Session": token } })).json(), session.token);
      await page.waitForFunction(size => {
        const data = document.querySelector("#canvas").dataset;
        return Number(data.videoWidth) === size.width && Number(data.videoHeight) === size.height;
      }, state, { timeout: 40000 });
      const geometry = await page.evaluate(() => {
        const a = document.querySelector("#canvas").getBoundingClientRect(), b = document.querySelector("#touch-layer").getBoundingClientRect();
        return { canvas: [a.x, a.y, a.width, a.height], touch: [b.x, b.y, b.width, b.height],
          frames: Number(document.querySelector("#canvas").dataset.videoFrames) };
      });
      assert.deepEqual(geometry.canvas, geometry.touch);
      const pixels = PNG.sync.read(await page.locator("#canvas").screenshot());
      let colored = 0;
      for (let i = 0; i < pixels.data.length; i += 4) if (pixels.data[i] > 8 || pixels.data[i + 1] > 8 || pixels.data[i + 2] > 8) colored++;
      assert.ok(colored > pixels.width * pixels.height * 0.01, "Black video");
      if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, label + ".png") });
      console.log(JSON.stringify({ label, width: state.width, height: state.height, frames: geometry.frames, colored, overlayAligned: true }));
    }
    await check("live-h264-desktop");
    if (process.argv.includes("--fallback")) {
      console.log("WAITING_FOR_VIDEO_ONLY_STOP");
      await page.waitForURL("**/*video=png*", { timeout: 60000 });
      await page.waitForFunction(() => document.querySelector("#image")?.naturalWidth > 0, undefined, { timeout: 40000 });
      console.log("Automatic PNG fallback rendered successfully");
    } else {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(18000);
      await check("live-h264-portrait");
      await page.setViewportSize({ width: 976, height: 720 });
      await page.waitForTimeout(18000);
      await check("live-h264-restored");
    }
  } catch (error) {
    console.error(JSON.stringify({ failure: page.url(), video: await page.locator("#canvas").evaluate(canvas => ({ ...canvas.dataset })).catch(() => ({})) }));
    if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, "live-h264-failure.png"), timeout: 5000 }).catch(() => {});
    throw error;
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
