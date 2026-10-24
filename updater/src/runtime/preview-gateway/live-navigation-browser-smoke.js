// SPDX-License-Identifier: GPL-3.0-only
// Exact regression geometry, measured by live-navigation-visibility-smoke.js.
// Leaves the corrected dynamic dimensions, but never changes presets or density.
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright-core");
const { PNG } = require("../waydroid-display/node_modules/pngjs");
const origin = "https://teslavm.mondriandsm.synology.me";

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 804, height: 638 } });
  let session;
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", async response => {
    if (response.url().endsWith("/api/displaySession") && response.ok()) session = await response.json();
  });
  try {
    await page.goto(origin, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector("#canvas")?.dataset.videoState === "ready",
      undefined, { timeout: 40000 });
    await page.waitForTimeout(2500);
    assert.ok(session);
    const readState = () => page.evaluate(async token => (await fetch("/api/displayState",
      { headers: { "X-Display-Session": token } })).json(), session.token);
    let state;
    for (let attempt = 0; attempt < 120; attempt++) {
      state = await readState();
      assert.equal(state.canControl, true, "Another browser owns control; do not steal it");
      if (state.transactionStatus === "idle" && state.width === 852 && state.height === 676) break;
      await page.waitForTimeout(250);
    }
    assert.equal(state.canControl, true, "Another browser owns control; do not steal it");
    assert.equal(state.transactionStatus, "idle");
    assert.equal(state.width, 852, "Measured button geometry changed; refuse coordinate guesses");
    assert.equal(state.height, 676);
    assert.equal(state.density, 180);
    assert.equal(state.resolutionPreset, 1, "This regression fixture requires the user's original preset1");
    assert.equal(state.isResponsive, 1);
    await page.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoHeight) === 676,
      undefined, { timeout: 40000 });
    const capture = async () => PNG.sync.read(await page.locator("#canvas").screenshot());
    for (const [name, x] of [["recents", 580], ["back", 428], ["home", 504]]) {
      const before = await capture();
      const box = await page.locator("#touch-layer").boundingBox();
      assert.ok(box?.width && box?.height);
      const started = performance.now();
      await page.mouse.click(box.x + box.width * x / 852, box.y + box.height * 644 / 676);
      let changed = 0;
      const deadline = performance.now() + 8000;
      while (changed < 1000 && performance.now() < deadline) {
        await page.waitForTimeout(100);
        const after = await capture();
        assert.equal(after.width, before.width); assert.equal(after.height, before.height);
        changed = 0;
        // Exclude navbar highlights: a tap must change Android app content.
        for (let i = 0; i < before.width * (before.height - 80) * 4; i += 4) {
          if (Math.abs(before.data[i] - after.data[i]) + Math.abs(before.data[i + 1] - after.data[i + 1]) +
              Math.abs(before.data[i + 2] - after.data[i + 2]) > 75) changed++;
        }
      }
      assert.ok(changed >= 1000, `${name} did not visibly change Android content`);
      console.log(JSON.stringify({ button: name, changedPixels: changed,
        browserToPictureMs: Math.round(performance.now() - started) }));
      await page.waitForTimeout(800);
    }
    if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({
      path: path.join(process.env.SMOKE_SCREENSHOT_DIR, "navigation-buttons-852x676.png") });
    assert.deepEqual(errors, []);
    assert.equal(new URL(page.url()).searchParams.get("video"), null);
    console.log(JSON.stringify({ navigation: "browser-touch", width: 852, height: 676,
      density: 180, preset: 1, h264: true, pageErrors: errors }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
