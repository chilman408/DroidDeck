// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright-core");
const { PNG } = require("../waydroid-display/node_modules/pngjs");
const origin = "https://teslavm.mondriandsm.synology.me";

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  try {
    const modes = process.argv.includes('--legacy-only') ? [true] :
      process.argv.includes('--webcodecs-only') ? [false] : [false, true];
    for (const legacy of modes) {
      const context = await browser.newContext();
      try {
        const sessionResponse = await context.request.post(origin + "/api/displaySession", {
          headers: { Origin: origin, "Content-Type": "application/json" }, data: {},
        });
        assert.equal(sessionResponse.status(), 200);
        const session = await sessionResponse.json();
        const stateResponse = await context.request.get(origin + "/api/displayState", {
          headers: { "X-Display-Session": session.token },
        });
        assert.equal(stateResponse.status(), 200);
        const state = await stateResponse.json();
        const page = await context.newPage();
        await page.setViewportSize({ width: state.width, height: state.height });
        await page.addInitScript(() => {
          window.testDisplaySessions = 0;
          const Native = WebSocket;
          window.WebSocket = new Proxy(Native, { construct(target, args) {
            const socket = new target(...args);
            if (new URL(args[0]).pathname === '/sockets/display') socket.addEventListener('message', event => {
              if (typeof event.data === 'string' && event.data.includes('"scrcpy-session"')) window.testDisplaySessions++;
            });
            return socket;
          } });
        });
        const errors = [];
        const consoleErrors = [];
        page.on("pageerror", error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
        // Test the actual compatibility branch, not a changed user-agent string.
        if (legacy) await page.addInitScript(() => { window.VideoDecoder = undefined; });
        if (process.env.TA_FRONTEND_DIR) {
          await page.route(origin + "/js/**", async route => {
            const relative = new URL(route.request().url()).pathname.slice(1);
            const root = path.resolve(process.env.TA_FRONTEND_DIR);
            const file = path.resolve(root, relative);
            assert.ok(file.startsWith(root + path.sep));
            await route.fulfill({ body: fs.readFileSync(file), contentType: file.endsWith(".wasm") ? "application/wasm" : "text/javascript" });
          });
        }
        await page.goto(origin + "/", { waitUntil: "domcontentloaded", timeout: 20000 });
        try {
          await page.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoFrames) > 0,
            undefined, { timeout: 30000 });
        } catch (error) {
          console.error(JSON.stringify({ decoder: legacy ? 'legacy' : 'webcodecs', errors, consoleErrors,
            video: await page.locator('#canvas').evaluate(canvas => ({ ...canvas.dataset })) }));
          throw error;
        }
        const pixels = PNG.sync.read(await page.locator("#canvas").screenshot());
        let nonblack = 0;
        for (let i = 0; i < pixels.data.length; i += 4) {
          if (pixels.data[i] > 8 || pixels.data[i + 1] > 8 || pixels.data[i + 2] > 8) nonblack++;
        }
        assert.ok(nonblack > pixels.width * pixels.height * 0.01, "Decoded video must not be black");
        if (legacy) assert.equal(await page.locator("#canvas").getAttribute("data-video-decoder"), "legacy");
        await page.locator("#home-audio-button").click();
        await page.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused &&
          window.__liveAudioEl.currentTime > 1, undefined, { timeout: 30000 });
        const before = await page.evaluate(() => window.__liveAudioEl.currentTime);
        const framesBefore = Number(await page.locator("#canvas").getAttribute("data-video-frames"));
        await page.waitForTimeout(4000);
        const result = await page.evaluate(() => ({ audioTime: window.__liveAudioEl.currentTime,
          audioPaused: window.__liveAudioEl.paused, audioError: window.__liveAudioEl.error?.code || null,
          volume: window.__liveAudioEl.volume, video: { ...document.querySelector("#canvas").dataset } }));
        assert.ok(result.audioTime > before + 1);
        assert.equal(result.audioError, null);
        assert.equal(result.video.videoState, "ready");
        assert.deepEqual(errors, []);
        if (process.argv.includes('--audio-reconnect')) {
          const sessionsBefore = await page.evaluate(() => window.testDisplaySessions);
          for (let cycle = 0; cycle < 3; cycle++) {
            await page.locator('#home-audio-button').click();
            await page.waitForTimeout(12000);
            assert.equal(await page.evaluate(() => window.testDisplaySessions), sessionsBefore,
              'Stopping audio must not restart the video stream');
            assert.ok(Number(await page.locator('#canvas').getAttribute('data-video-frames')) > framesBefore + 10);
            await page.locator('#home-audio-button').click();
            await page.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused &&
              window.__liveAudioEl.currentTime > 1, undefined, { timeout: 30000 });
            assert.equal(await page.evaluate(() => window.testDisplaySessions), sessionsBefore,
              'Restarting audio must not restart the video stream');
          }
        }
        if (process.argv.includes('--stability')) {
          for (let i = 0; i < 3; i++) {
            await page.locator('#home-settings-button').click();
            await page.locator('[data-page="settings"]').click();
            await page.waitForTimeout(1200);
            await page.locator('[data-page="home"]').click();
            await page.waitForTimeout(300);
            const restored = PNG.sync.read(await page.locator('#canvas').screenshot());
            let visible = 0;
            for (let j = 0; j < restored.data.length; j += 4) {
              if (restored.data[j] > 8 || restored.data[j + 1] > 8 || restored.data[j + 2] > 8) visible++;
            }
            assert.ok(visible > restored.width * restored.height * 0.01, 'Settings return must not blank video');
          }
          const prior = Number(await page.locator('#canvas').getAttribute('data-video-frames'));
          await page.evaluate(() => window.requestDisplayStreamReconnect());
          await page.waitForFunction(value => Number(document.querySelector('#canvas').dataset.videoFrames) > value,
            prior, { timeout: 15000 });
          assert.deepEqual(errors, []);
        }
        if (process.argv.includes("--moving")) assert.ok(Number(result.video.videoFrames) > framesBefore + 10,
          "Moving Android content must keep rendering while audio plays");
        if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({
          path: path.join(process.env.SMOKE_SCREENSHOT_DIR, legacy ? "legacy.png" : "webcodecs.png"),
        });
        console.log(JSON.stringify({ decoder: legacy ? "legacy" : "webcodecs", framesBefore, nonblack, ...result,
          audioReconnectCycles: process.argv.includes('--audio-reconnect') ? 3 : 0,
          settingsReturns: process.argv.includes('--stability') ? 3 : 0,
          finalVideo: await page.locator('#canvas').evaluate(canvas => ({ ...canvas.dataset })),
          finalAudioTime: await page.evaluate(() => window.__liveAudioEl.currentTime), pageErrors: errors, consoleErrors }));
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
