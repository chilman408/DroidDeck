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
