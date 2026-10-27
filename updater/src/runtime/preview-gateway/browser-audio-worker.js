// SPDX-License-Identifier: GPL-3.0-only
// Bounded live audio: acknowledge only after the main thread appends each fragment.
let protocol = 'droiddeck-audio-live-v1';
let socket = null, want = false, url = '', retry = null, watch = null, generation = 0, backoff = 250;
const now = () => performance.now(), LIMIT_MS = 1500;
function retire() {
  generation++; clearTimeout(retry); clearInterval(watch); retry = watch = null;
  if (socket) { socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null; socket.close(); socket = null; }
}
function connect() {
  retire(); if (!want) return;
  const epoch = generation;
  let metadata = null, acknowledgedAt = now();
  const failed = (reason = 'transport') => {
    if (epoch !== generation || !want) return;
    retire(); postMessage({ type: 'close', reason: typeof reason === 'string' ? reason : 'transport' });
    retry = setTimeout(connect, backoff); backoff = Math.min(2000, backoff * 2);
  };
  try {
    const ws = socket = new WebSocket(url, protocol); ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      if (epoch !== generation) return;
      if (ws.protocol !== protocol) { failed(); return; }
      backoff = 250; acknowledgedAt = now();
      postMessage({ type: 'open' }); ws.send(JSON.stringify({ type: 'audio-ready', stamp: acknowledgedAt }));
      watch = setInterval(() => { if (now() - acknowledgedAt > LIMIT_MS) failed('append-timeout'); }, 250);
    };
    ws.onmessage = event => {
      if (epoch !== generation) return;
      try {
        if (typeof event.data === 'string') {
          const value = JSON.parse(event.data);
          if (metadata || value.type !== 'audio-frame' || !Number.isSafeInteger(value.id) || value.id <= 0 ||
              !Number.isFinite(value.stamp) || value.stamp < 0 || now() - value.stamp < 0 || now() - value.stamp > LIMIT_MS) throw Error('metadata-freshness');
          metadata = value; return;
        }
        if (!(event.data instanceof ArrayBuffer) || !metadata || event.data.byteLength > 512 * 1024 ||
            now() - metadata.stamp > LIMIT_MS) throw Error('frame-freshness');
        const id = metadata.id; metadata = null;
        postMessage({ type: 'data', id, receivedAt: Date.now(), buf: event.data }, [event.data]);
      } catch (error) { failed(['metadata-freshness','frame-freshness'].includes(error.message) ? error.message : 'protocol'); }
    };
    ws.onerror = () => failed('socket-error'); ws.onclose = event => failed('socket-close-' + event.code);
    ws.ack = id => {
      if (epoch !== generation || ws.readyState !== WebSocket.OPEN || !Number.isSafeInteger(id) || id <= 0) return;
      acknowledgedAt = now(); ws.send(JSON.stringify({ type: 'audio-ack', id, stamp: acknowledgedAt }));
    };
  } catch (_) { failed(); }
}
self.onmessage = event => {
  const value = event.data || {};
  if (value.type === 'start' && typeof value.url === 'string') { want = true; url = value.url; protocol = value.codec === 'aac' ? 'droiddeck-aac-live-v1' : 'droiddeck-audio-live-v1'; backoff = 250; connect(); }
  else if (value.type === 'stop') { want = false; retire(); }
  else if (value.type === 'ack') socket?.ack?.(value.id);
};
