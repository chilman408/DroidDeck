// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to patched frontend');
const read = file => fs.readFileSync(path.join(frontend, file), 'utf8');
function metadata() {
  const window = {}, context = vm.createContext({ window, console });
  for (const file of ['js/core/shared.js', 'js/release-notes/release-notes-data.js']) vm.runInContext(read(file), context);
  return window.TAHtml;
}
test('approved DroidDeck identity replaces inherited About promotion and logo without changing control IDs', () => {
  const html = read('index.html'), about = html.split('id="page-about"')[1].split('id="page-release-notes"')[0];
  assert.match(html, /<title>DroidDeck<\/title>/); assert.match(about, /DroidDeck/);
  assert.match(about, /data-app-version/); assert.match(about, /Preview/);
  assert.doesNotMatch(about, /about-logo|2nd generation|Retrofits|teslaandroid\.com/);
  assert.doesNotMatch(html, /assets\/tesla-android-logo/);
  assert.match(html, /id="main-display-button"[^>]*>Primary Display/);
  assert.match(html, /id="rear-display-button"[^>]*>Secondary Display/);
  assert.match(about, /Waydroid.*TeslaAndroid/);
});
test('initial preview notes describe this migration instead of upstream Pi releases and state remaining limits', () => {
  const data = metadata(); assert.equal(data.constants.APP_VERSION, '0.1.0');
  assert.equal(data.releaseNotesData.versions.length, 1);
  const version = data.releaseNotesData.versions[0]; assert.equal(version.versionName, '0.1.0 Preview');
  const text = JSON.stringify(version); assert.match(text, /Waydroid/); assert.match(text, /GPU/);
  assert.match(text, /A\/V sync/); assert.match(text, /Secondary Display/);
  assert.match(text, /software video decoding/); assert.match(text, /portable installer/);
  assert.doesNotMatch(text, /NVMe Endurance|Fan profile|2026\.3\.1|60% reduction/);
  assert.ok(version.changelogItems.every(item => item.title && item.shortDescription && item.descriptionMarkdown));
});
test('presentation renaming preserves protocol keys and direct audio pipeline', () => {
  const shared = read('js/core/shared.js'), audio = read('js/audio/audioplayback.js');
  assert.match(shared, /DisplayRepository_isPrimaryDisplaySharedPreferencesKey/);
  assert.match(audio, /TAHtml_audioDelayMs/); assert.match(audio, /new MediaSource\(/);
  assert.doesNotMatch(audio, /Lossless Audio [^-|]*[-|] Tesla Android/);
  assert.match(audio, /album: 'DroidDeck'/);
  assert.doesNotMatch(read('js/shell/shell-controller.js'), /Connection with Tesla Android services lost/);
});
