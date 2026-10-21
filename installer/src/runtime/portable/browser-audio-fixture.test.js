// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), http = require('node:http'), path = require('node:path');
const { once } = require('node:events');
const { WebSocket, WebSocketServer } = require(require.resolve('ws', {
  paths: [path.join(__dirname, '../preview-gateway'), '/opt/droiddeck/runtime/preview-gateway'],
}));
const { ToneEnergy } = require('./generated-tone-stats');
function load() {
  assert.ok(fs.existsSync(path.join(__dirname, 'browser-audio-fixture.js')), 'Generated browser fixture must exist');
  return require('./browser-audio-fixture');
}
test('source emits framed 1kHz stereo and releases its timer on close', async () => {
  const { generatedSource } = load(); let tick, cancelled = 0;
  const source = generatedSource({ interval: fn => { tick = fn; return 7; }, cancel: id => { assert.equal(id, 7); cancelled++; } });
  const chunks = []; source.on('data', bytes => chunks.push(bytes));
  for (let i = 0; i < 10; i++) tick();
  await new Promise(resolve => setImmediate(resolve));
  const data = Buffer.concat(chunks); assert.equal(data.readUInt32BE(), 0x00726177);
  assert.equal(data.length, 38404); const tone = new ToneEnergy(); let peak = 0;
  for (let i = 4; i < data.length; i += 4) {
    const left = data.readInt16LE(i); assert.equal(left, data.readInt16LE(i + 2));
    peak = Math.max(peak, Math.abs(left)); tone.push(left);
  }
  assert.equal(peak, 512); assert.equal(tone.finish().matchingWindows, 20);
  source.destroy(); await once(source, 'close'); assert.equal(cancelled, 1);
});
test('fixture denies foreign Host/Origin, writes and path variants', () => {
  const { permitted } = load();
  const request = { method: 'GET', url: '/tone', headers: { host: '127.0.0.1:18188', origin: 'http://127.0.0.1:18188' } };
  assert.equal(permitted(request, true), true);
  for (const changed of [{ method: 'POST' }, { url: '/tone?anything' },
    { headers: { ...request.headers, host: 'localhost:18188' } },
    { headers: { ...request.headers, origin: 'http://foreign.example' } },
    { headers: { host: request.headers.host } }]) assert.equal(permitted({ ...request, ...changed }, true), false);
});
async function fixture() {
  const { createFixture } = load(); const relay = http.createServer();
  const sockets = new WebSocketServer({ server: relay });
  sockets.on('connection', client => client.send(Buffer.from('generated-only')));
  relay.shutdown = async () => {
    for (const client of sockets.clients) client.terminate();
    await new Promise(resolve => sockets.close(resolve)); await new Promise(resolve => relay.close(resolve));
  };
  const server = await createFixture({ createRelay: () => relay, WebSocket, WebSocketServer,
    assets: new Map([['/js/audio/audioplayback.js', Buffer.from('player')], ['/js/audio/audio-ws-worker.js', Buffer.from('worker')]]) });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, relay, port: server.address().port };
}
function get(port, filename, headers = {}) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: '127.0.0.1', port, path: filename, headers: { Host: '127.0.0.1:18188', ...headers } }, response => {
      let body = ''; response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve({ status: response.statusCode, body, headers: response.headers }));
    }).on('error', reject);
  });
}
test('serves only player assets and an uncached generated page; shutdown closes listeners', async () => {
  const f = await fixture();
  try {
    const page = await get(f.port, '/'); assert.equal(page.status, 200); assert.match(page.body, /id="play"/);
    assert.equal(page.headers['cache-control'], 'no-store');
    assert.equal((await get(f.port, '/js/audio/audioplayback.js')).body, 'player');
    for (const name of ['/etc/passwd', '/?secret', '/js/audio/audioplayback.js?x']) assert.equal((await get(f.port, name)).status, 404);
    assert.equal((await get(f.port, '/', { Host: 'foreign' })).status, 403);
  } finally { await f.server.shutdown(); }
  assert.equal(f.server.listening, false); assert.equal(f.relay.listening, false);
});
test('read-only connection delivers binary audio and rejects injected client messages', async () => {
  const f = await fixture();
  const client = new WebSocket('ws://127.0.0.1:' + f.port + '/tone', { origin: 'http://127.0.0.1:18188', headers: { Host: '127.0.0.1:18188' } });
  try {
    const message = once(client, 'message'); await once(client, 'open');
    assert.equal((await message)[0].toString(), 'generated-only');
    const closed = once(client, 'close'); client.send('input-is-forbidden'); await closed;
  } finally { client.terminate(); await f.server.shutdown(); }
  assert.equal(f.server.listening, false); assert.equal(f.relay.listening, false);
});
test('CLI rejects path/user/argument overrides before listening', async () => {
  const { main } = load();
  for (const args of [[], ['--serve', '/other'], ['activate']]) await assert.rejects(main(args), /Fixed disposable/);
});
