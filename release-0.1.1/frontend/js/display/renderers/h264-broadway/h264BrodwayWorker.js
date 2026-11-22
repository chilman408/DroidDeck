importScripts("Decoder.js", "YUVCanvas.js");

let pendingFrames = [];
let decoder = null;
let yuvCanvas = null;
let offscreenCanvas = null;

let width = 1280;
let height = 720;
let windowWidth = 1280;
let windowHeight = 720;

let renderScheduled = false;
let dropDeltaUntilKeyframe = true;

const MAX_RENDER_QUEUE_SIZE = 4;

function toSafeNumber(value, fallback) {
  const parsed = Number(value);
  if (Number.isFinite(parsed)) {
    return Math.max(1, Math.round(parsed));
  }
  return Math.max(1, Math.round(fallback));
}

function rebuildYuvCanvas() {
  if (!offscreenCanvas) {
    return;
  }

  offscreenCanvas.width = windowWidth;
  offscreenCanvas.height = windowHeight;

  yuvCanvas = new YUVCanvas({
    canvas: offscreenCanvas,
    width: width,
    height: height,
    type: "yuv420",
    reuseMemory: true,
  });
}

function applyRuntimeConfig(config) {
  const runtimeConfig = config || {};
  const nextWidth = toSafeNumber(runtimeConfig.displayWidth, width);
  const nextHeight = toSafeNumber(runtimeConfig.displayHeight, height);
  const nextWindowWidth = toSafeNumber(runtimeConfig.windowWidth, windowWidth);
  const nextWindowHeight = toSafeNumber(runtimeConfig.windowHeight, windowHeight);

  const sizeChanged = nextWidth !== width || nextHeight !== height;

  width = nextWidth;
  height = nextHeight;
  windowWidth = nextWindowWidth;
  windowHeight = nextWindowHeight;

  if (sizeChanged) {
    pendingFrames = [];
    dropDeltaUntilKeyframe = true;
    rebuildYuvCanvas();
  }
}

function scheduleRender() {
  if (renderScheduled) {
    return;
  }
  renderScheduled = true;
  self.setTimeout(renderLatestFrame, 0);
}

function renderLatestFrame() {
  renderScheduled = false;
  if (pendingFrames.length === 0 || !yuvCanvas) {
    return;
  }

  while (pendingFrames.length > 1) {
    pendingFrames.shift();
  }

  const frame = pendingFrames.shift();
  drawImageToCanvas(frame);

  if (pendingFrames.length > 0) {
    scheduleRender();
  }
}

function drawImageToCanvas(image) {
  if (!image || !yuvCanvas) {
    return;
  }

  const data = image.data;
  const ySize = image.width * image.height;
  const uvSize = (image.width >> 1) * (image.height >> 1);

  if (data.length < ySize + uvSize * 2) {
    return;
  }

  const y = data.subarray(0, ySize);
  const u = data.subarray(ySize, ySize + uvSize);
  const v = data.subarray(ySize + uvSize, ySize + uvSize * 2);

  yuvCanvas.drawNextOutputPicture({
    yData: y,
    uData: u,
    vData: v,
    yDataPerRow: image.width, yRowCnt: image.height,
    uDataPerRow: image.width >> 1, uRowCnt: image.height >> 1,
    vDataPerRow: image.width >> 1, vRowCnt: image.height >> 1,
  });
  self.postMessage({ frameRendered: true, width, height });
}

function splitNalUnits(data) {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (bytes.length < 5) {
    return [];
  }

  const units = [];
  let currentStart = -1;
  let index = 0;

  while (index <= bytes.length - 4) {
    let codeLength = 0;
    if (
      bytes[index] === 0x00 &&
      bytes[index + 1] === 0x00 &&
      bytes[index + 2] === 0x00 &&
      bytes[index + 3] === 0x01
    ) {
      codeLength = 4;
    } else if (
      bytes[index] === 0x00 &&
      bytes[index + 1] === 0x00 &&
      bytes[index + 2] === 0x01
    ) {
      codeLength = 3;
    }

    if (codeLength > 0) {
      if (currentStart >= 0 && index > currentStart) {
        units.push(bytes.subarray(currentStart, index));
      }
      currentStart = index;
      index += codeLength;
      continue;
    }

    index += 1;
  }

  if (currentStart >= 0 && currentStart < bytes.length) {
    units.push(bytes.subarray(currentStart));
  }

  if (units.length === 0) {
    units.push(bytes);
  }

  return units;
}

function getNalType(nal) {
  if (!nal || nal.length < 5) {
    return -1;
  }

  let offset = 0;
  if (
    nal.length >= 4 &&
    nal[0] === 0x00 &&
    nal[1] === 0x00 &&
    nal[2] === 0x00 &&
    nal[3] === 0x01
  ) {
    offset = 4;
  } else if (nal.length >= 3 && nal[0] === 0x00 && nal[1] === 0x00 && nal[2] === 0x01) {
    offset = 3;
  }

  if (offset >= nal.length) {
    return -1;
  }

  return nal[offset] & 0x1f;
}

function containsIdr(data) {
  const units = splitNalUnits(data);
  for (let index = 0; index < units.length; index += 1) {
    if (getNalType(units[index]) === 5) {
      return true;
    }
  }
  return false;
}

function shouldDecodeEncodedPacket(data) {
  const hasIdr = containsIdr(data);
  const overloaded = pendingFrames.length > MAX_RENDER_QUEUE_SIZE;

  if (overloaded && !hasIdr) {
    dropDeltaUntilKeyframe = true;
    return false;
  }

  if (dropDeltaUntilKeyframe && !hasIdr) {
    return false;
  }

  if (hasIdr) {
    dropDeltaUntilKeyframe = false;
  }

  return true;
}

function handleEncodedData(data) {
  if (!decoder) {
    return;
  }

  if (!shouldDecodeEncodedPacket(data)) {
    return;
  }

  decoder.decode(data);
}

function resetDecoder() {
  pendingFrames = [];
  dropDeltaUntilKeyframe = true;
  decoder = new Decoder({ rgb: false });
  decoder.onPictureDecoded = function (data, decodedWidth, decodedHeight) {
    if (!Number.isInteger(decodedWidth) || !Number.isInteger(decodedHeight) ||
        decodedWidth < width || decodedHeight < height || decodedWidth > 2048 || decodedHeight > 2048) return;
    // Decoder memory is reused; retain independent pixels until the render task.
    pendingFrames.push({ data: data.slice(), width: decodedWidth, height: decodedHeight });
    if (pendingFrames.length > MAX_RENDER_QUEUE_SIZE + 1) pendingFrames.splice(0, pendingFrames.length - 1);
    scheduleRender();
  };
}

self.onmessage = function (event) {
  if (
    event.data.canvas &&
    event.data.displayWidth !== undefined &&
    event.data.displayHeight !== undefined
  ) {
    offscreenCanvas = event.data.canvas;
    applyRuntimeConfig({
      displayWidth: event.data.displayWidth,
      displayHeight: event.data.displayHeight,
      windowWidth: event.data.windowWidth,
      windowHeight: event.data.windowHeight,
    });
    rebuildYuvCanvas();

    resetDecoder();
    self.postMessage({ h264QueueReady: 2, width, height });
  } else if (event.data.streamReset) {
    applyRuntimeConfig({ displayWidth: event.data.streamReset.width, displayHeight: event.data.streamReset.height });
    resetDecoder();
  } else if (event.data.config) {
    applyRuntimeConfig(event.data.config);
  } else if (event.data.h264Data) {
    try {
      handleEncodedData(new Uint8Array(event.data.h264Data));
    } finally {
      if (Number.isSafeInteger(event.data.h264Epoch) && event.data.h264Epoch >= 0 &&
          Number.isSafeInteger(event.data.h264Ticket) && event.data.h264Ticket > 0) {
        self.postMessage({ h264Processed: true, h264Epoch: event.data.h264Epoch, h264Ticket: event.data.h264Ticket });
      }
    }
  }
};
