// SPDX-License-Identifier: GPL-3.0-only
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
const { VIDEO_SETTINGS_FILE } = require('./video-settings');
const { AUDIO_SETTINGS_FILE } = require('./audio-gain');
const { ROOT } = require('../waydroid-display/runtime-commands');
function captureLayout(configuration) {
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const layout = config && deriveRuntimeLayout(config), uid = config?.uid ?? 1000;
  return Object.freeze({ root: layout?.root ?? ROOT, user: config?.user ?? 'hilman', uid,
    hostLibraries: layout?.hostLibraries ?? ROOT + '/libgbinder',
    videoSettingsFile: layout ? layout.preferences + '/browser-video.json' : VIDEO_SETTINGS_FILE,
    audioSettingsFile: layout ? layout.preferences + '/browser-audio.json' : AUDIO_SETTINGS_FILE,
    userEnvironment: Object.freeze(['XDG_RUNTIME_DIR=/run/user/' + uid, 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + uid + '/bus']) });
}
module.exports = { captureLayout };
