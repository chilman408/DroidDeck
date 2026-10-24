// SPDX-License-Identifier: GPL-3.0-only
// Decoder choice is automatic; no redundant Renderer control in Settings.
(function (global) {
  'use strict';
  const prototype = global.TAHtml?.SettingsController?.prototype;
  if (!prototype || typeof prototype._appendTile !== 'function') throw new Error('Settings controls unavailable');
  const append = prototype._appendTile;
  prototype._appendTile = function (tile, ...args) {
    if (tile?.title === 'Renderer') return;
    return append.call(this, tile, ...args);
  };
})(window);
