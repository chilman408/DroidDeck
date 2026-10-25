// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const frontend = process.env.TA_FRONTEND_DIR || process.argv[2];
if (!frontend) throw new Error("Set TA_FRONTEND_DIR to the patched pinned frontend checkout");
function fixture() {
  let now = 0, id = 0; const timers = new Map(), intervals = new Map(), listeners = new Map();
  const window = { location: { origin: "http://127.0.0.1:8080", protocol: "http:", host: "127.0.0.1:8080" },
    innerWidth: 1440, innerHeight: 900, devicePixelRatio: 3,
    setTimeout(fn, ms) { timers.set(++id, { fn, at: now + ms }); return id; }, clearTimeout(key) { timers.delete(key); },
    setInterval(fn) { intervals.set(++id, fn); return id; }, clearInterval(key) { intervals.delete(key); },
    addEventListener(name, fn, options) { if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push({ fn, options }); },
    requestAnimationFrame() {} };
  const document = { hidden: false, activeElement: null, getElementById: () => ({ getBoundingClientRect: () => ({ width: window.innerWidth, height: window.innerHeight }) }) };
  const context = vm.createContext({ window, document, console, URL, fetch: async () => { throw new Error("Unexpected fetch"); } });
  for (const file of ["js/core/shared.js", "js/core/api-client.js", "js/display/display-controller.js", "js/touchscreen/touchscreen-controller.js", "js/settings/settings-controller.js"]) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), "utf8"), context);
  }
  return { window, document, context, intervals,
    dispatch(name, event = {}) { for (const listener of [...listeners.get(name) || []]) {
      listener.fn(event); if (listener.options?.once) listeners.get(name).splice(listeners.get(name).indexOf(listener), 1);
    } }, tick(ms) { now += ms; for (const [key, timer] of [...timers])
    if (timer.at <= now) { timers.delete(key); timer.fn(); } }, flush: () => new Promise(resolve => setImmediate(resolve)) };
}
const plain = value => JSON.parse(JSON.stringify(value));

test("Settings opened during resize becomes usable on the idle poll without changing pages", () => {
  const f = fixture(), inputs = [{ disabled: true }, { disabled: true }];
  let renders = 0;
  const settings = { activeSectionId: "display", _displayControlGeneration: 3,
    content: { querySelectorAll: () => inputs }, displayController: { controlRequestInFlight: false },
    _renderActiveSection: () => { renders++; } };
  const refresh = f.window.TAHtml.SettingsController.prototype.refreshDisplayControl;
  refresh.call(settings, { displayControlSupported: 1, canControl: true, transactionStatus: "idle", generation: 3 });
  assert.equal(inputs.every(input => !input.disabled), true);
  refresh.call(settings, { displayControlSupported: 1, canControl: false, transactionStatus: "idle", generation: 3 });
  assert.equal(inputs.every(input => input.disabled), true);
  refresh.call(settings, { displayControlSupported: 1, canControl: true, transactionStatus: "idle", generation: 4 });
  assert.equal(renders, 1);
  settings.displayController.controlRequestInFlight = true;
  refresh.call(settings, { displayControlSupported: 1, canControl: true, transactionStatus: "idle", generation: 4 });
  assert.equal(inputs.every(input => input.disabled), true);
});
test("dynamic sizing selects supported ratios, uses even caps and every short-edge preset", () => {
  const f = fixture(), size = f.window.TAHtml.calculateDynamicSize;
  assert.equal(typeof size, "function");
  assert.deepEqual(plain(size({ width: 1440, height: 900 }, 0, null, true)), { width: 1440, height: 810 });
  assert.deepEqual(plain(size({ width: 390, height: 844 }, 0, null, true)), { width: 378, height: 672 });
  const wide = size({ width: 3840, height: 2160 }, 0, null, true);
  assert.ok(wide.width * wide.height <= 1600000 && wide.width <= 1920 && wide.height <= 1920);
  assert.ok(Math.abs(wide.width / wide.height / (3840 / 2160) - 1) < 0.01);
  for (const [preset, max] of [832, 720, 640, 544, 480].entries()) {
    const result = size({ width: 1920, height: 1080 }, preset, null, true);
    assert.ok(Math.min(result.width, result.height) <= max); assert.equal(result.width % 2 + result.height % 2, 0);
  }
  const locked = size({ width: 390, height: 844 }, 0, { width: 1280, height: 768 }, false);
  assert.ok(Math.abs(locked.width / locked.height / (1280 / 768) - 1) < 0.01);
  assert.equal(size({ width: 0, height: 900 }, 0, null, true), null);
  assert.equal(size({ width: 95, height: 900 }, 0, null, true), null);
});
test("each of the five ratios maximizes the contained browser picture, with deterministic ties", () => {
  const size = fixture().window.TAHtml.calculateDynamicSize;
  const ratios = [[4, 3], [1, 1], [16, 9], [9, 16], [3, 4]];
  const cases = [[1200, 900, 4, 3], [700, 700, 1, 1], [1920, 1080, 16, 9],
    [390, 844, 9, 16], [768, 1024, 3, 4], [1440, 900, 16, 9]];
  for (const [width, height, a, b] of cases) {
    const output = size({ width, height }, 0, {}, true);
    assert.equal(output.width * b, output.height * a);
    assert.ok(output.width <= width && output.height <= height);
  }
  for (const width of [192, 390, 804, 1000, 1440, 3840]) {
    for (const height of [192, 638, 720, 844, 2160]) {
      const output = size({ width, height }, 0, {}, true);
      assert.ok(output);
      const selected = ratios.find(([a, b]) => output.width * b === output.height * a);
      assert.ok(selected, "Output must retain an exact supported ratio");
      const coverage = ([a, b]) => Math.min(width / a, height / b) ** 2 * a * b;
      assert.ok(coverage(selected) >= Math.max(...ratios.map(coverage)) - 0.000001);
    }
  }
  const boundary = { width: 400, height: 200 * Math.sqrt(3) };
  assert.deepEqual(plain(size(boundary, 0, {}, true)), plain(size(boundary, 0, {}, true)));
  for (const area of [{ width: NaN, height: 100 }, { width: 100, height: Infinity }]) {
    assert.equal(size(area, 0, {}, true), null);
  }
  assert.equal(size({ width: 500, height: 500 }, 5, {}, true), null);
});
async function controllerFixture(owner = true) {
  const f = fixture(); const posts = [], sizes = [];
  let state = { width: 1280, height: 768, density: 180, resolutionPreset: 0, isResponsive: 1,
    renderer: 0, fixedDisplay: 0, displayControlSupported: 1, canControl: owner, generation: 0, transactionStatus: "idle" };
  const apiClient = { fetchDisplayState: async () => ({ ...state }), socketUrl: url => url + "?session=one",
    postDisplayState: async payload => { posts.push(payload); state = { ...state, ...payload, generation: state.generation + 1 }; return { ...state }; } };
  const rendererManager = { applyDisplaySize: (w, h) => sizes.push([w, h]), ensureDisplaySocket: async () => {}, closeDisplaySocket() {} };
  const controller = new f.window.TAHtml.DisplayController({ apiClient, rendererManager,
    flavor: { displayWebSocket: "ws://127.0.0.1/sockets/display" } });
  await controller.initialize();
  return { ...f, controller, posts, sizes, setState: patch => { state = { ...state, ...patch }; } };
}

test("navigation floor retains the selected ratio and density without affecting portrait", () => {
  const size = fixture().window.TAHtml.calculateDynamicSize;
  const state = { landscapeNavigationMinHeight: 676, density: 180, width: 804, height: 638 };
  const compact = size({ width: 804, height: 638 }, 1, state, true);
  assert.equal(compact.height, 678);
  assert.equal(compact.width * 3, compact.height * 4);
  assert.equal(state.density, 180);
  for (let preset = 0; preset <= 4; preset++) {
    const result = size({ width: 1280, height: 720 }, preset, state, true);
    assert.ok(result.height >= 676 && result.width * result.height <= 1600000);
  }
  assert.deepEqual(plain(size({ width: 390, height: 844 }, 0, state, true)), { width: 378, height: 672 });
  assert.deepEqual(plain(size({ width: 804, height: 638 }, 1, {}, true)), { width: 800, height: 600 });
  const ultraWide = size({ width: 3840, height: 720 }, 4, state, true);
  assert.ok(ultraWide.width <= 1920 && ultraWide.width * ultraWide.height <= 1600000);
  assert.equal(ultraWide.width * 9, ultraWide.height * 16);
});

test("navigation floor reaches the actual owner resize request, not just a sizing utility", async () => {
  const f = await controllerFixture();
  f.setState({ landscapeNavigationMinHeight: 676 });
  await f.controller.refreshControlState();
  f.controller.triggerResize({ width: 804, height: 638 });
  f.tick(1000); await f.flush();
  assert.equal(f.posts.at(-1).height, 678);
  assert.equal(f.posts.at(-1).width, 904);
  assert.equal(f.posts.at(-1).density, 180);
});
test("resize waits one stable second and sends only the latest size with measured density/generation", async () => {
  const f = await controllerFixture(); f.tick(500); f.controller.triggerResize({ width: 390, height: 844 });
  f.tick(999); await f.flush(); assert.equal(f.posts.length, 0);
  f.tick(1); await f.flush(); assert.equal(f.posts.length, 1);
  assert.equal(f.posts[0].width, 378); assert.equal(f.posts[0].height, 672); assert.equal(f.posts[0].density, 180);
  assert.equal(f.posts[0].generation, 0);
});
test("viewers, hidden pages, settings and keyboard transitions cannot submit viewport mutations", async () => {
  const viewer = await controllerFixture(false); viewer.tick(1000); await viewer.flush(); assert.equal(viewer.posts.length, 0);
  const f = await controllerFixture(); f.controller.setPaused(true); f.tick(1000); await f.flush(); assert.equal(f.posts.length, 0);
  f.controller.setPaused(false); f.document.hidden = true; f.tick(1000); await f.flush(); assert.equal(f.posts.length, 0);
  f.document.hidden = false; f.window.visualViewport = { height: 450 }; f.controller.triggerResizeFromViewport();
  f.tick(1000); await f.flush(); assert.equal(f.posts.length, 0);
  f.window.visualViewport = { height: 900 }; f.controller.triggerResizeFromViewport(); f.tick(1000); await f.flush(); assert.equal(f.posts.length, 1);
});

test("Settings navigation immediately releases and restores input without waiting for a status poll", async () => {
  const f = await controllerFixture(), notifications = [];
  f.controller.onControlStateChange = state => notifications.push(state.canControl);
  f.controller.setPaused(true);
  assert.deepEqual(notifications, [false]);
  f.controller.setPaused(false);
  assert.deepEqual(notifications, [false, true]);
  f.controller.controlRequestInFlight = true;
  f.controller.setPaused(true); f.controller.setPaused(false);
  assert.deepEqual(notifications.slice(-2), [false, false], "A transaction must keep input disabled on return");
});
test("settings use current aspect, preserve density, and helper errors retain committed display", async () => {
  const f = await controllerFixture(); f.setState({ isResponsive: 0 }); f.controller.setPaused(true);
  await f.controller.updateControlledSettings(draft => { draft.resolutionPreset = 4; });
  assert.equal(f.posts[0].density, 180); assert.ok(Math.min(f.posts[0].width, f.posts[0].height) <= 480);
  assert.ok(Math.abs(f.posts[0].width / f.posts[0].height / (1280 / 768) - 1) < 0.01);
  const old = plain(f.controller.getTouchscreenSize());
  f.controller.apiClient.postDisplayState = async () => { throw new Error("capture failed"); };
  await assert.rejects(f.controller.updateControlledSettings(draft => { draft.resolutionPreset = 0; }), /capture failed/);
  assert.deepEqual(plain(f.controller.getTouchscreenSize()), old);
});
test("enabling dynamic mode in Settings immediately selects a supported ratio", async () => {
  const f = await controllerFixture(); f.setState({ isResponsive: 0 }); f.controller.setPaused(true);
  await f.controller.updateControlledSettings(draft => { draft.isResponsive = 1; });
  assert.equal(f.posts[0].width * 9, f.posts[0].height * 16);
  assert.equal(f.posts[0].isResponsive, 1);
  assert.equal(f.posts[0].density, 180);
});
test("controlled touch does not open for viewers or send unsolicited input on initialization", () => {
  const f = fixture(); const sockets = [], messages = [];
  f.context.ReconnectingWebSocket = function(url) { this.readyState = 1; this.send = msg => messages.push(msg); this.close = () => {};
    sockets.push({ url, socket: this }); };
  const apiClient = { displaySession: { token: "one" }, socketUrl: url => url + "?session=one" };
  const options = { apiClient, canControl: () => false, touchLayer: null, socketUrl: "ws://127.0.0.1/sockets/touchscreen", getTouchscreenSize: () => ({ width: 960, height: 640 }) };
  const controller = new f.window.TAHtml.TouchscreenController(options); controller.initialize(); assert.equal(sockets.length, 0);
  options.canControl = () => true;
  const owner = new f.window.TAHtml.TouchscreenController(options); owner.initialize(); sockets[0].socket.onopen();
  assert.equal(sockets[0].url.endsWith("?session=one"), true); assert.deepEqual(messages, []);
});

test("leaving the preview releases active contacts before clearing local slots, without idle input", () => {
  const f = fixture(), messages = [];
  f.context.ReconnectingWebSocket = function() { this.readyState = 1; this.send = msg => messages.push(msg); this.close = () => {}; };
  const controller = new f.window.TAHtml.TouchscreenController({
    apiClient: { displaySession: { token: "one" }, socketUrl: url => url }, canControl: () => true,
    touchLayer: null, socketUrl: "ws://127.0.0.1/sockets/touchscreen", getTouchscreenSize: () => ({ width: 960, height: 640 }),
  });
  controller.initialize(); controller.touchScreenSocket.onopen();
  controller.slots[0].trackingId = 17;
  controller.resetTouchscreen();
  assert.equal(messages.length, 1);
  assert.ok(messages[0].includes("-1"));
  assert.equal(controller.slots.every(slot => slot.trackingId === -1), true);
  controller.resetTouchscreen(); assert.equal(messages.length, 1);
});

test("back-forward cache restores polling, renews an expired session, and reconnects even in Settings", async () => {
  const f = await controllerFixture(); let renewals = 0, connections = 0, expired = true;
  f.controller.setPaused(true);
  const api = f.controller.apiClient, fetchState = api.fetchDisplayState;
  api.setDisplaySession = () => {};
  api.openDisplaySession = async () => { renewals++; expired = false; };
  api.fetchDisplayState = async () => { if (expired) throw Object.assign(new Error("expired"), { status: 403 }); return fetchState(); };
  f.controller.rendererManager.ensureDisplaySocket = async () => { connections++; };
  for (let i = 0; i < 2; i++) {
    f.dispatch("pagehide", { persisted: true }); assert.equal(f.intervals.size, 0);
    f.dispatch("pageshow", { persisted: true }); await f.flush();
    assert.equal(f.intervals.size, 1); assert.ok(connections > i);
  }
  assert.equal(renewals, 1);
  f.controller.setPaused(false); f.tick(1000); await f.flush(); assert.equal(f.posts.length, 1);
});

test("a late resize response cannot reconnect input or display while the page is suspended", async () => {
  const f = await controllerFixture(); let resolvePost, connections = 0, controlEnabled = false;
  const state = { ...f.controller.remoteDisplayState, width: 390, height: 844 };
  f.controller.apiClient.postDisplayState = () => new Promise(resolve => { resolvePost = resolve; });
  f.controller.rendererManager.ensureDisplaySocket = async () => { connections++; };
  f.controller.onControlStateChange = state => { controlEnabled = state.canControl; };
  f.controller.triggerResize({ width: 390, height: 844 }); f.tick(1000); await f.flush();
  f.dispatch("pagehide", { persisted: true });
  resolvePost(state); await f.flush();
  assert.equal(connections, 0); assert.equal(controlEnabled, false);
});
