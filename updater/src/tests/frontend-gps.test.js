// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error("Set TA_FRONTEND_DIR to patched frontend");
function fixture(owner = true) {
  const sockets = [], positions = [], timers = new Map(), intervals = new Map(), requests = []; let id = 0, allowed = owner;
  class WebSocket {
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  const window = { TAHtml: { log() {} }, WebSocket,
    setTimeout(fn) { timers.set(++id, fn); return id; }, clearTimeout(key) { timers.delete(key); },
    setInterval(fn) { intervals.set(++id, fn); return id; }, clearInterval(key) { intervals.delete(key); } };
  const context = vm.createContext({ window, WebSocket, ReconnectingWebSocket: WebSocket, URL, Date,
    navigator: { geolocation: { getCurrentPosition(ok, error, options) { positions.push({ ok, error, options }); } },
      permissions: { query: async () => ({ state: "granted" }) } },
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true,
      json: async () => ({ browserGpsSupported: 1, browserGpsMock: 1 }) }; } });
  for (const file of ["js/core/shared.js", "js/core/api-client.js", "js/gps/gps-controller.js", "js/settings/settings-controller.js"]) vm.runInContext(fs.readFileSync(path.join(frontend, file), "utf8"), context);
  const api = new window.TAHtml.ApiClient("https://host/api");
  const create = async () => {
    await api.fetchSystemConfiguration(); api.setDisplaySession({ token: "a".repeat(64), csrfToken: "b".repeat(64) });
    return new window.TAHtml.GpsController({ socketUrl: "wss://host/sockets/gps", isEnabled: true, apiClient: api, canControl: () => allowed });
  };
  return { window, api, create, sockets, positions, timers, intervals, requests, revoke: () => { allowed = false; },
    ready(socket = sockets.at(-1)) { socket.readyState = 1; socket.onopen?.(); socket.onmessage?.({ data: '{"version":1,"ready":true,"mock":true}' }); },
    tick() { for (const fn of [...intervals.values()]) fn(); } };
}
test("GPS uses current owner session/CSRF and never prompts a viewer", async () => {
  const viewer = fixture(false), view = await viewer.create(); view.initialize(); await Promise.resolve();
  assert.equal(viewer.sockets.length, 0); assert.equal(viewer.positions.length, 0);
  const f = fixture(), gps = await f.create(); gps.initialize();
  assert.match(f.sockets[0].url, /session=a{64}/); assert.match(f.sockets[0].url, /csrf=b{64}/);
  assert.equal(f.positions.length, 0); f.ready(); await Promise.resolve(); f.tick();
  assert.equal(f.positions.length, 1); f.tick(); assert.equal(f.positions.length, 1, "Only one pending geolocation request");
});
test("GPS preserves original fix time and null motion, and ignores late callbacks after revocation", async () => {
  const f = fixture(), gps = await f.create(); gps.initialize(); f.ready(); await Promise.resolve(); f.tick();
  const position = { timestamp: Date.now() - 1000, coords: { latitude: 0, longitude: 0, accuracy: 5, speed: null, heading: null } };
  f.positions[0].ok(position); assert.equal(f.sockets[0].sent[0].timestamp, position.timestamp);
  assert.equal(f.sockets[0].sent[0].speed, null); assert.equal(f.sockets[0].sent[0].bearing, null);
  f.tick(); f.revoke(); gps.refreshAuthority(); f.positions[1].ok(position);
  assert.equal(f.sockets[0].sent.length, 1); assert.equal(f.sockets[0].readyState, 3);
});
test("GPS disable/re-enable does not overlap an outstanding request or send into a replaced session", async () => {
  const f = fixture(), gps = await f.create(); gps.initialize(); f.ready(); await Promise.resolve(); f.tick();
  gps.setEnabled(false); f.api.setDisplaySession({ token: "c".repeat(64), csrfToken: "d".repeat(64) });
  gps.setEnabled(true); f.ready(); await Promise.resolve(); f.tick(); assert.equal(f.positions.length, 1);
  f.positions[0].ok({ timestamp: Date.now(), coords: { latitude: 0, longitude: 0, accuracy: 5, speed: 0, heading: 0 } });
  assert.equal(f.sockets[1].sent.length, 0); f.tick(); assert.equal(f.positions.length, 2);
  assert.match(f.sockets[1].url, /session=c{64}/);
});
test("GPS settings writes use owner session CSRF rather than the unsupported legacy API", async () => {
  const f = fixture(); await f.create(); await f.api.setGpsState(0);
  assert.equal(f.requests[1].options.headers["X-Display-Session"], "a".repeat(64));
  assert.equal(f.requests[1].options.headers["X-Display-CSRF"], "b".repeat(64));
  assert.equal(f.requests[1].options.body, "0");
});
test("display polling cannot unlock a pending GPS settings save", () => {
  const f = fixture(), inputs = [{ disabled: true }];
  const settings = { activeSectionId: "gps", gpsSettingsPending: true, apiClient: { browserGpsManaged: true, browserGpsSupported: true },
    content: { querySelectorAll: () => inputs }, displayController: { controlRequestInFlight: false } };
  f.window.TAHtml.SettingsController.prototype.refreshDisplayControl.call(settings, { canControl: true, transactionStatus: "idle" });
  assert.equal(inputs[0].disabled, true);
});
test("GPS saves are serialized and a late response cannot override refreshed configuration", async () => {
  const f = fixture(); let handler, release, writes = 0, enabled = false;
  const blocked = new Promise(resolve => { release = resolve; });
  const settings = { activeSectionId: "gps", _renderToken: 1, apiClient: { browserGpsManaged: true, browserGpsSupported: true,
    setGpsState: async () => { writes++; await blocked; } },
    displayController: { controlRequestInFlight: false, remoteDisplayState: { canControl: true, transactionStatus: "idle" } },
    content: { querySelectorAll: () => [], innerHTML: "" }, _appendLoadingState() {}, _appendTile() {}, _appendDescription() {},
    _fetchSystemConfigurationSafe: async () => ({ "persist.tesla-android.gps.is_active": 0 }),
    _createSwitch: (_initial, change) => { handler = change; }, _handleSystemConfigurationChanged: async () => { enabled = false; },
    gpsController: { setEnabled(value) { enabled = value; } }, _renderActiveSection: async () => {},
    _renderErrorState() {}, refreshDisplayControl() {} };
  await f.window.TAHtml.SettingsController.prototype._renderGpsSection.call(settings, 1);
  const first = handler(true), second = handler(false); release(); await Promise.all([first, second]);
  assert.equal(writes, 1); assert.equal(enabled, false); assert.equal(settings.gpsSettingsPending, false);
});
