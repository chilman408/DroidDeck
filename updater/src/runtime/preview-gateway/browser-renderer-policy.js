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

  preferences.choice = function () {
    const value = choice();
    return value === 'webcodecs' ? 'legacy' : value;
  };
  preferences.effectiveChoice = function () { return 'legacy'; };
  preferences.options = function () {
    return [{ value: 'auto', label: 'Automatic' }, { value: 'legacy', label: 'h264 (legacy)' }];
  };
  preferences.select = function (value) {
    if (value !== 'auto' && value !== 'legacy') throw new Error('Unsupported browser renderer');
    return select(value);
  };
  prototype._rendererInfoForId = function (...args) {
    const info = rendererInfo.apply(this, args);
    // Includes old bookmarked query parameters and previously saved selections.
    // Keep the original packet format, socket and geometry; change decoder only.
    return info.id === 1 ? { ...info, id: 2,
      scriptPath: './js/display/renderers/h264-broadway/h264Brodway.js' } : info;
  };
})(window);
