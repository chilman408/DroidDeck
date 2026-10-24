// SPDX-License-Identifier: GPL-3.0-only
// Generated transport fixture only. No Android input or capture.
const assert = require('node:assert/strict'), net = require('node:net');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { WebSocket } = require('ws');
async function trial(binary, enabled) {
  const child = spawn(binary, [enabled ? '1' : '0'], { stdio: 'ignore' });
  let exit; const exited = new Promise(resolve => { exit = resolve; }); child.once('exit', exit);
  const sockets = new Set();
  const proxy = net.createServer(client => {
    const target = net.connect(29999, '127.0.0.1'); sockets.add(client); sockets.add(target);
    let timer;
    const close = () => { clearTimeout(timer); client.destroy(); target.destroy(); sockets.delete(client); sockets.delete(target); };
    const activity = () => { clearTimeout(timer); timer = setTimeout(close, 400); };
    client.on('data', activity); target.on('data', activity); client.on('error', close); target.on('error', close);
    client.once('close', close); target.once('close', close);
    client.pipe(target); target.pipe(client); activity();
  });
  let ws, pings = 0, closed = false;
  try {
    await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
    await delay(200);
    ws = new WebSocket('ws://127.0.0.1:' + proxy.address().port);
    ws.on('ping', () => pings++); ws.on('close', () => { closed = true; }); ws.on('error', () => {});
    await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
    await delay(1200);
    assert.equal(closed, !enabled);
    assert.equal(pings > 0, enabled);
    return { heartbeat: enabled, connectedAfterThreeIdleTimeouts: !closed, pings, applicationMessagesSent: 0 };
  } finally {
    ws?.terminate(); for (const socket of sockets) socket.destroy();
    await new Promise(resolve => proxy.close(resolve));
    child.kill('SIGTERM'); await exited;
  }
}
async function main() {
  if (process.platform !== 'linux' || process.argv.length !== 3) throw new Error('Linux fixture binary required');
  const before = await trial(process.argv[2], false), after = await trial(process.argv[2], true);
  console.log(JSON.stringify({ before, after, androidInputInjected: false }));
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { trial };
