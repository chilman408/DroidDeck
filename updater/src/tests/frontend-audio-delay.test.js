// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to patched frontend');
function fixture(stored = null, storageDenied = false) {
  const workers = [], media = [], timers = new Map(), events = new Map(), actions = new Map(), windowEvents = new Map(); let id = 0, plays = 0, now = 0;
  const storage = new Map(stored === null ? [] : [['TAHtml_audioDelayMs', stored]]);
  const audio = { currentTime: 0, readyState: 4, volume: 1, autoplay: true, paused: true,
    play() { plays++; this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; }, load() {}, removeAttribute() {},
    addEventListener(name, fn) { events.set(name, fn); } };
  class MediaSource {
    constructor() { this.events = {}; this.readyState = 'open'; media.push(this); }
    static isTypeSupported() { return true; }
    addEventListener(name, fn) { this.events[name] = fn; }
    addSourceBuffer() {
      this.end = .2;
      const self = this;
      return this.buffer = { updating: false, events: {}, appended: 0,
        buffered: { length: 1, start: () => 0, end: () => self.end },
        addEventListener(name, fn) { this.events[name] = fn; },
        appendBuffer() { this.appended++; }, remove() {}, abort() {} };
    }
    removeSourceBuffer() {} endOfStream() {}
  }
  const window = { MediaSource, audioWebsocketUrl: 'wss://example.test/audio',
    addEventListener(name, fn) { if (!windowEvents.has(name)) windowEvents.set(name, new Set()); windowEvents.get(name).add(fn); },
    removeEventListener(name, fn) { windowEvents.get(name)?.delete(fn); },
    dispatchEvent(event) { for (const fn of windowEvents.get(event.type) || []) fn(event); },
    localStorage: { getItem(key) { if (storageDenied) throw new Error('denied'); return storage.get(key) ?? null; },
      setItem(key, value) { if (storageDenied) throw new Error('denied'); storage.set(key, value); } } };
  const context = vm.createContext({ window, MediaSource, Uint8Array, document: { createElement: () => audio, body: { appendChild() {} } },
    navigator: { mediaSession: { setActionHandler(name, fn) { actions.set(name, fn); } } },
    MediaMetadata: class {}, URL: { createObjectURL: () => 'blob:diagnostic', revokeObjectURL() {} },
    Worker: class { constructor() { workers.push(this); } postMessage() {} terminate() { this.terminated = true; } },
    setTimeout(fn, ms) { timers.set(++id, { fn, ms }); return id; }, clearTimeout(key) { timers.delete(key); },
    setInterval() { return 1; }, clearInterval() {}, console, performance: { now: () => now },
    CustomEvent: class { constructor(type) { this.type = type; } },
    fetch() { throw new Error('Local audio delay must not write shared API'); } });
  vm.runInContext(fs.readFileSync(path.join(frontend, 'js/audio/audioplayback.js'), 'utf8'), context);
  return { window, audio, events, workers, media, timers, storage, actions, session: context.navigator.mediaSession, plays: () => plays,
    advance(ms) { now += ms; },
    fire(ms) { const entry = [...timers].find(([, value]) => value.ms === ms); assert.ok(entry); timers.delete(entry[0]); entry[1].fn(); } };
}
test('audio delay defaults to zero and valid preference remains isolated to this browser', () => {
  const f = fixture(); assert.equal(f.window.getAudioDelayMs(), 0);
  f.window.setAudioDelayMs(200); assert.equal(f.storage.get('TAHtml_audioDelayMs'), '200');
  assert.equal(fixture().window.getAudioDelayMs(), 0);
  assert.equal(fixture('200').window.getAudioDelayMs(), 200);
  assert.equal(fixture('-100').window.getAudioDelayMs(), -100);
  for (const bad of ['private', '-775', '900', '1.5', 'Infinity']) assert.equal(fixture(bad).window.getAudioDelayMs(), 0);
  const denied = fixture(null, true); assert.equal(denied.window.getAudioDelayMs(), 0);
  assert.doesNotThrow(() => denied.window.setAudioDelayMs(200));
});
test('nonzero delay waits from canplay, does not start early and retains direct HTML audio', () => {
  const f = fixture('200'); f.window.startAudioPlayback();
  assert.equal(f.audio.autoplay, false); assert.equal(f.plays(), 0);
  f.media[0].events.sourceopen(); f.media[0].buffer.events.updateend();
  assert.equal(f.plays(), 0); assert.equal(f.timers.size, 1);
  f.media[0].buffer.events.updateend(); assert.equal(f.timers.size, 1);
  f.fire(200); assert.equal(f.plays(), 1);
  assert.equal(f.window.getAudioDelayMs(), 200);
});
test('latency catchup does not discard configured extra audio delay', () => {
  const f = fixture('300'); f.window.startAudioPlayback(); const mse = f.media[0]; mse.events.sourceopen();
  mse.buffer.events.updateend(); f.fire(300);
  mse.end = 4; f.audio.currentTime = 2; mse.buffer.events.updateend();
  assert.equal(f.audio.currentTime, 3.6);
});
test('stop cancels delayed playback and stale source callbacks cannot restart it', () => {
  const f = fixture('200'); f.window.startAudioPlayback(); const old = f.media[0]; old.events.sourceopen(); old.buffer.events.updateend();
  f.window.stopAudioPlayback(); assert.equal(f.timers.size, 0);
  old.buffer.events.updateend(); assert.equal(f.plays(), 0); assert.equal(f.timers.size, 0);
});

test('repeated gestures and media-session play cannot bypass pending audio delay', () => {
  const f = fixture('200'); f.window.startAudioPlayback();
  f.window.primeAudioFromGesture(); f.actions.get('play')(); assert.equal(f.plays(), 0);
  f.media[0].events.sourceopen(); f.media[0].buffer.events.updateend();
  f.window.primeAudioFromGesture(); f.actions.get('play')(); assert.equal(f.plays(), 0);
  assert.equal(f.timers.size, 1); f.fire(200); assert.equal(f.plays(), 1);
});

test('media-session pause suspends pending startup until an explicit resume', () => {
  for (const ready of [false, true]) {
    const f = fixture('200'); f.window.startAudioPlayback();
    const mse = f.media[0]; mse.events.sourceopen();
    if (ready) mse.buffer.events.updateend();
    f.actions.get('pause')(); assert.equal(f.timers.size, 0);
    mse.buffer.events.updateend(); assert.equal(f.timers.size, 0); assert.equal(f.plays(), 0);
    f.actions.get('play')(); assert.equal(f.timers.size, 1); assert.equal(f.plays(), 0);
    f.fire(200); assert.equal(f.plays(), 1);
  }
});

test('automatic pipeline recovery retains a pending media-session pause', () => {
  const f = fixture('200'); f.window.startAudioPlayback();
  f.media[0].events.sourceopen(); f.media[0].buffer.events.updateend();
  f.actions.get('pause')(); f.events.get('error')(); f.fire(800);
  const recovered = f.media[1]; recovered.events.sourceopen(); recovered.buffer.events.updateend();
  assert.equal(f.plays(), 0); assert.equal(f.timers.size, 0);
  f.actions.get('play')(); f.fire(200); assert.equal(f.plays(), 1);
});

test('delayed explicit resume updates media-session state after playback starts', async () => {
  const f = fixture('200'); f.window.startAudioPlayback();
  f.media[0].events.sourceopen(); f.media[0].buffer.events.updateend();
  f.actions.get('pause')(); assert.equal(f.session.playbackState, 'paused');
  f.actions.get('play')(); assert.equal(f.session.playbackState, 'paused');
  f.fire(200); await Promise.resolve(); assert.equal(f.session.playbackState, 'playing');
});
test('changing delay resets only audio and ignores old worker and source events', () => {
  const f = fixture(); f.window.startAudioPlayback(); const old = f.media[0], worker = f.workers[0]; old.events.sourceopen();
  f.window.setAudioDelayMs(200); assert.equal(worker.terminated, true); assert.equal(f.media.length, 2);
  old.events.sourceopen(); assert.equal(f.timers.size, 0);
  const current = f.media[1]; current.events.sourceopen();
  worker.onmessage({ data: { type: 'data', buf: new ArrayBuffer(8) } }); assert.equal(current.buffer.appended, 0);
  current.buffer.events.updateend(); f.fire(200); assert.equal(f.plays(), 2);
});
test('disabled audio and invalid delay settings never open extra workers', () => {
  const f = fixture(); f.window.setAudioEnabled(false); f.window.setAudioDelayMs(200);
  assert.equal(f.workers.length, 0);
  assert.throws(() => f.window.setAudioDelayMs(-1));
  assert.throws(() => f.window.setAudioDelayMs(2000));
  assert.throws(() => f.window.setAudioDelayMs(13));
});

test('settings delay slider updates only local playback and ignores detached section callbacks', async () => {
  const nodes = [], tiles = []; let delayMs = 0, writes = 0;
  function element() {
    const value = { dataset: {}, style: {}, events: {}, children: [],
      appendChild(child) { this.children.push(child); }, setAttribute() {},
      addEventListener(name, fn) { this.events[name] = fn; } };
    nodes.push(value); return value;
  }
  const window = { document: { createElement: element },
    getAudioDelayMs: () => delayMs, setAudioDelayMs: value => { delayMs = value; } };
  const context = vm.createContext({ window, document: window.document, console });
  for (const file of ['js/core/shared.js', 'js/settings/settings-controller.js']) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  }
  const settings = Object.assign(Object.create(window.TAHtml.SettingsController.prototype), {
    activeSectionId: 'audio', _renderToken: 1, apiClient: { browserAudioSupported: true },
    displayController: { remoteDisplayState: { canControl: false, transactionStatus: 'idle' }, controlRequestInFlight: false },
    content: { innerHTML: '', querySelectorAll: () => nodes.filter(node => node.type === 'range') },
    _appendLoadingState() {}, _appendDivider() {}, _appendDescription() {}, _createSwitch: () => element(),
    _appendTile: value => tiles.push(value), _fetchSystemConfigurationSafe: async () => ({}),
    _saveAudioSettings: async () => { writes++; }
  });
  await settings._renderAudioSection(1);
  const input = nodes.find(node => node.className === 'settings-audio-delay-slider');
  assert.ok(input); assert.equal(input.value, '0'); assert.equal(input.disabled, false);
  assert.equal(input.step, '25'); assert.equal(input.max, '750');
  assert.equal(input.min, '-750');
  input.value = '200'; input.events.change(); assert.equal(delayMs, 200); assert.equal(writes, 0);
  settings.activeSectionId = 'gps'; input.value = '300'; input.events.change(); assert.equal(delayMs, 200);
  assert.equal(tiles.filter(tile => tile.title === 'A/V sync').length, 1);
});

test('negative sync advances audio relative to video without delaying or restarting audio', () => {
  const f = fixture('-200'); f.window.startAudioPlayback();
  assert.equal(f.plays(), 1); assert.equal(f.audio.autoplay, true);
  f.window.setAudioDelayMs(-300); assert.equal(f.media.length, 1);
  assert.equal(f.storage.get('TAHtml_audioDelayMs'), '-300');
});

test('negative sync schedules complete video packets in order, including idle final frames', () => {
  const f = fixture('-200'), sent = [];
  const queue = f.window.createAVSyncVideoQueue(value => sent.push(value), () => assert.fail('overflow'));
  const first = new ArrayBuffer(20), second = new ArrayBuffer(21);
  queue.push(first); f.advance(30); queue.push(second);
  assert.equal(sent.length, 0); assert.equal(f.timers.size, 1);
  f.advance(170); f.fire(170); assert.deepEqual(sent, [first]);
  f.advance(30); f.fire(30); assert.deepEqual(sent, [first, second]);
  assert.equal(f.timers.size, 0); queue.close();
});

test('zero flushes video compensation immediately and signed changes move deadlines', () => {
  const f = fixture('-200'), sent = [];
  const queue = f.window.createAVSyncVideoQueue(value => sent.push(value), () => assert.fail('overflow'));
  queue.push(new ArrayBuffer(12)); f.advance(50); f.window.setAudioDelayMs(-100);
  assert.equal([...f.timers.values()][0].ms, 50);
  f.window.setAudioDelayMs(0); assert.equal(sent.length, 1); assert.equal(f.timers.size, 0);
  queue.push(new ArrayBuffer(12)); assert.equal(sent.length, 2); queue.close();
});

test('video compensation reset and teardown cannot deliver stale frames', () => {
  const f = fixture('-200'), sent = [];
  const queue = f.window.createAVSyncVideoQueue(value => sent.push(value), () => assert.fail('overflow'));
  queue.push(new ArrayBuffer(12)); const stale = [...f.timers.values()][0].fn;
  queue.reset(); stale(); assert.equal(sent.length, 0);
  queue.push(new ArrayBuffer(12)); queue.close(); f.window.setAudioDelayMs(0);
  stale(); queue.push(new ArrayBuffer(12)); assert.equal(sent.length, 0); assert.equal(f.timers.size, 0);
});

test('video compensation caps bytes and packets, reconnecting once until a session reset', () => {
  for (const bytes of [12, 1024 * 1024]) {
    const f = fixture('-750'); let overflows = 0, sent = 0;
    const queue = f.window.createAVSyncVideoQueue(() => sent++, () => overflows++);
    for (let i = 0; i < 140; i++) queue.push(new ArrayBuffer(bytes));
    assert.equal(overflows, 1); assert.equal(sent, 0); assert.equal(f.timers.size, 0);
    queue.reset(); queue.push(new ArrayBuffer(12)); f.advance(750); f.fire(750);
    assert.equal(sent, 1); queue.close();
  }
});

test('a complete 120-packet cached GOP plus configuration fits the video compensation queue', () => {
  const f = fixture('-200'); let sent = 0;
  const queue = f.window.createAVSyncVideoQueue(() => sent++, () => assert.fail('cached GOP overflow'));
  queue.push(new ArrayBuffer(262156));
  for (let i = 0; i < 120; i++) queue.push(new ArrayBuffer(16000));
  f.advance(200); f.fire(200); assert.equal(sent, 121); queue.close();
});

test('negative sync uses the normal audio catchup target, not a negative buffer margin', () => {
  const f = fixture('-300'); f.window.startAudioPlayback(); const mse = f.media[0]; mse.events.sourceopen();
  mse.end = 4; f.audio.currentTime = 2; mse.buffer.events.updateend();
  assert.equal(f.audio.currentTime, 3.9);
});
