// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const { constants } = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
function createGpsStore(filename, kind) {
  if (!["preferences", "journal"].includes(kind)) throw new Error("Invalid GPS store");
  const directory = path.dirname(filename);
  const owned = stat => process.platform === "win32" || stat.uid === process.getuid();
  const privateMode = stat => process.platform === "win32" || !(stat.mode & 0o077);
  function validate(value) {
    const field = kind === "preferences" ? "enabled" : "lease";
    if (!value || Array.isArray(value) || value.version !== 1 || Object.keys(value).length !== 2 ||
        Object.keys(value).some(key => !["version", field].includes(key)) ||
        (kind === "preferences" ? typeof value.enabled !== "boolean" :
          typeof value.lease !== "string" || !/^[a-f0-9]{32}$/.test(value.lease))) {
      throw new Error("Invalid GPS stored state");
    }
    return { version: 1, [field]: value[field] };
  }
  async function checkDirectory() {
    const stat = await fs.lstat(directory);
    if (!stat.isDirectory() || !owned(stat) || !privateMode(stat)) throw new Error("GPS directory is not private");
  }
  async function syncDirectory() {
    if (process.platform === "win32") return;
    const fd = await fs.open(directory, "r");
    try { await fd.sync(); } finally { await fd.close(); }
  }
  async function load() {
    await checkDirectory(); let fd;
    try { fd = await fs.open(filename, constants.O_RDONLY | (constants.O_NOFOLLOW || 0)); }
    catch (error) { if (error.code === "ENOENT") return null; throw error; }
    try {
      const stat = await fd.stat();
      if (!stat.isFile() || stat.nlink !== 1 || !owned(stat) || !privateMode(stat) || stat.size > 512) {
        throw new Error("GPS state is not private");
      }
      return validate(JSON.parse(await fd.readFile("utf8")));
    } finally { await fd.close(); }
  }
  async function save(value) {
    if (value === null && kind !== "journal") throw new Error("Cannot remove GPS preference");
    const state = value === null ? null : validate(value);
    await load();
    if (state === null) {
      await fs.unlink(filename).catch(error => { if (error.code !== "ENOENT") throw error; });
      await syncDirectory(); return;
    }
    const temporary = path.join(directory, `.gps-${crypto.randomBytes(16).toString("hex")}.tmp`);
    let fd;
    try {
      fd = await fs.open(temporary, "wx", 0o600); await fd.writeFile(JSON.stringify(state) + "\n");
      await fd.sync(); await fd.close(); fd = null;
      await fs.rename(temporary, filename); await syncDirectory();
    } finally {
      if (fd) await fd.close();
      await fs.unlink(temporary).catch(error => { if (error.code !== "ENOENT") throw error; });
    }
  }
  return { load, save };
}
module.exports = { createGpsStore };
