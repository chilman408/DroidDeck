// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
function createAudioSettings(filename) {
  const parent = fs.lstatSync(path.dirname(filename));
  if (!parent.isDirectory() || (process.platform !== "win32" && (parent.mode & 0o022))) throw new Error("Invalid audio settings directory");
  const valid = s => s && s.version === 1 && typeof s.enabled === "boolean" && Number.isInteger(s.volume) && s.volume >= 0 && s.volume <= 150 &&
    Object.keys(s).length === 3;
  let state = { version: 1, enabled: true, volume: 100 };
  const stat = fs.lstatSync(filename, { throwIfNoEntry: false });
  if (stat) {
    if (!stat.isFile() || stat.size > 1024 || (process.platform !== "win32" && (stat.mode & 0o077))) throw new Error("Invalid audio settings file");
    state = JSON.parse(fs.readFileSync(filename, "utf8")); if (!valid(state)) throw new Error("Invalid saved audio state");
  }
  const events = new EventEmitter();
  return Object.assign(events, {
    snapshot: () => ({ enabled: state.enabled, volume: state.volume }),
    update(key, value) {
      if (!Number.isInteger(value) || (key === "enabled" ? ![0, 1].includes(value) : key !== "volume" || value < 0 || value > 150)) {
        throw Object.assign(new Error("Invalid audio preference"), { status: 400 });
      }
      const next = { ...state, [key]: key === "enabled" ? value === 1 : value };
      const temporary = filename + "." + crypto.randomBytes(8).toString("hex") + ".tmp";
      try {
        fs.writeFileSync(temporary, JSON.stringify(next) + "\n", { mode: 0o600, flag: "wx" });
        fs.renameSync(temporary, filename); state = next;
      } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
      events.emit("change", this.snapshot()); return this.snapshot();
    },
  });
}
module.exports = { createAudioSettings };
