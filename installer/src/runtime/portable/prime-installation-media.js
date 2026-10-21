// SPDX-License-Identifier: GPL-3.0-only
// Finite installer client: drain actual H264/AAC sockets, never record media.
const fs = require('node:fs'), https = require('node:https');
const { readRuntimeConfig } = require('./runtime-config');
const WebSocket = require('/opt/droiddeck/runtime/preview-gateway/node_modules/ws');

async function main() {
  if (process.platform !== 'linux' || process.argv.length !== 2) throw Error('Fixed Linux installation client required');
  const config = readRuntimeConfig('/etc/droiddeck/runtime.json');
  if (process.getuid() !== config.uid || process.getgid() !== config.gid) throw Error('Configured unprivileged user required');
  const origin = `https://${config.lanAddress}:${config.port}`;
  const ca = fs.readFileSync(config.home + '/.config/droiddeck/cert.pem');
  const requestSession = () => new Promise((resolve, reject) => {
    const req = https.request(origin + '/api/displaySession', { method: 'POST', ca, rejectUnauthorized: true,
      headers: { Origin: origin, 'Content-Type': 'application/json' }, timeout: 15000 }, response => {
      let raw = '';
      response.on('data', chunk => { raw += chunk; if (raw.length > 65536) req.destroy(Error('Bounded session required')); });
      response.on('end', () => {
        try {
          if (response.statusCode !== 200) throw Error('Installation session rejected');
          const value = JSON.parse(raw);
          if (!/^[a-f0-9]{32,128}$/.test(value.token)) throw Error('Exact installation session required');
          resolve(value);
        } catch (error) { reject(error); }
      });
    });
    req.once('error', reject); req.once('timeout', () => req.destroy(Error('Session deadline'))); req.end('{}');
  });
  let session;
  for (let attempt = 0; attempt < 30; attempt++) {
    try { session = await requestSession(); break; }
    catch (error) {
      // Only listener startup is retryable. Never retry failed TLS verification
      // or authorization by suppressing its error or adopting another identity.
      if (!['ECONNREFUSED', 'ECONNRESET'].includes(error.code) || attempt === 29) throw error;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  let ready = false;
  const sockets = [], received = { display: 0, audio: 0 };
  let closing = false;
  const close = () => { closing = true; for (const socket of sockets) socket.terminate(); };
  const fail = () => { if (closing) return; clearTimeout(timer); process.exitCode = 1; close(); };
  // Only this finite installation client expires, never the healthy VM services.
  const timer = setTimeout(fail, 120000);
  for (const kind of ['display', 'audio']) {
    const socket = new WebSocket(origin.replace('https:', 'wss:') + '/sockets/' + kind + '?session=' + session.token
      + (kind === 'display' ? '&format=h264' : ''), { ca, rejectUnauthorized: true, origin, handshakeTimeout: 15000 });
    sockets.push(socket);
    socket.on('error', fail);
    socket.on('message', raw => {
      received[kind] += raw.length;
      if (!ready && received.display > 1024 && received.audio > 1024) {
        ready = true;
        console.log(JSON.stringify({ kind: 'installation-media-route', active: true, tlsVerified: true,
          recordingSaved: false, androidInputInjected: false }));
      }
    });
  }
  process.once('SIGTERM', () => { clearTimeout(timer); close(); });
  process.once('SIGINT', () => { clearTimeout(timer); close(); });
}
if (require.main === module) main().catch(() => { console.error('Installation media route failed'); process.exitCode = 1; });
