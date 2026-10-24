// SPDX-License-Identifier: GPL-3.0-only
const { setTimeout: delay } = require("node:timers/promises");
const ORIGIN = "https://teslavm.mondriandsm.synology.me";
function uptime(info) {
  if (info?.deviceInfoVersion !== 1 || !Number.isFinite(info.uptime_seconds) || info.uptime_seconds < 0) {
    throw new Error("Invalid public VM uptime");
  }
  return info.uptime_seconds;
}
async function waitForPublicBoot({ readInfo, sleep = delay, now = Date.now, report = () => {}, timeoutMs = 240000 } = {}) {
  if (typeof readInfo !== "function" || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 240000) {
    throw new Error("Bounded public boot probe required");
  }
  const before = uptime(await readInfo()), deadline = now() + timeoutMs;
  let observedDown = false;
  report({ waitingForReboot: true, beforeUptime: before });
  while (now() < deadline) {
    await sleep(2000);
    let info;
    try { info = await readInfo(); uptime(info); }
    catch (_error) {
      if (!observedDown) { observedDown = true; report({ publicUnavailable: true }); }
      continue;
    }
    const after = uptime(info);
    if (observedDown && after < before && after < 300) {
      const result = { publicBootRecovered: true, beforeUptime: before, afterUptime: after, sshUsedByProbe: false };
      report(result); return result;
    }
  }
  throw new Error("Public preview did not recover with a fresh VM uptime within four minutes");
}
async function readPublicInfo({ request = fetch } = {}) {
  const response = await request(ORIGIN + "/api/deviceInfo", { signal: AbortSignal.timeout(2000), redirect: "error" });
  if (!response.ok) throw new Error("Public preview unavailable");
  return response.json();
}
async function main() {
  if (process.argv.length !== 2) throw new Error("Public boot probe takes no arguments and never triggers a reboot");
  await waitForPublicBoot({ readInfo: readPublicInfo, report: result => console.log(JSON.stringify(result)) });
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { waitForPublicBoot, uptime, readPublicInfo };
