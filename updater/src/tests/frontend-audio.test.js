// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error("Set TA_FRONTEND_DIR to patched frontend");
function fixture(supported = true, configuration = { browserAudioSupported: 1 }) {
  const workers = [], timers = new Map(), events = new Map(), gains = [], requests = [], mediaSources = [], intervals = []; let id = 0;
  const audio = { volume: 1, play: () => Promise.resolve(), pause() {}, load() {}, removeAttribute() {}, addEventListener() {} };
  class MediaSource {
    constructor() { this.events = {}; mediaSources.push(this); }
    static isTypeSupported() { return supported; }
    addEventListener(name, callback) { this.events[name] = callback; }
    addSourceBuffer() { return this.buffer = { updating: false, events: {}, removals: 0,
      buffered: { length: 1, start: () => 0, end: () => 5.1 },
      addEventListener(name, callback) { this.events[name] = callback; }, remove() { this.removals++; } }; }
  }
  class AudioContext { constructor() { this.state = "running"; this.destination = {}; } createMediaElementSource() { return { connect() {} }; }
    createGain() { const node = { gain: { value: 1 }, connect() {} }; gains.push(node); return node; } }
  const window = { TAHtml: {}, MediaSource, AudioContext, addEventListener(name, fn) { events.set(name, fn); }, dispatchEvent() {},
    setInterval() {}, clearInterval() {} };
  const context = vm.createContext({ window, document: { createElement: () => audio, body: { appendChild() {} }, querySelector: () => audio },
    MediaSource, URL: { createObjectURL: () => "blob:audio", revokeObjectURL() {} }, navigator: {}, CustomEvent: class {},
    Worker: class { constructor() { workers.push(this); } postMessage(m) { this.last = m; } terminate() { this.terminated = true; } },
    setTimeout(fn) { timers.set(++id, fn); return id; }, clearTimeout(key) { timers.delete(key); },
    setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; }, clearInterval() {},
    console, fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, json: async () => configuration }; } });
  for (const file of ["js/core/api-client.js", "js/audio/audioplayback.js", "js/audio/audio-controller.js"]) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), "utf8"), context);
  }
  return { window, workers, timers, gains, audio, requests, mediaSources, intervals };
}
test("audio writes use current session and CSRF only for the x86 capability", async () => {
  const f = fixture(), client = new f.window.TAHtml.ApiClient("/api");
  await client.fetchSystemConfiguration(); client.setDisplaySession({ token: "a".repeat(64), csrfToken: "b".repeat(64) });
  await client.setBrowserAudioVolume(150);
  assert.equal(f.requests[1].options.headers["X-Display-Session"], "a".repeat(64));
  assert.equal(f.requests[1].options.headers["X-Display-CSRF"], "b".repeat(64));
  assert.equal(f.requests[1].options.body, "150");
});
test("audio starts only after gesture, uses current socket session and stops on disabled configuration", () => {
  const f = fixture(); let session = "one";
  const controller = new f.window.TAHtml.AudioController({ audioWebsocketUrl: "wss://host/sockets/audio",
    apiClient: { socketUrl: url => url + "?session=" + session } });
  controller.applyConfiguration({ isAudioEnabled: true, audioVolume: 1 });
  assert.equal(f.workers.length, 0); session = "two"; controller.startFromGesture();
  assert.equal(f.workers[0].last.url, "wss://host/sockets/audio?session=two");
  controller.applyConfiguration({ isAudioEnabled: false, audioVolume: 1 });
  assert.equal(f.workers[0].terminated, true); controller.startFromGesture(); assert.equal(f.workers.length, 1);
});
test("upstream audio element plays directly without a Web Audio media-source graph", () => {
  const f = fixture(); f.window.audioWebsocketUrl = "wss://host/audio"; f.window.primeAudioFromGesture();
  f.window.startAudioPlayback(); assert.equal(f.gains.length, 0);
  f.window.setAudioVolume(0.5); assert.equal(f.audio.volume, 0.5);
  f.window.setAudioVolume(1.5); assert.equal(f.audio.volume, 1);
  f.window.setAudioVolume(0); assert.equal(f.audio.volume, 0);
});

test("managed PCM gain is not attenuated twice by the browser audio element", () => {
  const f = fixture(), client = { browserAudioGainSupported: true };
  const controller = new f.window.TAHtml.AudioController({ apiClient: client, audioWebsocketUrl: "wss://host/audio" });
  f.window.audioWebsocketUrl = "wss://host/audio";
  f.window.startAudioPlayback();
  for (const volume of [0, 0.5, 1, 1.5]) {
    controller.applyConfiguration({ isAudioEnabled: true, audioVolume: volume });
    assert.equal(controller.volume, volume); assert.equal(f.audio.volume, 1);
  }
  client.browserAudioGainSupported = false;
  controller.applyConfiguration({ isAudioEnabled: true, audioVolume: 0.5 });
  assert.equal(f.audio.volume, 0.5); assert.equal(f.gains.length, 0);
});

test("PCM gain capability requires both explicit audio and gain support", async () => {
  for (const [configuration, supported] of [[{ browserAudioSupported: 1, browserAudioGainSupported: 1 }, true],
    [{ browserAudioSupported: 1, browserAudioGainSupported: '1' }, false], [{ browserAudioGainSupported: 1 }, false],
    [{ browserAudioSupported: 1 }, false]]) {
    const f = fixture(true, configuration), client = new f.window.TAHtml.ApiClient('/api');
    assert.equal(client.browserAudioGainSupported, false); await client.fetchSystemConfiguration();
    assert.equal(client.browserAudioGainSupported, supported);
  }
});
test("unsupported audio MSE never opens a socket or loops", () => {
  const unsupported = fixture(false); unsupported.window.audioWebsocketUrl = "wss://host/audio";
  unsupported.window.startAudioPlayback(); assert.equal(unsupported.workers.length, 0); assert.equal(unsupported.timers.size, 0);
});
test("buffer eviction cannot recursively schedule itself through updateend", () => {
  const f = fixture(); f.window.audioWebsocketUrl = "wss://host/audio"; f.window.startAudioPlayback();
  const mse = f.mediaSources[0]; mse.events.sourceopen(); f.audio.currentTime = 5;
  mse.buffer.events.updateend(); assert.equal(mse.buffer.removals, 0);
  f.intervals.find(value => value.ms === 5000).fn(); assert.equal(mse.buffer.removals, 1);
  mse.buffer.events.updateend(); assert.equal(mse.buffer.removals, 1);
});
