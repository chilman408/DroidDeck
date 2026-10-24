// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const https = require('node:https');
const { execFileSync } = require('node:child_process');
const { createGateway } = require('./server');
const policy = fs.readFileSync(path.join(__dirname, 'browser-renderer-policy.js'), 'utf8');

function fixture(value, query = '') {
  const selections = [];
  const timers = [], navigations = [], cancelled = [];
  const preferences = { choice: () => value, select: value => selections.push(value) };
  const prototype = { _rendererInfoForId: id => ({ id, packetFormat: 'scrcpy', binaryType: 'arraybuffer',
    scriptPath: id === 1 ? './js/display/renderers/h264-webcodecs/h264WebCodecs.js' : 'unchanged' }) };
  const window = { TAHtml: { browserRenderer: preferences, DisplayRendererManager: function () {} },
    location: { search: query, href: 'https://example.test/' + query + '#home', replace: url => navigations.push(url) },
    isSecureContext: true, VideoDecoder() {}, document: { getElementById: () => ({ dataset: {} }) },
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimeout: id => cancelled.push(id) };
  window.TAHtml.DisplayRendererManager.prototype = prototype;
  vm.runInNewContext(policy, { window, URL, URLSearchParams });
  return { preferences, prototype, selections, timers, navigations, cancelled, window };
}

test('WebCodecs test link is explicit and recovers once to a non-trial legacy URL', () => {
  const f = fixture('auto', '?video=webcodecs-test&other=keep');
  assert.equal(f.preferences.effectiveChoice(), 'webcodecs');
  assert.equal(f.prototype._rendererInfoForId(2).id, 1);
  assert.equal(f.prototype._rendererInfoForId(2).id, 1);
  assert.equal(f.timers.length, 1);
  assert.equal(f.timers[0].delay, 15000);
  f.timers[0].fn();
  assert.deepEqual(f.navigations, ['https://example.test/?video=legacy&other=keep#home']);
  assert.equal(f.preferences.recover(), false);
  assert.equal(f.preferences.effectiveChoice(), 'legacy');
  assert.deepEqual(f.selections, []);
  const normal = fixture('webcodecs', '?video=legacy');
  assert.equal(normal.preferences.recover(), false);
  assert.equal(normal.prototype._rendererInfoForId(1).id, 2);
  assert.equal(normal.timers.length, 0);
});

test('a test link with decoded frames does not expire a healthy stream', () => {
  const f = fixture('auto', '?video=webcodecs-test');
  f.window.document.getElementById = () => ({ dataset: { videoFrames: '5' } });
  f.prototype._rendererInfoForId(2); f.timers[0].fn();
  assert.deepEqual(f.navigations, []);
  assert.equal(f.preferences.effectiveChoice(), 'webcodecs');
});

test('renderer settings row is omitted while unrelated settings still append', () => {
  const tiles = [];
  const window = { TAHtml: { SettingsController: function () {} } };
  window.TAHtml.SettingsController.prototype._appendTile = tile => tiles.push(tile);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'browser-settings-policy.js'), 'utf8'), { window });
  const controller = new window.TAHtml.SettingsController();
  controller._appendTile({ title: 'Renderer' }); controller._appendTile({ title: 'Resolution' });
  assert.deepEqual(tiles, [{ title: 'Resolution' }]);
});

test('old saved or bookmarked WebCodecs selections recover without writes or reload loops', () => {
  for (const value of ['auto', 'legacy', 'webcodecs']) {
    const { preferences, prototype, selections } = fixture(value);
    assert.equal(preferences.choice(), value === 'webcodecs' ? 'legacy' : value);
    assert.equal(preferences.effectiveChoice(), 'legacy');
    assert.deepEqual(Array.from(preferences.options(), o => o.value), ['auto', 'legacy']);
    for (const id of [0, 1, 2]) {
      const result = prototype._rendererInfoForId(id);
      assert.equal(result.id, id === 1 ? 2 : id);
      assert.equal(result.packetFormat, 'scrcpy');
      if (id === 1) assert.match(result.scriptPath, /h264-broadway/);
    }
    assert.deepEqual(selections, []);
    assert.throws(() => preferences.select('webcodecs'));
    preferences.select('legacy'); preferences.select('auto');
    assert.deepEqual(selections, ['legacy', 'auto']);
  }
});

test('gateway serves the policy after authentication, without modifying the frontend file', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'renderer-policy-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, 'index.html'), '<title>fixture</title>');
  fs.mkdirSync(path.join(root, 'js/display'), { recursive: true });
  const original = Buffer.from('window.fixtureOriginalRenderer = true;');
  const file = path.join(root, 'js/display/display-renderer-manager.js');
  fs.writeFileSync(file, original);
  const extra = [
    ['js/settings/settings-controller.js', 'browser-settings-policy.js'],
    ['js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js', 'webcodecs-worker-policy.js'],
  ];
  for (const [asset] of extra) {
    fs.mkdirSync(path.dirname(path.join(root, asset)), { recursive: true });
    fs.writeFileSync(path.join(root, asset), original);
  }
  fs.writeFileSync(path.join(root, 'openssl.cnf'), '[req]\ndistinguished_name=dn\n[dn]\n');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
    '-config', path.join(root, 'openssl.cnf'), '-keyout', path.join(root, 'key.pem'),
    '-out', path.join(root, 'cert.pem'), '-days', '1', '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=IP:127.0.0.1,DNS:renderer.test'], { stdio: 'ignore' });
  const tls = { key: fs.readFileSync(path.join(root, 'key.pem')), cert: fs.readFileSync(path.join(root, 'cert.pem')) };
  const password = 'generated-fixture-password-at-least-32-bytes';
  const server = createGateway({ frontendDir: root, publicOrigin: 'https://renderer.test:8443', tls,
    auth: { username: 'fixture', password } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const url = `https://127.0.0.1:${server.address().port}/js/display/display-renderer-manager.js`;
  const request = (headers = {}, method = 'GET', target = url) => new Promise((resolve, reject) => {
    const req = https.request(target, { ca: tls.cert, method, headers: { Host: 'renderer.test:8443', ...headers } }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: new Map(Object.entries(res.headers)), body: Buffer.concat(chunks) }));
    });
    req.on('error', reject); req.end();
  });
  assert.equal((await request()).status, 401);
  const headers = { Authorization: 'Basic ' + Buffer.from('fixture:' + password).toString('base64') };
  const response = await request(headers);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.match(response.headers.get('content-type'), /javascript/);
  const body = response.body;
  assert.deepEqual(body, Buffer.concat([original, Buffer.from('\n;\n'), Buffer.from(policy)]));
  const head = await request(headers, 'HEAD');
  assert.equal(head.status, 200);
  assert.equal(Number(head.headers.get('content-length')), body.length);
  assert.equal(head.body.byteLength, 0);
  assert.deepEqual(fs.readFileSync(file), original);
  for (const [asset, policyFile] of extra) {
    const target = `https://127.0.0.1:${server.address().port}/${asset}`;
    assert.equal((await request({}, 'GET', target)).status, 401);
    const response = await request(headers, 'GET', target);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.deepEqual(response.body, Buffer.concat([original, Buffer.from('\n;\n'),
      fs.readFileSync(path.join(__dirname, policyFile))]));
    assert.deepEqual(fs.readFileSync(path.join(root, asset)), original);
  }
});
