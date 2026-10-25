// SPDX-License-Identifier: GPL-3.0-only
// Appended to the reviewed worker. No forced hardware; bounded async admission.
// Shared pure helper: Node exports it for diagnostics; workers apply it below.
function normalizeScrcpyPacket(packet) {
  if(!(packet instanceof ArrayBuffer) || packet.byteLength<13 || packet.byteLength>2097164) return {packet,audRemoved:0};
  const bytes=new Uint8Array(packet),header=new DataView(packet);
  if(header.getUint32(8)!==bytes.length-12) return {packet,audRemoved:0};
  const units=[];
  for(let i=12;i+2<bytes.length;i++) {
    let code=0;
    if(bytes[i]===0 && bytes[i+1]===0 && bytes[i+2]===0 && bytes[i+3]===1) code=4;
    else if(bytes[i]===0 && bytes[i+1]===0 && bytes[i+2]===1) code=3;
    if(!code) continue;
    if(i+code>=bytes.length) return {packet,audRemoved:0};
    const nalHeader=bytes[i+code],type=nalHeader&31;
    if((nalHeader&128) || (type===9 && nalHeader!==9)) return {packet,audRemoved:0};
    if(!units.length && bytes.subarray(12,i).some(value=>value!==0)) return {packet,audRemoved:0};
    if(units.length===4096) return {packet,audRemoved:0};
    units.push({start:i,type});i+=code;
  }
  const removed=[];let removedBytes=0;
  for(let i=0;i<units.length;i++) if(units[i].type===9) {
    const start=units[i].start,end=units[i+1]?.start ?? bytes.length;
    removed.push({start,end});removedBytes+=end-start;
  }
  if(!removed.length) return {packet,audRemoved:0};
  const next=new Uint8Array(bytes.length-removedBytes);
  let read=0,write=0;
  for(const range of removed) {
    next.set(bytes.subarray(read,range.start),write);write+=range.start-read;read=range.end;
  }
  next.set(bytes.subarray(read),write);
  new DataView(next.buffer).setUint32(8,next.length-12);
  return {packet:next.buffer,audRemoved:removed.length};
}
if (typeof module === 'object' && module.exports) {
  module.exports = {normalizeScrcpyPacket};
} else {
(() => {
  'use strict';
  const build = buildDecoderConfig;
  const configure = configureDecoderFromSpsPps;
  const receive = self.onmessage;
  const scrcpyPacket = handleScrcpyPacket;
  handleScrcpyPacket = function (packet) {
    return scrcpyPacket(normalizeScrcpyPacket(packet).packet);
  };
  let generation = 0;
  let checking = false;
  let queued = [];
  let queuedBytes = 0;
  let failed = false;

  function invalidate() {
    generation++;
    checking = false;
    queued = [];
    queuedBytes = 0;
    failed = false;
  }
  function refuse() {
    failed = true;
    checking = false;
    queued = [];
    queuedBytes = 0;
    self.postMessage({ error: 'WebCodecs configuration unavailable; use legacy H264' });
  }
  buildDecoderConfig = function (sps) {
    const config = build(sps);
    return config ? { ...config, hardwareAcceleration: 'no-preference' } : null;
  };
  configureDecoderFromSpsPps = function () {
    const config = buildDecoderConfig(spsPps);
    if (!decoder || decoder.state === 'closed' || !config) return;
    const current = ++generation;
    checking = true;
    failed = false;
    queued = [];
    queuedBytes = 0;
    Promise.resolve().then(() => VideoDecoder.isConfigSupported(config)).then(result => {
      if (current !== generation || failed) return;
      if (!result?.supported) { refuse(); return; }
      checking = false;
      configure();
      if (!decoder || decoder.state !== 'configured') { refuse(); return; }
      self.postMessage({ webCodecsConfigured: true, accelerationPreference: 'no-preference' });
      const packets = queued;
      queued = [];
      queuedBytes = 0;
      for (const event of packets) {
        if (checking || failed || current !== generation) break;
        receive(event);
      }
    }).catch(() => { if (current === generation) refuse(); });
  };
  self.onmessage = function (event) {
    const data = event.data;
    if (data.canvas || data.scrcpySession) invalidate();
    const packet = data.scrcpyPacket || data.h264Data;
    if (packet && (checking || failed)) {
      if (failed) return;
      // Same closed packet/byte budget as the original stream. Never retain
      // unbounded video while a browser's capability promise is unresolved.
      if (queued.length >= 120 || packet.byteLength > 2097152 - queuedBytes) { refuse(); return; }
      queued.push(event);
      queuedBytes += packet.byteLength;
      return;
    }
    receive(event);
  };
})();
}
