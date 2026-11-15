// SPDX-License-Identifier: GPL-3.0-only
const path = require('node:path'), crypto = require('node:crypto');
const { readBoundedRegular } = require('../portable/read-bounded');

// This asset belongs to the signed gateway payload, so it updates and rolls
// back with the browser player without rewriting the protected frontend.
function readMediaArtwork(frontendDir) {
  let data;
  try {
    data = readBoundedRegular(path.join(__dirname, 'droiddeck-logo.png'), 1024 * 1024);
  } catch (error) {
    // Fresh frontend builds already carry the approved branding PNG. Only an
    // absent gateway asset permits this fixed compatibility location, supplied
    // by server configuration, never by a URL, receipt or arbitrary filename.
    // Corrupt/linked/oversized owned payloads must fail instead of falling back.
    if (error.code !== 'ENOENT' || typeof frontendDir !== 'string' || !path.isAbsolute(frontendDir)) throw error;
    data = readBoundedRegular(path.join(frontendDir, 'assets/droiddeck-logo.png'), 1024 * 1024);
  }
  if (data.length !== 664836 ||
      !data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
      data.readUInt32BE(8) !== 13 || data.toString('ascii', 12, 16) !== 'IHDR' ||
      data.readUInt32BE(16) !== 1254 || data.readUInt32BE(20) !== 1254 ||
      data[24] !== 8 || data[25] !== 6 || data[26] !== 0 || data[27] !== 0 || data[28] !== 0 ||
      crypto.createHash('sha256').update(data).digest('hex') !==
        'd50a79c92129aa9e6462eedb0294ce0b5c09da1b91433672225f31c5f938e475') {
    throw new Error('Approved gateway artwork bytes differ');
  }
  return data;
}
module.exports = { readMediaArtwork };
