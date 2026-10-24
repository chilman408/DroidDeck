// SPDX-License-Identifier: GPL-3.0-only
const net = require('node:net'), fs = require('node:fs/promises'), crypto = require('node:crypto');
const { createProxyStore, validateRecord, publicSnapshot } = require('./proxy-store');
const { normalizeHostname, DEFAULT_HOSTNAME } = require('./policy');
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { deriveRuntimeLayout } = require('../portable/runtime-config');
const SOCKET = '/run/droiddeck-connection/connection.sock';
function safeSnapshot(value) {
  const record = validateRecord(Object.fromEntries(['version', 'revision', 'hostname', 'previousHostname'].map(key => [key, value?.[key]])));
  const safe = publicSnapshot(record);
  if (JSON.stringify(safe) !== JSON.stringify(value)) throw new Error('Invalid connection helper state');
  return safe;
}
function createConnectionServer(store) {
  const sockets = new Set();
  const server = net.createServer(socket => {
    if (sockets.size >= 16) { socket.destroy(); return; }
    sockets.add(socket); let buffer = '', started = false;
    socket.setTimeout(5000, () => socket.destroy()); socket.on('error', () => {});
    socket.on('close', () => sockets.delete(socket));
    socket.on('data', chunk => {
      if (started) { socket.destroy(); return; }
      buffer += chunk.toString('utf8');
      if (Buffer.byteLength(buffer) > 1024) { started = true; socket.end(JSON.stringify({ version: 1, error: 'Request too large' }) + '\n'); return; }
      const newline = buffer.indexOf('\n'); if (newline < 0) return;
      started = true; let request;
      try {
        if (newline !== buffer.length - 1) throw new Error('One request required');
        request = JSON.parse(buffer);
        if (!request || request.version !== 1 || !/^[a-f0-9]{32}$/.test(request.id) ||
            !['inspect', 'update'].includes(request.operation) ||
            Object.keys(request).sort().join(',') !== (request.operation === 'inspect' ? 'id,operation,version' : 'id,operation,payload,version')) throw new Error('Invalid connection request');
        const state = request.operation === 'inspect' ? store.snapshot() : store.update(request.payload);
        socket.end(JSON.stringify({ version: 1, id: request.id, state: safeSnapshot(state) }) + '\n');
      } catch (error) {
        socket.end(JSON.stringify({ version: 1, id: request?.id, error: 'Connection request rejected', status: error.status === 409 ? 409 : 400 }) + '\n');
      }
    });
  });
  server.on('close', () => { for (const socket of sockets) socket.destroy(); });
  server.shutdown = () => { for (const socket of sockets) socket.destroy(); return new Promise(resolve => server.close(resolve)); };
  return server;
}
function createConnectionClient(socketPath = SOCKET, { connect = () => net.connect(socketPath), timeoutMs = 5000 } = {}) {
  let state, pending = false;
  const request = (operation, payload) => new Promise((resolve, reject) => {
    const socket = connect(), id = crypto.randomBytes(16).toString('hex'); let buffer = '', settled = false;
    const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(timer); socket.destroy(); error ? reject(error) : resolve(value); };
    const timer = setTimeout(() => finish(new Error('Connection helper timed out')), timeoutMs);
    socket.on('error', () => finish(new Error('Connection helper unavailable')));
    socket.on('connect', () => socket.write(JSON.stringify({ version: 1, id, operation, ...(operation === 'update' ? { payload } : {}) }) + '\n'));
    socket.on('data', chunk => { buffer += chunk.toString('utf8'); if (Buffer.byteLength(buffer) > 4096) finish(new Error('Connection reply too large')); });
    socket.on('end', () => {
      try {
        if (buffer.indexOf('\n') !== buffer.length - 1) throw new Error('Invalid connection reply');
        const response = JSON.parse(buffer);
        if (response.version !== 1 || response.id !== id) throw new Error('Invalid connection correlation');
        if (response.error) throw Object.assign(new Error('Connection request rejected'), { status: response.status === 409 ? 409 : 400 });
        finish(null, safeSnapshot(response.state));
      } catch (error) { finish(error); }
    });
    socket.on('close', () => { if (!settled) finish(new Error('Connection helper disconnected')); });
  });
  return {
    async initialize() { state = await request('inspect'); },
    async refresh() { state = await request('inspect'); return { ...state }; },
    snapshot() { if (!state) throw new Error('Connection helper unavailable'); return { ...state }; },
    async update(payload) {
      if (pending) throw Object.assign(new Error('Connection change pending'), { status: 409 });
      pending = true;
      try { state = await request('update', payload); return { ...state }; }
      finally { pending = false; }
    },
  };
}
async function main({ configuration, args = process.argv.slice(2) } = {}) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || !Array.isArray(args) || args.length) throw new Error('Protected Linux connection helper required');
  configuration = await loadWorkerConfiguration({ role: 'privileged', read: () => configuration });
  const layout = configuration && deriveRuntimeLayout(configuration), gid = configuration?.gid ?? 1000;
  const root = (layout?.root ?? '/opt/teslaandroid-waydroid') + '/runtime/browser-connection';
  for (const file of [root, root + '/helper.js', root + '/proxy-store.js', root + '/policy.js', ...(configuration ? [root + '/socket-startup.js'] : [])]) {
    const stat = await fs.lstat(file);
    if (stat.uid !== 0 || stat.mode & 0o022 || (!stat.isFile() && !stat.isDirectory())) throw new Error('Unsafe helper code');
  }
  const directory = (layout?.state ?? '/var/lib/teslaandroid-waydroid') + '/browser-connection';
  await fs.mkdir(directory, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
  let initialHostname = configuration?.hostname ?? DEFAULT_HOSTNAME;
  const proxy = configuration ? '/etc/droiddeck/proxy-origin' : '/etc/teslaandroid-waydroid/proxy-origin';
  const stat = await fs.lstat(proxy).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
  if (stat) {
    if (!stat.isFile() || stat.uid !== 0 || stat.mode & 0o022 || stat.size > 256) throw new Error('Unsafe initial proxy origin');
    const text = (await fs.readFile(proxy, 'utf8')).trim(), url = new URL(text);
    initialHostname = normalizeHostname(url.hostname);
    if (text !== 'https://' + initialHostname) throw new Error('Invalid initial proxy origin');
  }
  const store = createProxyStore(directory + '/profile.json', { initialHostname, uid: 0 });
  const socketDirectory = layout?.sockets ?? '/run/droiddeck-connection';
  const socketPath = configuration ? socketDirectory + '/connection.sock' : SOCKET;
  const runtime = await fs.lstat(socketDirectory);
  if (!runtime.isDirectory() || runtime.uid !== 0 || runtime.mode & 0o022 ||
      (configuration && (runtime.gid !== gid || runtime.mode & 0o007))) throw new Error('Unsafe connection socket directory');
  const server = createConnectionServer(store);
  if (configuration) await require('./socket-startup').startProtectedConnectionServer({ server, filename: socketPath, gid });
  else {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socketPath, resolve); });
    await fs.chown(socketPath, 0, gid); await fs.chmod(socketPath, 0o660);
  }
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void server.shutdown(); });
}
if (require.main === module) main().catch(() => { console.error('Browser connection helper unavailable'); process.exitCode = 1; });
module.exports = { createConnectionServer, createConnectionClient, safeSnapshot, SOCKET, main };
