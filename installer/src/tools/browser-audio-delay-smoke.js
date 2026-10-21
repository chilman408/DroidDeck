// SPDX-License-Identifier: GPL-3.0-only
// Browser-local playback checks only; no Android taps, recordings or shared settings writes.
const crypto = require('node:crypto');
const ORIGIN = 'https://teslavm.mondriandsm.synology.me';
const FILES = [
  ['js/audio/audioplayback.js', '4c18cf01f9d7a1d85b99cb9d2aa326a9ec649e3af57d057db7ec00f548c4e7a7'],
  ['js/settings/settings-controller.js', 'a939a150665faa7cbc730b88a7b949946d6adca6e259ee64b9b5ef801c16567d'],
];
const SIGNED_FILES = [
  ['js/audio/audioplayback.js', 'd76d86d1930d337edd7054f16e5c6281c3fab558a9ad26623290c3df7fb138c1'],
  ['js/display/renderers/h264-broadway/h264Brodway.js', '223dafda0847e4b1ee50579c9f0046c6554744898ba6b035ec258c635d9a39b7'],
  ['js/display/renderers/h264-webcodecs/h264WebCodecs.js', 'ba44f732c3e9ae03bff9b162150f52f32709c26349c809a7cb97bd65b384d32a'],
  ['js/settings/settings-controller.js', '56709feb6dbee943a5dadc03b31cdf8d37aab6293dfe35be6d6ed1cf5726fa64'],
];
const CLEANUP_FILES = [
  ['js/settings/settings-controller.js', '34c98c1e702fb64d645e2df2cbddd4b2d8cc3ec832e64645b8d36194f454ac58'],
  ['css/app.css', '59211a32f902170b3a3d94a9cc91111448b93b239d012a726bf912eeed62183f'],
];
let stage = 'initialization';
let audioProgress = null;
let bitrateMenuReadOnly = false;
let displayRoleReflected = false;
function bitrateMenuDecision(state, disabled) {
  if (typeof state?.canControl !== 'boolean' || state.transactionStatus !== 'idle') return 'wait';
  if (!state.canControl) return disabled ? 'viewer' : 'error';
  return disabled ? 'wait' : 'owner';
}
async function main() {
  const mode = process.argv[2];
  const roleStartup = mode === '--display-role-startup-live';
  const roleClaim = roleStartup || mode === '--display-role-live';
  const connection = roleClaim || mode === '--browser-connection-live';
  const bitrate3 = connection || mode === '--bitrate-3-candidate' || mode === '--bitrate-3-live';
  const donation = bitrate3 || mode === '--droiddeck-donation-candidate' || mode === '--droiddeck-donation-live';
  const brand = donation || mode === '--droiddeck-brand-candidate' || mode === '--droiddeck-brand-live';
  const profiles = require('./deploy-auto-h264-recovery');
  let brandFiles = profiles.deploymentProfile(['--droiddeck-brand']).files;
  if (donation) {
    const changes = profiles.deploymentProfile(['--droiddeck-donation']).files;
    brandFiles = [...brandFiles.map(item => changes.find(change => change.file === item.file) || { ...item, before: item.after }), changes[0]];
  }
  const bitrateFiles = profiles.deploymentProfile(['--bitrate-3-frontend']).files;
  if (bitrate3) brandFiles = [...brandFiles.map(item => ({ ...item, before: item.after })), ...bitrateFiles];
  const connectionFiles = profiles.deploymentProfile(['--browser-connection-frontend']).files;
  if (connection) brandFiles = [...brandFiles.filter(item => !connectionFiles.some(change => change.file === item.file)), ...connectionFiles];
  if (roleClaim) {
    const changes = profiles.deploymentProfile(['--display-role-frontend']).files;
    brandFiles = [...brandFiles.filter(item => !changes.some(change => change.file === item.file)), ...changes];
  }
  if (roleStartup) {
    const change = profiles.deploymentProfile(['--display-role-startup']).files[0];
    brandFiles = brandFiles.map(item => item.file === change.file ? change : item);
  }
  const projectUpdates = brand || mode === '--project-updates-candidate' || mode === '--project-updates-live';
  const cleanup = projectUpdates || mode === '--settings-cleanup-candidate' || mode === '--settings-cleanup-live';
  const signed = cleanup || mode === '--signed-candidate' || mode === '--signed-live';
  const candidate = mode === '--bitrate-3-candidate' || mode === '--droiddeck-donation-candidate' || mode === '--droiddeck-brand-candidate' || mode === '--project-updates-candidate' || mode === '--settings-cleanup-candidate' || mode === '--signed-candidate';
  const network = signed || mode === '--network-restore';
  if (process.platform !== 'win32' || process.argv.length > 3 || (mode && !network)) throw new Error('Fixed Windows smoke test required');
  const { chromium } = require('playwright-core');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 904, height: 678 } });
    if (candidate) {
      const fs = require('node:fs'), path = require('node:path');
      for (const [relative, sha] of brand ? brandFiles.map(item => [item.file, item.after]) : projectUpdates ? [['js/core/api-client.js', '8299e34eea9145b735614a57b158fdac08aa31e925315da49025067a25a3f97a']] : cleanup ? CLEANUP_FILES : SIGNED_FILES) {
        const body = fs.readFileSync(path.join(bitrate3 ? 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/droiddeck-bitrate-candidate-20261012a' : donation ? 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/droiddeck-donation-candidate-20261011z' : brand ? 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/droiddeck-brand-candidate-20261011y' : projectUpdates ? 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/project-update-candidate-20261011x' : cleanup ? 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/display-menu-candidate-20261011w' : 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/av-sync-signed-candidate-20261011v', relative));
        if (crypto.createHash('sha256').update(body).digest('hex') !== sha) throw new Error('Unexpected candidate');
        const handler = route => route.fulfill({ status: 200, contentType: relative.endsWith('.svg') ? 'image/svg+xml' : relative.endsWith('.png') ? 'image/png' : relative.endsWith('.html') ? 'text/html' : relative.endsWith('.css') ? 'text/css' : 'text/javascript',
          headers: { 'cache-control': 'no-store' }, body });
        await context.route(ORIGIN + '/' + relative, handler);
        if (relative === 'index.html') await context.route(ORIGIN + '/', handler);
      }
    }
    stage = 'deployed-files';
    const files = brand ? [...brandFiles.filter(item => !candidate || item.before !== null).map(item => [item.file, candidate ? item.before : item.after]),
      SIGNED_FILES[1], SIGNED_FILES[2], ...(bitrate3 ? [] : [CLEANUP_FILES[0]]),
      ...(roleClaim ? [] : [['js/core/api-client.js', connection ? connectionFiles[0].after : '8299e34eea9145b735614a57b158fdac08aa31e925315da49025067a25a3f97a']])] : projectUpdates ? [...SIGNED_FILES.slice(0, 3), ...CLEANUP_FILES,
      ['js/core/api-client.js', candidate ? '04fb78866c9976c731fde737683104097e3f3c171a2cf23c74792ecedb520663' : '8299e34eea9145b735614a57b158fdac08aa31e925315da49025067a25a3f97a']] : cleanup ? [...SIGNED_FILES.slice(0, 3), ...(candidate ? [SIGNED_FILES[3],
      ['css/app.css', 'f016d55141bca95ec81fac95d5a5da3ef3d10c1a166307a2f54f4ac9372c76ab']] : CLEANUP_FILES)] : signed && !candidate ? [...SIGNED_FILES,
      ['css/app.css', 'f016d55141bca95ec81fac95d5a5da3ef3d10c1a166307a2f54f4ac9372c76ab']] : network ? [FILES[0],
      ['js/settings/settings-controller.js', 'e36e138f1db164a501fda08e05beb0482123b8cfc93829648a0019fe51cfae33'],
      ['css/app.css', 'f016d55141bca95ec81fac95d5a5da3ef3d10c1a166307a2f54f4ac9372c76ab']] : FILES;
    for (const [relative, sha] of files) {
      const response = await context.request.get(ORIGIN + '/' + relative);
      const actualHash = crypto.createHash('sha256').update(await response.body()).digest('hex');
      if (response.status() !== 200 || !response.headers()['cache-control']?.includes('no-store') ||
          actualHash !== sha) {
        console.error(JSON.stringify({ assetMismatch: relative, status: response.status(), expected: sha, actual: actualHash }));
        throw new Error('Unexpected deployment');
      }
    }
    const config = await (await context.request.get(ORIGIN + '/api/configuration')).json();
    if (config['persist.tesla-android.browser_audio.is_enabled'] !== 1) throw new Error('Do not enable disabled shared audio');
    const page = await context.newPage();
    let observedAuthority;
    page.on('response', async response => {
      if (new URL(response.url()).pathname === '/api/displayState' && response.request().method() === 'GET' && response.ok()) {
        try { const state = await response.json(); observedAuthority = { canControl: state.canControl, transactionStatus: state.transactionStatus,
          displayRole: state.displayRole, roleClaimSupported: state.displayRoleClaimSupported }; }
        catch (_) {}
      }
    });
    let errors = 0, blockedWrites = 0, upstreamReleaseRequests = 0, roleClaimsSent = 0; const blockedWriteEndpoints = {};
    page.on('pageerror', () => { errors++; });
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.hostname === 'api.github.com' && url.pathname.includes('/releases/latest')) upstreamReleaseRequests++;
    });
    await page.route(ORIGIN + '/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (roleClaim && endpoint === '/api/displayRole' && request.method() === 'POST' && request.postDataJSON()?.claim === true) {
        roleClaimsSent++; await route.abort(); return;
      }
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) {
        blockedWrites++;
        const category = ['/api/displayState', '/api/videoState', '/api/rearDisplayState'].includes(endpoint) ? endpoint : 'other';
        blockedWriteEndpoints[category] = (blockedWriteEndpoints[category] || 0) + 1;
        await route.abort();
      } else await route.continue();
    });
    await page.addInitScript(() => {
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, 'true');
    });
    async function advancing() {
      await page.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused && window.__liveAudioEl.currentTime > .1, undefined, { timeout: 20000 });
      const before = await page.evaluate(() => window.__liveAudioEl.currentTime);
      await page.waitForTimeout(1500);
      const after = await page.evaluate(() => window.__liveAudioEl.currentTime);
      audioProgress = await page.evaluate(({ before, after }) => ({
        before, after, paused: window.__liveAudioEl.paused, readyState: window.__liveAudioEl.readyState,
        bufferedEnd: window.__liveAudioEl.buffered.length ? window.__liveAudioEl.buffered.end(window.__liveAudioEl.buffered.length - 1) : 0,
      }), { before, after });
      if (after - before < .5) throw new Error('Audio not advancing');
    }
    stage = 'default-playback';
    await page.goto(ORIGIN, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForFunction(() => typeof window.getAudioDelayMs === 'function');
    if (await page.evaluate(() => window.getAudioDelayMs()) !== 0) throw new Error('Fresh browser delay not zero');
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0, undefined, { timeout: 30000 });
    stage = 'audio-gesture';
    await page.locator('#home-audio-button').click(); await advancing();
    stage = 'settings-open';
    await page.locator('#home-settings-button').click();
    if (connection) {
      stage = 'browser-connection-read-only';
      await page.locator('[data-page="settings"]').click();
      await page.locator('#settings-sidebar button').filter({ hasText: 'Browser connection', exact: true }).click();
      const hostname = page.locator('#connection-hostname'); await hostname.waitFor();
      if (await hostname.inputValue() !== new URL(ORIGIN).hostname ||
          !(await page.locator('#settings-content').textContent()).includes('Managed by reverse proxy')) throw new Error('Connection upgrade hostname mismatch');
      for (const width of [904, 390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        if (await page.locator('#settings-content').evaluate(element => [...element.querySelectorAll('input,button,.settings-tile-content,.settings-tile-trailing,.settings-trailing-text')].some(child => {
          const box = child.getBoundingClientRect(); return box.x < 0 || box.right > innerWidth || (child.tagName !== 'INPUT' && child.scrollWidth > child.clientWidth + 1);
        }))) throw new Error('Connection layout exceeds viewport');
      }
      await page.setViewportSize({ width: 904, height: 678 });
    }
    if (brand) {
      stage = 'droiddeck-about';
      await page.locator('[data-page="about"]').click();
      await page.waitForFunction(() => document.querySelector('.droiddeck-logo')?.complete && document.querySelector('.droiddeck-logo').naturalWidth > 0);
      if (await page.title() !== 'DroidDeck' || await page.locator('.droiddeck-name').textContent() !== 'DroidDeck' ||
          !(await page.locator('.about-version-value').textContent()).includes('0.1.0 Preview')) throw new Error('Brand identity mismatch');
      for (const width of [904, 390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        if (await page.locator('.about-content').evaluate(element => [...element.children].some(child => {
          const box = child.getBoundingClientRect(); return box.x < 0 || box.right > innerWidth;
        }))) throw new Error('About exceeds viewport');
        await page.locator('#page-about').screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/droiddeck-about-' + width + '-20261011y.png' });
      }
      await page.locator('[data-page="releaseNotes"]').click();
      if (await page.locator('#release-versions .release-version-title').count() !== 1 ||
          await page.locator('#release-versions button').count() !== 9 ||
          !(await page.locator('#release-details').textContent()).includes('Waydroid')) throw new Error('Project release notes mismatch');
      await page.locator('#page-release-notes').screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/droiddeck-release-notes-320-20261011y.png' });
      await page.setViewportSize({ width: 904, height: 678 });
      if (await page.evaluate(() => navigator.mediaSession.metadata?.album) !== 'DroidDeck') throw new Error('Media identity mismatch');
    }
    if (donation) {
      stage = 'donation-presentation';
      await page.locator('[data-page="donations"]').click();
      const expected = 'https://www.paypal.com/donate/?business=TFWNLUC7ZGSPS&no_recurring=0&item_name=Thank+you+for+your+support%21++I+appreciate+it.&currency_code=USD';
      if ((await page.locator('.donation-text').textContent()).trim() !== 'This is a self funded project, your contribution means the world to me!' ||
          await page.locator('#page-donations a').getAttribute('href') !== expected) throw new Error('Donation identity mismatch');
      const { PNG } = require('pngjs'), jsQR = require('jsqr');
      await page.waitForFunction(() => document.querySelector('.donation-qr')?.complete && document.querySelector('.donation-qr').naturalWidth > 0);
      for (const width of [904, 390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        if (await page.locator('.donation-content').evaluate(element => [...element.children].some(child => {
          const box = child.getBoundingClientRect(); return box.x < 0 || box.right > innerWidth;
        }))) throw new Error('Donation exceeds viewport');
        const png = PNG.sync.read(await page.locator('.donation-qr').screenshot());
        const decoded = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
        if (decoded?.data !== expected) throw new Error('Rendered QR mismatch');
        await page.locator('#page-donations').screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/droiddeck-donation-' + width + '-20261011z.png' });
      }
      await page.setViewportSize({ width: 904, height: 678 });
    }
    await page.locator('[data-page="settings"]').click();
    if (cleanup) {
      stage = 'merged-display-settings';
      const menu = page.locator('#settings-sidebar button');
      if (await menu.filter({ hasText: 'Rear Display', exact: true }).count() || await menu.filter({ hasText: 'Display', exact: true }).count() !== 1) {
        throw new Error('Display menu not merged');
      }
      await menu.filter({ hasText: 'Display', exact: true }).click();
      const roles = page.locator('[data-rear-role]'); await roles.waitFor({ state: 'visible' });
      if (!(await roles.textContent()).includes('Primary Display') || !(await roles.textContent()).includes('Secondary Display')) throw new Error('Display labels missing');
      if (bitrate3) {
        stage = 'three-mbps-menu';
        const trigger = page.locator('[data-video-bitrate] .settings-dropdown-trigger');
        const option = page.locator('[data-video-bitrate] [data-value="3000000"]');
        if ((await option.textContent()).trim() !== '3 Mbps') throw new Error('3 Mbps menu missing');
        bitrateMenuReadOnly = await trigger.isDisabled();
        let decision = bitrateMenuDecision(observedAuthority, bitrateMenuReadOnly);
        for (let attempt = 0; decision === 'wait' && attempt < 40; attempt++) {
          await page.waitForTimeout(250);
          bitrateMenuReadOnly = await trigger.isDisabled();
          decision = bitrateMenuDecision(observedAuthority, bitrateMenuReadOnly);
        }
        if (decision === 'wait' || decision === 'error') throw new Error('Bitrate authority mismatch');
        if (bitrateMenuReadOnly) {
          if (!await option.isDisabled()) throw new Error('Viewer bitrate option enabled');
        } else {
          await trigger.click();
          if (!await option.isVisible()) throw new Error('3 Mbps menu missing');
          await trigger.click();
        }
      }
      if (roleClaim) {
        stage = 'display-role-reflection';
        await page.waitForFunction(() => document.querySelector('[data-rear-role] input:checked'));
        const selected = await roles.locator('input:checked').inputValue();
        if (observedAuthority?.roleClaimSupported !== 1 || selected !== observedAuthority.displayRole) throw new Error('Display role does not reflect server state');
        displayRoleReflected = true;
      }
      for (const width of [904, 390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        if (await page.locator('#settings-content').evaluate(element => [...element.querySelectorAll('.settings-tile')].some(tile => {
          const box = tile.getBoundingClientRect(); return box.x < 0 || box.right > innerWidth;
        }))) throw new Error('Display layout exceeds viewport');
        await page.locator('#settings-content').screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/settings-cleanup-display-' + width + '-20261011w.png' });
      }
      stage = 'useful-device-info';
      await menu.filter({ hasText: 'Device', exact: true }).click();
      const device = page.locator('[data-vm-device-info]'); await device.waitFor({ state: 'visible' });
      if (/CarPlay Module|LTE Modem|Serial Number|Release type/.test(await device.textContent())) throw new Error('Legacy Device placeholders remain');
      for (const width of [904, 390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        if (await device.evaluate(element => [...element.querySelectorAll('.settings-tile')].some(tile => {
          const box = tile.getBoundingClientRect(); return box.x < 0 || box.right > innerWidth;
        }))) throw new Error('Device layout exceeds viewport');
        await device.screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/settings-cleanup-device-' + width + '-20261011w.png' });
      }
      await page.setViewportSize({ width: 904, height: 678 });
    }
    if (network) {
      stage = 'network-diagnostics';
      const info = await (await context.request.get(ORIGIN + '/api/deviceInfo')).json();
      if (info.networkInfoVersion !== 1 || info.lan_stream_origin !== 'https://192.168.2.90:8443' ||
          info.network_mode !== 'Waydroid NAT' || !info.android_addresses?.some(value => /^192\.168\.240\.\d+$/.test(value.address))) {
        throw new Error('VM Network metadata unavailable');
      }
      await page.locator('#settings-sidebar button').filter({ hasText: 'Network', exact: true }).click();
      const panel = page.locator('[data-vm-network-info]');
      await panel.waitFor({ state: 'visible' });
      const body = await panel.textContent();
      if (cleanup && /Wi-Fi hotspot|Offline routing|Telemetry proxy|Firmware proxy/.test(body)) throw new Error('Legacy Network placeholders remain');
      if (!body.includes(ORIGIN) || !body.includes(info.lan_stream_origin) ||
          !info.android_addresses.every(value => body.includes(value.address)) || await panel.locator('input,button,select').count()) {
        throw new Error('VM Network page mismatch');
      }
      for (const width of [904, 320]) {
        await page.setViewportSize({ width, height: width === 320 ? 844 : 678 });
        if (await panel.evaluate(element => [...element.querySelectorAll('.settings-tile')].some(tile => {
          const box = tile.getBoundingClientRect(); return box.x < 0 || box.right > innerWidth;
        }))) throw new Error('VM Network layout exceeds viewport');
        await panel.screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/network-restore-' + width + '-20261011t.png' });
      }
      await page.setViewportSize({ width: 904, height: 678 });
    }
    await page.locator('#settings-sidebar button').filter({ hasText: 'Audio', exact: true }).click();
    const slider = page.locator('.settings-audio-delay-slider');
    const tile = page.locator('.settings-tile').filter({ has: slider });
    stage = 'local-delay-playback';
    await slider.evaluate(input => {
      input.value = '200'; input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await advancing();
    stage = 'pending-start-controls';
    await page.evaluate(() => {
      const el = window.__liveAudioEl, originalPlay = el.play;
      const session = navigator.mediaSession, originalSet = session.setActionHandler;
      const state = window.__AudioDelaySmoke = { plays: 0, actions: {} };
      el.play = function(...args) { state.plays++; return originalPlay.apply(this, args); };
      session.setActionHandler = function(name, fn) { state.actions[name] = fn; return originalSet.call(this, name, fn); };
      state.restore = () => { el.play = originalPlay; session.setActionHandler = originalSet; };
      window.setAudioDelayMs(750);
    });
    await page.waitForFunction(() => window.__liveAudioEl.readyState >= 3 && !window.__liveAudioEl.autoplay, undefined, { timeout: 20000 });
    await page.evaluate(() => {
      window.primeAudioFromGesture(); window.__AudioDelaySmoke.actions.play();
      window.__AudioDelaySmoke.actions.pause();
    });
    await page.waitForTimeout(900);
    if (await page.evaluate(() => window.__AudioDelaySmoke.plays) !== 0) throw new Error('Pending delay bypassed');
    await page.evaluate(() => window.__AudioDelaySmoke.actions.play());
    await page.waitForTimeout(250);
    if (await page.evaluate(() => window.__AudioDelaySmoke.plays) !== 0) throw new Error('Resume bypassed delay');
    await advancing();
    await page.evaluate(() => { window.__AudioDelaySmoke.restore(); window.setAudioDelayMs(200); });
    await advancing();
    if (await page.evaluate(() => localStorage.getItem('TAHtml_audioDelayMs')) !== '200') throw new Error('Delay not saved locally');
    if (signed) {
      stage = 'negative-sync';
      const result = await page.evaluate(() => {
        const src = window.__liveAudioEl.src;
        window.setAudioDelayMs(0); const zeroSrc = window.__liveAudioEl.src;
        window.setAudioDelayMs(-200);
        return { negativeAccepted: window.getAudioDelayMs() === -200,
          audioSourceRetained: window.__liveAudioEl.src === zeroSrc, previousSource: Boolean(src) };
      });
      if (!result.negativeAccepted || !result.audioSourceRetained) throw new Error('Negative sync restarts audio');
      await advancing();
      await slider.evaluate(input => { input.value = '-200'; input.dispatchEvent(new Event('input', { bubbles: true })); });
      if (await slider.getAttribute('min') !== '-750') throw new Error('Negative sync slider missing');
    }
    for (const width of [904, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 678 });
      const fits = await tile.evaluate(element => {
        const tile = element.getBoundingClientRect();
        const title = element.querySelector('.settings-tile-title').getBoundingClientRect();
        const slider = element.querySelector('input').getBoundingClientRect();
        return tile.x >= 0 && tile.right <= innerWidth && title.right <= slider.x && slider.right <= innerWidth;
      });
      if (!fits) throw new Error('Audio delay tile overlaps or exceeds viewport');
      await tile.screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/audio-delay-tile-' + width + '-20261011s.png' });
    }
    stage = 'settings-exit';
    await page.locator('[data-page="home"]').click();
    await page.locator('#canvas').waitFor({ state: 'visible' });
    await advancing();
    stage = 'refresh-persistence';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.getAudioDelayMs === 'function');
    if (await page.evaluate(() => window.getAudioDelayMs()) !== (signed ? -200 : 200)) throw new Error('Delay not retained');
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0, undefined, { timeout: 30000 });
    await page.locator('#home-audio-button').click(); await advancing();
    stage = 'zero-restores';
    await page.evaluate(() => window.setAudioDelayMs(0)); await advancing();
    if (projectUpdates && (!(await page.locator('#home-update-button').isHidden()) || upstreamReleaseRequests)) throw new Error('Unrelated update advertised');
    if (errors) throw new Error('Browser error');
    if (roleClaimsSent) throw new Error('Passive probe attempted to claim control');
    console.log(JSON.stringify({ deployedFilesVerified: files.length, networkRestored: network, noStore: true, defaultDelayMs: 0,
      delayedAudioAdvancing: true, refreshRetainsLocalDelay: true, zeroRestoresPlayback: true,
      settingsExitKeepsAudio: true,
      pendingGestureGuard: true, pendingPauseHonored: true, resumeRetainsDelay: true,
      signedSync: signed, candidateOnly: candidate, negativeSyncRetainsAudioSource: signed,
      mergedDisplaySettings: cleanup, usefulOnlyNetworkAndDevice: cleanup,
      droiddeckBrandAndLogo: brand,
      exactRpi4DonationAndRenderedQr: donation,
      displayRoleReflected,
      roleClaimsSent,
      threeMbpsOptionWithoutSharedSave: bitrate3,
      bitrateMenuReadOnly,
      projectUpdateCheckDisabledUntilReleaseChannel: projectUpdates, upstreamReleaseRequests,
      desktopMobileTileFits: true, pageErrors: errors, blockedSharedWrites: blockedWrites, blockedWriteEndpoints,
      savedSettingsUiOnly: true, savedAudio: false, sharedSettingsChanged: false,
      scope: 'Edge browser smoke checks; Tesla lip sync remains untimed user validation' }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => {
  const safe = ['Fresh browser delay not zero', 'Audio not advancing', 'Delay not saved locally',
    'Audio delay tile overlaps or exceeds viewport', 'Delay not retained', 'Browser error',
    'Brand identity mismatch', 'About exceeds viewport', 'Project release notes mismatch', 'Media identity mismatch',
    'Donation identity mismatch', 'Donation exceeds viewport', 'Rendered QR mismatch', '3 Mbps menu missing', 'Viewer bitrate option enabled', 'Bitrate authority mismatch',
    'Pending delay bypassed', 'Resume bypassed delay', 'VM Network metadata unavailable',
    'VM Network page mismatch', 'VM Network layout exceeds viewport'];
  console.error(JSON.stringify({ audioDelaySmokeFailed: true, stage, audioProgress,
    reason: safe.includes(error.message) ? error.message : error.name }));
  process.exitCode = 1;
});
module.exports = { bitrateMenuDecision };
