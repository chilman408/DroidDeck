// SPDX-License-Identifier: GPL-3.0-only
// Bounded live audio: main-thread append receipts own transport credit.
let protocol = 'droiddeck-audio-live-v2', codec = 'flac', bitRate = 128000, ceilingBitRate = 128000, automatic = false;
let healthySince = null, lastSample = null, badSince = null, changedAt = -Infinity;
let baselineAge = Infinity, lastPromotion = -Infinity, promotionBlockedUntil = -Infinity;
function protocolForRate() { return codec === 'aac' ? 'droiddeck-aac' + (bitRate === 128000 ? '' : '-' + bitRate / 1000) + '-live-v2' : 'droiddeck-audio-live-v2'; }
function changeRate(next) {
  if (next === bitRate) return false;
  const time=now();
  if(next>bitRate)lastPromotion=time;
  else if(time-lastPromotion<8000)promotionBlockedUntil=time+18000;
  bitRate = next; protocol = protocolForRate(); healthySince = lastSample = badSince = null; changedAt = time;
  postMessage({type:'quality',bitRate,automatic});connect();return true;
}
function qualitySample(age) {
  if (!automatic || codec !== 'aac') return false;
  const time = now();
  if (lastSample !== null && time - lastSample > 1000) healthySince = badSince = null;
  lastSample = time;
  if(age<700)baselineAge=Math.min(baselineAge,age);
  const late=Math.max(0,age-(Number.isFinite(baselineAge)?baselineAge:0));
  if(late>=300){if(badSince===null)badSince=time;}else badSince=null;
  if(late<150){if(healthySince===null)healthySince=time;}else healthySince=null;
  if(bitRate>64000 && time-changedAt>=2000 && (late>=800 || badSince!==null&&time-badSince>=500))
    return changeRate(late>=800?64000:bitRate===256000?128000:64000);
  const recovery=bitRate===64000?8000:15000;
  if(bitRate<ceilingBitRate && healthySince!==null && time-healthySince>=recovery && time-changedAt>=recovery && time>=promotionBlockedUntil)
    return changeRate(Math.min(ceilingBitRate,bitRate===64000?128000:256000));
  return false;
}
let socket = null, want = false, url = '', retry = null, watch = null, generation = 0, backoff = 250;
const now = () => performance.now(), LIMIT_MS = 1500, TRANSPORT_MS = 10000, MAX_BYTES = 512 * 1024;
function retire() {
  generation++; clearTimeout(retry); clearInterval(watch); retry = watch = null;
  if (socket) { socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null; socket.close(); socket = null; }
}
function connect() {
  retire(); if (!want) return;
  const epoch = generation, v2 = protocol.endsWith('-v2'), timeout = v2 ? TRANSPORT_MS : LIMIT_MS;
  let metadata = null, acknowledgedAt = now(), lastId = 0, pendingBytes = 0;
  const pending = new Map();
  const failed = (reason = 'transport') => {
    if (epoch !== generation || !want) return;
    if (automatic && codec === 'aac' && bitRate > 64000 && now() - changedAt >= 2000) {
      bitRate=bitRate===256000?128000:64000;protocol=protocolForRate();changedAt=now();healthySince=lastSample=badSince=null;if(now()-lastPromotion<8000)promotionBlockedUntil=now()+18000;
      postMessage({type:'quality',bitRate,automatic});
    }
    // Report the connection that failed before advancing to the next generation.
    postMessage({ type: 'close', epoch, reason: typeof reason === 'string' ? reason : 'transport' });
    retire(); retry = setTimeout(connect, backoff); backoff = Math.min(2000, backoff * 2);
  };
  try {
    const ws = socket = new WebSocket(url, protocol); ws.binaryType = 'arraybuffer';
    const acknowledge = id => {
      if (epoch !== generation || ws.readyState !== WebSocket.OPEN) return;
      acknowledgedAt = now(); ws.send(JSON.stringify({ type: 'audio-ack', id, stamp: acknowledgedAt }));
    };
    ws.onopen = () => {
      if (epoch !== generation) return;
      if (ws.protocol !== protocol) { failed(); return; }
      backoff = 250; acknowledgedAt = now();
      postMessage({ type: 'open', epoch }); ws.send(JSON.stringify({ type: 'audio-ready', stamp: acknowledgedAt }));
      watch = setInterval(() => {
        if (now() - acknowledgedAt > timeout || [...pending.values()].some(frame => now() - frame.at > timeout)) failed('append-timeout');
      }, 250);
    };
    ws.onmessage = event => {
      if (epoch !== generation || ws.readyState !== WebSocket.OPEN) return;
      try {
        if (typeof event.data === 'string') {
          const value = JSON.parse(event.data);
          if (!value || metadata || value.type !== 'audio-frame' || !Number.isSafeInteger(value.id) || value.id <= 0 ||
              !Number.isFinite(value.stamp) || value.stamp < 0 || now() - value.stamp < 0 ||
              (!v2 && now() - value.stamp > LIMIT_MS) ||
              (v2 && (value.id <= lastId || !['init', 'media'].includes(value.kind) ||
                Object.keys(value).sort().join(',') !== 'id,kind,stamp,type' || now() - value.stamp > TRANSPORT_MS))) throw Error('metadata-freshness');
          metadata = value; return;
        }
        if (!(event.data instanceof ArrayBuffer) || !metadata || event.data.byteLength > MAX_BYTES ||
            (v2 && event.data.byteLength === 0) || now() - metadata.stamp > timeout) throw Error('frame-freshness');
        const { id, stamp, kind } = metadata; metadata = null;
        const age = now() - stamp; lastId = id;
        if (kind === 'media' && qualitySample(age)) return;
        if (v2 && kind === 'media' && age > LIMIT_MS) { acknowledge(id); return; }
        if (v2 && (pending.size >= 8 || pendingBytes + event.data.byteLength > MAX_BYTES)) throw Error('protocol');
        pending.set(id, { at: now(), bytes: event.data.byteLength }); pendingBytes += event.data.byteLength;
        postMessage({ type: 'data', epoch, id, ...(v2 ? { kind } : {}),
          receivedAt: Date.now() - (v2 && kind === 'media' ? age : 0), buf: event.data }, [event.data]);
      } catch (error) { failed(['metadata-freshness', 'frame-freshness'].includes(error.message) ? error.message : 'protocol'); }
    };
    ws.onerror = () => failed('socket-error'); ws.onclose = event => failed('socket-close-' + event.code);
    ws.ack = (id, owner) => {
      if (epoch !== generation || owner !== epoch || ws.readyState !== WebSocket.OPEN || !Number.isSafeInteger(id) || !pending.has(id)) return;
      pendingBytes -= pending.get(id).bytes; pending.delete(id); acknowledge(id);
    };
  } catch (_) { failed(); }
}
self.onmessage = event => {
  const value = event.data || {};
  if (value.type === 'start' && typeof value.url === 'string') {
    want = true; url = value.url; codec = value.codec === 'aac' ? 'aac' : 'flac';
    ceilingBitRate = [64000,128000,256000].includes(value.bitRate) ? value.bitRate : 128000;
    automatic = codec === 'aac' && value.automatic === true && value.transportVersion !== 1;
    bitRate = automatic ? Math.min([64000,128000,256000].includes(value.currentBitRate) ? value.currentBitRate : 128000,ceilingBitRate) : ceilingBitRate; healthySince = lastSample = badSince = null; changedAt = -Infinity;baselineAge=Infinity;lastPromotion=promotionBlockedUntil=-Infinity;
    protocol = (value.codec === 'aac' ? 'droiddeck-aac-live-v' : 'droiddeck-audio-live-v') + (value.transportVersion === 1 ? '1' : '2');
    if (value.transportVersion !== 1) protocol = protocolForRate();
    postMessage({type:'quality',bitRate,automatic}); backoff = 250; connect();
  } else if (value.type === 'stop') { want = false; retire(); }
  else if (value.type === 'ack') socket?.ack?.(value.id, value.epoch);
};
