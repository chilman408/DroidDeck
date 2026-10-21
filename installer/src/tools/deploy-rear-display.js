// SPDX-License-Identifier: GPL-3.0-only
// Fixed disposable .90 preview deployment; rollback never restores user preferences.
const fs = require("node:fs/promises"), path = require("node:path"), crypto = require("node:crypto");
const { execFile } = require("node:child_process"), { promisify } = require("node:util");
const execute = promisify(execFile);
const ROOT = "/opt/teslaandroid-waydroid", HOME = "/home/hilman/teslaandroid-waydroid";
const FRONTEND = HOME + "/frontend";
const RUNTIME = ["preview-gateway/display-control.js", "preview-gateway/display-helper-client.js",
  "preview-gateway/gps-control.js", "preview-gateway/server.js", "preview-gateway/rear-display-settings.js",
  "waydroid-display/transaction.js", "waydroid-display/helper.js", "waydroid-display/runtime-commands.js"];
const WEB = ["js/core/api-client.js", "js/display/display-controller.js", "js/settings/settings-controller.js", "css/app.css"];
function deploymentProfile(argv) {
  if (argv.length === 2) return { stage: HOME + "/rear-deploy-stage-20261009",
    backup: "/var/lib/teslaandroid-waydroid/backups/rear-display-20261009", runtime: RUNTIME, web: WEB };
  if (argv.length === 3 && argv[2] === "--network") return { stage: HOME + "/network-deploy-stage-20261009",
    backup: "/var/lib/teslaandroid-waydroid/backups/network-info-20261009",
    runtime: ["preview-gateway/device-info.js", "preview-gateway/server.js"],
    web: ["js/settings/settings-controller.js", "css/app.css"] };
  throw new Error("Only the fixed rear or --network deployment is permitted");
}
const PREFS = ["/var/lib/teslaandroid-waydroid/display.json", "/var/lib/teslaandroid-waydroid/gps.json",
  "/home/hilman/.config/teslaandroid-waydroid/browser-access.json",
  "/home/hilman/.config/teslaandroid-waydroid/browser-audio.json",
  "/home/hilman/.config/teslaandroid-waydroid/browser-video.json",
  "/home/hilman/.config/teslaandroid-waydroid/rear-display.json"];
const sha = buffer => crypto.createHash("sha256").update(buffer).digest("hex");
async function optionalStat(filename) {
  try { return await fs.lstat(filename); } catch (error) { if (error.code === "ENOENT") return null; throw error; }
}
async function preferenceHashes() {
  const result = {};
  for (const filename of PREFS) {
    const stat = await optionalStat(filename);
    if (stat && (!stat.isFile() || stat.nlink !== 1)) throw new Error("Unsafe preference file");
    result[filename] = stat ? sha(await fs.readFile(filename)) : null;
  }
  return result;
}
async function main() {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("Fixed .90 deployment requires Linux root");
  const { stage: STAGE, backup: BACKUP, runtime, web } = deploymentProfile(process.argv);
  const service = (...args) => execute("/usr/bin/systemctl", args, { timeout: 120000 });
  if ((await service("is-active", "tesla-waydroid-runtime.service")).stdout.trim() !== "active") throw new Error("Owned runtime must be active before deployment");
  const files = [...runtime.map(file => ({ source: STAGE + "/runtime/" + file, target: ROOT + "/runtime/" + file })),
    ...web.map(file => ({ source: STAGE + "/frontend/" + file, target: FRONTEND + "/" + file }))];
  const before = await preferenceHashes(), records = [];
  for (const [index, file] of files.entries()) {
    const source = await fs.lstat(file.source), target = await optionalStat(file.target);
    if (!source.isFile() || source.nlink !== 1 || source.size > 1024 * 1024) throw new Error("Unsafe staged artifact");
    if (target && (!target.isFile() || target.nlink !== 1 || target.uid !== 0 || (target.mode & 0o022))) throw new Error("Unsafe installed artifact");
    const parent = await fs.lstat(path.dirname(file.target));
    if (!parent.isDirectory() || parent.uid !== 0 || (parent.mode & 0o022)) throw new Error("Unsafe installed parent");
    records.push({ ...file, saved: BACKUP + "/code-" + index, existed: Boolean(target), data: await fs.readFile(file.source) });
  }
  await fs.mkdir(BACKUP, { mode: 0o700 });
  for (const record of records) if (record.existed) {
    await fs.copyFile(record.target, record.saved); await fs.chmod(record.saved, 0o600);
  }
  await fs.writeFile(BACKUP + "/manifest.json", JSON.stringify({ preferences: before,
    files: records.map(({ target, saved, existed, data }) => ({ target, saved, existed, installedSha256: sha(data) })) }, null, 2) + "\n", { flag: "wx", mode: 0o600 });
  await service("stop", "tesla-waydroid-runtime.service");
  try {
    for (const record of records) {
      await fs.writeFile(record.target, record.data, { mode: 0o644 });
      await fs.chown(record.target, 0, 0); await fs.chmod(record.target, 0o644);
    }
    await require(ROOT + "/runtime/waydroid-display/runtime-preflight").verifyProtectedTree(ROOT + "/runtime");
    await service("start", "tesla-waydroid-runtime.service");
    const helper = require(ROOT + "/runtime/preview-gateway/display-helper-client").createHelperClient("/run/teslaandroid-waydroid/display.sock");
    const deadline = Date.now() + 90000; let ready;
    while (Date.now() < deadline) {
      try { ready = await helper.inspect(); if (!ready.recoveryRequired) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (!ready || ready.recoveryRequired) throw new Error("Display helper did not recover after planned restart");
    if (JSON.stringify(await preferenceHashes()) !== JSON.stringify(before)) throw new Error("User preferences changed during deployment");
    console.log(JSON.stringify({ deployed: true, backup: BACKUP, preferencesUnchanged: true, state: ready }));
  } catch (error) {
    await service("stop", "tesla-waydroid-runtime.service");
    for (const record of records) {
      if (record.existed) { await fs.copyFile(record.saved, record.target); await fs.chmod(record.target, 0o644); }
      else await fs.unlink(record.target).catch(problem => { if (problem.code !== "ENOENT") throw problem; });
    }
    await service("start", "tesla-waydroid-runtime.service");
    throw error;
  }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { deploymentProfile };
