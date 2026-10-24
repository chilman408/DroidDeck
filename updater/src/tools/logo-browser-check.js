// SPDX-License-Identifier: GPL-3.0-only
// About/static assets only; no Android input, media capture, login or settings writes.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const { PNG } = require('../runtime/waydroid-display/node_modules/pngjs');
const { verifyBundle } = require('../runtime/portable/frontend-bundle');
const { verifyLogo } = require('../runtime/branding/logo');
async function main(args) {
  if (args.length < 2 || args.length > 3 || (args[2] && args[2] !== '--live')) throw new Error('Use BUNDLE NEW_SCREENSHOT_DIRECTORY [--live]');
  const directory = path.resolve(args[0]), output = path.resolve(args[1]);
  const manifest = verifyBundle(directory), logo = verifyLogo();
  const image = fs.readFileSync(path.join(directory, 'assets/droiddeck-logo.png'));
  assert.ok(image.equals(fs.readFileSync(logo.path)));
  const html = fs.readFileSync(path.join(directory, 'index.html'), 'utf8');
  const section = /<section id="page-about"[\s\S]*?<\/section>/.exec(html)?.[0];
  assert.ok(section); fs.mkdirSync(output);
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.abort());
    await page.setContent('<!doctype html><meta charset="utf-8"><title>DroidDeck logo check</title>' +
      section.replace('./assets/droiddeck-logo.png', 'data:image/png;base64,' + image.toString('base64')));
    await page.addStyleTag({ path: path.join(directory, 'css/app.css') });
    await page.evaluate(() => {
      document.querySelector('[data-app-version]').textContent = '0.1.0';
      document.querySelector('#page-about').style.height = '100vh';
    });
    const checked = [];
    for (const [width, height] of [[904, 720], [390, 844], [320, 740]]) {
      await page.setViewportSize({ width, height });
      await page.locator('.droiddeck-logo').evaluate(image => image.decode());
      const geometry = await page.locator('.droiddeck-logo').evaluate(image => {
        const box = image.getBoundingClientRect(), style = getComputedStyle(image);
        return { x: box.x, y: box.y, width: box.width, height: box.height,
          naturalWidth: image.naturalWidth, background: style.backgroundColor,
          overflow: document.documentElement.scrollWidth > innerWidth };
      });
      assert.equal(geometry.naturalWidth, logo.width);
      assert.equal(geometry.width, 160); assert.equal(geometry.height, 160);
      assert.equal(geometry.background, 'rgb(255, 255, 255)'); assert.equal(geometry.overflow, false);
      assert.ok(geometry.x >= 0 && geometry.x + geometry.width <= width && geometry.y >= 0);
      const screenshot = await page.locator('.droiddeck-logo').screenshot();
      const pixels = PNG.sync.read(screenshot); let green = 0, dark = 0;
      for (let i = 0; i < pixels.data.length; i += 4) {
        const [r, g, b] = pixels.data.subarray(i, i + 3);
        if (g > 100 && g > r * 1.4 && g > b * 1.2) green++;
        if (Math.max(r, g, b) < 70) dark++;
      }
      assert.ok(green > 1000 && dark > 1000, 'Both Android and browser/casting shapes must render');
      await page.screenshot({ path: path.join(output, 'about-' + width + '.png') });
      checked.push({ width, height, greenPixels: green, darkPixels: dark });
    }
    let liveAssets = 0;
    if (args[2] === '--live') {
      for (const file of ['assets/droiddeck-logo.png', 'css/app.css']) {
        const response = await context.request.get('https://192.168.2.90:8443/' + file);
        assert.equal(response.status(), 200); assert.equal(response.headers()['cache-control'], 'no-store');
        const body = await response.body(), source = fs.readFileSync(path.join(directory, file));
        const normalize = data => file.endsWith('.css') ? Buffer.from(data.toString('utf8').replace(/\r\n/g, '\n')) : data;
        assert.ok(normalize(body).equals(normalize(source)), 'Live branding asset differs: ' + file);
        liveAssets++;
      }
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ brandingViewports: checked, liveAssets, pageErrors: 0, liveWrites: 0,
      androidOperations: 0, bundleFiles: manifest.files.length, logoSha256: logo.sha256 }));
  } finally { await browser.close(); }
}
if (require.main === module) main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
