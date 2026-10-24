// SPDX-License-Identifier: GPL-3.0-only
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');

async function main() {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 960 } });
    const errors = [], counters = { markers: 0, receipts: 0, binary: 0 };
    let streamUrl;
    page.on('pageerror', e => errors.push(e.message));
    page.on('websocket', socket => {
      if (!socket.url().includes('format=h264')) return;
      streamUrl = socket.url();
      socket.on('framereceived', ({ payload }) => {
        if (typeof payload !== 'string') { counters.binary++; return; }
        try { if (JSON.parse(payload).type === 'scrcpy-flow-frame') counters.markers++; } catch {}
      });
      socket.on('framesent', ({ payload }) => {
        try { if (JSON.parse(String(payload)).type === 'scrcpy-flow-ack') counters.receipts++; } catch {}
      });
    });
    await page.goto('https://192.168.2.90:8443/?video=legacy', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => Number(document.getElementById('canvas')?.dataset.videoFrames || 0) > 0,
      undefined, { timeout: 30000 });
    await page.waitForTimeout(1500);
    const picture = await page.evaluate(() => {
      const c = document.getElementById('canvas');
      return { frames: Number(c.dataset.videoFrames), decoder: c.dataset.videoDecoder,
        width: Number(c.dataset.videoWidth), height: Number(c.dataset.videoHeight) };
    });
    if (!streamUrl || !counters.markers || !counters.receipts || errors.length) throw Error('Live H264 receipt/render proof failed: ' + JSON.stringify({ counters, errors }));
    const probe = await page.evaluate(url => new Promise((resolve, reject) => {
      const ws = new WebSocket(url, 'droiddeck-live-v1'); ws.binaryType = 'arraybuffer';
      let marker, seen = 0, closed = false;
      const held = [];
      const timer = setTimeout(() => { closed = true; ws.close(); resolve({ receivedWithoutReceipt: seen }); }, 1500);
      ws.onerror = () => { clearTimeout(timer); reject(Error('Live slow-viewer connection failed')); };
      ws.onclose = event => { if (!closed) { clearTimeout(timer); reject(Error('Live slow viewer closed: ' + event.code)); } };
      ws.onmessage = event => {
        if (typeof event.data === 'string') {
          const v = JSON.parse(event.data); if (v.type === 'scrcpy-flow-frame') marker = v.id;
        } else { seen++; held.push(marker); }
      };
    }), streamUrl);
    if (!probe.receivedWithoutReceipt || probe.receivedWithoutReceipt > 4) throw Error('Live network credit is unbounded');
    console.log(JSON.stringify({ picture, counters, probe, pageErrors: errors.length, androidInputInjected: false }));
  } finally { await browser.close(); }
}

if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
