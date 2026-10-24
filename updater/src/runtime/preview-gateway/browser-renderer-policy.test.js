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

function fixture(value) {
  const selections = [];
  const preferences = { choice: () => value, select: value => selections.push(value) };
  const prototype = { _rendererInfoForId: id => ({ id, packetFormat: 'scrcpy', binaryType: 'arraybuffer',
    scriptPath: id === 1 ? './js/display/renderers/h264-webcodecs/h264WebCodecs.js' : 'unchanged' }) };
  const window = { TAHtml: { browserRenderer: preferences, DisplayRendererManager: function () {} } };
  window.TAHtml.DisplayRendererManager.prototype = prototype;
  vm.runInNewContext(policy, { window });
  return { preferences, prototype, selections };
}

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
  const request = (headers = {}, method = 'GET') => new Promise((resolve, reject) => {
    const req = https.request(url, { ca: tls.cert, method, headers: { Host: 'renderer.test:8443', ...headers } }, res => {
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
});
