// SPDX-License-Identifier: GPL-3.0-only
const { PNG } = require("pngjs");

function readFrame(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 33 || buffer.length > 16 * 1024 * 1024) {
    throw new Error("Invalid capture size");
  }
  // Bound allocation before handing the complete image to the PNG decoder.
  if (!buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
      buffer.toString("ascii", 12, 16) !== "IHDR") throw new Error("Invalid PNG capture");
  const width = buffer.readUInt32BE(16), height = buffer.readUInt32BE(20);
  if (!width || !height || width > 1920 || height > 1920) throw new Error("Invalid capture dimensions");
  const png = PNG.sync.read(buffer, { checkCRC: true });
  let nonblack = false;
  for (let i = 0; i < png.data.length; i += 4) {
    if (png.data[i + 3] && (png.data[i] || png.data[i + 1] || png.data[i + 2])) {
      nonblack = true;
      break;
    }
  }
  return { width: png.width, height: png.height, nonblack };
}

module.exports = { readFrame };
