// SPDX-License-Identifier: GPL-3.0-only
// Appended by the gateway to the existing renderer manager. Browser-local only.
(function (global) {
  'use strict';
  const preferences = global.TAHtml?.browserRenderer;
  const prototype = global.TAHtml?.DisplayRendererManager?.prototype;
  if (!preferences || !prototype) throw new Error('Browser renderer controls unavailable');
  const rendererKey = 'TeslaAndroid_browserH264Renderer_v1';
  const rendererInfo = prototype._rendererInfoForId;
  const query = new URLSearchParams(global.location.search).get('video');
  const available = global.isSecureContext && typeof global.VideoDecoder === 'function';
  const trial = query === 'webcodecs-test';
  let recovering = false;
  let startupTimer = null;

  preferences.choice = function () {
    if (recovering || query === 'legacy') return 'legacy';
    if (trial || query === 'webcodecs') return available ? 'webcodecs' : 'legacy';
    let value = 'auto';
    try { value = global.localStorage.getItem(rendererKey); } catch {}
    if (!['auto','legacy','webcodecs'].includes(value)) value = 'auto';
    return value === 'webcodecs' && !available ? 'legacy' : value;
  };
  preferences.effectiveChoice = function () { return this.choice() === 'webcodecs' ? 'webcodecs' : 'legacy'; };
  preferences.options = function () {
    return [{ value: 'auto', label: 'Automatic' },
      ...(available ? [{ value: 'webcodecs', label: 'H264 (WebCodecs)' }] : []),
      { value: 'legacy', label: 'H264 (Legacy)' }];
  };
  preferences.select = function (value) {
    if (!['auto', 'legacy', ...(available ? ['webcodecs'] : [])].includes(value)) throw new Error('Unsupported browser renderer');
    global.localStorage.setItem(rendererKey, value);
    try { global.sessionStorage.removeItem('TeslaAndroid_autoH264Fallback_v1'); } catch {}
    const url = new URL(global.location.href);
    url.searchParams.delete('video');
    if (url.href === global.location.href) global.location.reload();
    else global.location.replace(url.href);
  };
  preferences.recover = function () {
    if (recovering || this.effectiveChoice() !== 'webcodecs') return false;
    recovering = true;
    global.clearTimeout(startupTimer);
    const url = new URL(global.location.href);
    url.searchParams.set('video', 'legacy');
    // A failed saved selection falls back for this browser's future visits too.
    // Explicit diagnostic trials leave saved preferences alone.
    if (!trial) {
      try { global.localStorage.setItem(rendererKey, 'legacy'); } catch {}
    }
    global.location.replace(url.href);
    return true;
  };
  prototype._rendererInfoForId = function (...args) {
    const info = rendererInfo.apply(this, args);
    if (preferences.effectiveChoice() === 'webcodecs' && info.packetFormat === 'scrcpy') {
      if (startupTimer === null) startupTimer = global.setTimeout(() => {
        const canvas = global.document.getElementById('canvas');
        if (!Number(canvas?.dataset.videoFrames || 0)) preferences.recover();
      }, 15000);
      return { ...info, id: 1, scriptPath: './js/display/renderers/h264-webcodecs/h264WebCodecs.js' };
    }
    // Automatic remains the accepted legacy default. Keep packet format,
    // socket and geometry unchanged; this browser selects its decoder only.
    return info.id === 1 ? { ...info, id: 2,
      scriptPath: './js/display/renderers/h264-broadway/h264Brodway.js' } : info;
  };
})(window);
