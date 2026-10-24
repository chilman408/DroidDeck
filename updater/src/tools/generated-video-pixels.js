// SPDX-License-Identifier: GPL-3.0-only
const crypto = require('node:crypto');

function validateFrame({ width, height, data } = {}) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 64 || height < 64 ||
      width > 4096 || height > 4096 || !Buffer.isBuffer(data) || data.length !== width * height * 4) {
    throw new Error('Invalid generated pixel frame');
  }
}
function generatedVideoPixels({ width, height, data }) {
  validateFrame({ width, height, data });
  const hash = crypto.createHash('sha256');
  let colorful = 0, samples = 0;
  // Ignore browser text/navigation and require moving color inside the video area.
  for (let y = Math.floor(height * 0.3); y < Math.floor(height * 0.6); y += 3) {
    for (let x = Math.floor(width * 0.3); x < Math.floor(width * 0.7); x += 3) {
      const index = (y * width + x) * 4;
      const rgb = data.subarray(index, index + 3);
      if (Math.max(...rgb) > 100 && Math.max(...rgb) - Math.min(...rgb) > 60) colorful++;
      hash.update(rgb);
      samples++;
    }
  }
  return { colorfulRatio: colorful / samples, signature: hash.digest('hex') };
}

function generatedMotion(before, after) {
  validateFrame(before); validateFrame(after);
  if (before.width !== after.width || before.height !== after.height) throw new Error('Generated geometry changed');
  let changed = 0, difference = 0, samples = 0;
  for (let y = Math.floor(after.height * 0.3); y < Math.floor(after.height * 0.6); y += 3) {
    for (let x = Math.floor(after.width * 0.3); x < Math.floor(after.width * 0.7); x += 3) {
      const index = (y * after.width + x) * 4;
      const delta = [0, 1, 2].map(channel => Math.abs(after.data[index + channel] - before.data[index + channel]));
      if (Math.max(...delta) >= 24) changed++;
      difference += delta.reduce((sum, value) => sum + value, 0) / 3;
      samples++;
    }
  }
  const changedRatio = changed / samples, meanDifference = difference / samples;
  // Encoder noise may change hashes; require substantial changes across the video region.
  return { changedRatio, meanDifference, moving: changedRatio >= 0.03 && meanDifference >= 3 };
}
module.exports = { generatedVideoPixels, generatedMotion };
