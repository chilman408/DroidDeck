// SPDX-License-Identifier: GPL-3.0-only
// Disposable-VM fixture: open a render node only after proving mode-based group access.
const fs = require('node:fs');
if (process.platform !== 'linux' || process.getuid() < 1000 || process.getuid() !== process.geteuid() ||
    process.getgid() < 1000 || process.argv.length !== 2) throw new Error('Non-root Linux fixture only');
const uid = process.getuid(), gid = process.getgid(), groups = process.getgroups().sort((a, b) => a - b);
const path = '/dev/dri/renderD128', before = fs.lstatSync(path);
if (!before.isCharacterDevice() || before.uid !== 0 || before.nlink !== 1 || before.mode & 0o007 ||
    before.rdev !== 226 * 256 + 128) throw new Error('Unexpected disposable render node');
const groupModeEligible = groups.includes(before.gid) && (before.mode & 0o060) === 0o060;
let renderOpenVerified = false;
if (groupModeEligible) {
  const descriptor = fs.openSync(path, fs.constants.O_RDWR | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const opened = fs.fstatSync(descriptor), after = fs.lstatSync(path);
    for (const key of ['dev', 'ino', 'uid', 'gid', 'mode', 'nlink', 'rdev']) {
      if (opened[key] !== before[key] || after[key] !== before[key]) throw new Error('Render node changed');
    }
    renderOpenVerified = true;
  } finally { fs.closeSync(descriptor); }
}
if (process.getuid() !== uid || process.getgid() !== gid || JSON.stringify(process.getgroups().sort((a, b) => a - b)) !== JSON.stringify(groups)) {
  throw new Error('Process credentials changed');
}
console.log(JSON.stringify({ uid, gid, groups, renderGid: before.gid, groupModeEligible,
  renderOpenVerified, gpuRenderingVerified: false, ioctlsIssued: 0, deviceWrites: 0 }));
