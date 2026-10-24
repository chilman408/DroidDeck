// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright-core");
const origin = "https://teslavm.mondriandsm.synology.me";
const volumeKey = "persist.tesla-android.browser_audio.volume";
const enabledKey = "persist.tesla-android.browser_audio.is_enabled";
async function main() {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const context = await browser.newContext(), errors = [];
  let original, lastVolume, token, csrf, page, viewerContext;
  try {
    const configuration = await context.request.get(origin + "/api/configuration"); assert.equal(configuration.status(), 200);
    original = await configuration.json(); assert.equal(original.browserAudioGainSupported, 1);
    assert.equal(original[enabledKey], 1, "Do not enable a user's disabled audio setting for this probe");
    const created = await context.request.post(origin + "/api/displaySession", {
      headers: { Origin: origin, "Content-Type": "application/json" }, data: {},
    }); assert.equal(created.status(), 200);
    const initialSession = await created.json();
    const state = await (await context.request.get(origin + "/api/displayState", {
      headers: { "X-Display-Session": initialSession.token },
    })).json();
    page = await context.newPage(); await page.setViewportSize({ width: state.width, height: state.height });
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      if (new URL(request.url()).pathname === "/api/browserAudioVolume" && request.method() === "POST") {
        const headers = request.headers(); token = headers["x-display-session"]; csrf = headers["x-display-csrf"];
      }
    });
    await page.addInitScript(() => {
      window.gainTestVideoSessions = 0; window.gainTestAudioGraphs = 0;
      const Native = WebSocket;
      window.WebSocket = new Proxy(Native, { construct(target, args) {
        const socket = new target(...args);
        if (new URL(args[0]).pathname === "/sockets/display") socket.addEventListener("message", event => {
          if (typeof event.data === "string" && event.data.includes('"scrcpy-session"')) window.gainTestVideoSessions++;
        });
        return socket;
      } });
      const prototype = window.AudioContext?.prototype;
      if (prototype?.createMediaElementSource) {
        const original = prototype.createMediaElementSource;
        prototype.createMediaElementSource = function (...args) { window.gainTestAudioGraphs++; return original.apply(this, args); };
      }
    });
    if (process.argv.includes("--legacy-capability")) await page.addInitScript(() => { window.VideoDecoder = undefined; });
    await page.goto(origin + "/", { waitUntil: "domcontentloaded" });
    const ready = () => page.waitForFunction(() => document.querySelector("#canvas")?.dataset.videoState === "ready" &&
      Number(document.querySelector("#canvas").dataset.videoFrames) > 0, undefined, { timeout: 30000 });
    const owned = () => page.waitForResponse(async response => {
      if (new URL(response.url()).pathname !== "/api/displayState" || response.status() !== 200) return false;
      const state = await response.json(); return state.canControl === true && state.transactionStatus === "idle";
    }, { timeout: 30000 });
    await ready(); await owned(); await page.locator("#home-audio-button").click();
    await page.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused && window.__liveAudioEl.currentTime > 1,
      undefined, { timeout: 30000 });
    const audioBefore = await page.evaluate(() => window.__liveAudioEl.currentTime);
    const sessionsBefore = await page.evaluate(() => window.gainTestVideoSessions);
    const settings = async () => {
      await page.locator("#home-settings-button").click(); await page.locator('[data-page="settings"]').click();
      await page.locator("#settings-sidebar button").filter({ hasText: "Audio", exact: true }).click();
      await page.locator(".settings-volume-slider").waitFor();
    };
    for (const volume of [0, 50, 100, 150]) {
      await settings();
      const response = page.waitForResponse(res => new URL(res.url()).pathname === "/api/browserAudioVolume" && res.request().method() === "POST");
      await page.locator(".settings-volume-slider").evaluate((slider, value) => {
        slider.value = String(value); slider.dispatchEvent(new Event("change", { bubbles: true }));
      }, volume);
      assert.equal((await response).status(), 200); lastVolume = volume;
      await page.waitForFunction(value => document.querySelector(".settings-volume-slider")?.value === String(value), volume);
      const saved = await (await context.request.get(origin + "/api/configuration")).json(); assert.equal(saved[volumeKey], volume);
      const priorTime = await page.evaluate(() => window.__liveAudioEl.currentTime);
      await page.locator('[data-page="home"]').click(); await ready();
      await page.waitForFunction(time => window.__liveAudioEl.currentTime > time + 0.6, priorTime, { timeout: 10000 });
      assert.equal(await page.evaluate(() => window.__liveAudioEl.volume), 1);
      assert.equal(await page.evaluate(() => window.gainTestVideoSessions), sessionsBefore);
    }
    viewerContext = await browser.newContext();
    const viewerSession = await (await viewerContext.request.post(origin + "/api/displaySession", {
      headers: { Origin: origin, "Content-Type": "application/json" }, data: {},
    })).json();
    const denied = await viewerContext.request.post(origin + "/api/browserAudioVolume", { headers: {
      Origin: origin, "Content-Type": "text/plain", "X-Display-Session": viewerSession.token, "X-Display-CSRF": viewerSession.csrfToken,
    }, data: "100" }); assert.equal(denied.status(), 403);
    const viewer = await viewerContext.newPage(); await viewer.setViewportSize({ width: state.width, height: state.height });
    viewer.on("pageerror", error => errors.push(error.message));
    if (process.argv.includes("--legacy-capability")) await viewer.addInitScript(() => { window.VideoDecoder = undefined; });
    await viewer.goto(origin + "/", { waitUntil: "domcontentloaded" });
    await viewer.waitForFunction(() => document.querySelector("#canvas")?.dataset.videoState === "ready", undefined, { timeout: 30000 });
    await viewer.locator("#home-audio-button").click();
    await viewer.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused && window.__liveAudioEl.currentTime > 0.5,
      undefined, { timeout: 30000 });
    await viewer.locator("#home-settings-button").click(); await viewer.locator('[data-page="settings"]').click();
    await viewer.locator("#settings-sidebar button").filter({ hasText: "Audio", exact: true }).click();
    await viewer.locator(".settings-volume-slider").waitFor();
    assert.equal(await viewer.locator(".settings-volume-slider").isDisabled(), true);
    assert.equal(await viewer.locator("#settings-content .settings-switch input").isDisabled(), true);
    await viewerContext.close(); viewerContext = undefined;
    await page.reload({ waitUntil: "domcontentloaded" }); await ready(); await owned(); await settings();
    assert.equal(await page.locator(".settings-volume-slider").inputValue(), "150");
    // The page reload has renewed the owner; acquire its headers with an unchanged submission.
    const renewed = page.waitForResponse(res => new URL(res.url()).pathname === "/api/browserAudioVolume" && res.request().method() === "POST");
    await page.locator(".settings-volume-slider").evaluate(slider => slider.dispatchEvent(new Event("change", { bubbles: true })));
    assert.equal((await renewed).status(), 200);
    if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, "audio-gain.png") });
    assert.deepEqual(errors, []); assert.equal(await page.evaluate(() => window.gainTestAudioGraphs), 0);
    console.log(JSON.stringify({ renderer: process.argv.includes("--legacy-capability") ? "legacy" : "webcodecs",
      volumes: [0, 50, 100, 150], audioBefore, continuousBeforeReload: true, videoSessionsUnchanged: true,
      persisted: true, viewerDenied: true, viewerUiDisabled: true, viewerCanListen: true, noWebAudioGraph: true, errors }));
  } catch (error) {
    console.error(error);
    if (page) console.error(JSON.stringify(await page.evaluate(() => ({
      videoState: document.querySelector('#canvas')?.dataset.videoState,
      audioTime: window.__liveAudioEl?.currentTime, paused: window.__liveAudioEl?.paused,
      volume: window.__liveAudioEl?.volume, audioGraphs: window.gainTestAudioGraphs,
    })).catch(() => ({ pageClosed: true }))));
    throw error;
  } finally {
    try {
      if (original && lastVolume !== undefined && token && csrf) {
        const current = await (await context.request.get(origin + "/api/configuration")).json();
        if (current[volumeKey] !== lastVolume || current[enabledKey] !== original[enabledKey]) {
          throw new Error("Audio preferences changed concurrently; original volume not restored");
        }
        if (current[volumeKey] === original[volumeKey]) {
          console.log(JSON.stringify({ originalVolumeUnchanged: original[volumeKey] }));
        } else {
          const restored = await context.request.post(origin + "/api/browserAudioVolume", { headers: {
            Origin: origin, "Content-Type": "text/plain", "X-Display-Session": token, "X-Display-CSRF": csrf,
          }, data: String(original[volumeKey]) });
          assert.equal(restored.status(), 200, "Restore original volume with the current owner session");
          console.log(JSON.stringify({ restoredVolume: original[volumeKey] }));
        }
      }
    } finally { await viewerContext?.close(); await context.close(); await browser.close(); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
