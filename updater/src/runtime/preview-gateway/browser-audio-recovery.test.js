// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
function player(query = "?audio=flac", supportsAac = true) {
  const workers=[], sources=[], actions={}, audioEvents={}, timers=new Map(),intervals=[]; let now=10000, serial=0;
  const audio={currentTime:0,readyState:4,paused:true,volume:1,play(){this.paused=false;return Promise.resolve();},pause(){this.paused=true;},load(){},removeAttribute(){},addEventListener(name,fn){audioEvents[name]=fn;}};
  class MediaSource {
    constructor(){this.events={};sources.push(this);}
    static isTypeSupported(mime){return supportsAac || !mime.includes("mp4a");}
    addEventListener(name,fn){this.events[name]=fn;}
    addSourceBuffer(mime){this.mime=mime;return this.buffer={updating:false,events:{},appended:[],end:0,buffered:{length:1,start:()=>0,end:()=>this.buffer.end},
      addEventListener(name,fn){this.events[name]=fn;},appendBuffer(bytes){assert.equal(this.updating,false);this.appended.push(bytes);this.updating=true;},remove(){this.updating=true;}};}
  }
  const navigations=[];
  const window={location:{search:query.split('#')[0],href:'https://example.test/'+query,replace:url=>navigations.push(url),reload:()=>navigations.push('reload')},MediaSource,localStorage:{getItem:()=>null,setItem(){}},addEventListener(){},dispatchEvent(){},audioWebsocketUrl:'wss://example.test/sockets/audio'};
  const context={window,MediaSource,document:{createElement:()=>audio,body:{appendChild(){}}},URLSearchParams,URL:Object.assign(class extends URL {},{createObjectURL:()=> 'blob:audio',revokeObjectURL(){}}),
    Date:{now:()=>now},performance:{now:()=>now},CustomEvent:class {},navigator:{mediaSession:{setActionHandler(name,fn){actions[name]=fn;}}},MediaMetadata:class {constructor(value){Object.assign(this,value);}},
    Worker:class{constructor(){this.messages=[];workers.push(this);}postMessage(value){this.messages.push(value);}terminate(){this.terminated=true;}},
    setTimeout(fn,ms){timers.set(++serial,{fn,ms});return serial;},clearTimeout(id){timers.delete(id);},setInterval(fn,ms){intervals.push({fn,ms});return ++serial;},clearInterval(){}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'browser-audio-playback.js'),'utf8'),context);
  window.startAudioPlayback();const worker=workers.at(-1); worker.onmessage({data:{type:'open'}});const source=sources.at(-1);source.events.sourceopen();
  const send=(id,age=0)=>worker.onmessage({data:{type:'data',id,buf:new Uint8Array([1,2]).buffer,receivedAt:now-age}});
  function end(){source.buffer.updating=false;source.buffer.events.updateend();}
  return {context,intervals,window,audio,audioEvents,worker,workers,sources,source,send,end,actions,timers,navigations,advance(ms){now+=ms;}};
}
test('audio seeks a minutes-old playback cursor before the next append can block latency recovery',()=>{
  const f=player();f.send(1);f.send(2);f.source.buffer.end=180;f.audio.currentTime=1;
  assert.equal(f.worker.messages.filter(m=>m.type==='ack').length,0,'Receipt waits for append completion');
  f.end();assert.ok(f.audio.currentTime>=179.8);assert.equal(f.source.buffer.appended.length,2);
  assert.deepEqual(f.worker.messages.filter(m=>m.type==='ack').map(m=>m.id),[1]);
  f.end();assert.deepEqual(f.worker.messages.filter(m=>m.type==='ack').map(m=>m.id),[1,2]);
});
test('old main-thread audio is discarded, stops immediately and cannot restart after disabling',()=>{
  const f=player();f.send(1,180000);assert.equal(f.worker.terminated,true);assert.equal(f.audio.paused,true);
  assert.equal(f.source.buffer.appended.length,0);assert.equal(f.timers.size,1);
  f.window.setAudioEnabled(false);assert.equal(f.timers.size,0);
});
test('reconnect uses a new MSE timeline and ignores old worker callbacks while preserving pause, volume and A/V offset',()=>{
  const f=player();f.window.setAudioVolume(.5);f.window.setAudioDelayMs(-250);f.actions.pause();
  const old=f.source;f.worker.onmessage({data:{type:'close'}});f.worker.onmessage({data:{type:'open'}});
  const fresh=f.sources.at(-1);fresh.events.sourceopen();assert.notEqual(old,fresh);assert.equal(f.audio.paused,true);
  assert.equal(f.audio.volume,.5);assert.equal(f.window.getAudioDelayMs(),-250);
  f.actions.play();assert.equal(f.audio.paused,false);f.window.stopAudioPlayback();
  const count=f.sources.length;f.worker.onmessage({data:{type:'open'}});assert.equal(f.sources.length,count);
});
function workerFixture(){
  let now=1000;const sockets=[],messages=[],timers=new Map(),intervals=new Map();let serial=0;
  class WebSocket {static OPEN=1;constructor(url,protocol){this.url=url;this.protocol=protocol;this.readyState=1;this.sent=[];sockets.push(this);}send(value){this.sent.push(JSON.parse(value));}close(){this.closed=true;}}
  const self={};const context={self,WebSocket,performance:{now:()=>now},Date:{now:()=>now},ArrayBuffer,postMessage:value=>messages.push(value),
    setTimeout(fn){timers.set(++serial,fn);return serial;},clearTimeout(id){timers.delete(id);},setInterval(fn){intervals.set(++serial,fn);return serial;},clearInterval(id){intervals.delete(id);}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'browser-audio-worker.js'),'utf8'),context);
  self.onmessage({data:{type:'start',url:'wss://example.test/sockets/audio'}});sockets[0].onopen();
  return {self,sockets,messages,timers,intervals,advance(ms){now+=ms;},packet(stamp=1000){const socket=sockets.at(-1);socket.onmessage({data:JSON.stringify({type:'audio-frame',id:1,stamp})});if(socket.onmessage)socket.onmessage({data:new ArrayBuffer(2)});}};
}
test('worker rejects network-delayed audio using its own echoed clock, without trusting server/client clock agreement',()=>{
  const f=workerFixture();f.advance(180000);f.packet();assert.equal(f.sockets[0].closed,true);
  assert.equal(f.messages.filter(m=>m.type==='data').length,0);assert.equal(f.timers.size,1);
  f.self.onmessage({data:{type:'stop'}});assert.equal(f.timers.size,0);
});
test('worker stalls behind main-thread receipts for at most 1.5 seconds, then reconnects instead of replaying',()=>{
  const f=workerFixture();f.packet();assert.equal(f.messages.filter(m=>m.type==='data').length,1);
  assert.equal(f.sockets[0].sent.length,1,'Receiving a packet alone does not release transport credit');
  f.advance(1600);[...f.intervals.values()][0]();assert.equal(f.sockets[0].closed,true);
  assert.equal(f.timers.size,1);[...f.timers.values()][0]();f.sockets[1].onopen();
  f.packet(2600);f.self.onmessage({data:{type:'ack',id:1}});assert.equal(f.sockets[1].sent.at(-1).type,'audio-ack');
});


test('AAC defaults on; explicit FLAC is negotiated separately and unsupported AAC reports fallback',()=>{
  const standard=player();assert.equal(standard.window.DroidDeckAudio.snapshot().effective,'flac');
  const trial=player('');assert.match(trial.source.mime,/mp4a.40.2/);
  assert.equal(trial.worker.messages[0].codec,'aac');assert.equal(trial.window.DroidDeckAudio.snapshot().targetBitRate,128000);
  const unsupported=player('?audio=aac',false);assert.match(unsupported.source.mime,/flac/);
  assert.equal(unsupported.window.DroidDeckAudio.snapshot().effective,'flac');assert.match(unsupported.window.DroidDeckAudio.snapshot().fallbackReason,/does not support AAC/);
  const duplicate=player('?audio=aac&audio=flac');assert.equal(duplicate.window.DroidDeckAudio.snapshot().effective,'aac');
});

test('repeated AAC startup failure returns only that browser to FLAC; healthy AAC is never expired',()=>{
  const f=player('?audio=aac');
  f.worker.onmessage({data:{type:'close'}});f.worker.onmessage({data:{type:'open'}});
  f.worker.onmessage({data:{type:'close'}});f.worker.onmessage({data:{type:'open'}});
  f.worker.onmessage({data:{type:'close'}});
  assert.equal(f.window.DroidDeckAudio.snapshot().effective,'flac');assert.match(f.window.DroidDeckAudio.snapshot().fallbackReason,/failed/);
  assert.equal(f.worker.terminated,true);
});


test('explicit FLAC preserves other URL choices and default AAC removes only its override; healthy AAC never expires',()=>{
  const f=player('?video=webcodecs&audio=aac#home');f.window.DroidDeckAudio.select('flac');
  assert.equal(f.navigations[0],'https://example.test/?video=webcodecs&audio=flac#home');
  const back=player('?video=legacy&audio=flac#home');back.window.DroidDeckAudio.select('aac');assert.equal(back.navigations[0],'https://example.test/?video=legacy#home');
  f.audio.paused=false;f.audio.currentTime=1;f.audioEvents.timeupdate();
  for(let i=0;i<8;i++){f.worker.onmessage({data:{type:'close'}});f.worker.onmessage({data:{type:'open'}});}
  assert.equal(f.window.DroidDeckAudio.snapshot().effective,'aac');assert.equal(f.window.DroidDeckAudio.snapshot().fallbackReason,null);
});


test('MSE updating=false before queued updateend cannot overwrite an outstanding fragment receipt',()=>{
  const f=player();f.send(1);f.source.buffer.updating=false;
  // MSE flips updating before dispatching updateend; a worker message can run between them.
  f.send(2);assert.equal(f.source.buffer.appended.length,1);
  f.end();assert.equal(f.source.buffer.appended.length,2);assert.deepEqual(f.worker.messages.filter(m=>m.type==='ack').map(m=>m.id),[1]);
  f.end();assert.deepEqual(f.worker.messages.filter(m=>m.type==='ack').map(m=>m.id),[1,2]);
});


test('media title and DroidDeck artwork remain identical at startup and periodic metadata refresh',()=>{
 const f=player('');const metadata=()=>f.context.navigator.mediaSession.metadata;
 for(let i=0;i<2;i++){
  assert.equal(metadata().title,'DroidDeck Audio');assert.equal(metadata().artwork[0].src,'https://example.test/assets/droiddeck-logo.png');assert.equal(metadata().artwork[0].sizes,'1254x1254');assert.equal(metadata().artwork[0].type,'image/png');
  f.intervals.find(t=>t.ms===60000).fn();
 }
});
