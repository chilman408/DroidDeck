// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright-core");
async function main() {
  const base = process.argv[2]; assert.ok(["http://127.0.0.1:18444/", "http://127.0.0.1:18445/"].includes(base));
  const controlsOnly = process.argv.includes("--controls-only"), signalOnly = process.argv.includes("--signal-only");
  const browserServer = await chromium.launchServer({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const browser = await chromium.connect(browserServer.wsEndpoint());
  console.log(JSON.stringify({ testBrowserPid: browserServer.process().pid }));
  const page = await browser.newPage({ viewport: { width: 976, height: 720 } });
  await page.addInitScript(() => {
    const Native = window.AudioContext;
    window.AudioContext = class extends Native { constructor(...args) { super(...args); window.audioTestContext = this; }
      createGain() { const node = super.createGain(); if (!window.audioTestGain) window.audioTestGain = node; return node; } };
  });
  let session; const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (["error", "warning"].includes(message.type())) console.error(message.text()); });
  page.on("response", async response => { if (response.url().endsWith("/api/displaySession") && response.ok()) session = await response.json(); });
  page.on("response", async response => { if (response.status() >= 400 && new URL(response.url()).pathname.startsWith("/api/"))
    console.error(JSON.stringify({ path: new URL(response.url()).pathname, status: response.status(), body: await response.text().catch(() => "<closed>") })); });
  try {
    await page.goto(process.argv.includes("--png") ? base + "?video=png" : base, { waitUntil: "domcontentloaded" });
    console.log("Audio test: page loaded");
    let measured;
    if (!controlsOnly) {
    await page.waitForFunction(() => {
      const button = document.querySelector("#home-audio-button");
      return button && !button.hidden && button.title === "Enable browser audio";
    }, undefined, { polling: 100, timeout: 30000 });
    await page.locator("#home-audio-button").click();
    console.log("Audio test: gesture sent");
    await page.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused && window.__liveAudioEl.currentTime > 0.5, undefined, { polling: 100, timeout: 30000 });
    console.log("Audio test: clock advancing");
    await page.evaluate(() => {
      const ctx = window.audioTestContext, analyser = ctx.createAnalyser(), silent = ctx.createGain();
      silent.gain.value = 0; window.audioTestGain.connect(analyser); analyser.connect(silent); silent.connect(ctx.destination);
      window.audioTestAnalyser = analyser;
    });
    await page.waitForFunction(() => {
      const samples = new Float32Array(2048); window.audioTestAnalyser.getFloatTimeDomainData(samples);
      return samples.some(value => Math.abs(value) > 0.00001);
    }, undefined, { polling: 100, timeout: 15000 });
    measured = await page.evaluate(() => {
      const samples = new Float32Array(2048); window.audioTestAnalyser.getFloatTimeDomainData(samples);
      return { currentTime: window.__liveAudioEl.currentTime, peak: Math.max(...samples.map(Math.abs)), gain: window.audioTestGain.gain.value };
    });
    assert.ok(measured.peak > 0);
    console.log(JSON.stringify({ signal: measured }));
    if (signalOnly) return;
    }
    await page.locator("#home-settings-button").click();
    await page.locator('[data-page="settings"]').click();
    await page.locator("#settings-sidebar button").filter({ hasText: "Audio" }).click();
    const settingsState = await page.evaluate(async token => (await fetch("/api/displayState", { headers: { "X-Display-Session": token } })).json(), session.token);
    assert.equal(settingsState.canControl, true, "Run with a fresh isolated gateway and no other owner");
    const slider = page.locator(".settings-volume-slider"); await slider.waitFor();
    await slider.evaluate(input => { input.value = "150"; input.dispatchEvent(new Event("change", { bubbles: true })); });
    await page.waitForFunction(controls => controls ? document.querySelector(".settings-volume-slider")?.value === "150" :
      window.audioTestGain.gain.value === 1.5, controlsOnly, { polling: 100, timeout: 10000 });
    await page.waitForFunction(async () => (await (await fetch("/api/configuration")).json())["persist.tesla-android.browser_audio.volume"] === 150,
      undefined, { polling: 100, timeout: 10000 });
    const savedVolume = await page.evaluate(async () => (await fetch("/api/configuration")).json());
    assert.equal(savedVolume["persist.tesla-android.browser_audio.volume"], 150);
    console.log("Audio test: volume saved");
    const disabled = page.locator("#settings-content .settings-switch"); await disabled.click();
    await page.waitForFunction(() => (!window.__liveAudioEl || window.__liveAudioEl.paused) && window.getAudioEnabled() === false, undefined, { polling: 100, timeout: 10000 });
    await page.waitForTimeout(1200);
    const config = await page.evaluate(async () => (await fetch("/api/configuration")).json());
    assert.equal(config["persist.tesla-android.browser_audio.is_enabled"], 0);
    assert.equal(config["persist.tesla-android.browser_audio.volume"], 100);
    await disabled.click();
    await page.waitForFunction(() => window.getAudioEnabled() === true, undefined, { polling: 100, timeout: 10000 });
    const state = await page.evaluate(async token => (await fetch("/api/displayState", { headers: { "X-Display-Session": token } })).json(), session.token);
    assert.equal(state.width, 976); assert.equal(state.height, 720); assert.equal(state.recoveryRequired, false);
    if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, "live-audio-settings.png") });
    console.log(JSON.stringify({ decodedPlayback: measured, volume150: true, disabledConfiguration: true,
      disabledStopsPlayback: !controlsOnly, reenabled: true, display: state, pageErrors: errors }));
    assert.deepEqual(errors, []);
  } catch (error) {
    console.error(error);
    console.error(JSON.stringify(await Promise.race([page.evaluate(() => ({ context: window.audioTestContext?.state,
      playing: !window.__liveAudioEl?.paused, currentTime: window.__liveAudioEl?.currentTime,
      gain: window.audioTestGain?.gain.value, audioError: window.__liveAudioEl?.error?.message })).catch(() => ({})),
      new Promise(resolve => setTimeout(() => resolve({ diagnosticsTimedOut: true }), 3000))])));
    if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, "live-audio-failure.png"), timeout: 5000 }).catch(() => {});
    throw error;
  } finally {
    await Promise.race([browser.close(), new Promise(resolve => setTimeout(resolve, 5000))]);
    await Promise.race([browserServer.kill(), new Promise(resolve => setTimeout(resolve, 5000))]);
  }
}
if (require.main === module) main().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
