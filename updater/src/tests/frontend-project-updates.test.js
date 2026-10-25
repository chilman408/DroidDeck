// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to patched frontend');
function fixture() {
  const window = { addEventListener() {} }, context = vm.createContext({ window, console, document: { createElement: () => ({}) } });
  for (const file of ['js/core/shared.js', 'js/core/api-client.js', 'js/home/home-controller.js']) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  }
  const api = new window.TAHtml.ApiClient('/api'); let requests = 0;
  api._fetchJson = async () => { requests++; return { name: '9999.99.99' }; };
  return { window, api, requests: () => requests };
}
test('unreleased VM project does not query or advertise upstream Raspberry Pi downloads', async () => {
  const f = fixture(); assert.equal(await f.api.fetchLatestRelease(), null);
  assert.equal(f.requests(), 0);
});
test('corner download badge stays hidden after a release check when no project download exists', async () => {
  const f = fixture(), button = { hidden: false, appendChild() {} };
  const home = new f.window.TAHtml.HomeController({ apiClient: f.api, updateButton: button });
  await home.checkForUpdates();
  assert.equal(home.isUpdateAvailable, false); assert.equal(home.latestVersion, null);
  assert.equal(button.hidden, true); assert.equal(f.requests(), 0);
});
