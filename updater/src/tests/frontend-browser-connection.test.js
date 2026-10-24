// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
function fixture() {
  const window = {}, context = vm.createContext({ window, URL, console });
  for (const file of ['js/core/shared.js', 'js/core/api-client.js', 'js/settings/settings-controller.js']) {
    vm.runInContext(fs.readFileSync(path.join(process.env.TA_FRONTEND_DIR, file), 'utf8'), context);
  }
  return window.TAHtml;
}
test('browser connection uses owner session/CSRF endpoint, without TLS key or provider-token payloads', async () => {
  const TA = fixture(), api = new TA.ApiClient('/api'), calls = [];
  assert.throws(() => api.saveBrowserConnection({}), /session/);
  api.setDisplaySession({ token: 'a'.repeat(64), csrfToken: 'b'.repeat(64) });
  api._fetchJson = async (url, options) => { calls.push({ url, options }); return {}; };
  await api.fetchBrowserConnection();
  await api.saveBrowserConnection({ revision: 0, hostname: 'deck.example' });
  assert.equal(calls[0].url, '/api/browserConnection');
  assert.equal(calls[1].options.headers['X-Display-CSRF'], 'b'.repeat(64));
  assert.equal(calls[1].options.headers['X-Display-Session'], 'a'.repeat(64));
  assert.deepEqual(JSON.parse(calls[1].options.body), { revision: 0, hostname: 'deck.example' });
});
test('connection edit gates viewers, busy state and detached sections without blocking reading', () => {
  const TA = fixture(), settings = Object.assign(Object.create(TA.SettingsController.prototype), {
    activeSectionId: 'browserConnection', connectionSettingsPending: false,
    displayController: { controlRequestInFlight: false, remoteDisplayState: { canControl: true, transactionStatus: 'idle' } },
  });
  assert.equal(settings._connectionCanEdit(), true);
  for (const state of [{ canControl: false, transactionStatus: 'idle' }, { canControl: true, transactionStatus: 'resizing' }]) {
    settings.displayController.remoteDisplayState = state; assert.equal(settings._connectionCanEdit(), false);
  }
  settings.displayController.remoteDisplayState = { canControl: true, transactionStatus: 'idle' };
  settings.connectionSettingsPending = true; assert.equal(settings._connectionCanEdit(), false);
  settings.connectionSettingsPending = false; settings.activeSectionId = 'display'; assert.equal(settings._connectionCanEdit(), false);
});
