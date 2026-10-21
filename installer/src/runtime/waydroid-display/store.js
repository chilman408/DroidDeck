// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const constants = require("node:fs").constants;
const path = require("node:path");
const crypto = require("node:crypto");
const { validateDesired } = require("./transaction");

function createStore(filename) {
  const directory = path.dirname(filename);
  const owned = stat => process.platform === "win32" || stat.uid === process.getuid();
  async function checkDirectory() {
    const stat = await fs.lstat(directory);
    if (!stat.isDirectory() || !owned(stat) || (process.platform !== "win32" && (stat.mode & 0o077))) {
      throw new Error("Display state directory must be privately owned");
    }
  }
  async function load() {
    await checkDirectory();
    let fd;
    try { fd = await fs.open(filename, constants.O_RDONLY | (constants.O_NOFOLLOW || 0)); }
    catch (error) { if (error.code === "ENOENT") return null; throw error; }
    try {
      const stat = await fd.stat();
      if (!stat.isFile() || stat.nlink !== 1 || !owned(stat) || stat.size > 4096 ||
          (process.platform !== "win32" && (stat.mode & 0o077))) throw new Error("Display preferences are not private owned state");
      return validateDesired(JSON.parse(await fd.readFile("utf8")));
    } finally { await fd.close(); }
  }
  async function save(value) {
    const state = validateDesired(value);
    await checkDirectory();
    // Validate the existing file before replacement; a corrupt state needs explicit recovery.
    await load();
    const temporary = path.join(directory, `.display-${crypto.randomBytes(16).toString("hex")}.tmp`);
    let fd;
    try {
      fd = await fs.open(temporary, "wx", 0o600);
      await fd.writeFile(JSON.stringify(state) + "\n");
      await fd.sync();
      await fd.close();
      fd = null;
      await fs.rename(temporary, filename);
      if (process.platform !== "win32") {
        const dir = await fs.open(directory, "r");
        try { await dir.sync(); } finally { await dir.close(); }
      }
    } finally {
      if (fd) await fd.close();
      await fs.unlink(temporary).catch(error => { if (error.code !== "ENOENT") throw error; });
    }
  }
  return { load, save };
}
module.exports = { createStore };
