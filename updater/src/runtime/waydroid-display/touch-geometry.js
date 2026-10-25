// SPDX-License-Identifier: GPL-3.0-only
function calculateCalibration(size, viewport) {
  const { logical, physical, device, rotation } = viewport || {};
  if (rotation !== 0 || !Array.isArray(logical) || logical.length !== 4 ||
      !Array.isArray(physical) || physical.length !== 4 || !Array.isArray(device) || device.length !== 2 ||
      ![...logical, ...physical, ...device].every(Number.isInteger) ||
      logical[0] !== 0 || logical[1] !== 0 || logical[2] !== size.width || logical[3] !== size.height ||
      !device.every(value => value >= 96 && value <= 8192) ||
      physical[0] < 0 || physical[1] < 0 || physical[2] <= physical[0] || physical[3] <= physical[1] ||
      physical[2] > device[0] || physical[3] > device[1]) throw new Error("Invalid synthetic touch viewport");
  return [(physical[2] - physical[0]) / device[0], 0, physical[0] * size.width / device[0],
    0, (physical[3] - physical[1]) / device[1], physical[1] * size.height / device[1]];
}
module.exports = { calculateCalibration };
