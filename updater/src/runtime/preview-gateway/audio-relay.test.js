// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { PassThrough } = require("node:stream");
const { EventEmitter, once } = require("node:events");
const { WebSocket } = require("ws");
const wait = () => new Promise(r => setTimeout(r, 25));

test('audio mux matches upstream low-latency FLAC container settings', () => {
  const { encoderArgs } = require('./audio-relay');
  const args = encoderArgs();
  const value = option => args[args.indexOf(option) + 1];
  assert.equal(value('-frag_duration'), '40000');
  assert.equal(value('-movflags'), 'empty_moov+separate_moof+default_base_moof+frag_discont');
  assert.equal(value('-brand'), 'isom');
  assert.equal(value('-max_interleave_delta'), '0');
  assert.equal(value('-use_editlist'), '0');
  assert.equal(value('-write_prft'), '0');
  assert.equal(value('-compression_level'), '0');
});

test('real FFmpeg emits short playable fragments and preserves stereo PCM exactly', {
  skip: process.platform !== 'linux' || !fs.existsSync('/usr/bin/ffmpeg'),
}, () => {
  const { spawnSync } = require('node:child_process');
  const { encoderArgs } = require('./audio-relay');
  const { Mp4Fragments } = require('./audio-stream');
  const input = Buffer.alloc(48000 * 4);
  for (let frame = 0; frame < 48000; frame++) {
    input.writeInt16LE(Math.round(12000 * Math.sin(frame * 2 * Math.PI * 440 / 48000)), frame * 4);
    input.writeInt16LE(Math.round(8000 * Math.sin(frame * 2 * Math.PI * 660 / 48000)), frame * 4 + 2);
  }
  const encoded = spawnSync('/usr/bin/ffmpeg', encoderArgs(), { input, timeout: 10000, maxBuffer: 2 * 1024 * 1024 });
  assert.equal(encoded.error, undefined);
  assert.equal(encoded.status, 0, encoded.stderr.toString());
  const messages = [];
  const parser = new Mp4Fragments((type, bytes) => messages.push({ type, bytes }));
  for (let offset = 0; offset < encoded.stdout.length; offset += 137) parser.push(encoded.stdout.subarray(offset, offset + 137));
  assert.equal(parser.pending.length, 0);
  assert.equal(messages[0].type, 'init');
  assert.equal(messages[0].bytes.toString('ascii', 8, 12), 'isom');
  assert.ok(messages.filter(message => message.type === 'fragment').length >= 15,
    'One second of PCM must not be batched into five 200ms fragments');
  const decoded = spawnSync('/usr/bin/ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin',
    '-i', 'pipe:0', '-f', 's16le', '-acodec', 'pcm_s16le', 'pipe:1'], {
    input: Buffer.concat(messages.map(message => message.bytes)), timeout: 10000, maxBuffer: 2 * 1024 * 1024,
  });
  assert.equal(decoded.error, undefined);
  assert.equal(decoded.status, 0, decoded.stderr.toString());
  assert.deepEqual(decoded.stdout, input);
});
test("slow audio viewers cannot accumulate an unbounded queue", () => {
  const { sendAudio } = require("./audio-relay"); let sent = 0, terminated = false;
  const client = { readyState: 1, bufferedAmount: 512 * 1024 + 1, send() { sent++; }, terminate() { terminated = true; } };
  sendAudio(client, Buffer.from("data")); assert.equal(sent, 0); assert.equal(terminated, true);
});
function atom(type) { const b = Buffer.alloc(10); b.writeUInt32BE(10); b.write(type, 4); return b; }
test("one bounded audio encoder serves late viewers with init, never replays stale audio, and rejects writes", async () => {
  assert.ok(fs.existsSync(__dirname + "/audio-relay.js"), "audio relay not implemented");
  const { createAudioRelay, encoderArgs } = require("./audio-relay");
  assert.ok(encoderArgs().includes("flac")); assert.ok(encoderArgs().includes("pipe:0"));
  const child = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
    kill() { process.nextTick(() => child.emit("close")); return true; } });
  const source = new PassThrough(); let encoders = 0;
  const relay = createAudioRelay({ connect: () => source, spawnEncoder: () => { encoders++; return child; } });
  await new Promise(resolve => relay.listen(0, "127.0.0.1", resolve)); const clients = [];
  async function viewer() {
    const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`), messages = [];
    clients.push(client); client.on("message", b => messages.push(b)); await once(client, "open"); await wait();
    return { client, messages };
  }
  try {
    const a = await viewer(); assert.equal(encoders, 1);
    source.write(Buffer.from([0, 0x72, 0x61, 0x77, 1, 2, 3, 4]));
    const init = Buffer.concat([atom("ftyp"), atom("moov")]), fragment = Buffer.concat([atom("moof"), atom("mdat")]);
    child.stdout.write(Buffer.concat([init, fragment]));
    await wait(); assert.deepEqual(a.messages, [init, fragment]);
    const b = await viewer(); assert.deepEqual(b.messages, [init]); assert.equal(encoders, 1);
    const closed = once(a.client, "close"); a.client.send("no input"); assert.equal((await closed)[0], 1008);
    const failed = once(b.client, "close"); source.destroy(new Error("capture gone")); await failed;
  } finally { for (const client of clients) client.terminate(); await relay.shutdown(); }
});
test("capture starts on demand and last viewer releases encoder and source after idle grace", async () => {
  const { createAudioRelay } = require("./audio-relay");
  let source, child, started = 0, stopped = 0;
  const relay = createAudioRelay({ idleMs: 10, connect: () => { source = new PassThrough(); return source; },
    spawnEncoder: () => {
      started++; child = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
        kill() { stopped++; process.nextTick(() => child.emit("close")); return true; } }); return child;
    } });
  await new Promise(r => relay.listen(0, "127.0.0.1", r)); assert.equal(started, 0);
  const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`);
  try {
    await once(client, "open"); assert.equal(started, 1); client.close(); await once(client, "close"); await wait();
    assert.equal(source.destroyed, true); assert.equal(child.stdin.destroyed, true); assert.ok(stopped > 0);
  } finally { client.terminate(); await relay.shutdown(); }
});

test('live PCM volume changes keep the same source, encoder and viewer connection', async () => {
  const { createAudioRelay } = require('./audio-relay');
  const source = new PassThrough(), output = []; let starts = 0, volume = 50;
  const child = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
    kill() { process.nextTick(() => child.emit('close')); return true; } });
  child.stdin.on('data', bytes => output.push(bytes));
  const relay = createAudioRelay({ keepCapture: true, volume: () => volume, connect: () => source,
    spawnEncoder: () => { starts++; return child; } });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`);
  try {
    await once(client, 'open');
    const frame = Buffer.alloc(4); frame.writeInt16LE(10000, 0); frame.writeInt16LE(-10000, 2);
    source.write(Buffer.concat([Buffer.from([0, 0x72, 0x61, 0x77]), frame])); await wait();
    volume = 150; source.write(frame); await wait(); volume = 0; source.write(frame); await wait();
    const expected = Buffer.alloc(12); [5000, -5000, 15000, -15000, 0, 0].forEach((value, i) => expected.writeInt16LE(value, i * 2));
    assert.deepEqual(Buffer.concat(output), expected); assert.equal(starts, 1);
    assert.equal(client.readyState, WebSocket.OPEN); assert.equal(source.destroyed, false); assert.equal(child.stdin.destroyed, false);
  } finally { client.terminate(); await relay.shutdown(); }
});

test('real FLAC round trip preserves amplified stereo samples and low-latency fragments', {
  skip: process.platform !== 'linux' || !fs.existsSync('/usr/bin/ffmpeg'),
}, async () => {
  const { PcmGainStream } = require('./audio-gain'), { encoderArgs } = require('./audio-relay');
  const { spawnSync } = require('node:child_process'), { Mp4Fragments } = require('./audio-stream');
  const input = Buffer.alloc(48000 * 4), expected = Buffer.alloc(input.length);
  for (let i = 0; i < 96000; i++) {
    const sample = Math.round(26000 * Math.sin(i * 0.06));
    input.writeInt16LE(sample, i * 2); expected.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(sample * 1.5))), i * 2);
  }
  const gain = new PcmGainStream(() => 150), chunks = []; gain.on('data', bytes => chunks.push(bytes));
  const ended = once(gain, 'end'); gain.end(input); await ended;
  assert.deepEqual(Buffer.concat(chunks), expected);
  const encoded = spawnSync('/usr/bin/ffmpeg', encoderArgs(), { input: Buffer.concat(chunks), timeout: 10000, maxBuffer: 2 * 1024 * 1024 });
  assert.equal(encoded.status, 0, encoded.stderr.toString());
  const fragments = []; new Mp4Fragments((type, bytes) => fragments.push({ type, bytes })).push(encoded.stdout);
  assert.ok(fragments.filter(fragment => fragment.type === 'fragment').length >= 15);
  const decoded = spawnSync('/usr/bin/ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', '-i', 'pipe:0',
    '-f', 's16le', '-acodec', 'pcm_s16le', 'pipe:1'], { input: encoded.stdout, timeout: 10000, maxBuffer: 2 * 1024 * 1024 });
  assert.equal(decoded.status, 0, decoded.stderr.toString()); assert.deepEqual(decoded.stdout, expected);
});
test('persistent playback capture drains between viewers without changing Android audio routes or replaying stale media', async () => {
  const { createAudioRelay } = require('./audio-relay');
  const source = new PassThrough(); let starts = 0;
  const child = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
    kill() { process.nextTick(() => child.emit('close')); return true; } });
  const relay = createAudioRelay({ keepCapture: true, idleMs: 10, connect: () => source,
    spawnEncoder: () => { starts++; return child; } });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const clients = [];
  async function viewer() {
    const socket = new WebSocket(`ws://127.0.0.1:${relay.address().port}`), messages = [];
    clients.push(socket); socket.on('message', bytes => messages.push(bytes));
    await once(socket, 'open'); await wait(); return { socket, messages };
  }
  try {
    const first = await viewer();
    source.write(Buffer.from([0, 0x72, 0x61, 0x77, 1, 2, 3, 4]));
    const init = Buffer.concat([atom('ftyp'), atom('moov')]), fragment = Buffer.concat([atom('moof'), atom('mdat')]);
    child.stdout.write(Buffer.concat([init, fragment])); await wait();
    first.socket.close(); await once(first.socket, 'close'); await wait();
    assert.equal(source.destroyed, false); assert.equal(child.stdin.destroyed, false);
    child.stdout.write(fragment); await wait();
    const second = await viewer();
    assert.equal(starts, 1); assert.deepEqual(second.messages, [init]);
    child.stdout.write(fragment); await wait(); assert.deepEqual(second.messages, [init, fragment]);
  } finally { for (const client of clients) client.terminate(); await relay.shutdown(); }
  assert.equal(source.destroyed, true);
});


test('live audio caps unacknowledged fragments, drops unsent history and isolates a stalled listener', async t => {
  const { createAudioRelay } = require('./audio-relay');
  const source = new PassThrough(); let starts = 0;
  const child = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
    kill() { process.nextTick(() => child.emit('close')); return true; } });
  const relay = createAudioRelay({ keepCapture: true, liveTimeoutMs: 600, connect: () => source,
    spawnEncoder: () => { starts++; return child; } });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const clients = []; t.after(async () => { for (const c of clients) c.terminate(); await relay.shutdown(); });
  async function viewer(ack) {
    const socket = new WebSocket(`ws://127.0.0.1:${relay.address().port}`, 'droiddeck-audio-live-v1');
    clients.push(socket); const received = []; let meta;
    socket.on('message', (bytes, binary) => {
      if (!binary) { meta = JSON.parse(bytes.toString()); return; }
      received.push({ id: meta.id, stamp: meta.stamp, bytes: Buffer.from(bytes) });
      if (ack()) socket.send(JSON.stringify({ type: 'audio-ack', id: meta.id, stamp: performance.now() }));
    });
    await once(socket, 'open'); socket.send(JSON.stringify({ type: 'audio-ready', stamp: performance.now() })); await wait();
    return { socket, received };
  }
  const slow = await viewer(() => false), healthy = await viewer(() => true);
  child.stdout.write(Buffer.concat([atom('ftyp'), atom('moov')])); await wait();
  const fragment = Buffer.concat([atom('moof'), atom('mdat')]);
  for (let i = 0; i < 25; i++) { child.stdout.write(fragment); await wait(); }
  assert.equal(slow.received.length, 8, 'Only eight fragments can enter the entire socket path without receipts');
  assert.ok(healthy.received.length >= 24); assert.equal(starts, 1);
  if (slow.socket.readyState !== WebSocket.CLOSED) await once(slow.socket, 'close');
  assert.equal(source.destroyed, false, 'One bad listener must not restart Android capture');
  const late = await viewer(() => true); assert.equal(late.received.length, 1, 'Reconnect receives init, not a minute of old audio');
  child.stdout.write(fragment); await wait(); assert.equal(late.received.length, 2);
});


test('AAC-LC uses a closed 128 kbps target without changing the default FLAC encoder', () => {
  const { encoderArgs } = require('./audio-relay'); const args = encoderArgs('aac');
  const value = option => args[args.indexOf(option) + 1];
  assert.equal(value('-c:a'), 'aac'); assert.equal(value('-profile:a'), 'aac_low'); assert.equal(value('-b:a'), '128k');
  assert.ok(encoderArgs().includes('flac')); assert.throws(() => encoderArgs('aac; injected'), /Closed/);
});

test('AAC shares one gained PCM capture, isolates its failure from FLAC, and can be turned off without restarting Android', async t => {
  const { createAudioRelay } = require('./audio-relay');const source = new PassThrough();let captures = 0, aacStarts = 0;
  const fake = () => { const c=Object.assign(new EventEmitter(),{stdin:new PassThrough(),stdout:new PassThrough(),stderr:new PassThrough(),killed:0,
    kill(){this.killed++;process.nextTick(()=>this.emit('close'));return true;}});c.pcm=[];c.stdin.on('data',b=>c.pcm.push(b));return c; };
  const flac=fake(),aac=fake();
  const relay=createAudioRelay({keepCapture:true,idleMs:10,volume:()=>50,connect:()=>{captures++;return source;},spawnEncoder:()=>flac,spawnAacEncoder:()=>{aacStarts++;return aac;}});
  await new Promise(r=>relay.listen(0,'127.0.0.1',r));const clients=[];
  t.after(async()=>{for(const c of clients)c.terminate();await relay.shutdown();});
  async function open(protocol){const c=new WebSocket(`ws://127.0.0.1:${relay.address().port}`,protocol);clients.push(c);c.messages=[];c.on('message',(b,binary)=>{if(binary)c.messages.push(Buffer.from(b));});await once(c,'open');if(protocol)c.send(JSON.stringify({type:'audio-ready',stamp:performance.now()}));await wait();return c;}
  const old=await open(),trial=await open('droiddeck-aac-live-v1');assert.equal(captures,1);assert.equal(aacStarts,1);
  const pcm=Buffer.alloc(4);pcm.writeInt16LE(10000);pcm.writeInt16LE(-10000,2);source.write(Buffer.concat([Buffer.from([0,0x72,0x61,0x77]),pcm]));await wait();
  assert.deepEqual(Buffer.concat(aac.pcm),Buffer.concat(flac.pcm));assert.equal(Buffer.concat(aac.pcm).readInt16LE(),5000);
  const fInit=Buffer.concat([atom('ftyp'),atom('moov')]),aInit=Buffer.from(fInit);aInit[8]=97;
  const fragment=Buffer.concat([atom('moof'),atom('mdat')]);flac.stdout.write(Buffer.concat([fInit,fragment]));aac.stdout.write(Buffer.concat([aInit,fragment]));await wait();
  assert.deepEqual(old.messages,[fInit,fragment]);assert.deepEqual(trial.messages,[aInit,fragment]);
  const closed=once(trial,'close');aac.stdout.emit('data',Buffer.from('invalid MP4 data'));await closed;await wait();
  assert.equal(source.destroyed,false);assert.equal(flac.killed,0);assert.equal(old.readyState,WebSocket.OPEN);
  flac.stdout.write(fragment);await wait();assert.equal(old.messages.length,3);assert.ok(aac.killed>0);
});

test('native FFmpeg AAC-LC output is decodable stereo with a bounded network bitrate', {
  skip: process.platform !== 'linux' || !fs.existsSync('/usr/bin/ffmpeg'),
},()=>{
  const {spawnSync}=require('node:child_process'),{encoderArgs}=require('./audio-relay');const seconds=3,input=Buffer.alloc(48000*4*seconds);
  for(let i=0;i<48000*seconds;i++){input.writeInt16LE(Math.round(12000*Math.sin(i*2*Math.PI*440/48000)),i*4);input.writeInt16LE(Math.round(10000*Math.sin(i*2*Math.PI*660/48000)),i*4+2);}
  const encoded=spawnSync('/usr/bin/ffmpeg',encoderArgs('aac'),{input,maxBuffer:2*1024*1024,timeout:10000});assert.equal(encoded.status,0,encoded.stderr.toString());
  const probe=spawnSync('/usr/bin/ffprobe',['-v','error','-show_entries','stream=codec_name,profile,sample_rate,channels','-of','json','-i','pipe:0'],{input:encoded.stdout,timeout:10000});
  assert.equal(probe.status,0);const stream=JSON.parse(probe.stdout).streams[0];assert.equal(stream.codec_name,'aac');assert.equal(stream.profile,'LC');assert.equal(stream.sample_rate,'48000');assert.equal(stream.channels,2);
  const decoded=spawnSync('/usr/bin/ffmpeg',['-v','error','-i','pipe:0','-f','s16le','pipe:1'],{input:encoded.stdout,maxBuffer:2*1024*1024,timeout:10000});assert.equal(decoded.status,0,decoded.stderr.toString());assert.ok(decoded.stdout.length>=input.length);
  const bps=encoded.stdout.length*8/seconds;assert.ok(bps>64000&&bps<200000);console.log(JSON.stringify({aacProfile:stream.profile,targetBps:128000,containerBps:Math.round(bps),generatedOnly:true}));
});
