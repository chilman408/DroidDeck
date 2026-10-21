// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");
const { PNG } = require("../waydroid-display/node_modules/pngjs");
async function main() {
  const base = process.argv[2];
  assert.equal(base, "http://127.0.0.1:18445/");
  const server = await chromium.launchServer({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const browser = await chromium.connect(server.wsEndpoint());
  const page = await browser.newPage({ viewport: { width: 1306, height: 960 } });
  await page.addInitScript(() => {
    window.testTouchSockets = []; window.testPointerEvents = []; window.testVideoSessionFrames = [];
    const Native = WebSocket;
    window.WebSocket = new Proxy(Native, { construct(target, args) {
      const socket = new target(...args);
      if (new URL(args[0]).pathname === "/sockets/display") socket.addEventListener("message", event => {
        if (typeof event.data === "string" && event.data.includes('"scrcpy-session"')) {
          window.testVideoSessionFrames.push(Number(document.querySelector("#canvas")?.dataset.videoFrames || 0));
        }
      });
      if (new URL(args[0]).pathname === "/sockets/touchscreen") {
        socket.testSends = 0; const send = socket.send.bind(socket);
        socket.send = payload => { socket.testSends++; return send(payload); };
        window.testTouchSockets.push(socket);
      }
      return socket;
    } });
    document.addEventListener("pointerdown", event => window.testPointerEvents.push(event.target.id), true);
  });
  const errors = [], connections = []; let session;
  page.on("pageerror", error => errors.push(error.message));
  page.on("websocket", ws => connections.push(new URL(ws.url()).pathname));
  page.on("response", async response => {
    if (response.url().endsWith("/api/displaySession") && response.ok()) session = await response.json();
  });
  try {
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoFrames) > 0 &&
      document.querySelector("#home-audio-button")?.title === "Enable browser audio", undefined, { timeout: 40000 });
    async function pixels(label) {
      assert.equal(new URL(page.url()).searchParams.get("video"), null, "Unexpected PNG fallback");
      const png = PNG.sync.read(await page.locator("#canvas").screenshot());
      let colored = 0;
      for (let i = 0; i < png.data.length; i += 4) if (png.data[i] > 8 || png.data[i + 1] > 8 || png.data[i + 2] > 8) colored++;
      console.log(JSON.stringify({ label, colored, pixels: png.width * png.height,
        video: await page.locator("#canvas").evaluate(canvas => ({ ...canvas.dataset })) }));
      assert.ok(colored > png.width * png.height * 0.01, "Black video: " + label);
    }
    await pixels("initial");
    for (let i = 0; i < 5; i++) { await page.waitForTimeout(1100); await pixels("idle-poll-" + i); }
    if (!process.argv.includes("--touch-only")) {
    for (let i = 0; i < 3; i++) {
      await page.locator("#home-settings-button").click();
      await page.locator('[data-page="settings"]').click();
      await page.waitForTimeout(2200);
      await page.locator('[data-page="home"]').click();
      await page.waitForTimeout(200);
      await pixels("settings-return-" + i);
    }
    await page.waitForTimeout(32000);
    await pixels("idle-over-watchdog");
    const priorSessions = await page.evaluate(() => window.testVideoSessionFrames.length);
    await page.evaluate(() => window.requestDisplayStreamReconnect());
    await page.waitForFunction(prior => window.testVideoSessionFrames.length > prior &&
      Number(document.querySelector("#canvas").dataset.videoFrames) > window.testVideoSessionFrames.at(-1),
      priorSessions, { polling: 50, timeout: 10000 });
    await pixels("H264-reconnected");
    }
    if (process.argv.includes("--touch") || process.argv.includes("--touch-only")) {
      // Run only while the public gateway is stopped and Android show_touches is temporarily enabled.
      const box = await page.locator("#touch-layer").boundingBox(), latency = [];
      for (const fraction of [0.2, 0.5, 0.8, 0.2, 0.5, 0.8]) {
        const x = box.x + box.width * fraction, y = box.y + box.height * 0.4;
        await page.mouse.move(x, y);
        const clip = { x: Math.floor(x - 16), y: Math.floor(y - 16), width: 32, height: 32 };
        const baseline = PNG.sync.read(await page.screenshot({ clip }));
        const before = await page.locator("#canvas").evaluate(canvas => Number(canvas.dataset.videoFrames));
        const start = performance.now();
        try {
          await page.mouse.down();
          await page.waitForFunction(value => Number(document.querySelector("#canvas").dataset.videoFrames) > value,
            before, { polling: 16, timeout: 5000 });
          let changed = 0;
          await assertEventually(async () => {
            const png = PNG.sync.read(await page.screenshot({ clip })); changed = 0;
            for (let i = 0; i < png.data.length; i += 4) {
              if (Math.abs(png.data[i] - baseline.data[i]) + Math.abs(png.data[i + 1] - baseline.data[i + 1]) +
                Math.abs(png.data[i + 2] - baseline.data[i + 2]) > 75) changed++;
            }
            return changed > 50;
          }, 5000);
          latency.push({ ms: Math.round(performance.now() - start), changedPixels: changed });
        } finally { await page.mouse.up(); }
        await page.waitForTimeout(350);
      }
      console.log(JSON.stringify({ pointerToVisiblePixelsMs: latency }));
    }
    const state = await page.evaluate(async token => (await fetch("/api/displayState", { headers: { "X-Display-Session": token } })).json(), session.token);
    assert.equal(state.width, 980); assert.equal(state.height, 720);
    assert.equal(state.recoveryRequired, false);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ connections, pageErrors: errors, state }));
  } catch (error) {
    console.error(JSON.stringify(await page.evaluate(() => ({ sockets: window.testTouchSockets.map(socket =>
      ({ readyState: socket.readyState, sends: socket.testSends })), targets: window.testPointerEvents,
      video: { ...document.querySelector("#canvas").dataset }, pointerEvents: document.querySelector("#touch-layer").style.pointerEvents }))));
    throw error;
  } finally {
    await Promise.race([browser.close(), new Promise(resolve => setTimeout(resolve, 3000))]);
    await Promise.race([server.close(), new Promise(resolve => setTimeout(resolve, 3000))]);
    if (server.process().exitCode == null) server.process().kill();
  }
}
async function assertEventually(predicate, timeout) {
  const deadline = performance.now() + timeout;
  while (!(await predicate())) {
    assert.ok(performance.now() < deadline, "Expected live observation before deadline");
    await new Promise(resolve => setTimeout(resolve, 30));
  }
}
if (require.main === module) main().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
