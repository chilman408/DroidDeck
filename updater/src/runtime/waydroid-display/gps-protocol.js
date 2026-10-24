// SPDX-License-Identifier: GPL-3.0-only
const { normalizeLocation } = require("../preview-gateway/gps-location");
function validateRequest(value) {
  const fields = { inspect: [], enabled: ["enabled"], begin: ["client"], heartbeat: ["client"],
    deliver: ["client", "fix"], end: ["client"] };
  const keys = typeof value?.operation === "string" && Object.hasOwn(fields, value.operation) ? fields[value.operation] : null;
  if (!value || Array.isArray(value) || value.version !== 1 || !keys ||
      typeof value.id !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(value.id) ||
      Object.keys(value).length !== 3 + keys.length ||
      Object.keys(value).some(key => !["version", "id", "operation", ...keys].includes(key)) ||
      (keys.includes("client") && (typeof value.client !== "string" || !/^[a-f0-9]{32}$/.test(value.client))) ||
      (value.operation === "enabled" && typeof value.enabled !== "boolean")) throw new Error("Invalid GPS request");
  if (value.operation === "deliver") {
    const fix = value.fix;
    if (!fix || Array.isArray(fix) || Object.keys(fix).length !== 6 ||
        Object.keys(fix).some(key => !["latitude", "longitude", "accuracy", "timestamp", "speed", "bearing"].includes(key)) ||
        ["latitude", "longitude", "accuracy", "timestamp"].some(key => typeof fix[key] !== "number") ||
        ["speed", "bearing"].some(key => fix[key] !== null && typeof fix[key] !== "number")) throw new Error("Invalid GPS fix");
    normalizeLocation({ latitude: fix.latitude, longitude: fix.longitude, vertical_accuracy: fix.accuracy,
      timestamp: fix.timestamp, speed: fix.speed, bearing: fix.bearing });
  }
  return value;
}
function validateState(value) {
  const fields = ["enabled", "active", "supported", "mock", "recoveryRequired"];
  if (!value || Array.isArray(value) || Object.keys(value).length !== fields.length ||
      fields.some(key => typeof value[key] !== "boolean") || value.mock !== true ||
      Object.keys(value).some(key => !fields.includes(key))) throw new Error("Invalid GPS state");
  return { ...value };
}
module.exports = { validateRequest, validateState };
