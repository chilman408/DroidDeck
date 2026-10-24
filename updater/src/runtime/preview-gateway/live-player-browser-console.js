// SPDX-License-Identifier: GPL-3.0-only
// Bounded diagnostic console for single real browser taps; no injected keyevents.
const assert = require("node:assert/strict");
const path = require("node:path");
const readline = require("node:readline");
const { chromium } = require("playwright-core");
const origin = "https://teslavm.mondriandsm.synology.me";
async function main() {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 804, height: 638 } });
  await page.addInitScript(() => {
    window.playerProbePackets = [];
    const Native = window.WebSocket;
    window.WebSocket = new Proxy(Native, { construct(target, args) {
      const socket = new target(...args);
      if (new URL(args[0]).pathname === "/sockets/touchscreen") {
        const send = socket.send.bind(socket);
        socket.send = packet => { window.playerProbePackets.push(packet); return send(packet); };
      }
      return socket;
    } });
  });
  let session, input, timer;
  page.on("response", async response => {
    if (response.url().endsWith("/api/displaySession") && response.ok()) session = await response.json();
  });
  page.on("pageerror", error => console.error(error.message));
  const state = async () => {
    assert.ok(session);
    return page.evaluate(async token => (await fetch("/api/displayState",
      { headers: { "X-Display-Session": token } })).json(), session.token);
  };
  try {
    await page.goto(origin, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector("#canvas")?.dataset.videoState === "ready",
      undefined, { timeout: 40000 });
    const before = await state();
    console.log(JSON.stringify({ ready: true, width: before.width, height: before.height,
      owner: before.canControl, transaction: before.transactionStatus }));
    input = readline.createInterface({ input: process.stdin });
    const resetDeadline = () => { clearTimeout(timer); timer = setTimeout(() => input.close(), 180000); };
    resetDeadline();
    for await (const line of input) {
      resetDeadline();
      try {
        assert.ok(line.length < 512);
        const command = JSON.parse(line);
        if (command.command === "exit") break;
        const current = await state();
        if (command.command === "viewport") {
          assert.equal(current.canControl, true, "Another browser owns input");
          assert.equal(current.isResponsive, 1, "Do not enable disabled dynamic sizing");
          assert.ok([command.width, command.height].every(v => Number.isInteger(v) && v >= 96 && v <= 1920));
          await page.setViewportSize({ width: command.width, height: command.height });
          await page.waitForTimeout(4000);
        } else if (command.command === "tap") {
          assert.equal(current.canControl, true, "Another browser owns input");
          assert.equal(current.transactionStatus, "idle");
          assert.equal(current.width, command.width); assert.equal(current.height, command.height);
          assert.ok([command.x, command.y].every(Number.isInteger));
          assert.ok(command.x >= 0 && command.x < current.width && command.y >= 0 && command.y < current.height);
          const holdMs = command.holdMs ?? 0;
          assert.ok(Number.isInteger(holdMs) && holdMs >= 0 && holdMs <= 200);
          if (command.reveal) {
            assert.ok([command.reveal.x, command.reveal.y].every(Number.isInteger));
            assert.ok(command.reveal.x >= 0 && command.reveal.x < current.width &&
              command.reveal.y >= 0 && command.reveal.y < current.height);
          }
          await page.evaluate(() => { window.playerProbePackets = []; });
          const box = await page.locator("#touch-layer").boundingBox();
          assert.ok(box?.width && box?.height);
          if (command.reveal) {
            await page.mouse.click(box.x + box.width * command.reveal.x / current.width,
              box.y + box.height * command.reveal.y / current.height);
            await page.waitForTimeout(250);
          }
          await page.mouse.move(box.x + box.width * command.x / current.width,
            box.y + box.height * command.y / current.height);
          await page.mouse.down();
          try {
            if (holdMs) {
              await page.waitForTimeout(holdMs);
              if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({
                path: path.join(process.env.SMOKE_SCREENSHOT_DIR, "youtube-player-down.png") });
            }
          } finally { await page.mouse.up(); }
          await page.waitForTimeout(500);
        } else assert.equal(command.command, "capture");
        if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({
          path: path.join(process.env.SMOKE_SCREENSHOT_DIR, "youtube-player.png") });
        console.log(JSON.stringify({ command: command.command, width: current.width, height: current.height,
          owner: current.canControl, transaction: current.transactionStatus,
          video: await page.locator("#canvas").evaluate(canvas => ({ ...canvas.dataset })),
          touch: await page.evaluate(() => ({ width: window.displayWidth, height: window.displayHeight,
            packets: window.playerProbePackets })) }));
      } catch (error) { console.log(JSON.stringify({ refused: error.message })); }
    }
  } finally { clearTimeout(timer); input?.close(); await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
