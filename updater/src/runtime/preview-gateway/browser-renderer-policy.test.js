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

function fixture(value, query = '', available = true, oldGuard = false) {
  const selections = [];
  const timers = [], navigations = [], cancelled = [], writes=[];
  let saved=value;
  const preferences = { choice: () => oldGuard?'legacy':value, select: value => {if(oldGuard&&value==='webcodecs')throw new Error('Old guard');selections.push(value);} };
  const prototype = { _rendererInfoForId: id => ({ id, packetFormat: 'scrcpy', binaryType: 'arraybuffer',
    scriptPath: id === 1 ? './js/display/renderers/h264-webcodecs/h264WebCodecs.js' : 'unchanged' }) };
  const window = { TAHtml: { browserRenderer: preferences, DisplayRendererManager: function () {} },
    location: { search: query, href: 'https://example.test/' + query + '#home', replace: url => navigations.push(url),reload:()=>navigations.push('reload') },
    isSecureContext: true, VideoDecoder() {}, document: { getElementById: () => ({ dataset: {} }) },
    localStorage:{getItem:()=>saved,setItem:(key,value)=>{saved=value;writes.push({key,value});}},
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimeout: id => cancelled.push(id) };
  window.TAHtml.DisplayRendererManager.prototype = prototype;
  if(!available)delete window.VideoDecoder;
  vm.runInNewContext(policy, { window, URL, URLSearchParams });
  return { preferences, prototype, selections, timers, navigations, cancelled, writes, window };
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

test('renderer settings row returns when WebCodecs is available, without altering other settings', () => {
 for(const available of [true,false]){
  const tiles = [];
  const window = { TAHtml: { SettingsController: function () {},browserRenderer:{options:()=>available?[{value:'webcodecs'},{value:'legacy'}]:[{value:'legacy'}]} } };
  window.TAHtml.SettingsController.prototype._appendTile = tile => tiles.push(tile);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'browser-settings-policy.js'), 'utf8'), { window });
  const controller = new window.TAHtml.SettingsController();
  controller._appendTile({ title: 'Renderer' }); controller._appendTile({ title: 'Resolution' });
  assert.deepEqual(tiles, available?[{title:'Renderer'},{ title: 'Resolution' }]:[{ title: 'Resolution' }]);
 }
});

test('browser selection enables WebCodecs while automatic remains legacy by default', () => {
  for (const value of ['auto', 'legacy', 'webcodecs']) {
    const { preferences, prototype, selections,writes } = fixture(value);
    assert.equal(preferences.choice(), value);
    assert.equal(preferences.effectiveChoice(), value==='webcodecs'?'webcodecs':'legacy');
    assert.deepEqual(Array.from(preferences.options(), o => o.value), ['auto', 'webcodecs','legacy']);
    for (const id of [0, 1, 2]) {
      const result = prototype._rendererInfoForId(id);
      assert.equal(result.id, value==='webcodecs'?1:id === 1 ? 2 : id);
      assert.equal(result.packetFormat, 'scrcpy');
      if(value==='webcodecs')assert.match(result.scriptPath,/h264-webcodecs/);
      else if (id === 1) assert.match(result.scriptPath, /h264-broadway/);
    }
    assert.deepEqual(selections, []);
    preferences.select('webcodecs');preferences.select('legacy'); preferences.select('auto');
    assert.deepEqual(selections, []);
    assert.deepEqual(writes.map(x=>x.value), ['webcodecs','legacy', 'auto']);
  }
});
test('restored browser preference bypasses historical legacy-only wrappers',()=>{
 const f=fixture('webcodecs','',true,true);
 assert.equal(f.preferences.choice(),'webcodecs');
 assert.doesNotThrow(()=>f.preferences.select('webcodecs'));
 assert.equal(f.preferences.effectiveChoice(),'webcodecs');
 assert.deepEqual(f.writes,[{key:'TeslaAndroid_browserH264Renderer_v1',value:'webcodecs'}]);
});

test('saved WebCodecs failure persists browser-local legacy and navigates only once',()=>{
 const f=fixture('webcodecs','?other=keep');f.prototype._rendererInfoForId(2);
 assert.equal(f.preferences.recover(),true);assert.equal(f.preferences.recover(),false);
 assert.deepEqual(f.writes,[{key:'TeslaAndroid_browserH264Renderer_v1',value:'legacy'}]);
 assert.deepEqual(f.navigations,['https://example.test/?other=keep&video=legacy#home']);
 assert.equal(f.preferences.effectiveChoice(),'legacy');
 const unsupported=fixture('webcodecs','',false);
 assert.equal(unsupported.preferences.effectiveChoice(),'legacy');
 assert.throws(()=>unsupported.preferences.select('webcodecs'));
 assert.deepEqual(Array.from(unsupported.preferences.options(),x=>x.value),['auto','legacy']);
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
