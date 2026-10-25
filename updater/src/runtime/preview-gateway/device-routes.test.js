// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), http = require('node:http');
const { createGateway } = require('./server');
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
test('device information is read-only, exact-origin, bounded and never routes arbitrary diagnostics to a helper', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ta-device-')); fs.writeFileSync(path.join(directory, 'index.html'), 'test');
  let upstreamCalls = 0, reads = 0, fail = false, oversized = false;
  const upstream = http.createServer((_req, res) => { upstreamCalls++; res.end('{}'); }); await listen(upstream);
  const gateway = createGateway({ frontendDir: directory, targets: { api: `http://127.0.0.1:${upstream.address().port}`,
    display: 'ws://127.0.0.1:9991', touchscreen: 'ws://127.0.0.1:9992' },
    deviceInfoProvider: () => { reads++; if (fail) throw new Error('private-path');
      return oversized ? { deviceInfoVersion: 1, text: 'x'.repeat(5000) } : { deviceInfoVersion: 1, cpu_temperature: null }; } });
  await listen(gateway); const base = `http://127.0.0.1:${gateway.address().port}`;
  t.after(async () => { await new Promise(resolve => gateway.close(resolve)); await new Promise(resolve => upstream.close(resolve));
    fs.rmSync(directory, { recursive: true, force: true }); });
  assert.throws(() => createGateway({ frontendDir: directory, deviceInfoProvider: 'command' }), /provider/);
  const info = await fetch(base + '/api/deviceInfo'); assert.equal(info.status, 200); assert.equal(info.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await info.json(), { deviceInfoVersion: 1, cpu_temperature: null });
  assert.equal((await fetch(base + '/api/deviceInfo', { headers: { Origin: 'https://evil.example' } })).status, 403);
  const denied = await fetch(base + '/api/deviceInfo', { method: 'POST', body: 'reboot' }); assert.equal(denied.status, 405); assert.equal(denied.headers.get('allow'), 'GET');
  assert.equal((await fetch(base + '/api/%64eviceInfo')).status, 400); assert.equal(reads, 1); assert.equal(upstreamCalls, 0);
  fail = true; const failed = await fetch(base + '/api/deviceInfo'); assert.equal(failed.status, 503);
  assert.equal((await failed.text()).includes('private-path'), false);
  fail = false; oversized = true; assert.equal((await fetch(base + '/api/deviceInfo')).status, 503);
});
