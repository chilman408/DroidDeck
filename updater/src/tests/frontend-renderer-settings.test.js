// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched frontend');
function fixture() {
  const saved = new Map(), session = new Map(), navigations = [];
  const window = { isSecureContext: true, VideoDecoder() {}, location: {
    search: '', href: 'https://example.test/?other=keep#home', reload: () => navigations.push(window.location.href),
  }, history: { replaceState(_state, _title, url) { window.location.href = url; } },
    sessionStorage: { getItem: key => session.get(key) ?? null,
      setItem: (key, value) => session.set(key, value), removeItem: key => session.delete(key) },
    localStorage: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) } };
  const document = { getElementById: () => ({ transferControlToOffscreen() {} }) };
  const context = vm.createContext({ window, document, URL, URLSearchParams, console });
  for (const file of ['js/core/shared.js', 'js/display/display-renderer-manager.js', 'js/settings/settings-controller.js']) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  }
  return { window, saved, session, navigations, prefs: window.TAHtml.browserRenderer,
    manager: new window.TAHtml.DisplayRendererManager({}) };
}
test('renderer preference persists locally, defaults to automatic and never mutates Android', () => {
  const f = fixture(); assert.ok(f.prefs, 'Browser renderer controls are missing');
  assert.equal(f.prefs.choice(), 'auto');
  f.prefs.select('legacy');
  assert.equal(f.prefs.choice(), 'legacy');
  assert.equal(f.manager._rendererInfoForId(0, { streamFormats: ['h264'] }).id, 2);
  assert.equal(f.navigations[0], 'https://example.test/?other=keep#home');
  f.prefs.select('webcodecs');
  assert.equal(f.manager._rendererInfoForId(0, { streamFormats: ['h264'] }).id, 1);
  f.prefs.select('auto'); assert.equal(f.prefs.choice(), 'auto');
  assert.throws(() => f.prefs.select('png'));
  assert.throws(() => f.prefs.select('command'));
});

test('automatic uses proven legacy H264 even when WebCodecs exists, without changing the saved choice or reloading', () => {
  const f = fixture();
  assert.equal(f.prefs.recover(), false);
  assert.equal(f.prefs.choice(), 'auto');
  assert.equal(f.prefs.effectiveChoice(), 'legacy');
  assert.equal(f.manager._rendererInfoForId(0, { streamFormats: ['h264'] }).id, 2);
  assert.equal(f.saved.size, 0);
  assert.equal(f.navigations.length, 0);
  assert.equal(f.prefs.recover(), false);
  assert.equal(f.navigations.length, 0);
  const next = fixture();
  for (const [key, value] of f.session) next.session.set(key, value);
  assert.equal(next.prefs.effectiveChoice(), 'legacy');
  assert.equal(next.prefs.recover(), false);
  assert.equal(next.navigations.length, 0);
  next.prefs.select('auto');
  assert.equal(next.prefs.effectiveChoice(), 'legacy', 'Automatic must not retry an unproven vehicle decoder');
  assert.equal(next.session.size, 0);
});

test('automatic recovery never overrides explicit legacy or WebCodecs diagnostics', () => {
  for (const choice of ['legacy', 'webcodecs']) {
    const f = fixture(); f.prefs.select(choice);
    const before = f.navigations.length;
    assert.equal(f.prefs.recover(), false);
    assert.equal(f.navigations.length, before);
    const query = fixture(); query.window.location.search = '?video=' + choice;
    assert.equal(query.prefs.recover(), false);
    assert.equal(query.navigations.length, 0);
    assert.equal(query.session.size, 0);
  }
});

test('blocked or silently ignored session storage still defaults to legacy without URL changes or reloads', () => {
  for (const silent of [false, true]) {
    const f = fixture();
    f.window.location.replace = url => f.navigations.push(url);
    f.window.sessionStorage.setItem = () => { if (!silent) throw Error('Storage blocked'); };
    f.window.sessionStorage.getItem = () => { throw Error('Storage blocked'); };
    assert.equal(f.prefs.effectiveChoice(), 'legacy');
    assert.equal(f.prefs.recover(), false);
    assert.equal(f.navigations.length, 0);
    assert.equal(f.saved.size, 0);
    assert.equal(f.prefs.recover(), false);
    assert.equal(f.navigations.length, 0);
    const next = fixture(); next.window.location.search = '?other=keep&video=legacy';
    assert.equal(next.prefs.recover(), false);
    assert.equal(next.manager._rendererInfoForId(0, { streamFormats: ['h264'] }).id, 2);
  }
});
test('explicit diagnostics override saved choice; blocked storage and unsupported WebCodecs are bounded', () => {
  const f = fixture(); assert.ok(f.prefs, 'Browser renderer controls are missing');
  f.prefs.select('legacy'); f.window.location.search = '?video=webcodecs';
  assert.equal(f.prefs.choice(), 'webcodecs');
  f.window.location.href = 'https://example.test/?video=png&other=keep#home';
  f.prefs.select('auto');
  assert.equal(f.navigations.at(-1), 'https://example.test/?other=keep#home');
  f.window.VideoDecoder = undefined;
  assert.equal(f.prefs.choice(), 'auto');
  assert.equal(f.manager._rendererInfoForId(0, { streamFormats: ['h264'] }).id, 2);
  assert.equal(f.prefs.options().some(option => option.value === 'webcodecs'), false);
  f.window.localStorage.getItem = () => { throw Error('Storage blocked'); };
  f.window.location.search = '';
  assert.equal(f.prefs.choice(), 'auto');
  f.window.localStorage.setItem = () => { throw Error('Storage blocked'); };
  const before = f.navigations.length;
  assert.throws(() => f.prefs.select('legacy'), /Storage blocked/);
  assert.equal(f.navigations.length, before);
});
test('renderer is available to viewers without enabling global resolution writes', async () => {
  const f = fixture(), tiles = [], selections = [], globalInput = {}, localInput = { closest: () => ({}) };
  const config = { displayControlSupported: 1, streamFormats: ['h264'], canControl: false,
    transactionStatus: 'idle', generation: 2, resolutionPreset: 1, isResponsive: 1 };
  const settings = { _renderToken: 1, activeSectionId: 'display',
    apiClient: { fetchDisplayState: async () => config }, _normalizeDisplayState: raw => raw,
    _appendLoadingState() {}, _appendDivider() {}, _appendTile: tile => tiles.push(tile),
    _createSelect(options, value, callback) { const root = { dataset: {} }; selections.push({ options, value, callback, root }); return root; },
    _createSwitch: () => ({ dataset: {} }), _createTrailingText: text => text,
    content: { innerHTML: '', querySelectorAll: () => [globalInput, localInput] },
    displayController: { controlRequestInFlight: false }, _renderActiveSection() {},
  };
  const prototype = f.window.TAHtml.SettingsController.prototype;
  Object.setPrototypeOf(settings, prototype);
  await prototype._renderDisplaySection.call(settings, 1);
  const renderer = selections.find(select => select.options.some(option => option.value === 'legacy'));
  assert.ok(renderer, 'Renderer Settings is still a placeholder');
  assert.equal(tiles.find(tile => tile.title === 'Renderer').trailing, renderer.root);
  assert.equal(renderer.root.dataset.browserRenderer, '1');
  prototype.refreshDisplayControl.call(settings, config);
  assert.equal(globalInput.disabled, true);
  assert.equal(localInput.disabled, undefined);
  await renderer.callback('legacy'); assert.equal(f.prefs.choice(), 'legacy');
});
