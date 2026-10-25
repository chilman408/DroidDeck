// SPDX-License-Identifier: GPL-3.0-only
// The existing frontend supplies a browser-local selector; show both decoders.
(function (global) {
  'use strict';
  const prototype = global.TAHtml?.SettingsController?.prototype;
  if (!prototype || typeof prototype._appendTile !== 'function') throw new Error('Settings controls unavailable');
  const append = prototype._appendTile;
  prototype._appendTile = function (tile, ...args) {
    if (tile?.title === 'Renderer' &&
        !global.TAHtml?.browserRenderer?.options().some(option => option.value === 'webcodecs')) return;
    return append.call(this, tile, ...args);
  };
})(window);
