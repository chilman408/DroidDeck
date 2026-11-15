// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const { PassThrough } = require('node:stream');
const { EventEmitter, once } = require('node:events');
const { WebSocket } = require('ws');
const { createAudioRelay } = require('./audio-relay');

function worker(codec = 'aac', transportVersion = 2, quality = {}) {
  let time = 1000, serial = 0;
  const sockets = [], messages = [], timers = new Map(), intervals = new Map();
  class Socket {
    static OPEN = 1;
    constructor(url, protocol) { Object.assign(this, { url, protocol, readyState: 1, sent: [] }); sockets.push(this); }
    send(value) { assert.equal(this.readyState, 1); this.sent.push(JSON.parse(value)); }
    close() { this.readyState = 3; }
  }
  const self = {};
  vm.runInNewContext(fs.readFileSync(__dirname + '/browser-audio-worker.js', 'utf8'), {
    self, WebSocket: Socket, ArrayBuffer, performance: { now: () => time }, Date: { now: () => time },
    postMessage: value => messages.push(value),
    setTimeout(fn) { timers.set(++serial, fn); return serial; }, clearTimeout(id) { timers.delete(id); },
    setInterval(fn) { intervals.set(++serial, fn); return serial; }, clearInterval(id) { intervals.delete(id); },
  });
  const send = value => self.onmessage({ data: value });
  send({ type: 'start', url: 'ws://fixture/audio', codec, transportVersion, ...quality }); sockets[0].onopen();
  return { sockets, messages, timers, send,
    advance(ms) { time += ms; }, watch() { for (const fn of [...intervals.values()]) fn(); },
    reconnect() { const fn = [...timers.values()][0]; assert.ok(fn); fn(); sockets.at(-1).onopen(); },
    text(value) { sockets.at(-1).onmessage?.({ data: JSON.stringify(value) }); },
    binary(size = 2) { sockets.at(-1).onmessage?.({ data: new ArrayBuffer(size) }); },
    frame(id, stamp = time, kind = 'media') { this.text({ type: 'audio-frame', id, stamp, kind }); this.binary(); },
    data() { return messages.filter(m => m.type === 'data'); },
    epoch() { return messages.filter(m => m.type === 'open').at(-1).epoch; },
  };
}

test('v2 survives three seconds of jitter, retains init, discards stale media and releases its credit', () => {
  const f = worker(); assert.equal(f.sockets[0].protocol, 'droiddeck-aac-live-v2');
  f.advance(3000); f.watch(); assert.equal(f.sockets[0].readyState, 1);
  f.frame(1, 1000, 'init'); f.frame(2, 1000);
  assert.equal(f.data().length, 1); assert.equal(f.data()[0].kind, 'init');
  assert.equal(f.data()[0].receivedAt, 4000); assert.equal(f.data()[0].epoch, f.epoch());
  assert.deepEqual(f.sockets[0].sent.at(-1), { type: 'audio-ack', id: 2, stamp: 4000 });
  f.send({ type: 'ack', id: 1, epoch: f.epoch() }); f.frame(3, 4000);
  assert.deepEqual(f.data().map(m => m.id), [1, 3]); assert.equal(f.timers.size, 0);
});

test('freshness is checked at binary arrival and the 1500ms boundary is inclusive', () => {
  const f = worker('flac'); assert.equal(f.sockets[0].protocol, 'droiddeck-audio-live-v2');
  f.frame(1, 1000, 'init'); f.send({ type: 'ack', id: 1, epoch: f.epoch() });
  f.text({ type: 'audio-frame', id: 2, stamp: 1000, kind: 'media' }); f.advance(1501); f.binary();
  assert.deepEqual(f.data().map(m => m.id), [1]); assert.equal(f.sockets[0].sent.at(-1).id, 2);
  f.frame(3, 1001); assert.deepEqual(f.data().map(m => m.id), [1, 3]);
});

test('v2 expires at ten seconds and old epoch receipts cannot acknowledge reused ids', () => {
  const f = worker(); f.frame(1, 1000, 'init'); const old = f.epoch(), callback = f.sockets[0].onmessage;
  f.advance(10000); f.watch(); assert.equal(f.sockets[0].readyState, 1);
  f.advance(1); f.watch(); assert.equal(f.sockets[0].readyState, 3);
  assert.equal(f.messages.at(-1).epoch, old); assert.equal(f.messages.at(-1).type, 'close');
  f.reconnect(); const epoch = f.epoch(); assert.notEqual(epoch, old);
  f.frame(1, 11001, 'init'); const count = f.sockets[1].sent.length;
  for (const value of [{ type: 'ack', id: 1, epoch: old }, { type: 'ack', id: 1 }, { type: 'ack', id: 2, epoch }]) f.send(value);
  callback({ data: new ArrayBuffer(2) }); assert.equal(f.sockets[1].sent.length, count);
  f.send({ type: 'ack', id: 1, epoch }); assert.equal(f.sockets[1].sent.at(-1).id, 1);
  f.send({ type: 'stop' }); f.send({ type: 'ack', id: 1, epoch }); assert.equal(f.timers.size, 0);
});

test('v2 rejects malformed metadata and broken metadata/binary ordering without forwarding audio', () => {
  for (const metadata of [
    { id: 0 }, { id: -1 }, { id: 1.5 }, { id: Number.MAX_SAFE_INTEGER + 1 },
    { stamp: -1 }, { stamp: 1001 }, { stamp: '1000' }, { stamp: null },
    { kind: 'fragment' }, { kind: null }, { kind: undefined }, { type: 'other' },
  ]) {
    const f = worker(); f.text({ type: 'audio-frame', id: 1, stamp: 1000, kind: 'init', ...metadata }); f.binary();
    assert.equal(f.sockets[0].readyState, 3, JSON.stringify(metadata)); assert.equal(f.data().length, 0);
  }
  for (const failure of ['orphan', 'overlap', 'oversize', 'empty', 'duplicate']) {
    const f = worker();
    if (failure !== 'orphan') f.text({ type: 'audio-frame', id: 1, stamp: 1000, kind: 'init' });
    if (failure === 'overlap') f.text({ type: 'audio-frame', id: 2, stamp: 1000, kind: 'media' });
    f.binary(failure === 'oversize' ? 512 * 1024 + 1 : failure === 'empty' ? 0 : 2);
    if (failure === 'duplicate') { f.send({ type: 'ack', id: 1, epoch: f.epoch() }); f.frame(1); }
    assert.equal(f.sockets[0].readyState, 3, failure);
  }
});

test('explicit v1 keeps the 1.5 second watchdog and metadata without kind', () => {
  const f = worker('aac', 1); assert.equal(f.sockets[0].protocol, 'droiddeck-aac-live-v1');
  f.text({ type: 'audio-frame', id: 1, stamp: 1000 }); f.binary(); assert.equal(f.data().length, 1);
  f.advance(1501); f.watch(); assert.equal(f.sockets[0].readyState, 3);
});

function atom(type, size = 10, marker = 0) { const b = Buffer.alloc(size, marker); b.writeUInt32BE(size); b.write(type, 4); return b; }
const init = Buffer.concat([atom('ftyp'), atom('moov')]);
const fragment = (marker = 0, size = 10) => Buffer.concat([atom('moof'), atom('mdat', size, marker)]);
const turn = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) { for (let i = 0; i < 200; i++) { if (predicate()) return; await new Promise(r => setTimeout(r, 2)); } assert.ok(predicate(), 'relay event did not arrive'); }
async function relayFixture(t) {
  let time = 1000, captures = 0, flacStarts = 0, aacStarts = 0;
  const source = new PassThrough();
  const encoder = () => { const e = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), kill() { process.nextTick(() => e.emit('close')); } }); e.stdin.resume(); return e; };
  const flac = encoder(), aac = encoder(), clients = [];
  const relay = createAudioRelay({ keepCapture: true, clock: () => time, connect: () => { captures++; return source; },
    spawnEncoder: () => { flacStarts++; return flac; }, spawnAacEncoder: () => { aacStarts++; return aac; } });
  await new Promise(r => relay.listen(0, '127.0.0.1', r));
  t.after(async () => { for (const c of clients) c.terminate(); await relay.shutdown(); });
  async function viewer(protocol) {
    const socket = new WebSocket(`ws://127.0.0.1:${relay.address().port}`, protocol); clients.push(socket);
    const frames = [], wire = []; let metadata;
    socket.on('message', (bytes, binary) => {
      wire.push(binary ? 'binary' : 'metadata');
      if (!binary) metadata = JSON.parse(bytes.toString());
      else { frames.push({ ...metadata, bytes: Buffer.from(bytes) }); metadata = null; }
    });
    await once(socket, 'open');
    const send = value => socket.send(JSON.stringify(value));
    if (protocol) send({ type: 'audio-ready', stamp: 1000 });
    await turn(); await new Promise(r => setTimeout(r, 10));
    return { socket, frames, wire, send };
  }
  return { source, flac, aac, viewer, advance(ms) { time += ms; }, counts: () => [captures, flacStarts, aacStarts] };
}

for (const codec of ['aac', 'audio']) test(`real ${codec} v2 relay keeps init/media paired, survives jitter and drops unsent history`, async t => {
  const f = await relayFixture(t), slow = await f.viewer(`droiddeck-${codec}-live-v2`), healthy = await f.viewer(`droiddeck-${codec}-live-v2`);
  const output = codec === 'aac' ? f.aac.stdout : f.flac.stdout;
  output.write(init); await until(() => slow.frames.length === 1 && healthy.frames.length === 1);
  assert.equal(slow.frames[0].kind, 'init');
  for (let i = 0; i < 20; i++) output.write(fragment(i));
  await until(() => slow.frames.length === 8 && healthy.frames.length === 8);
  assert.deepEqual(slow.frames.slice(1).map(m => m.kind), Array(7).fill('media'));
  assert.deepEqual(slow.wire, Array.from({ length: 8 }, () => ['metadata', 'binary']).flat());
  f.advance(3000); await new Promise(r => setTimeout(r, 280)); assert.equal(slow.socket.readyState, WebSocket.OPEN);
  for (const frame of healthy.frames) healthy.send({ type: 'audio-ack', id: frame.id, stamp: 4000 });
  await new Promise(r => setTimeout(r, 15)); assert.equal(healthy.frames.length, 8, 'ACK must not replay dropped media');
  output.write(fragment(99)); await until(() => healthy.frames.length === 9);
  assert.equal(slow.frames.length, 8); assert.equal(healthy.frames[8].bytes.at(-1), 99); assert.equal(healthy.frames[8].stamp, 4000);
  assert.deepEqual(f.counts(), codec === 'aac' ? [1, 1, 1] : [1, 1, 0]); assert.equal(f.source.destroyed, false);
  const close = once(slow.socket, 'close'); f.advance(7001); await close;
  assert.equal(f.source.destroyed, false); assert.equal(healthy.socket.readyState, WebSocket.OPEN);
});

test('v2 relay bounds outstanding bytes below 512KiB and never queues unsent fragments', async t => {
  const f = await relayFixture(t), v = await f.viewer('droiddeck-audio-live-v2');
  f.flac.stdout.write(init); await until(() => v.frames.length === 1);
  const large = fragment(7, 300 * 1024);
  f.flac.stdout.write(large); await until(() => v.frames.length === 2);
  f.flac.stdout.write(large); await new Promise(r => setTimeout(r, 15)); assert.equal(v.frames.length, 2);
  v.send({ type: 'audio-ack', id: 2, stamp: 1000 }); await new Promise(r => setTimeout(r, 15)); assert.equal(v.frames.length, 2);
  f.flac.stdout.write(fragment(9)); await until(() => v.frames.length === 3); assert.equal(v.frames[2].bytes.at(-1), 9);
});

test('real relay retains v1 1.5s timeout, raw binary framing and rejects malformed receipts', async t => {
  const f = await relayFixture(t), old = await f.viewer('droiddeck-audio-live-v1'), raw = await f.viewer();
  f.flac.stdout.write(Buffer.concat([init, fragment()])); await until(() => old.frames.length === 2 && raw.frames.length === 2);
  assert.equal(old.frames[0].kind, undefined); assert.deepEqual(raw.wire, ['binary', 'binary']);
  const close = once(old.socket, 'close'); f.advance(1501); await close; assert.equal(raw.socket.readyState, WebSocket.OPEN);
  const bad = await f.viewer('droiddeck-audio-live-v2'), rejected = once(bad.socket, 'close');
  bad.send({ type: 'audio-ack', id: 0, stamp: 1000 }); assert.equal((await rejected)[0], 1008);
});

test('v2 worker cannot be kept alive by ACKs for unknown ids or by receipts for newer frames', () => {
  const f = worker(); f.frame(1, 1000, 'init'); f.advance(9000);
  f.frame(2, 10000); f.send({ type: 'ack', id: 2, epoch: f.epoch() });
  f.advance(1001); f.send({ type: 'ack', id: 999, epoch: f.epoch() }); f.watch();
  assert.equal(f.sockets[0].readyState, 3, 'Unacknowledged init still expires even when newer frames get receipts');
});

test('v2 worker bounds retained init and outstanding payloads, and ignores a closed socket', () => {
  for (const mode of ['bytes', 'count', 'closed']) {
    const f = worker(); f.text({ type: 'audio-frame', id: 1, stamp: 1000, kind: 'init' }); f.binary(mode === 'bytes' ? 300 * 1024 : 2);
    if (mode === 'closed') {
      f.sockets[0].readyState = 3; f.send({ type: 'ack', id: 1, epoch: f.epoch() }); f.frame(2);
      assert.equal(f.sockets[0].sent.length, 1); assert.equal(f.data().length, 1); continue;
    }
    if (mode === 'bytes') { f.text({ type: 'audio-frame', id: 2, stamp: 1000, kind: 'media' }); f.binary(300 * 1024); }
    else for (let id = 2; id <= 9; id++) f.frame(id);
    assert.equal(f.sockets[0].readyState, 3); assert.equal(f.data().length, mode === 'bytes' ? 1 : 8);
  }
});

test('v2 relay retries blocked init before media without emitting orphan metadata', async t => {
  const descriptor = Object.getOwnPropertyDescriptor(WebSocket.prototype, 'bufferedAmount'); let blocked = false;
  Object.defineProperty(WebSocket.prototype, 'bufferedAmount', { configurable: true, get() {
    return this._isServer && blocked ? 512 * 1024 : descriptor.get.call(this);
  } });
  try {
    const f = await relayFixture(t), v = await f.viewer('droiddeck-audio-live-v2');
    blocked = true; f.flac.stdout.write(Buffer.concat([init, fragment(1)])); await new Promise(r => setTimeout(r, 15));
    assert.deepEqual(v.wire, [], 'Admission must reserve both metadata and binary before sending');
    blocked = false; f.flac.stdout.write(fragment(2)); await until(() => v.frames.length === 2);
    assert.deepEqual(v.frames.map(m => [m.id, m.kind]), [[1, 'init'], [2, 'media']]);
    assert.deepEqual(v.frames[0].bytes, init); assert.equal(v.frames[1].bytes.at(-1), 2);
  } finally { Object.defineProperty(WebSocket.prototype, 'bufferedAmount', descriptor); }
});

test('v2 relay rejects invalid receipts and never lets duplicate or unknown ids refresh the clock', async t => {
  const f = await relayFixture(t);
  for (const invalid of [
    { id: -1 }, { id: 1.5 }, { id: '1' }, { id: Number.MAX_SAFE_INTEGER + 1 },
    { stamp: -1 }, { stamp: '1000' }, { stamp: null }, { stamp: 999 }, { extra: true },
  ]) {
    const v = await f.viewer('droiddeck-audio-live-v2');
    if (!v.frames.length) { f.flac.stdout.write(init); await until(() => v.frames.length === 1); }
    const close = once(v.socket, 'close'); v.send({ type: 'audio-ack', id: 1, stamp: 1000, ...invalid });
    assert.equal((await close)[0], 1008, JSON.stringify(invalid));
  }
  const v = await f.viewer('droiddeck-audio-live-v2'); await until(() => v.frames.length === 1);
  v.send({ type: 'audio-ack', id: 1, stamp: 1001 }); v.send({ type: 'audio-ack', id: 1, stamp: 9000 });
  v.send({ type: 'audio-ack', id: 999, stamp: 9000 }); await new Promise(r => setTimeout(r, 15));
  f.flac.stdout.write(fragment()); await until(() => v.frames.length === 2);
  assert.equal(v.frames[1].stamp, 1001); assert.equal(v.socket.readyState, WebSocket.OPEN);
});

test('v2 relay times out a missing ready handshake after ten seconds without restarting capture', async t => {
  const f = await relayFixture(t);
  const v = await f.viewer('droiddeck-audio-live-v2');
  // A second raw WebSocket deliberately omits the live ready handshake.
  const socket = new WebSocket(v.socket.url, 'droiddeck-audio-live-v2');
  try {
    await once(socket, 'open'); f.advance(3000); await new Promise(r => setTimeout(r, 280)); assert.equal(socket.readyState, WebSocket.OPEN);
    const close = once(socket, 'close'); f.advance(7001); await close; assert.equal(f.source.destroyed, false);
  } finally { socket.terminate(); }
});

test('AAC socket failures reconnect with the same codec and preserve the failed epoch', () => {
  const f = worker();
  for (let i = 0; i < 4; i++) {
    const epoch = f.epoch(); f.sockets.at(-1).onerror();
    assert.equal(f.messages.at(-1).reason, 'socket-error'); assert.equal(f.messages.at(-1).epoch, epoch);
    f.reconnect(); assert.equal(f.sockets.at(-1).protocol, 'droiddeck-aac-live-v2');
  }
  f.send({ type: 'stop' });
});


test('audio profiles negotiate explicit64/256 and automatic downshift/upshift uses fresh sustained receipts',()=>{
 for(const rate of [64000,256000]){const f=worker('aac',2,{bitRate:rate});assert.equal(f.sockets[0].protocol,'droiddeck-aac-'+rate/1000+'-live-v2');}
 const f=worker('aac',2,{bitRate:256000,automatic:true});assert.equal(f.sockets[0].protocol,'droiddeck-aac-live-v2');
 f.advance(1600);f.frame(1,1000);assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-64-live-v2');f.sockets.at(-1).onopen();
 // Eight healthy seconds restore128;256 still needs a separate15-second window.
 let stamp=2600;
 for(let i=0;i<82;i++){f.advance(100);stamp+=100;f.frame(i+1,stamp);const data=f.data().at(-1);if(data && data.epoch===f.epoch())f.send({type:'ack',id:data.id,epoch:data.epoch});}
 assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-live-v2');
 assert.equal(f.messages.filter(m=>m.type==='quality').at(-1).bitRate,128000);
});


test('automatic audio cannot promote from idle gaps and manual mode never changes rate',()=>{
 for(const automatic of [false,true]){const f=worker('aac',2,{bitRate:256000,currentBitRate:64000,automatic});for(let i=0;i<3;i++){f.advance(31000);f.frame(i+1,32000+i*31000);const d=f.data().at(-1);if(d)f.send({type:'ack',id:d.id,epoch:d.epoch});}assert.equal(f.sockets.length,1);assert.equal(f.sockets[0].protocol,automatic?'droiddeck-aac-64-live-v2':'droiddeck-aac-256-live-v2');}
});

test('responsive AAC downshift detects500ms sustained lateness without waiting1.5seconds',()=>{
 const f=worker('aac',2,{bitRate:128000,automatic:true});let id=1,time=1000;
 f.frame(id++);const data=f.data().at(-1);f.send({type:'ack',id:data.id,epoch:data.epoch});
 for(let i=0;i<5;i++){f.advance(100);time+=100;f.frame(id++,time-350);const d=f.data().at(-1);if(d&&d.epoch===f.epoch())f.send({type:'ack',id:d.id,epoch:d.epoch});}
 assert.equal(f.sockets.length,1);f.advance(100);time+=100;f.frame(id++,time-350);assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-64-live-v2');
});
test('responsive AAC restores128after8seconds and256onlyafteranother15healthyseconds',()=>{
 const f=worker('aac',2,{bitRate:256000,currentBitRate:64000,automatic:true});let id=1;
 const fresh=()=>{f.frame(id++);const d=f.data().at(-1);if(d&&d.epoch===f.epoch())f.send({type:'ack',id:d.id,epoch:d.epoch});};fresh();
 for(let i=0;i<79;i++){f.advance(100);fresh();}assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-64-live-v2');
 f.advance(100);fresh();assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-live-v2');f.sockets.at(-1).onopen();
 for(let i=0;i<149;i++){f.advance(100);fresh();}assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-live-v2');
 f.advance(200);fresh();assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-256-live-v2');
});
test('a failed AAC promotion backs off instead of switching source every8seconds',()=>{
 const f=worker('aac',2,{bitRate:128000,currentBitRate:64000,automatic:true});let id=1,time=1000;
 const fresh=()=>{f.frame(id++);const d=f.data().at(-1);if(d&&d.epoch===f.epoch())f.send({type:'ack',id:d.id,epoch:d.epoch});};fresh();
 for(let i=0;i<80;i++){f.advance(100);time+=100;fresh();}assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-live-v2');f.sockets.at(-1).onopen();
 f.advance(2000);time+=2000;f.frame(id++,time-900);assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-64-live-v2');f.sockets.at(-1).onopen();
 for(let i=0;i<100;i++){f.advance(100);time+=100;fresh();}assert.equal(f.sockets.at(-1).protocol,'droiddeck-aac-64-live-v2');
});
