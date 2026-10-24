// SPDX-License-Identifier: GPL-3.0-only
// Appended to the reviewed worker. No forced hardware; bounded async admission.
(() => {
  'use strict';
  const build = buildDecoderConfig;
  const configure = configureDecoderFromSpsPps;
  const receive = self.onmessage;
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
