// SPDX-License-Identifier: GPL-3.0-only
// Appended by the gateway to the existing renderer manager. Browser-local only.
(function (global) {
  'use strict';
  const preferences = global.TAHtml?.browserRenderer;
  const prototype = global.TAHtml?.DisplayRendererManager?.prototype;
  if (!preferences || !prototype) throw new Error('Browser renderer controls unavailable');
  const choice = preferences.choice.bind(preferences);
  const select = preferences.select.bind(preferences);
  const rendererInfo = prototype._rendererInfoForId;
  const trial = new URLSearchParams(global.location.search).get('video') === 'webcodecs-test' &&
    global.isSecureContext && typeof global.VideoDecoder === 'function';
  let recovering = false;
  let startupTimer = null;

  preferences.choice = function () {
    if (trial && !recovering) return 'webcodecs';
    const value = choice();
    return value === 'webcodecs' ? 'legacy' : value;
  };
  preferences.effectiveChoice = function () { return trial && !recovering ? 'webcodecs' : 'legacy'; };
  preferences.options = function () {
    return [{ value: 'auto', label: 'Automatic' }, { value: 'legacy', label: 'h264 (legacy)' }];
  };
  preferences.select = function (value) {
    if (value !== 'auto' && value !== 'legacy') throw new Error('Unsupported browser renderer');
    return select(value);
  };
  preferences.recover = function () {
    if (!trial || recovering) return false;
    recovering = true;
    global.clearTimeout(startupTimer);
    const url = new URL(global.location.href);
    url.searchParams.set('video', 'legacy');
    // The new URL is outside the trial: at most one recovery navigation, no
    // preference writes and no automatic retry on future normal visits.
    global.location.replace(url.href);
    return true;
  };
  prototype._rendererInfoForId = function (...args) {
    const info = rendererInfo.apply(this, args);
    if (trial && !recovering && info.packetFormat === 'scrcpy') {
      if (startupTimer === null) startupTimer = global.setTimeout(() => {
        const canvas = global.document.getElementById('canvas');
        if (!Number(canvas?.dataset.videoFrames || 0)) preferences.recover();
      }, 15000);
      return { ...info, id: 1, scriptPath: './js/display/renderers/h264-webcodecs/h264WebCodecs.js' };
    }
    // Includes old bookmarked query parameters and previously saved selections.
    // Keep the original packet format, socket and geometry; change decoder only.
    return info.id === 1 ? { ...info, id: 2,
      scriptPath: './js/display/renderers/h264-broadway/h264Brodway.js' } : info;
  };
})(window);
