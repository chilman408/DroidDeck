// SPDX-License-Identifier: GPL-3.0-only
const { execute } = require("./host-adapter");
async function assertSessionVacant({ run = execute } = {}) {
  const response = JSON.parse((await run("/usr/bin/busctl", ["--user", "--json=short", "call",
    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", "s", "id.waydro.Session"],
  { env: process.env })).toString());
  if (response.type !== "b" || !Array.isArray(response.data) || response.data.length !== 1 || response.data[0] !== false) {
    throw new Error("An existing Waydroid session must not be replaced");
  }
}
async function main() {
  if (process.platform !== "linux" || process.getuid?.() !== 1000 || process.argv.length !== 2) {
    throw new Error("Fixed Waydroid session preflight requires the desktop user and no arguments");
  }
  await assertSessionVacant();
}
if (require.main === module) main().catch(() => { console.error("Waydroid session startup unavailable"); process.exitCode = 1; });
module.exports = { assertSessionVacant };
