// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs');
function readBoundedRegular(filename, limit) {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 8 * 1024 * 1024) throw new Error('Invalid file bound');
  const before = fs.lstatSync(filename);
  if (!before.isFile() || before.nlink !== 1 || before.size > limit) throw new Error('Unsafe or oversized file');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > limit || stat.ino !== before.ino || stat.dev !== before.dev) throw new Error('File changed');
    const data = Buffer.alloc(stat.size + 1); let length = 0;
    while (length < data.length) {
      const bytes = fs.readSync(fd, data, length, data.length - length, length);
      if (!bytes) break;
      length += bytes;
    }
    const after = fs.fstatSync(fd);
    if (!after.isFile() || after.nlink !== 1 || length !== stat.size || after.size !== stat.size ||
        after.mtimeMs !== stat.mtimeMs || after.ctimeMs !== stat.ctimeMs) throw new Error('File changed');
    return data.subarray(0, length);
  } finally { fs.closeSync(fd); }
}
module.exports = { readBoundedRegular };
