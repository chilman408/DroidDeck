// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const frontend = process.env.TA_FRONTEND_DIR || process.argv[2];
if (!frontend) throw new Error("Patched frontend path required");
function load(files, context) { for (const file of files) vm.runInContext(fs.readFileSync(path.join(frontend, file), "utf8"), context); }
test("H264 selection is browser-local, capability-gated and explicitly reversible to PNG", () => {
  const window = { isSecureContext: true, VideoDecoder() {}, location: { search: "" } };
  const document = { getElementById: () => ({ transferControlToOffscreen() {} }) };
  const context = vm.createContext({ window, document, console, URL, URLSearchParams });
  load(["js/core/shared.js", "js/display/display-renderer-manager.js"], context);
  const manager = new window.TAHtml.DisplayRendererManager({});
  const state = { renderer: 0, streamFormats: ["png", "h264"] };
  assert.equal(manager._rendererInfoForId(0, state).packetFormat, "scrcpy");
  window.location.search = "?video=png"; assert.equal(manager._rendererInfoForId(0, state).id, 0);
  window.location.search = ""; window.isSecureContext = false; assert.equal(manager._rendererInfoForId(0, state).id, 2);
  window.isSecureContext = true; assert.equal(manager._rendererInfoForId(0, {}).id, 0);
});

test("browsers without WebCodecs use upstream legacy H264, never automatic PNG", () => {
  const window = { isSecureContext: true, location: { search: "" } };
  const context = vm.createContext({ window, document: { getElementById: () => ({ transferControlToOffscreen() {} }) }, console, URL, URLSearchParams });
  load(["js/core/shared.js", "js/display/display-renderer-manager.js"], context);
  const manager = new window.TAHtml.DisplayRendererManager({});
  const info = manager._rendererInfoForId(0, { streamFormats: ["png", "h264"] });
  assert.equal(info.id, 2); assert.equal(info.packetFormat, "scrcpy");
  assert.equal(info.binaryType, "arraybuffer");
});

test("legacy H264 unwraps complete access units and prepends configuration to keyframes", () => {
  const messages = [];
  const canvas = { style: {}, dataset: {}, clientWidth: 762, clientHeight: 720, transferControlToOffscreen: () => ({}) };
  const window = { TAHtmlVideoPacketFormat: "scrcpy", innerWidth: 762, innerHeight: 720, addEventListener() {}, location: { protocol: "https:" } };
  const context = vm.createContext({ window, Uint8Array, ArrayBuffer, DataView, console, URL,
    document: { getElementById: name => name === "canvas" ? canvas : { style: {} } },
    Worker: function() { this.postMessage = message => messages.push(message); } });
  load(["js/display/renderers/h264-broadway/h264Brodway.js"], context);
  context.worker.onmessage({ data: { h264QueueReady: 2 } });
  context.drawDisplayFrame('{"type":"scrcpy-session","width":762,"height":720}');
  const config = [0,0,0,1,0x67,0x42,0xc0,0x28,1,0,0,0,1,0x68,1];
  context.drawDisplayFrame(videoPacket(0x40, 0, config));
  const slices = [0,0,0,1,0x65,1,0,0,0,1,0x65,2];
  context.drawDisplayFrame(videoPacket(0x20, 1234, slices));
  const frame = messages.find(message => message.h264Data);
  assert.deepEqual([...new Uint8Array(frame.h264Data)], [...config, ...slices]);
  const count = messages.length;
  context.drawDisplayFrame(videoPacket(0, 1300, [0,0,0,1,0x41,1]).slice(0, 13));
  assert.equal(messages.length, count, "Malformed packet must not reach the decoder");
});

test("legacy decoder resets for a stream session and crops padded YUV planes to visible size", () => {
  const timers = [], pictures = [], messages = [], decoders = [];
  const context = vm.createContext({ console, Uint8Array, importScripts() {},
    self: { setTimeout: fn => timers.push(fn), postMessage: message => messages.push(message) },
    Decoder: function() { this.decode = () => {}; decoders.push(this); },
    YUVCanvas: function(options) { this.drawNextOutputPicture = picture => pictures.push({ options, picture }); } });
  load(["js/display/renderers/h264-broadway/h264BrodwayWorker.js"], context);
  context.self.onmessage({ data: { canvas: {}, displayWidth: 762, displayHeight: 720, windowWidth: 762, windowHeight: 720 } });
  context.self.onmessage({ data: { streamReset: { width: 762, height: 720 } } });
  assert.equal(decoders.length, 2, "Reconnect must not reuse stale reference pictures");
  decoders[1].onPictureDecoded(new Uint8Array(768 * 720 * 1.5), 768, 720);
  timers.shift()();
  assert.equal(pictures[0].options.width, 762);
  assert.equal(pictures[0].picture.yData.length, 768 * 720);
  assert.equal(pictures[0].picture.yDataPerRow, 768);
  assert.equal(messages[0].width, 762);
  assert.equal(messages[0].height, 720);
});
test("decoder preserves complete AVC access units, key flags and real packet timestamps", () => {
  const decoded = [], configurations = [];
  const context = vm.createContext({ console, Uint8Array, ArrayBuffer, DataView, Number,
    self: { postMessage() {} }, EncodedVideoChunk: function(options) { Object.assign(this, options); },
    fakeDecoder: { state: "configured", decodeQueueSize: 0, reset() {}, configure: value => configurations.push(value), decode: value => decoded.push(value) } });
  load(["js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js"], context);
  vm.runInContext("decoder = fakeDecoder;", context);
  function packet(flags, timestamp, data) { const b = Buffer.alloc(data.length + 12); b.writeBigUInt64BE(BigInt(timestamp)); b[0] |= flags;
    b.writeUInt32BE(data.length, 8); b.set(data, 12); return new Uint8Array(b).buffer; }
  const config = [0,0,0,1,0x67,0x42,0x80,0x1f,1, 0,0,0,1,0x68,1];
  context.handleScrcpyPacket(packet(0x40, 0, config));
  const slices = [0,0,0,1,0x65,1,2, 0,0,0,1,0x65,3,4];
  context.handleScrcpyPacket(packet(0x20, 123456, slices));
  assert.equal(configurations.length, 1); assert.equal(configurations[0].codec, "avc1.42801f");
  assert.equal(decoded.length, 1); assert.equal(decoded[0].timestamp, 123456); assert.equal(decoded[0].type, "key");
  assert.deepEqual([...decoded[0].data], [...config, ...slices]);
  const truncated = packet(0, 133456, [0,0,0,1,0x41,1]).slice(0, 13);
  context.handleScrcpyPacket(truncated); assert.equal(decoded.length, 1);
});

test("rendered dimensions use visible video size rather than AVC macroblock padding", () => {
  const messages = [];
  const context = vm.createContext({ console, self: { postMessage: message => messages.push(message) },
    fakeGL: { bindTexture() {}, texImage2D() {}, clear() {}, drawArrays() {} } });
  load(["js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js"], context);
  vm.runInContext('gl = fakeGL; frameTexture = {}; packetFormat = "scrcpy";', context);
  context.drawImageToCanvas({ codedWidth: 400, codedHeight: 848, displayWidth: 390, displayHeight: 844, close() {} });
  assert.equal(messages[0].width, 390); assert.equal(messages[0].height, 844);
});

test("video failures reconnect H264 without navigation or mode changes", () => {
  let timeout, navigation, reconnects = 0;
  const canvas = { style: {}, dataset: {}, clientWidth: 976, clientHeight: 720, transferControlToOffscreen: () => ({}) };
  const window = { TAHtmlVideoPacketFormat: "scrcpy", innerWidth: 976, innerHeight: 720, addEventListener() {},
    requestDisplayStreamReconnect: () => { reconnects++; },
    location: { protocol: "https:", href: "https://192.168.2.90:8443/?test=keep#home", replace: url => { navigation = url; } } };
  const context = vm.createContext({ window, document: { getElementById: id => id === "canvas" ? canvas : { style: {} } },
    console, URL, Number, ArrayBuffer, clearTimeout() {}, setTimeout: (callback, delay) => { timeout = { callback, delay }; },
    Worker: function() { this.postMessage = () => {}; } });
  load(["js/display/renderers/h264-webcodecs/h264WebCodecs.js"], context);
  assert.ok(timeout.delay > 20000);
  context.worker.onmessage({ data: { frameRendered: true, width: 976, height: 720 } });
  assert.equal(canvas.dataset.videoFrames, "1");
  context.worker.onmessage({ data: { error: "unsupported codec" } });
  assert.equal(navigation, undefined);
  const recovery = timeout;
  context.drawDisplayFrame('{"type":"scrcpy-session","width":976,"height":720}');
  context.worker.onmessage({ data: { error: "second decoder error before reconnect" } });
  context.worker.onmessage({ data: { frameRendered: true, width: 976, height: 720 } });
  assert.equal(canvas.dataset.videoState, "reconnecting", "A pre-error queued frame cannot cancel decoder recovery");
  recovery.callback();
  assert.equal(reconnects, 1);
  assert.equal(window.TAHtmlVideoPacketFormat, "scrcpy");
});

function workerFixture() {
  const resized = [], configurations = [], decoded = [], draws = [];
  const canvas = { _width: 1280, _height: 720, get width() { return this._width; }, get height() { return this._height; },
    set width(value) { this._width = value; resized.push(["width", value]); },
    set height(value) { this._height = value; resized.push(["height", value]); } };
  const context = vm.createContext({ console, Uint8Array, ArrayBuffer, DataView, Number,
    self: { postMessage() {} }, EncodedVideoChunk: function(value) { Object.assign(this, value); },
    fakeCanvas: canvas, fakeGL: { viewport() {}, bindTexture() {}, drawArrays: () => draws.push(true) },
    fakeDecoder: { state: "configured", decodeQueueSize: 0, reset() {}, configure: value => configurations.push(value), decode: value => decoded.push(value) } });
  load(["js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js"], context);
  vm.runInContext('offscreenCanvas = fakeCanvas; gl = fakeGL; decoder = fakeDecoder; frameTexture = {}; spsPps = new Uint8Array([0,0,0,1,0x67,0x42,0x80,0x1f]); dropDeltaUntilKeyframe = false;', context);
  return { context, resized, configurations, decoded, draws };
}

test("unchanged status polling preserves rendered pixels and decoder state", () => {
  const { context, resized, configurations } = workerFixture();
  context.applyRuntimeConfig({ displayWidth: 1280, displayHeight: 720, windowWidth: 1280, windowHeight: 720 });
  assert.equal(resized.length, 0, "Unchanged canvas dimensions must not erase its pixels");
  assert.equal(configurations.length, 0);
});

test("presentation resize repaints retained texture without waiting for a new keyframe", () => {
  const { context, configurations, draws } = workerFixture();
  context.applyRuntimeConfig({ windowWidth: 976, windowHeight: 549 });
  assert.equal(configurations.length, 0, "CSS layout is not a new encoded stream");
  assert.equal(draws.length, 1, "An idle Android screen must repaint without a fresh frame");
  assert.equal(vm.runInContext("dropDeltaUntilKeyframe", context), false);
});

test("encoded size change still resets decoding until a keyframe", () => {
  const { context, configurations } = workerFixture();
  context.applyRuntimeConfig({ displayWidth: 390, displayHeight: 844 });
  assert.equal(configurations.length, 1);
  assert.equal(vm.runInContext("dropDeltaUntilKeyframe", context), true);
});

test("scrcpy encoded geometry is authoritative rather than rounded logical control dimensions", () => {
  const { context, configurations } = workerFixture();
  vm.runInContext('packetFormat = "scrcpy";', context);
  context.applyRuntimeConfig({ displayWidth: 1279, displayHeight: 720 });
  assert.equal(configurations.length, 0);
  assert.equal(vm.runInContext("dropDeltaUntilKeyframe", context), false);
  context.applyRuntimeConfig({ displayWidth: 390, displayHeight: 844 }, true);
  assert.equal(configurations.length, 1);
});

test("new scrcpy session replaces an errored closed decoder", () => {
  const { context } = workerFixture(); let decoders = 0;
  context.VideoDecoder = function(options) { decoders++; this.state = "unconfigured"; this.options = options; };
  vm.runInContext('decoder.state = "closed";', context);
  context.self.onmessage({ data: { scrcpySession: { width: 980, height: 720 } } });
  assert.equal(decoders, 1);
});

function videoPacket(flags, timestamp, payload) {
  const buffer = Buffer.alloc(payload.length + 12); buffer.writeBigUInt64BE(BigInt(timestamp));
  buffer[0] |= flags; buffer.writeUInt32BE(payload.length, 8); buffer.set(payload, 12);
  return new Uint8Array(buffer).buffer;
}

test("cached GOP burst drains in decode order without dropping dependent delta frames", () => {
  const f = workerFixture();
  f.context.fakeDecoder.decode = value => { f.decoded.push(value); f.context.fakeDecoder.decodeQueueSize++; };
  f.context.handleScrcpyPacket(videoPacket(0x40, 0, [0,0,0,1,0x67,0x42,0x80,0x1f,1,0,0,0,1,0x68,1]));
  for (let i = 0; i < 17; i++) f.context.handleScrcpyPacket(videoPacket(i === 0 ? 0x20 : 0, i * 33333, [0,0,0,1,i === 0 ? 0x65 : 0x41,1]));
  assert.ok(f.decoded.length <= 8, "Hardware queue must stay bounded");
  while (f.decoded.length < 17) {
    const before = f.decoded.length; f.context.fakeDecoder.decodeQueueSize = 0;
    vm.runInContext("scrcpyInFlight = 0;", f.context);
    f.context.drainScrcpyPackets();
    assert.ok(f.decoded.length > before, "Every dependent packet must eventually decode");
  }
  assert.deepEqual(f.decoded.map(frame => frame.timestamp), Array.from({ length: 17 }, (_, i) => i * 33333));
  assert.equal(vm.runInContext("dropDeltaUntilKeyframe", f.context), false);
});

test("codec changes cancel old requests before resetting new decoder accounting", () => {
  const f = workerFixture(), steps = [];
  f.context.fakeDecoder.reset = () => steps.push("reset");
  f.context.fakeDecoder.configure = () => steps.push("configure");
  f.context.handleScrcpyPacket(videoPacket(0x40, 0, [0,0,0,1,0x67,0x42,0x80,0x1f,1,0,0,0,1,0x68,1]));
  assert.deepEqual(steps, ["reset", "configure"]);
});

test("hidden Settings view preserves the last visible presentation geometry", () => {
  const updates = [];
  const window = { innerWidth: 1920, innerHeight: 1080, updateDisplayRendererConfig: value => updates.push(value) };
  const context = vm.createContext({ window, console });
  load(["js/core/shared.js", "js/display/display-renderer-manager.js"], context);
  const manager = new window.TAHtml.DisplayRendererManager({ displayRoot: { clientWidth: 0, clientHeight: 0 } });
  manager.lastDisplaySize = { displayWidth: 976, displayHeight: 720, windowWidth: 976, windowHeight: 720 };
  manager.updateRendererRuntimeConfig();
  assert.equal(updates[0].windowWidth, 976);
  assert.equal(updates[0].windowHeight, 720);
});

test("idle rendered screen keeps H264 while outstanding undecoded packets retain a bounded watchdog", () => {
  const timers = new Map(); let id = 0;
  const canvas = { style: {}, dataset: {}, clientWidth: 980, clientHeight: 720, transferControlToOffscreen: () => ({}) };
  const window = { TAHtmlVideoPacketFormat: "scrcpy", innerWidth: 980, innerHeight: 720, addEventListener() {},
    location: { protocol: "https:", href: "https://example.test/", replace() {} } };
  const context = vm.createContext({ window, document: { getElementById: name => name === "canvas" ? canvas : { style: {} } },
    console, URL, Number, ArrayBuffer, clearTimeout: token => timers.delete(token),
    setTimeout: (callback, delay) => { timers.set(++id, { callback, delay }); return id; },
    Worker: function() { this.postMessage = () => {}; } });
  load(["js/display/renderers/h264-webcodecs/h264WebCodecs.js"], context);
  assert.equal(timers.size, 1, "Initial stream must have a startup deadline");
  context.worker.onmessage({ data: { frameRendered: true, width: 980, height: 720 } });
  assert.equal(timers.size, 0, "An unchanged Android screen is not a stalled decoder");
  context.drawDisplayFrame(new ArrayBuffer(24));
  assert.equal(timers.size, 1, "Packets without rendered output must still time out");
  const firstTimer = [...timers.keys()][0];
  context.drawDisplayFrame(new ArrayBuffer(24));
  assert.equal([...timers.keys()][0], firstTimer, "Continuous undecoded traffic cannot extend the deadline forever");
  context.worker.onmessage({ data: { frameRendered: true, width: 980, height: 720, pendingDecode: 3 } });
  assert.equal(timers.size, 1, "One completed frame cannot hide outstanding decoding that later stalls");
  context.worker.onmessage({ data: { frameRendered: true, width: 980, height: 720 } });
  assert.equal(timers.size, 0);
});
