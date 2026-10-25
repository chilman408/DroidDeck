// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), net = require('node:net');
const { createConnectionServer, createConnectionClient } = require('./helper');
const { publicSnapshot } = require('./proxy-store');
const initial = () => publicSnapshot({ version: 1, revision: 0, hostname: 'deck.example', previousHostname: null });
test('bounded helper requests correlate responses and update only hostname metadata', async t => {
  let state = initial();
  const server = createConnectionServer({ snapshot: () => state, update: value => {
    assert.deepEqual(Object.keys(value).sort(), ['hostname', 'revision']);
    state = publicSnapshot({ version: 1, revision: 1, hostname: value.hostname, previousHostname: state.hostname }); return state;
  } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const client = createConnectionClient('unused', { connect: () => net.connect(server.address().port, '127.0.0.1') });
  await client.initialize(); assert.deepEqual(client.snapshot(), state);
  await client.update({ revision: 0, hostname: 'new.example' });
  assert.equal(client.snapshot().origin, 'https://new.example');
  assert.equal(client.snapshot().previousOrigin, 'https://deck.example');
});
test('helper refuses unknown commands and multiple messages without changing state', async t => {
  let writes = 0;
  const server = createConnectionServer({ snapshot: initial, update: () => { writes++; } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); t.after(() => server.close());
  const send = payload => new Promise(resolve => { const socket = net.connect(server.address().port, '127.0.0.1');
    let result = ''; socket.on('connect', () => socket.write(payload)); socket.on('data', chunk => result += chunk); socket.on('end', () => resolve(result)); });
  for (const payload of ['{}\n', JSON.stringify({ version: 1, id: 'a'.repeat(32), operation: 'shell' }) + '\n', '{}\n{}\n', 'x'.repeat(1025)]) {
    assert.ok(JSON.parse(await send(payload)).error);
  }
  assert.equal(writes, 0);
});
