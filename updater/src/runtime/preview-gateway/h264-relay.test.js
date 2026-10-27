// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { PassThrough } = require("node:stream");
const { WebSocket } = require("ws");
const { once } = require("node:events");
const { createVideoRelay } = require("./h264-relay");
function header(width, height) { const b = Buffer.alloc(12); b.writeUInt32BE(0x80000000); b.writeUInt32BE(width, 4); b.writeUInt32BE(height, 8); return b; }
function frame(flags) { const b = Buffer.alloc(17); b[0] = flags; b.writeUInt32BE(5, 8); b.set([0, 0, 0, 1, 0x65], 12); return b; }
test("shares one encoder, sends cached configuration, waits for keyframe and ignores browser input", async () => {
  let connections = 0; const source = new PassThrough();
  const relay = createVideoRelay({ connect: () => { connections++; return source; }, reconnectMs: 20 });
  await new Promise(resolve => relay.listen(0, "127.0.0.1", resolve));
  const a = new WebSocket(`ws://127.0.0.1:${relay.address().port}`), messages = [];
  a.on("message", (b, binary) => messages.push({ b, binary }));
  try {
    await once(a, "open"); source.write(Buffer.concat([Buffer.from("h264"), header(390, 844), frame(0x40), frame(0)]));
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(messages.filter(m => m.binary).length, 0);
    const got = once(a, "message"); source.write(frame(0x20)); await got;
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(messages.filter(m => m.binary).length, 2);
    assert.equal(connections, 1);
    const closed = once(a, "close"); a.send("do-not-forward-input"); await closed;
  } finally { a.terminate(); await relay.shutdown(); }
});

test("oversized GOP and changed session cannot replay old frames", async () => {
  const source = new PassThrough();
  const relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, "127.0.0.1", resolve));
  const clients = [];
  async function viewer() {
    const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`), messages = [];
    clients.push(client);
    client.on("message", (b, binary) => messages.push({ b, binary }));
    await once(client, "open");
    await new Promise(resolve => setTimeout(resolve, 25));
    return messages;
  }
  try {
    const oversized = Buffer.alloc(2 * 1024 * 1024); oversized[0] = 0x20;
    oversized.writeUInt32BE(oversized.length - 12, 8);
    source.write(Buffer.concat([Buffer.from("h264"), header(976, 720), frame(0x40)]));
    source.write(oversized);
    assert.equal((await viewer()).filter(m => m.binary).length, 0);
    source.write(frame(0x20));
    assert.equal((await viewer()).filter(m => m.binary).length, 2);
    source.write(Buffer.concat([header(390, 844), frame(0)]));
    const resized = await viewer();
    assert.equal(resized.filter(m => m.binary).length, 0);
    assert.equal(JSON.parse(resized[0].b).width, 390);
  } finally { for (const client of clients) client.terminate(); await relay.shutdown(); }
});

test("late viewer receives a bounded decodable picture even while Android is idle", async () => {
  const source = new PassThrough();
  const relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, "127.0.0.1", resolve));
  const clients = [];
  async function viewer() {
    const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`), messages = [];
    clients.push(client);
    client.on("message", (b, binary) => messages.push({ b, binary }));
    await once(client, "open");
    await new Promise(resolve => setTimeout(resolve, 25));
    return messages;
  }
  try {
    source.write(Buffer.concat([Buffer.from("h264"), header(976, 720), frame(0x40), frame(0x20), frame(0)]));
    const idle = await viewer();
    assert.deepEqual(idle.filter(m => m.binary).map(m => m.b[0]), [0x40, 0x20, 0]);
    // A growing GOP must not retain unbounded data or replay missing references.
    for (let i = 0; i < 121; i++) source.write(frame(0));
    assert.equal((await viewer()).filter(m => m.binary).length, 0);
    source.write(frame(0x20));
    assert.deepEqual((await viewer()).filter(m => m.binary).map(m => m.b[0]), [0x40, 0x20]);
    source.write(frame(0x40));
    assert.equal((await viewer()).filter(m => m.binary).length, 0);
  } finally { for (const client of clients) client.terminate(); await relay.shutdown(); }
});

test('flow viewer cannot accumulate historical packets and resumes at newest keyframe', async () => {
  const source = new PassThrough(), relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`, 'droiddeck-live-v1');
  const messages = [], markers = [];
  client.on('message', (bytes, binary) => {
    if (binary) messages.push(bytes);
    else { const value = JSON.parse(bytes); if (value.type === 'scrcpy-flow-frame') markers.push(value.id); }
  });
  try {
    await once(client, 'open');
    source.write(Buffer.concat([Buffer.from('h264'), header(960, 720), frame(0x40), frame(0x20)]));
    for (let i = 0; i < 80; i++) source.write(frame(0));
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(messages.length, 4, 'Proxy draining cannot grant remote browser credit');
    const latest = frame(0x20); latest[16] = 0x77;
    source.write(latest);
    const delta = frame(0); delta[16] = 0x78; source.write(delta);
    markers.slice().forEach(id => client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id })));
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(messages[0][0], 0x40, 'The acknowledged configuration is retained across keyframes');
    assert.deepEqual(messages.slice(4).map(b => [b[0], b[16]]), [[0x20, 0x77], [0, 0x78]]);
    const count = messages.length;
    client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: markers[0] }));
    await new Promise(resolve => setTimeout(resolve, 15));
    assert.equal(messages.length, count, 'Duplicate receipt cannot add credit');
  } finally { client.terminate(); await relay.shutdown(); }
});

test('flow late viewer drains the maximum cached idle GOP without needing new motion', async () => {
  const source = new PassThrough(), relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  source.write(Buffer.concat([Buffer.from('h264'), header(960, 720), frame(0x40), frame(0x20)]));
  for (let i = 1; i < 120; i++) source.write(frame(0));
  const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`, 'droiddeck-live-v1');
  let marker, count = 0;
  const done = new Promise(resolve => client.on('message', (bytes, binary) => {
    if (!binary) { const v = JSON.parse(bytes); if (v.type === 'scrcpy-flow-frame') marker = v.id; }
    else { count++; client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: marker })); if (count === 121) resolve(); }
  }));
  try { await done; assert.equal(count, 121); }
  finally { client.terminate(); await relay.shutdown(); }
});

test('a blocked flow viewer does not block another viewer or share receipt credit', async () => {
  const source = new PassThrough(), relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const url = `ws://127.0.0.1:${relay.address().port}`;
  const slow = new WebSocket(url, 'droiddeck-live-v1'), good = new WebSocket(url, 'droiddeck-live-v1');
  let slowCount = 0, goodCount = 0, marker;
  slow.on('message', (_b, binary) => { if (binary) slowCount++; });
  good.on('message', (bytes, binary) => {
    if (!binary) { const v = JSON.parse(bytes); if (v.type === 'scrcpy-flow-frame') marker = v.id; }
    else { goodCount++; good.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: marker })); }
  });
  try {
    await Promise.all([once(slow, 'open'), once(good, 'open')]);
    source.write(Buffer.concat([Buffer.from('h264'), header(960, 720), frame(0x40), frame(0x20)]));
    for (let i = 0; i < 12; i++) { source.write(frame(0)); await new Promise(r => setTimeout(r, 10)); }
    assert.equal(slowCount, 4); assert.equal(goodCount, 14);
    const closed = once(good, 'close'); good.send('{"type":"touch","id":1}');
    assert.equal((await closed)[0], 1008);
  } finally { slow.terminate(); good.terminate(); await relay.shutdown(); }
});

test('a large legal keyframe travels alone instead of bypassing byte credit', async () => {
  const source = new PassThrough(), relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`, 'droiddeck-live-v1');
  const markers = [], lengths = [];
  client.on('message', (bytes, binary) => {
    if (binary) lengths.push(bytes.length);
    else { const v = JSON.parse(bytes); if (v.type === 'scrcpy-flow-frame') markers.push(v.id); }
  });
  try {
    await once(client, 'open');
    const large = Buffer.alloc(2 * 1024 * 1024 + 12); large[0] = 0x20; large.writeUInt32BE(large.length - 12, 8);
    source.write(Buffer.concat([Buffer.from('h264'), header(960, 720), frame(0x40)]));
    source.write(large); source.write(frame(0));
    await new Promise(r => setTimeout(r, 25)); assert.deepEqual(lengths, [17]);
    client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: markers[0] }));
    await new Promise(r => setTimeout(r, 25)); assert.deepEqual(lengths, [17, large.length]);
    client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: markers[1] }));
    await new Promise(r => setTimeout(r, 25)); assert.deepEqual(lengths, [17, large.length, 17]);
  } finally { client.terminate(); await relay.shutdown(); }
});

test('continuous large keyframes cannot starve behind repeated configuration on a slow link', async () => {
  const source = new PassThrough(), relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`, 'droiddeck-live-v1');
  const receipts = []; let marker, keys = 0;
  client.on('message', (bytes, binary) => {
    if (!binary) { const v = JSON.parse(bytes); if (v.type === 'scrcpy-flow-frame') marker = v.id; }
    else { receipts.push(marker); if (bytes[0] === 0x20) keys++; }
  });
  try {
    await once(client, 'open');
    source.write(Buffer.concat([Buffer.from('h264'), header(960, 720), frame(0x40)]));
    for (let i = 0; i < 30; i++) {
      const key = Buffer.alloc(700000); key[0] = 0x20; key.writeUInt32BE(key.length - 12, 8);
      source.write(key); await new Promise(r => setTimeout(r, 6));
      if (i >= 2 && receipts.length) client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: receipts.shift() }));
    }
    await new Promise(r => setTimeout(r, 20));
    assert.ok(keys > 2, 'Configuration repetition must not keep the oversized-keyframe credit barrier occupied forever');
  } finally { client.terminate(); await relay.shutdown(); }
});

test('resizing retains physical credit and sends the new configuration before its keyframe', async () => {
  const source = new PassThrough(), relay = createVideoRelay({ connect: () => source });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const client = new WebSocket(`ws://127.0.0.1:${relay.address().port}`, 'droiddeck-live-v1');
  const ids = [], packets = [];
  client.on('message', (bytes, binary) => {
    if (binary) packets.push(bytes);
    else { const v = JSON.parse(bytes); if (v.type === 'scrcpy-flow-frame') ids.push(v.id); }
  });
  try {
    await once(client, 'open');
    source.write(Buffer.concat([Buffer.from('h264'), header(960, 720), frame(0x40), frame(0x20), frame(0), frame(0)]));
    await new Promise(r => setTimeout(r, 25)); assert.equal(packets.length, 4);
    const cfg = frame(0x40); cfg[16] = 0x68;
    const key = frame(0x20); key[16] = 0x79;
    source.write(Buffer.concat([header(720, 960), cfg, key]));
    await new Promise(r => setTimeout(r, 25)); assert.equal(packets.length, 4);
    ids.slice().forEach(id => client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id })));
    await new Promise(r => setTimeout(r, 25));
    assert.deepEqual(packets.slice(4).map(p => [p[0], p[16]]), [[0x40, 0x68], [0x20, 0x79]]);
  } finally { client.terminate(); await relay.shutdown(); }
});


test('relay feedback is numeric, session scoped, ignores duplicate receipts and refuses ambiguous reconnects', async () => {
  let now = 0; const source = new PassThrough(), relay = createVideoRelay({ connect: () => source, clock: () => now });
  await new Promise(resolve => relay.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${relay.address().port}`, token = 'a'.repeat(64);
  const client = new WebSocket(base.replace('http:', 'ws:'), 'droiddeck-live-v1', { headers: { 'X-DroidDeck-Display-Session': token } });
  const ids = []; let duplicate;
  client.on('message', (bytes, binary) => { if (!binary) { const v = JSON.parse(bytes); if (v.type === 'scrcpy-flow-frame') ids.push(v.id); } });
  const metrics = async key => (await fetch(base + '/flow-state', { headers: { 'X-DroidDeck-Display-Session': key } })).json();
  try {
    await once(client, 'open'); source.write(Buffer.concat([Buffer.from('h264'), header(960, 720), frame(0x40), frame(0x20)]));
    await new Promise(resolve => setTimeout(resolve, 15)); now = 1200;
    const v = await metrics(token); assert.equal(v.available, true); assert.equal(v.oldestMs, 1200); assert.equal(v.ackedPackets, 0);
    assert.equal(JSON.stringify(v).includes(token), false); assert.equal(JSON.stringify(v).includes('packet'), false);
    assert.deepEqual(await metrics('b'.repeat(64)), { available: false });
    assert.equal((await fetch(base + '/flow-state')).status, 403);
    client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: ids[0] })); await new Promise(r => setTimeout(r, 15));
    const a = await metrics(token); assert.equal(a.ackedPackets, 1); assert.equal(a.ackDelayMs, 1200); assert.equal(a.acknowledgedBytes, 17);
    client.send(JSON.stringify({ type: 'scrcpy-flow-ack', id: ids[0] })); await new Promise(r => setTimeout(r, 15));
    assert.deepEqual(await metrics(token), a);
    duplicate = new WebSocket(base.replace('http:', 'ws:'), 'droiddeck-live-v1', { headers: { 'X-DroidDeck-Display-Session': token } });
    await once(duplicate, 'open'); assert.deepEqual(await metrics(token), { available: false });
  } finally { client.terminate(); duplicate?.terminate(); await relay.shutdown(); }
});
