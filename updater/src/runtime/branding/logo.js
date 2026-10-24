// SPDX-License-Identifier: GPL-3.0-only
const path = require('node:path'), crypto = require('node:crypto');
const { readBoundedRegular } = require('../portable/read-bounded');
function verifyLogo(directory = __dirname) {
  const receipt = JSON.parse(readBoundedRegular(path.join(directory, 'logo-receipt.json'), 4096).toString('utf8'));
  if (!receipt || typeof receipt !== 'object' || Array.isArray(receipt) ||
      Object.keys(receipt).sort().join(',') !== 'file,height,kind,product,sha256,transparent,version,width' ||
      receipt.version !== 1 || receipt.product !== 'DroidDeck' || receipt.kind !== 'browser-cast-logo' ||
      receipt.file !== 'droiddeck-logo.png' || receipt.transparent !== true ||
      typeof receipt.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(receipt.sha256) ||
      !Number.isSafeInteger(receipt.width) || receipt.width < 128 || receipt.width > 2048 || receipt.height !== receipt.width) {
    throw new Error('Invalid approved logo receipt');
  }
  const file = path.join(directory, receipt.file), data = readBoundedRegular(file, 1024 * 1024);
  if (data.length < 33 || !data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
      data.readUInt32BE(8) !== 13 || data.toString('ascii', 12, 16) !== 'IHDR' ||
      data.readUInt32BE(16) !== receipt.width || data.readUInt32BE(20) !== receipt.height ||
      data[24] !== 8 || data[25] !== 6 || data[26] !== 0 || data[27] !== 0 || data[28] !== 0 ||
      crypto.createHash('sha256').update(data).digest('hex') !== receipt.sha256) {
    throw new Error('Approved transparent logo bytes differ');
  }
  return { ...receipt, path: file };
}
if (require.main === module) {
  try {
    if (process.argv.length !== 2) throw new Error('No logo path overrides permitted');
    const { path: ignored, ...receipt } = verifyLogo();
    console.log(JSON.stringify({ verified: true, ...receipt }));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
module.exports = { verifyLogo };
