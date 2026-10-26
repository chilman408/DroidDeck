// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { PassThrough } = require('node:stream');
const { once } = require('node:events');
const { WebSocket } = require('ws');
const { createVideoRelay } = require('./h264-relay');
const { createGateway } = require('./server');
const { createDisplayControl } = require('./display-control');
const { createVideoSettings } = require('./video-settings');
const listen = s => new Promise(resolve => s.listen(0, '127.0.0.1', resolve));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function frame(flags) { const b = Buffer.alloc(2048); b[0] = flags; b.writeUInt32BE(b.length - 12, 8); b.set([0,0,0,1,0x65],12); return b; }
test('real gateway uses only current-primary relay feedback, strips forged session headers and stops automatic writes on disconnect', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-auto-wire-')); fs.chmodSync(dir, 0o700); fs.writeFileSync(path.join(dir, 'index.html'), 'test');
  const store = createVideoSettings(path.join(dir,'video.json')), source = new PassThrough();
  const relay = createVideoRelay({ connect: () => source }); await listen(relay);
  const display = createDisplayControl({ helper: { inspect: async () => ({ width: 976, height: 720, density: 180, resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } }); await display.initialize();
  const gateway = createGateway({ frontendDir: dir, displayControl: display, videoSettings: store,
    targets: { api: 'http://127.0.0.1:18081', display: 'ws://127.0.0.1:19091', touchscreen: 'ws://127.0.0.1:19999', h264: `ws://127.0.0.1:${relay.address().port}` } }); await listen(gateway);
  const base = `http://127.0.0.1:${gateway.address().port}`, owner = display.createSession(), viewer = display.createSession();
  const clients = []; let timer, acknowledge = true;
  t.after(async () => { clearInterval(timer); for (const c of clients) c.terminate(); await new Promise(resolve => gateway.close(resolve)); await relay.shutdown(); fs.rmSync(dir,{force:true,recursive:true}); });
  function connect(session, ack) {
    const c = new WebSocket(base.replace('http:','ws:') + '/sockets/display?format=h264&session='+session.token,'droiddeck-live-v1',
      { headers: { Origin: base, 'X-DroidDeck-Display-Session': 'f'.repeat(64) } });
    clients.push(c); let id;
    c.on('message',(b,binary)=>{ if(!binary){const v=JSON.parse(b);if(v.type==='scrcpy-flow-frame')id=v.id;}else if(ack())c.send(JSON.stringify({type:'scrcpy-flow-ack',id})); });
    return c;
  }
  const primary = connect(owner,()=>acknowledge); await once(primary,'open'); const slow = connect(viewer,()=>false); await once(slow,'open');
  const header=Buffer.alloc(12);header.writeUInt32BE(0x80000000);header.writeUInt32BE(976,4);header.writeUInt32BE(720,8);
  source.write(Buffer.concat([Buffer.from('h264'),header,frame(0x40),frame(0x20)]));
  timer=setInterval(()=>source.write(frame(0)),30);
  const response=await fetch(base+'/api/videoAutomatic',{method:'POST',headers:{Origin:base,'Content-Type':'application/json','X-Display-Session':owner.token,'X-Display-CSRF':owner.csrfToken},body:JSON.stringify({revision:0,profileRevision:0,enabled:true})});
  assert.equal(response.status,200);await sleep(6500);assert.equal(store.snapshot().bitRate,4000000,'Blocked secondary cannot control shared quality');
  const foreign=await fetch(`http://127.0.0.1:${relay.address().port}/flow-state`,{headers:{'X-DroidDeck-Display-Session':'f'.repeat(64)}});
  assert.deepEqual(await foreign.json(),{available:false},'Gateway strips the forged client-supplied relay identity');
  acknowledge=false;await sleep(6500);assert.equal(store.snapshot().bitRate,3000000,'Real primary receipt backlog lowers bitrate');
  primary.terminate();await sleep(100);const revision=store.snapshot().revision;
  await sleep(4000);assert.equal(store.snapshot().revision,revision,'No connected primary means no autonomous write');
});
