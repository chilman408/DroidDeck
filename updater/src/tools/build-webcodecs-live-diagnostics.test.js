// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');

for (const scenario of ['error','frames','aud']) test(`live metadata survives ${scenario}`, async () => {
  const fail=scenario==='error',aud=scenario==='aud';
  const { build } = require('./build-webcodecs-live-diagnostics');
  const html = build({audCompatibility:aud}); assert.equal(html, build({audCompatibility:aud}));
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push(req.url); res.setHeader('Content-Type', 'text/html');
    if (req.url.startsWith('/js/diagnostics/live.html')) return res.end(html);
    if (req.url === '/') return res.end('<!doctype html><html><head><title>DroidDeck</title><script defer src="./js/app.js"></script></head><body><canvas id="canvas"></canvas></body></html>');
    res.setHeader('Content-Type', 'text/javascript');
    if (req.url === '/js/app.js') return res.end(`
      window.TAHtml={browserRenderer:{effectiveChoice:()=> 'webcodecs'}};
      const w=new Worker('./js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js');
      w.onmessage=e=>{if(e.data.error){const u=new URL(location);u.searchParams.set('video','legacy');location.replace(u)}
        if(e.data.frameRendered)document.getElementById('canvas').dataset.videoFrames='3'};
      w.postMessage({scrcpySession:{width:1280,height:720,secret:'SENSITIVE_SENTINEL'}});
      const b=new ArrayBuffer(22),v=new Uint8Array(b);v[0]=64;v.set([0,0,0,1,103,66,192,40,0,0],12);
      new DataView(b).setUint32(8,10);w.postMessage({scrcpyPacket:b},[b]);
      ${aud ? "const k=new Uint8Array(22);k[0]=32;k.set([0,0,1,9,240,0,0,1,101,128],12);new DataView(k.buffer).setUint32(8,10);w.postMessage({scrcpyPacket:k.buffer},[k.buffer]);" : ''}
    `);
    if (aud && req.url.endsWith('h264WebCodecsWorker.js')) return res.end(`
      function handleScrcpyPacket(packet){const b=new Uint8Array(packet);
        if(b[0]&64){postMessage({webCodecsConfigured:true});return;}
        for(let i=12;i+3<b.length;i++)if(b[i]===0&&b[i+1]===0&&b[i+2]===1&&(b[i+3]&31)===9){postMessage({error:'DataError: key frame is required'});return;}
        postMessage({frameRendered:true});}
      onmessage=e=>{if(e.data.scrcpyPacket)handleScrcpyPacket(e.data.scrcpyPacket)};
    `);
    if (req.url.endsWith('h264WebCodecsWorker.js')) return res.end(`onmessage=e=>{if(e.data.scrcpyPacket){
      postMessage({webCodecsConfigured:true,accelerationPreference:'no-preference',secret:'SENSITIVE_SENTINEL'});
      ${fail ? "postMessage({error:'NotSupportedError: SENSITIVE_SENTINEL'});" : "postMessage({frameRendered:true,width:1280,height:720,secret:'SENSITIVE_SENTINEL'});"}
    }};`);
    res.statusCode = 404; res.end('missing');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try {
    const page = await browser.newPage(), errors = [], foreign = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (new URL(r.url()).origin !== origin) foreign.push(r.url()); });
    await page.goto(origin + '/js/diagnostics/live.html?video=webcodecs-test');
    if (!fail) { await page.waitForFunction(() => document.getElementById('canvas')?.dataset.videoFrames === '3' || new URLSearchParams(location.search).get('video')==='legacy'); if(await page.locator('#codec-finish').isVisible())await page.locator('#codec-finish').click(); }
    await page.waitForFunction(() => document.getElementById('codec-result')?.textContent.includes('live-webcodecs-diagnostic'));
    const raw = await page.locator('#codec-result').textContent(), report = JSON.parse(raw);
    assert.equal(report.workerCreated, true);
    assert.equal(report.configured, true);
    assert.equal(report.packets, aud?2:1); assert.equal(report.configPackets, 1);
    assert.equal(report.codec, 'avc1.42c028');
    assert.deepEqual(report.session, {width:1280,height:720});
    if (fail) { assert.equal(report.errorCategory,'NotSupportedError'); assert.equal(report.finalVideoQuery,'legacy'); }
    else { assert.equal(report.rendered,1); assert.equal(report.canvasFrames,3); assert.equal(report.finalVideoQuery,'report'); }
    if(aud){
      assert.equal(report.compatibility,'omit-aud');assert.equal(report.audRemoved,1);
      const fixture=require('./fixtures/webcodecs-colour-pattern.json');
      const {normalizeScrcpyPacket}=require('./webcodecs-access-unit-policy');
      const payload=Uint8Array.from([0,0,0,1,9,240,...Buffer.from(fixture.data,'base64')]);
      const packet=new Uint8Array(12+payload.length);packet[0]=32;packet.set(payload,12);new DataView(packet.buffer).setUint32(8,payload.length);
      const normalized=normalizeScrcpyPacket(packet.buffer);assert.equal(normalized.audRemoved,1);
      const visible=await page.evaluate(async({fixture,bytes})=>{
        let frame,decoder;
        try{
          frame=await new Promise((resolve,reject)=>{
            const timer=setTimeout(()=>reject(new Error('DecodeTimeout')),5000);
            decoder=new VideoDecoder({output:f=>{clearTimeout(timer);resolve(f)},error:reject});
            decoder.configure({codec:fixture.codec,codedWidth:fixture.width,codedHeight:fixture.height});
            decoder.decode(new EncodedVideoChunk({type:'key',timestamp:0,data:Uint8Array.from(bytes)}));decoder.flush().catch(reject);
          });
          const c=document.createElement('canvas');c.width=fixture.width;c.height=fixture.height;const ctx=c.getContext('2d');ctx.drawImage(frame,0,0);
          const colours=[[40,40],[240,40],[40,140],[240,140]].map(([x,y])=>Array.from(ctx.getImageData(x,y,1,1).data).slice(0,3));
          return colours;
        }finally{frame?.close();if(decoder&&decoder.state!=='closed')decoder.close();}
      },{fixture,bytes:Array.from(new Uint8Array(normalized.packet).subarray(12))});
      const colours=new Set(visible.map(([r,g,b])=>r>150&&g>150&&b>150?'white':r>150&&g<100&&b<100?'red':g>150&&r<100&&b<100?'green':b>150&&r<100&&g<100?'blue':'unknown'));
      assert.deepEqual([...colours].sort(),['blue','green','red','white']);
    }
    assert.ok(!raw.includes('SENSITIVE_SENTINEL')); assert.ok(raw.length < 8192);
    assert.ok(requests.includes('/js/app.js')); assert.deepEqual(foreign,[]); assert.deepEqual(errors,[]);
    const count = requests.filter(x=>x==='/').length;
    await page.reload(); assert.equal(requests.filter(x=>x==='/').length,count);
    console.log(JSON.stringify({fallback:fail,reportRetained:true,metadataOnly:true,originalWorker:true}));
  } finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
});

test('diagnostic ends on its own bound and refuses to stream when session storage is blocked', async () => {
  const { build } = require('./build-webcodecs-live-diagnostics');
  let rootRequests=0,lateStorageFailure=false,unloadStorageFailure=false;
  const server=http.createServer((req,res)=>{
    res.setHeader('Content-Type','text/html');
    if(req.url.startsWith('/js/diagnostics/live.html')) return res.end(build());
    if(req.url==='/') {rootRequests++;return res.end('<!doctype html><html><head><title>DroidDeck</title></head><body><canvas id="canvas"></canvas>'+ (lateStorageFailure ? '<script>Storage.prototype.setItem=()=>{throw new DOMException("Full","QuotaExceededError")}</script>' : '')+(unloadStorageFailure ? '<script>setTimeout(()=>{Storage.prototype.setItem=()=>{throw new DOMException("Full","QuotaExceededError")};const u=new URL(location);u.searchParams.set("video","legacy");location.replace(u)},20)</script>' : '')+'</body></html>');}
    res.statusCode=404;res.end();
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try {
    const page=await browser.newPage();
    await page.addInitScript(()=>{
      const native=setTimeout;
      window.setTimeout=(fn,ms,...args)=>native(fn,ms===45000?80:ms,...args);
    });
    await page.goto(origin+'/js/diagnostics/live.html?video=webcodecs-test');
    await page.waitForFunction(()=>new URLSearchParams(location.search).get('video')==='report');
    const report=JSON.parse(await page.locator('#codec-result').textContent());
    assert.equal(report.workerCreated,false);assert.equal(report.rendered,0);assert.equal(rootRequests,1);
    const blocked=await browser.newPage();
    await blocked.addInitScript(()=>{Storage.prototype.removeItem=()=>{throw new DOMException('Blocked','SecurityError')};});
    await blocked.goto(origin+'/js/diagnostics/live.html?video=webcodecs-test');
    assert.match(await blocked.locator('#codec-result').textContent(),/Session storage is required/);
    assert.equal(rootRequests,1);
    const quota=await browser.newPage();
    await quota.addInitScript(()=>{Storage.prototype.setItem=()=>{throw new DOMException('Full','QuotaExceededError')};});
    await quota.goto(origin+'/js/diagnostics/live.html?video=webcodecs-test');
    assert.match(await quota.locator('#codec-result').textContent(),/Session storage is required/);
    assert.equal(rootRequests,1);
    lateStorageFailure=true;
    const late=await browser.newPage();
    await late.addInitScript(()=>{const native=setTimeout;window.setTimeout=(fn,ms,...args)=>native(fn,ms===45000?80:ms,...args);});
    await late.goto(origin+'/js/diagnostics/live.html?video=webcodecs-test');
    await late.waitForFunction(()=>new URLSearchParams(location.search).get('video')==='report');
    assert.match(await late.locator('#codec-result').textContent(),/Report storage failed during/);
    assert.equal(rootRequests,2);
    lateStorageFailure=false;unloadStorageFailure=true;
    const unload=await browser.newPage();
    await unload.goto(origin+'/js/diagnostics/live.html?video=webcodecs-test');
    await unload.waitForFunction(()=>new URLSearchParams(location.search).get('video')==='legacy');
    assert.match(await unload.locator('#codec-result').textContent(),/No diagnostic report was retained/);
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
});
