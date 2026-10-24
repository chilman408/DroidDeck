// SPDX-License-Identifier: GPL-3.0-only
// Finite installer client: drain actual H264/AAC sockets, never record media.
const fs = require('node:fs'), https = require('node:https');
const { readRuntimeConfig } = require('./runtime-config');

async function waitForSession(request, { now = () => performance.now(),
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) } = {}) {
  // The installer gives this finite probe 60s. Runtime workers can legitimately
  // take more than 30s to bind HTTPS; retain headroom for parent-side cleanup.
  const deadline = now() + 55000;
  for (;;) {
    const remaining = deadline - now();
    if (remaining <= 0) break;
    try { return await request(Math.max(1, Math.ceil(Math.min(15000, remaining)))); }
    catch (error) {
      if (!['ECONNREFUSED', 'ECONNRESET'].includes(error.code)) throw error;
      const retryRemaining = deadline - now();
      if (retryRemaining <= 0) break;
      await sleep(Math.min(1000, retryRemaining));
    }
  }
  throw Object.assign(Error('Installation listener readiness deadline'), { code: 'INSTALLATION_LISTENER_DEADLINE' });
}

function failureSummary(error) {
  const codes = ['INSTALLATION_LISTENER_DEADLINE', 'ECONNREFUSED', 'ECONNRESET',
    'CERT_HAS_EXPIRED', 'ERR_TLS_CERT_ALTNAME_INVALID', 'DEPTH_ZERO_SELF_SIGNED_CERT',
    'SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE'];
  return { kind: 'installation-media-route-failure',
    reason: codes.includes(error?.code) ? error.code : 'SESSION_FAILURE',
    ...(Number.isInteger(error?.status) && error.status >= 400 && error.status <= 599 ? { status: error.status } : {}) };
}

async function main() {
  if (process.platform !== 'linux' || process.argv.length !== 2) throw Error('Fixed Linux installation client required');
  const config = readRuntimeConfig('/etc/droiddeck/runtime.json');
  if (process.getuid() !== config.uid || process.getgid() !== config.gid) throw Error('Configured unprivileged user required');
  const origin = `https://${config.lanAddress}:${config.port}`;
  const ca = fs.readFileSync(config.home + '/.config/droiddeck/cert.pem');
  const requestSession = timeout => new Promise((resolve, reject) => {
    const req = https.request(origin + '/api/displaySession', { method: 'POST', ca, rejectUnauthorized: true,
      headers: { Origin: origin, 'Content-Type': 'application/json' }, timeout }, response => {
      let raw = '';
      response.on('data', chunk => { raw += chunk; if (raw.length > 65536) req.destroy(Error('Bounded session required')); });
      response.on('end', () => {
        try {
          if (response.statusCode !== 200) throw Object.assign(Error('Installation session rejected'), { status: response.statusCode });
          const value = JSON.parse(raw);
          if (!/^[a-f0-9]{32,128}$/.test(value.token)) throw Error('Exact installation session required');
          resolve(value);
        } catch (error) { reject(error); }
      });
    });
    req.once('error', reject); req.once('timeout', () => req.destroy(Error('Session deadline'))); req.end('{}');
  });
  const session = await waitForSession(requestSession);
  const WebSocket = require('/opt/droiddeck/runtime/preview-gateway/node_modules/ws');
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
if (require.main === module) main().catch(error => { console.error(JSON.stringify(failureSummary(error))); process.exitCode = 1; });
module.exports = { waitForSession, failureSummary };
