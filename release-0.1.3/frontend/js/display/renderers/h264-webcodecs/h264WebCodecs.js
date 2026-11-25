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
    packetFormat: window.TAHtmlVideoPacketFormat || "annexb",
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

function createWorkerWithFileFallback(workerPath) {
  try {
    return new Worker(workerPath);
  } catch (primaryError) {
    if (window.location.protocol !== "file:") {
      throw primaryError;
    }

    var workerSource = readTextFromPath(workerPath);
    if (workerSource) {
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
  worker = createWorkerWithFileFallback("./js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js");
} catch (error) {
  console.error("Failed to initialize h264WebCodecs worker", error);
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
      packetFormat: initialRuntimeConfig.packetFormat,
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

var videoWatchdog = null;
var videoRecoveryTimer = null;
var videoRecoveryPending = false;
var videoPacketsReceived = false;
function recoverVideoDecoder(reason) {
  var policy = window.TAHtml && window.TAHtml.browserRenderer;
  if (window.TAHtmlVideoPacketFormat === "scrcpy" && policy && typeof policy.recover === "function" && policy.recover()) {
    clearTimeout(videoWatchdog);
    videoWatchdog = null;
    clearTimeout(videoRecoveryTimer);
    videoRecoveryTimer = null;
    canvas.dataset.videoState = "recovering-legacy";
    return;
  }
  reconnectVideoStream(reason);
}
function reconnectVideoStream(reason) {
  if (window.TAHtmlVideoPacketFormat !== "scrcpy") return;
  syncVideoQueue?.reset();
  clearTimeout(videoWatchdog);
  videoWatchdog = null;
  videoRecoveryPending = true;
  canvas.dataset.videoState = "reconnecting";
  if (videoRecoveryTimer !== null) return;
  console.warn("H264 reconnect", reason && (reason.message || reason));
  videoRecoveryTimer = setTimeout(function () {
    videoRecoveryTimer = null;
    if (typeof window.requestDisplayStreamReconnect === "function") window.requestDisplayStreamReconnect();
    watchVideoFrames();
  }, 1000);
}
function watchVideoFrames() {
  if (window.TAHtmlVideoPacketFormat !== "scrcpy") return;
  if (videoWatchdog !== null) return;
  // A verified logical resize may occupy the full 20-second transaction budget.
  videoWatchdog = setTimeout(function () {
    // Only a startup with incoming packets establishes a stalled decoder.
    if (videoPacketsReceived && !Number(canvas.dataset.videoFrames || 0)) recoverVideoDecoder("No decoded output");
    else reconnectVideoStream("No decoded output");
  }, 30000);
}
if (worker) {
  worker.onmessage = function (event) {
    if (event.data.error) { recoverVideoDecoder(event.data.error); return; }
    if (event.data.frameRendered) {
      canvas.dataset.videoFrames = String(Number(canvas.dataset.videoFrames || 0) + 1);
      canvas.dataset.videoWidth = String(event.data.width);
      canvas.dataset.videoHeight = String(event.data.height);
      if (!videoRecoveryPending) {
        canvas.dataset.videoState = "ready";
        clearTimeout(videoWatchdog);
        videoWatchdog = null;
        if (event.data.pendingDecode > 0) watchVideoFrames();
        clearTimeout(videoRecoveryTimer);
        videoRecoveryTimer = null;
      }
    }
  };
  worker.onerror = recoverVideoDecoder;
}
if (!worker) setTimeout(function () { recoverVideoDecoder("Worker unavailable"); }, 0);
watchVideoFrames();

window.addEventListener("beforeunload", function () {
  syncVideoQueue?.close();
  if (worker && worker.__blobUrl) {
    URL.revokeObjectURL(worker.__blobUrl);
  }
});

var syncVideoQueue = typeof window.createAVSyncVideoQueue === "function" ? window.createAVSyncVideoQueue(
  consumeDisplayFrame, function () { reconnectVideoStream("A/V sync queue overflow"); }) : null;
function drawDisplayFrame(arrayBuffer) {
  if (window.TAHtmlVideoPacketFormat === "scrcpy" && syncVideoQueue) {
    if (typeof arrayBuffer === "string") { syncVideoQueue.reset(); consumeDisplayFrame(arrayBuffer); }
    else if (arrayBuffer instanceof ArrayBuffer) syncVideoQueue.push(arrayBuffer);
    return;
  }
  consumeDisplayFrame(arrayBuffer);
}
function consumeDisplayFrame(arrayBuffer) {
  if (!worker) {
    return;
  }
  if (window.TAHtmlVideoPacketFormat === "scrcpy") {
    if (typeof arrayBuffer === "string") {
      try {
        var metadata = JSON.parse(arrayBuffer);
        if (metadata.type !== "scrcpy-session" ||
            !Number.isInteger(metadata.width) || !Number.isInteger(metadata.height) ||
            metadata.width < 96 || metadata.height < 96 ||
            metadata.width > 1920 || metadata.height > 1920 ||
            metadata.width % 2 || metadata.height % 2 ||
            metadata.width * metadata.height > 1600000) throw new Error("Invalid video size");
        worker.postMessage({ scrcpySession: metadata });
        videoRecoveryPending = false;
        watchVideoFrames();
      } catch (_error) { reconnectVideoStream("Invalid stream metadata"); }
    } else if (arrayBuffer instanceof ArrayBuffer) {
      videoPacketsReceived = true;
      watchVideoFrames();
      worker.postMessage({ scrcpyPacket: arrayBuffer }, [arrayBuffer]);
    }
  } else {
    worker.postMessage({ h264Data: arrayBuffer });
  }
}
