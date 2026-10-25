// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error("Set TA_FRONTEND_DIR to the patched frontend");
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test('role radios reflect a viewer role change on polling even when generation is unchanged', () => {
  const f = fixture(), proto = f.window.TAHtml.SettingsController.prototype;
  const primary = { value: 'primary', checked: true, closest: () => ({}) }, secondary = { value: 'rear', checked: false, closest: () => ({}) };
  const settings = Object.assign(Object.create(proto), { activeSectionId: 'display', _displayControlGeneration: 0, displayController: f.controller,
    content: { querySelectorAll: selector => selector.includes('role') ? [primary, secondary] : [] } });
  settings.refreshDisplayControl({ ...f.state(), displayRole: 'rear', canControl: false });
  assert.equal(primary.checked, false); assert.equal(secondary.checked, true); assert.equal(primary.disabled, false);
});

test('authoritative assigned or swapped roles never get reasserted by an old local preference', async () => {
  const f = fixture(); f.controller.controlledPrimaryPreference = true;
  f.patch({ displayRoleClaimSupported: 1, displayRole: 'rear', canControl: false });
  await f.controller._registerStoredRole(f.state());
  assert.equal(f.writes.length, 0); assert.equal(f.controller.controlledPrimaryPreference, false);
});

test('cached primary preference cannot register before a role-capable display connection receives its role', async () => {
  const f = fixture(); f.patch({ displayRoleClaimSupported: 1, displayRole: null, canControl: false });
  for (const key of f.window.TAHtml.constants.DISPLAY_PREF_KEYS) f.stored.set(key, 'true');
  await f.controller._registerStoredRole(f.state());
  assert.equal(f.writes.length, 0);
  f.patch({ displayRole: 'rear' }); await f.controller._registerStoredRole(f.state());
  assert.equal(f.writes.length, 0); assert.equal(f.controller.controlledPrimaryPreference, false);
});

test('selecting the preferred role sends one explicit generation-fenced claim, ordinary polling sends none', async () => {
  const f = fixture(), requests = []; f.patch({ displayRoleClaimSupported: 1, displayRole: 'rear', generation: 7, canControl: false });
  f.api.postDisplayRole = async (role, options) => { requests.push({ role, options: plain(options) }); f.patch({ displayRole: role, canControl: true }); return f.state(); };
  await f.controller.selectPrimaryDisplay(true);
  assert.deepEqual(requests, [{ role: 'primary', options: { claim: true, generation: 7 } }]);
  await f.controller.refreshControlState(); assert.equal(requests.length, 1);
});
function fixture() {
  const stored = new Map(), timers = new Map(); let id = 0;
  const window = { location: { origin: "http://localhost", protocol: "http:", host: "localhost" },
    innerWidth: 1280, innerHeight: 720, requestAnimationFrame() {}, addEventListener() {},
    setTimeout(fn) { timers.set(++id, fn); return id; }, clearTimeout(key) { timers.delete(key); },
    setInterval() { return ++id; }, clearInterval() {},
    localStorage: { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) } };
  const document = { hidden: false, activeElement: null,
    getElementById: () => ({ getBoundingClientRect: () => ({ width: 1280, height: 720 }) }) };
  const context = vm.createContext({ window, document, console, URL });
  for (const file of ["js/core/shared.js", "js/core/api-client.js", "js/display/display-controller.js", "js/settings/settings-controller.js"]) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), "utf8"), context);
  }
  let state = { width: 1280, height: 720, density: 180, generation: 0, resolutionPreset: 1,
    isResponsive: 1, displayControlSupported: 1, canControl: true, canResize: true,
    transactionStatus: "idle", rearDisplayControlSupported: 1, isRearDisplayEnabled: 0,
    isRearDisplayPrioritised: 0, rearDisplayRevision: 0, displayRole: null };
  const writes = [], notices = [], connections = [];
  const api = { fetchDisplayState: async () => ({ ...state }),
    postDisplayRole: async role => { writes.push({ role }); state = { ...state, displayRole: role }; return { ...state }; },
    postRearDisplaySettings: async payload => { writes.push(plain(payload)); state = { ...state, generation: state.generation + 1,
      rearDisplayRevision: payload.revision + 1, isRearDisplayEnabled: Number(payload.enabled),
      isRearDisplayPrioritised: Number(payload.prioritised) }; return { ...state }; },
    postDisplayState: async payload => { writes.push(plain(payload)); return { ...state, ...payload }; } };
  const controller = new window.TAHtml.DisplayController({ apiClient: api,
    rendererManager: { applyDisplaySize() {}, closeDisplaySocket() {}, ensureDisplaySocket: async () => connections.push(true) },
    flavor: { displayWebSocket: "ws://localhost/sockets/display" } });
  controller.remoteDisplayState = { ...state };
  controller.display = { viewSize: { width: 1280, height: 720 }, adjustedSize: { width: 1280, height: 720 } };
  controller.onControlStateChange = value => notices.push(plain(value));
  controller._showDisplayTypeSelectionDialog = () => { controller.dialogShown = true; };
  controller._hideDisplayTypeSelectionDialog = () => { controller.dialogShown = false; };
  return { window, api, controller, writes, notices, stored, timers, connections,
    state: () => state, patch: value => { state = { ...state, ...value }; },
    flush: () => new Promise(resolve => setImmediate(resolve)) };
}

test("rear role and preferences use separate session-CSRF endpoints without display mutation", async () => {
  const f = fixture(), api = new f.window.TAHtml.ApiClient("/api"), requests = [];
  await assert.rejects(api.postDisplayRole("rear"), /session/);
  api.setDisplaySession({ token: "a".repeat(64), csrfToken: "b".repeat(64) });
  api._fetchJson = async (url, options) => { requests.push({ url, options }); return { displayRole: "rear" }; };
  await api.postDisplayRole("rear");
  await api.postRearDisplaySettings({ revision: 0, enabled: true, prioritised: false });
  assert.deepEqual(requests.map(item => item.url), ["/api/displayRole", "/api/rearDisplayState"]);
  for (const { options } of requests) {
    assert.equal(options.headers["X-Display-Session"], "a".repeat(64));
    assert.equal(options.headers["X-Display-CSRF"], "b".repeat(64));
  }
  assert.deepEqual(JSON.parse(requests[0].options.body), { role: "rear" });
});

test("unassigned browsers prompt only when rear support is enabled, without implicit main assignment", async () => {
  const f = fixture(); await f.controller._registerStoredRole(f.state());
  assert.equal(f.controller.dialogShown, undefined); assert.equal(f.writes.length, 0);
  f.patch({ isRearDisplayEnabled: 1 }); await f.controller._registerStoredRole(f.state());
  assert.equal(f.controller.dialogShown, true); assert.equal(f.writes.length, 0);
  f.controller.isPaused = true; f.controller.dialogShown = false;
  await f.controller._registerStoredRole(f.state()); assert.equal(f.controller.dialogShown, false);
});

test("transient role failure during startup retains view-only streaming and polling until registration succeeds", async () => {
  for (const status of [409, 503]) {
    const f = fixture(); let failing = true;
    for (const key of f.window.TAHtml.constants.DISPLAY_PREF_KEYS) f.stored.set(key, "true");
    f.api.postDisplayRole = async role => {
      if (failing) throw Object.assign(new Error("Role temporarily unavailable"), { status });
      f.patch({ displayRole: role }); return f.state();
    };
    await f.controller.initialize();
    assert.ok(f.connections.length > 0, "A role failure must not abandon the H264 stream");
    assert.ok(f.controller.controlPollTimer != null, "Authority registration must have a retry path");
    assert.equal(f.controller.remoteDisplayState.canControl, false);
    assert.equal(f.controller.remoteDisplayState.canResize, false);
    await f.controller.refreshControlState();
    assert.equal(f.controller.remoteDisplayState.canControl, false);
    failing = false;
    await f.controller.refreshControlState();
    assert.equal(f.controller.remoteDisplayState.displayRole, "primary");
    assert.equal(f.controller.remoteDisplayState.canControl, true);
    assert.equal(f.controller.remoteDisplayState.canResize, true);
    assert.equal(f.controller.roleRegistration, null);
  }
});

test("startup never swallows role authentication, invalid request or unexpected failures", async () => {
  for (const status of [400, 401, 403, undefined]) {
    const f = fixture();
    for (const key of f.window.TAHtml.constants.DISPLAY_PREF_KEYS) f.stored.set(key, "true");
    f.api.postDisplayRole = async () => { throw Object.assign(new Error("Role failed"), { status }); };
    await assert.rejects(f.controller.initialize(), /Role failed/);
    assert.equal(f.connections.length, 0);
    assert.equal(f.controller.controlPollTimer, undefined);
  }
});

test("accepted role is tab-local and restored in renewed sessions even if another tab changes storage", async () => {
  const f = fixture(); await f.controller.selectPrimaryDisplay(false);
  assert.equal(f.controller.remoteDisplayState.displayRole, "rear");
  for (const key of f.window.TAHtml.constants.DISPLAY_PREF_KEYS) f.stored.set(key, "true");
  f.patch({ displayRole: null });
  await f.controller._registerStoredRole(f.state());
  assert.equal(f.writes.at(-1).role, "rear");
  assert.equal(f.controller.controlledPrimaryPreference, false);
});

test("blocked storage does not discard a role accepted by the server", async () => {
  const f = fixture(); f.window.localStorage.setItem = () => { throw new Error("blocked"); };
  await f.controller.selectPrimaryDisplay(false);
  assert.equal(f.controller.controlledPrimaryPreference, false);
  f.patch({ displayRole: null }); await f.controller._registerStoredRole(f.state());
  assert.equal(f.writes.at(-1).role, "rear");
});

test("viewers may choose local roles but cannot change shared preferences", async () => {
  const f = fixture(); f.patch({ canControl: false, canResize: false });
  await f.controller.selectPrimaryDisplay(false);
  await assert.rejects(f.controller.updateRearSettings({ revision: 0, enabled: true, prioritised: true }), /view-only/);
  assert.deepEqual(f.writes, [{ role: "rear" }]);
});

test("nonpreferred owner cannot submit either automatic or manual geometry changes", async () => {
  const f = fixture(); f.patch({ canResize: false });
  f.controller.resize.pendingViewSize = { width: 800, height: 600 };
  await f.controller._sendControlledResize(); assert.equal(f.writes.length, 0);
  await assert.rejects(f.controller.updateControlledSettings(draft => { draft.resolutionPreset = 2; }), /view-only/);
  assert.equal(f.writes.length, 0);
});

test("role selection cannot overlap shared saves, and releases input before writing", async () => {
  const f = fixture(), hold = deferred();
  f.api.postRearDisplaySettings = async payload => { f.writes.push(plain(payload)); await hold.promise; return f.state(); };
  const saving = f.controller.updateRearSettings({ revision: 0, enabled: true, prioritised: false });
  await f.flush(); assert.equal(f.notices.at(-1).canControl, false);
  await assert.rejects(f.controller.selectPrimaryDisplay(false), /busy/);
  assert.equal(f.writes.length, 1); hold.resolve(); await saving;
  assert.equal(f.controller.controlRequestInFlight, false);
});

test("a late status poll cannot overwrite a freshly selected role or reconnect stale input", async () => {
  const f = fixture(), hold = deferred(), stale = { ...f.state() }; let calls = 0;
  f.api.fetchDisplayState = async () => ++calls === 1 ? hold.promise : f.state();
  const polling = f.controller.refreshControlState();
  await f.controller.selectPrimaryDisplay(false);
  const notices = f.notices.length, connections = f.connections.length; hold.resolve(stale); await polling;
  assert.equal(f.controller.remoteDisplayState.displayRole, "rear");
  assert.equal(f.notices.length, notices); assert.equal(f.connections.length, connections);
});

test("selection waits for an already-started registration instead of reversing request order", async () => {
  const f = fixture(), hold = deferred(); let first = true;
  f.controller.controlledPrimaryPreference = true;
  f.api.postDisplayRole = async role => { f.writes.push({ role }); if (first) { first = false; await hold.promise; }
    f.patch({ displayRole: role }); return f.state(); };
  const polling = f.controller.refreshControlState(); await f.flush();
  const selecting = f.controller.selectPrimaryDisplay(false); await f.flush();
  assert.deepEqual(f.writes, [{ role: "primary" }]);
  hold.resolve(); await Promise.all([polling, selecting]);
  assert.deepEqual(f.writes, [{ role: "primary" }, { role: "rear" }]);
  assert.equal(f.controller.remoteDisplayState.displayRole, "rear");
});

test("role-only and revision-only polls refresh the authoritative local snapshot", async () => {
  const f = fixture(); f.patch({ displayRole: "rear", canResize: false, rearDisplayRevision: 2 });
  await f.controller.refreshControlState();
  assert.equal(f.controller.remoteDisplayState.canResize, false);
  assert.equal(f.controller.remoteDisplayState.rearDisplayRevision, 2);
  assert.equal(f.controller.remoteDisplayState.displayRole, "rear");
});

test("Display includes secondary controls using the same authoritative state fetch", async () => {
  const f = fixture(), proto = f.window.TAHtml.SettingsController.prototype; let roles = 0;
  const settings = Object.assign(Object.create(proto), { _renderToken: 1, activeSectionId: "display",
    apiClient: { fetchDisplayState: async () => f.state() }, displayController: f.controller,
    _appendLoadingState() {}, _appendDivider() {}, _appendTile() {}, _createSelect: () => ({ dataset: {} }),
    _createSwitch: () => ({ dataset: {} }), _createTrailingText: value => value,
    _renderControlledRearDisplay() { roles++; }, content: { innerHTML: "", querySelectorAll: () => [] } });
  await settings._renderDisplaySection(1); assert.equal(roles, 1);
});

test("rear Settings keeps role selectable for viewers while shared switches stay disabled", () => {
  const f = fixture(), proto = f.window.TAHtml.SettingsController.prototype, role = {}, shared = {};
  const settings = Object.assign(Object.create(proto), { activeSectionId: "display", _rearControlGeneration: 0,
    _displayControlGeneration: 0, displayController: f.controller, content: {
      querySelectorAll: selector => selector.includes("role") ? [role] : selector.includes("shared") ? [shared] : [] } });
  settings.refreshDisplayControl({ ...f.state(), canControl: false });
  assert.equal(role.disabled, false); assert.equal(shared.disabled, true);
  settings.refreshDisplayControl({ ...f.state(), transactionStatus: "transferring" });
  assert.equal(role.disabled, true); assert.equal(shared.disabled, true);
});

test("nonpreferred owner retains encoder controls while resolution controls remain disabled", () => {
  const f = fixture(), proto = f.window.TAHtml.SettingsController.prototype;
  const size = { closest: selector => selector === "[data-display-size]" ? {} : null }, video = {};
  const settings = Object.assign(Object.create(proto), { activeSectionId: "display", _displayControlGeneration: 0, displayController: f.controller,
    content: { querySelectorAll: selector => selector === "button, input" ? [size, video] : [] } });
  proto.refreshDisplayControl.call(settings, { ...f.state(), canResize: false });
  assert.equal(size.disabled, true); assert.equal(video.disabled, false);
});

test("failed role changes restore controls without persisting the rejected preference", async () => {
  const f = fixture(); f.controller.controlledPrimaryPreference = true; f.patch({ displayRole: "primary" });
  f.api.postDisplayRole = async () => { throw new Error("retirement failed"); };
  await assert.rejects(f.controller.selectPrimaryDisplay(false), /retirement failed/);
  assert.equal(f.controller.controlRequestInFlight, false);
  assert.equal(f.controller.controlledPrimaryPreference, true); assert.equal(f.stored.size, 0);
});

test("the sidebar has one Display section and old Rear Display navigation maps to it", async () => {
  const f = fixture(), Class = f.window.TAHtml.SettingsController;
  Class.prototype._bindBannerEvents = Class.prototype._bindGlobalEvents = () => {};
  const settings = new Class({ displayController: f.controller }); let renders = 0;
  settings._renderSidebar = settings._refreshNetworkBanner = () => {};
  settings._renderActiveSection = async () => { renders++; };
  assert.deepEqual(Array.from(settings.sections.filter(section => /display/i.test(section.id)), section => section.title), ['Display']);
  await settings.setActiveSection('rearDisplay'); assert.equal(settings.activeSectionId, 'display'); assert.equal(renders, 1);
});

test("merged role controls cannot acquire shared authority or overlap an encoder save", () => {
  const f = fixture(), proto = f.window.TAHtml.SettingsController.prototype;
  const role = { closest: selector => selector.includes('data-rear-role') ? {} : null };
  const shared = { closest: selector => selector.includes('data-rear-shared') ? {} : null };
  const video = {}, renderer = { closest: () => ({}) };
  const settings = Object.assign(Object.create(proto), { activeSectionId: 'display', _displayControlGeneration: 0,
    displayController: f.controller, content: { querySelectorAll: selector =>
      selector.includes('role') ? [role] : selector.includes('shared') ? [shared] : [role, shared, video, renderer] } });
  settings.refreshDisplayControl({ ...f.state(), canControl: false });
  assert.equal(role.disabled, false); assert.equal(shared.disabled, true); assert.equal(video.disabled, true);
  settings.videoSettingsPending = true;
  settings.refreshDisplayControl(f.state());
  assert.equal(role.disabled, true); assert.equal(shared.disabled, true); assert.equal(video.disabled, true);
  assert.equal(renderer.disabled, undefined);
});

test("detached and busy secondary-role callbacks never write; completed role changes refresh Display", async () => {
  const f = fixture(), proto = f.window.TAHtml.SettingsController.prototype; let renders = 0;
  const settings = Object.assign(Object.create(proto), { activeSectionId: 'audio', displayController: f.controller,
    refreshDisplayControl() {}, _renderActiveSection: async () => { renders++; } });
  await settings._changeControlledRear(f.state(), { role: 'rear' }); assert.equal(f.writes.length, 0);
  settings.activeSectionId = 'display'; settings.videoSettingsPending = true;
  await settings._changeControlledRear(f.state(), { role: 'rear' }); assert.equal(f.writes.length, 0);
  settings.videoSettingsPending = false; f.controller.controlRequestInFlight = true;
  await settings._changeControlledRear(f.state(), { role: 'rear' }); assert.equal(f.writes.length, 0);
  f.controller.controlRequestInFlight = false;
  await settings._changeControlledRear(f.state(), { role: 'rear' });
  assert.deepEqual(f.writes, [{ role: 'rear' }]); assert.equal(renders, 1); assert.equal(settings.rearSettingsPending, false);
});
