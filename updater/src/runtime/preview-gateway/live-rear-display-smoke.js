// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict"), { chromium } = require("playwright-core");
const fs = require('node:fs'), { X509Certificate, createHash } = require('node:crypto');
const { PNG } = require("../waydroid-display/node_modules/pngjs");
const fields = ["width", "height", "density", "resolutionPreset", "isResponsive"];
async function main(args = process.argv.slice(2)) {
  assert.ok(args.length === 0 || args.length === 2 && args[0] === '--disposable', 'Explicit --disposable PUBLIC_CERT or original no-argument probe required');
  const disposable = args.length === 2;
  const origin = disposable ? 'https://192.168.2.215:8443' : 'https://teslavm.mondriandsm.synology.me';
  const launchArgs = ['--autoplay-policy=no-user-gesture-required'];
  if (disposable) {
    const certificate = new X509Certificate(fs.readFileSync(args[1]));
    assert.equal(certificate.checkIP('192.168.2.215'), '192.168.2.215');
    assert.equal(process.env.NODE_EXTRA_CA_CERTS, args[1], 'API client must explicitly trust the same public certificate');
    launchArgs.push('--ignore-certificate-errors-spki-list=' + createHash('sha256').update(
      certificate.publicKey.export({ type: 'spki', format: 'der' })).digest('base64'));
  }
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true, args: launchArgs });
  const contexts = [], peers = [], errors = []; let original, profile, changed = false;
  const headers = peer => ({ Origin: origin, "Content-Type": "application/json",
    "X-Display-Session": peer.session.token, "X-Display-CSRF": peer.session.csrfToken });
  const state = async peer => {
    const response = await peer.context.request.get(origin + "/api/displayState", { headers: headers(peer) });
    assert.equal(response.status(), 200); return response.json();
  };
  async function idle(peer, predicate = () => true) {
    const deadline = Date.now() + 35000;
    while (Date.now() < deadline) {
      const current = await state(peer);
      assert.ok(!["authority_failed", "recovery_required"].includes(current.transactionStatus));
      if (current.transactionStatus === "idle" && predicate(current)) return current;
      await peer.page.waitForTimeout(200);
    }
    throw new Error("Rear authority did not settle");
  }
  async function visible(peer) {
    await peer.page.waitForFunction(() => document.querySelector("#canvas")?.dataset.videoState === "ready" &&
      Number(document.querySelector("#canvas").dataset.videoFrames) > 0, undefined, { timeout: 35000 });
    const png = PNG.sync.read(await peer.page.locator("#canvas").screenshot()); let nonblack = 0;
    for (let i = 0; i < png.data.length; i += 4) if (png.data[i] > 8 || png.data[i + 1] > 8 || png.data[i + 2] > 8) nonblack++;
    assert.ok(nonblack > png.width * png.height * .01, "Rear streaming returned black");
    assert.equal(new URL(peer.page.url()).searchParams.has("video"), false);
  }
  async function settings(peer) {
    console.error('Checking disposable display settings for ' + (peer.role || 'probe'));
    await peer.page.locator("#home-settings-button").click(); await peer.page.locator('[data-page="settings"]').click();
    await peer.page.locator('#settings-sidebar button').filter({ hasText: 'Display', exact: true }).click();
    await peer.page.locator("[data-rear-role]").waitFor();
  }
  try {
    const probe = await browser.newContext(); contexts.push(probe);
    const response = await probe.request.post(origin + "/api/displaySession", { headers: { Origin: origin, "Content-Type": "application/json" }, data: {} });
    assert.equal(response.status(), 200);
    const token = (await response.json()).token;
    original = await (await probe.request.get(origin + "/api/displayState", { headers: { "X-Display-Session": token } })).json();
    assert.equal(original.rearDisplayControlSupported, 1);
    for (const role of ["primary", "rear"]) {
      const context = await browser.newContext({ viewport: { width: original.width, height: original.height } }); contexts.push(context);
      const page = await context.newPage(), peer = { context, page, role }; peers.push(peer);
      page.on("pageerror", error => errors.push(error.message));
      page.on("response", async reply => { if (new URL(reply.url()).pathname === "/api/displaySession" && reply.ok()) peer.session = await reply.json(); });
      await page.addInitScript(value => {
        for (const key of ["DisplayRepository_isPrimaryDisplaySharedPreferencesKey", "flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey"]) localStorage.setItem(key, String(value === "primary"));
        window.VideoDecoder = undefined;
        window.rearDisplayCloses = 0;
        window.WebSocket = new Proxy(WebSocket, { construct(target, args) {
          const socket = new target(...args);
          if (new URL(args[0]).pathname === "/sockets/display") socket.addEventListener("close", () => { window.rearDisplayCloses++; });
          return socket;
        } });
      }, role);
      await page.goto(origin + "/", { waitUntil: "domcontentloaded" }); await visible(peer); assert.ok(peer.session);
      // Exercise the actual role-selection UI as well as startup preference restoration.
      await settings(peer);
      await page.locator('[data-rear-role] input[value="' + role + '"]').check();
      await idle(peer, current => current.displayRole === role);
      await page.locator('[data-page="home"]').click(); await visible(peer);
      await page.waitForFunction(() => typeof window.startAudioFromGesture === 'function');
      await page.evaluate(() => {
        const button = document.createElement('button'); button.id = 'rear-test-audio';
        button.textContent = 'Audio test'; button.style.cssText = 'position:fixed;top:0;right:0;z-index:2147483647';
        button.addEventListener('click', () => window.startAudioFromGesture()); document.body.appendChild(button);
      });
      await page.locator('#rear-test-audio').click();
      await page.locator('#rear-test-audio').evaluate(element => element.remove());
      await page.waitForFunction(() => [...document.querySelectorAll('audio')].some(audio => !audio.paused && audio.currentTime > 1),
        undefined, { timeout: 30000 }).catch(async error => {
          console.error(JSON.stringify({ role, audio: await page.locator('audio').evaluateAll(elements => elements.map(audio => ({
            time: audio.currentTime, paused: audio.paused, ready: audio.readyState, error: audio.error?.code || null }))) }));
          throw error;
        });
    }
    const [primary, rear] = peers;
    const owner = await idle(primary); assert.equal(owner.canControl, true, "Another browser owns control; do not steal it");
    profile = await (await primary.context.request.get(origin + "/api/rearDisplayState", { headers: headers(primary) })).json();
    assert.equal(profile.enabled, false, "Do not overwrite a user's enabled rear configuration for this probe");
    await settings(primary); await settings(rear);
    assert.equal(await rear.page.locator("[data-rear-shared] input").first().isDisabled(), true);
    const before = await Promise.all(peers.map(async peer => peer.page.evaluate(() => ({ closes: window.rearDisplayCloses, time: document.querySelector('audio').currentTime }))));
    changed = true;
    await primary.page.locator('[data-rear-shared]').click();
    const enabled = await idle(primary, current => current.isRearDisplayEnabled === 1);
    if (!enabled.isRearDisplayPrioritised) {
      // The current merged Display panel exposes only the shared enable switch.
      // Exercise the supported priority API rather than a removed second tile.
      const reply = await primary.context.request.post(origin + '/api/rearDisplayState', {
        headers: headers(primary), data: { revision: enabled.rearDisplayRevision, enabled: true, prioritised: true } });
      assert.equal(reply.status(), 200);
    }
    await idle(rear, current => current.canControl && current.canResize);
    await primary.page.waitForTimeout(1500);
    assert.equal((await state(primary)).canControl, false);
    assert.equal(await primary.page.locator("[data-rear-shared] input").first().isDisabled(), true);
    for (const [index, peer] of peers.entries()) {
      const after = await peer.page.evaluate(() => ({ closes: window.rearDisplayCloses, time: document.querySelector('audio').currentTime,
        paused: document.querySelector('audio').paused, error: document.querySelector('audio').error?.code || null }));
      assert.equal(after.closes, before[index].closes, "Handoff closed a video stream");
      assert.ok(after.time > before[index].time); assert.equal(after.paused, false); assert.equal(after.error, null);
      await peer.page.locator('[data-page="home"]').click(); await visible(peer);
    }
    console.log(JSON.stringify({ origin, disposable, rearPriority: true, nativeAcknowledged: true, dualH264: true, dualAudioContinuous: true,
      renderer: "legacy-capability", pageErrors: errors })); assert.deepEqual(errors, []);
  } finally {
    try {
      for (const peer of peers) await peer.page.route("**/api/displayState", route => route.request().method() === "POST" ? route.abort() : route.continue());
      if (changed && profile) {
        let owner;
        for (const peer of peers) if ((await idle(peer)).canControl) owner = peer;
        assert.ok(owner, "Missing owner for rear preference restoration");
        const current = await state(owner);
        const reply = await owner.context.request.post(origin + "/api/rearDisplayState", { timeout: 35000, headers: headers(owner),
          data: { revision: current.rearDisplayRevision, enabled: profile.enabled, prioritised: profile.prioritised } });
        assert.equal(reply.status(), 200, "Rear preferences restore failed");
      }
      if (original && peers.length) {
        let owner;
        for (const peer of peers) if ((await idle(peer)).canControl) owner = peer;
        if (owner) {
          const current = await state(owner);
          if (fields.some(key => current[key] !== original[key])) {
            const reply = await owner.context.request.post(origin + "/api/displayState", { timeout: 30000, headers: headers(owner),
              data: { ...Object.fromEntries(fields.map(key => [key, original[key]])), generation: current.generation } });
            assert.equal(reply.status(), 200, "Display preferences restore failed");
          }
        }
      }
    } finally { await browser.close(); }
  }
}
if (require.main === module) main().catch(error => { console.error(error.stack); process.exitCode = 1; });
