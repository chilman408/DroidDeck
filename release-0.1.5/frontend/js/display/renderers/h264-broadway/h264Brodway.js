var img = document.getElementById("image");
var canvas = document.getElementById("canvas");
img.style.display = "none";
canvas.style.display = "block";

function toSafeNumber(value, fallback) {
  var parsed = Number(value);
  if (Number.isFinite(parsed)) return Math.max(1, Math.round(parsed));
  return Math.max(1, Math.round(fallback));
}

function resolveInitialDisplayWidth() {
  var candidate = typeof displayWidth !== "undefined" ? displayWidth : window.displayWidth;
  return toSafeNumber(candidate, 1280);
}

function resolveInitialDisplayHeight() {
  var candidate = typeof displayHeight !== "undefined" ? displayHeight : window.displayHeight;
  return toSafeNumber(candidate, 720);
}

function resolveViewportSize() {
  return {
    width: toSafeNumber(canvas.clientWidth || window.innerWidth, window.innerWidth || 1),
    height: toSafeNumber(canvas.clientHeight || window.innerHeight, window.innerHeight || 1),
  };
}

function buildRuntimeConfig(overrides) {
  var viewport = resolveViewportSize();
  var config = overrides || {};
  return {
    displayWidth: toSafeNumber(config.displayWidth, resolveInitialDisplayWidth()),
    displayHeight: toSafeNumber(config.displayHeight, resolveInitialDisplayHeight()),
    windowWidth: toSafeNumber(config.windowWidth, viewport.width),
    windowHeight: toSafeNumber(config.windowHeight, viewport.height),
  };
}

function applyCanvasViewport(runtimeConfig) {
  canvas.style.width = String(runtimeConfig.windowWidth) + "px";
  canvas.style.height = String(runtimeConfig.windowHeight) + "px";
}

function readTextFromPath(path) {
  try {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", path, false);
    xhr.send();
    if (xhr.status === 0 || (xhr.status >= 200 && xhr.status < 300)) {
      return xhr.responseText;
    }
  } catch (_error) {
    // no-op
  }
  return null;
}

function createWorkerWithFileFallback(workerPath, options) {
  try {
    return new Worker(workerPath);
  } catch (primaryError) {
    if (window.location.protocol !== "file:") {
      throw primaryError;
    }

    var workerSource = readTextFromPath(workerPath);
    if (workerSource) {
      var dependencies = (options && options.dependencies) || [];
      if (dependencies.length > 0) {
        var dependencySources = [];
        for (var index = 0; index < dependencies.length; index += 1) {
          var dependencySource = readTextFromPath(dependencies[index]);
          if (!dependencySource) {
            throw primaryError;
          }
          dependencySources.push(dependencySource);
        }

        workerSource = workerSource.replace(/^\s*importScripts\([\s\S]*?\);\s*/m, "");
        workerSource = dependencySources.join("\n;\n") + "\n;\n" + workerSource;
      }

      var blobUrl = URL.createObjectURL(
        new Blob([workerSource], { type: "text/javascript" }),
      );
      var worker = new Worker(blobUrl);
      worker.__blobUrl = blobUrl;
      return worker;
    }

    var absoluteWorkerUrl = new URL(workerPath, window.location.href).href;
    var bootstrapSource = "importScripts(" + JSON.stringify(absoluteWorkerUrl) + ");";
    var bootstrapBlobUrl = URL.createObjectURL(
      new Blob([bootstrapSource], { type: "text/javascript" }),
    );
    var bootstrapWorker = new Worker(bootstrapBlobUrl);
    bootstrapWorker.__blobUrl = bootstrapBlobUrl;
    return bootstrapWorker;
  }
}

var worker = null;
try {
  worker = createWorkerWithFileFallback("./js/display/renderers/h264-broadway/h264BrodwayWorker.js?taQueue=2", {
    dependencies: [
      "./js/display/renderers/h264-broadway/Decoder.js",
      "./js/display/renderers/h264-broadway/YUVCanvas.js",
    ],
  });
} catch (error) {
  console.error("Failed to initialize h264Brodway worker", error);
}
const offscreenCanvas = canvas.transferControlToOffscreen();
const initialRuntimeConfig = buildRuntimeConfig();

window.displayWidth = initialRuntimeConfig.displayWidth;
window.displayHeight = initialRuntimeConfig.displayHeight;
applyCanvasViewport(initialRuntimeConfig);

if (worker) {
  worker.postMessage(
    {
      canvas: offscreenCanvas,
      displayWidth: initialRuntimeConfig.displayWidth,
      displayHeight: initialRuntimeConfig.displayHeight,
      windowWidth: initialRuntimeConfig.windowWidth,
      windowHeight: initialRuntimeConfig.windowHeight,
    },
    [offscreenCanvas],
  );
}

window.updateDisplayRendererConfig = function updateDisplayRendererConfig(nextConfig) {
  var runtimeConfig = buildRuntimeConfig(nextConfig || {});
  window.displayWidth = runtimeConfig.displayWidth;
  window.displayHeight = runtimeConfig.displayHeight;
  applyCanvasViewport(runtimeConfig);
  if (worker) {
    worker.postMessage({
      config: runtimeConfig,
    });
  }
};

window.addEventListener("resize", function () {
  if (typeof window.updateDisplayRendererConfig === "function") {
    window.updateDisplayRendererConfig();
  }
});

if (worker) {
  worker.onerror = worker.onmessageerror = function (event) {
    event.preventDefault?.(); failLegacyWorker("worker-error");
  };
  worker.onmessage = function (event) {
    if (legacyFailed) return;
    if (event.data.h264QueueReady === 2) { legacyWorkerReady = true; drainLegacyQueue(); }
    if (event.data.h264Processed === true) {
      var key = event.data.h264Epoch + ":" + event.data.h264Ticket;
      if (legacyInFlight.has(key)) {
        clearTimeout(legacyInFlight.get(key)); legacyInFlight.delete(key); drainLegacyQueue();
      }
    }
    if (event.data.frameRendered) {
      canvas.dataset.videoFrames = String(Number(canvas.dataset.videoFrames || 0) + 1);
      canvas.dataset.videoState = "ready";
      canvas.dataset.videoWidth = String(event.data.width);
      canvas.dataset.videoHeight = String(event.data.height);
      canvas.dataset.videoDecoder = "legacy";
    }
  };
}

window.addEventListener("beforeunload", function () {
  syncVideoQueue?.close();
  if (worker && worker.__blobUrl) {
    URL.revokeObjectURL(worker.__blobUrl);
  }
});

var legacyConfig = null;
var legacyEpoch = 0;
var legacyTicket = 0;
var legacyInFlight = new Map();
var legacyFailed = false;
var legacyAwaitKeyframe = true;
var legacyWorkerReady = false;
var legacyQueue = [];
var legacyQueueBytes = 0;
var legacyPendingReset = null;
function drainLegacyQueue() {
  if (!legacyWorkerReady || legacyFailed) return;
  if (legacyPendingReset) {
    if (legacyInFlight.size) return;
    worker.postMessage({ streamReset: legacyPendingReset }); legacyPendingReset = null;
  }
  while (legacyInFlight.size < 2 && legacyQueue.length) {
    var buffer = legacyQueue.shift(); legacyQueueBytes -= buffer.byteLength;
    var ticket = ++legacyTicket;
    var key = legacyEpoch + ":" + ticket;
    legacyInFlight.set(key, typeof setTimeout === "function" ? setTimeout(function () {
      failLegacyWorker("worker-stalled");
    }, 5000) : null);
    worker.postMessage({ h264Data: buffer, h264Epoch: legacyEpoch, h264Ticket: ticket }, [buffer]);
  }
}
function failLegacyWorker(state) {
  if (legacyFailed) return;
  legacyFailed = true; legacyWorkerReady = false;
  syncVideoQueue?.close();
  legacyQueue = []; legacyQueueBytes = 0; legacyPendingReset = null;
  legacyInFlight.forEach(function (timer) { clearTimeout(timer); }); legacyInFlight.clear();
  worker?.terminate(); canvas.dataset.videoState = state;
  // One cache-busted reload, never an incompatible decoder or reload loop.
  if (typeof window.location?.href === "string") {
    var reload = new URL(window.location.href);
    if (reload.searchParams.get("taLegacyQueue") !== "2") {
      reload.searchParams.set("taLegacyQueue", "2"); window.location.replace(reload.href);
    }
  }
}
if (typeof setTimeout === "function") setTimeout(function () {
  if (!legacyWorkerReady) failLegacyWorker("worker-protocol-error");
}, 5000);
var syncVideoQueue = typeof window.createAVSyncVideoQueue === "function" ? window.createAVSyncVideoQueue(
  consumeDisplayFrame, function () {
    if (typeof window.requestDisplayStreamReconnect === "function") window.requestDisplayStreamReconnect();
  }) : null;
function drawDisplayFrame(arrayBuffer) {
  if (window.TAHtmlVideoPacketFormat === "scrcpy" && syncVideoQueue) {
    if (typeof arrayBuffer === "string") { syncVideoQueue.reset(); consumeDisplayFrame(arrayBuffer); }
    else if (arrayBuffer instanceof ArrayBuffer) syncVideoQueue.push(arrayBuffer);
    return;
  }
  consumeDisplayFrame(arrayBuffer);
}
function consumeDisplayFrame(arrayBuffer) {
  if (!worker || legacyFailed) {
    return;
  }
  if (window.TAHtmlVideoPacketFormat === "scrcpy") {
    if (typeof arrayBuffer === "string") {
      var metadata;
      try { metadata = JSON.parse(arrayBuffer); } catch (_error) { return; }
      if (metadata.type !== "scrcpy-session" || !Number.isInteger(metadata.width) ||
          !Number.isInteger(metadata.height) || metadata.width < 96 || metadata.height < 96 ||
          metadata.width > 1920 || metadata.height > 1920 || metadata.width % 2 || metadata.height % 2 ||
          metadata.width * metadata.height > 1600000) return;
      legacyConfig = null;
      legacyEpoch++;
      legacyQueue = []; legacyQueueBytes = 0;
      legacyAwaitKeyframe = true;
      legacyPendingReset = metadata; drainLegacyQueue();
      return;
    }
    if (!(arrayBuffer instanceof ArrayBuffer) || arrayBuffer.byteLength < 13 || arrayBuffer.byteLength > 2097164) return;
    var header = new DataView(arrayBuffer);
    if (header.getUint32(8) !== arrayBuffer.byteLength - 12) return;
    var bytes = new Uint8Array(arrayBuffer);
    var payload = bytes.subarray(12);
    if (bytes[0] & 0x40) {
      if (payload.byteLength <= 262144) legacyConfig = payload.slice();
      return;
    }
    if (!legacyConfig) return;
    var isKeyframe = Boolean(bytes[0] & 0x20);
    if (legacyAwaitKeyframe && !isKeyframe) return;
    if (isKeyframe) {
      // Keep the newest complete GOP when decoding falls behind live arrivals.
      legacyQueue = []; legacyQueueBytes = 0;
      var accessUnit = new Uint8Array(legacyConfig.length + payload.length);
      accessUnit.set(legacyConfig); accessUnit.set(payload, legacyConfig.length);
      arrayBuffer = accessUnit.buffer;
      legacyAwaitKeyframe = false;
    } else arrayBuffer = payload.slice().buffer;
    if (legacyQueue.length >= 120 || legacyQueueBytes + arrayBuffer.byteLength > 2 * 1024 * 1024) {
      legacyQueue = []; legacyQueueBytes = 0; legacyAwaitKeyframe = true;
      if (typeof window.requestDisplayStreamReconnect === "function") window.requestDisplayStreamReconnect();
      return;
    }
    legacyQueue.push(arrayBuffer); legacyQueueBytes += arrayBuffer.byteLength;
    drainLegacyQueue();
    return;
  }
  worker.postMessage({ h264Data: arrayBuffer }, [arrayBuffer]);
}
