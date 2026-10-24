// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched frontend');
function fixture() {
  const window = { isSecureContext: true, VideoDecoder() {}, location: { search: '' },
    localStorage: { getItem: () => null } };
  const context = vm.createContext({ window, URL, URLSearchParams, console });
  for (const file of ['js/core/shared.js', 'js/core/api-client.js', 'js/display/display-renderer-manager.js', 'js/settings/settings-controller.js']) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  }
  return window.TAHtml;
}
test('video targets use a separate owner-CSRF API and never submit arbitrary display fields', async () => {
  const TA = fixture(), api = new TA.ApiClient('/api'), calls = [];
  assert.throws(() => api.postVideoTargets({}), /session/);
  api.setDisplaySession({ token: 'a'.repeat(64), csrfToken: 'b'.repeat(64) });
  api._fetchJson = async (url, options) => { calls.push({ url, options }); return { revision: 1 }; };
  await api.postVideoTargets({ revision: 0, bitRate: 2000000, maxFps: 45 });
  assert.equal(calls[0].url, '/api/videoState');
  assert.equal(calls[0].options.headers['X-Display-Session'], 'a'.repeat(64));
  assert.equal(calls[0].options.headers['X-Display-CSRF'], 'b'.repeat(64));
  assert.deepEqual(JSON.parse(calls[0].options.body), { revision: 0, bitRate: 2000000, maxFps: 45 });
});
test('encoder menus are owner controlled while browser renderer remains viewer selectable', async () => {
  const TA = fixture(), tiles = [], selections = [], writes = [], globalInput = {}, localInput = { closest: () => ({}) };
  const config = { displayControlSupported: 1, streamFormats: ['h264'], canControl: false, transactionStatus: 'idle',
    generation: 2, resolutionPreset: 1, isResponsive: 1, videoControlSupported: 1,
    videoTargets: { version: 1, revision: 4, bitRate: 4000000, maxFps: 30 } };
  const settings = { _renderToken: 1, activeSectionId: 'display',
    apiClient: { fetchDisplayState: async () => config, postVideoTargets: async value => writes.push(value) },
    _normalizeDisplayState: value => value, _appendLoadingState() {}, _appendDivider() {},
    _appendTile: tile => tiles.push(tile), _createSwitch: () => ({ dataset: {} }), _createTrailingText: value => value,
    _createSelect(options, value, callback) { const root = { dataset: {} }; selections.push({ options, value, callback, root }); return root; },
    content: { innerHTML: '', querySelectorAll: () => [globalInput, localInput] },
    displayController: { controlRequestInFlight: false }, _renderActiveSection() {},
  };
  const proto = TA.SettingsController.prototype;
  Object.setPrototypeOf(settings, proto);
  settings.refreshDisplayControl = state => proto.refreshDisplayControl.call(settings, state);
  await proto._renderDisplaySection.call(settings, 1);
  assert.equal(globalInput.disabled, true); assert.equal(localInput.disabled, undefined);
  const bitrate = selections.find(select => select.root.dataset.videoBitrate);
  const rate = selections.find(select => select.root.dataset.videoFrameRate);
  assert.ok(bitrate && rate, 'Encoder controls are missing');
  assert.deepEqual(Array.from(bitrate.options, option => option.value), [1000000, 2000000, 3000000, 4000000, 8000000, 12000000]);
  assert.deepEqual(Array.from(rate.options, option => option.value), [30, 45, 60]);
  await bitrate.callback('3000000');
  assert.deepEqual(JSON.parse(JSON.stringify(writes[0])), { revision: 4, bitRate: 3000000, maxFps: 30 });
  assert.equal(settings.videoSettingsPending, false);
  assert.equal(tiles.some(tile => tile.title === 'Target frame rate'), true);
  settings.videoSettingsPending = true; config.canControl = true;
  settings.refreshDisplayControl(config); assert.equal(globalInput.disabled, true);
});
