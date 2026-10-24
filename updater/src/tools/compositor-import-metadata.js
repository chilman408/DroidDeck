// SPDX-License-Identifier: GPL-3.0-only
const { execute } = require('../runtime/waydroid-display/host-adapter');
const COMMAND = /^\/usr\/bin\/weston --backend=pipewire --renderer=(?:pixman|gl) --socket=wayland-teslaandroid --width=1280 --height=720 --idle-time=0 --no-config --shell=kiosk-shell\.so$/;
const categories = Object.freeze({
  dmaBufImportFailure: /(?:fail(?:ed|ure)?|error|cannot|could not|invalid).*?(?:import.*?(?:dma.?buf|buffer)|dma.?buf)|(?:import.*?(?:dma.?buf|buffer)|dma.?buf).*?(?:fail(?:ed|ure)?|error|invalid)/i,
  unsupportedBufferFormat: /(?:unsupported|invalid|unrecognized).*?(?:buffer|format|modifier)|(?:buffer|format|modifier).*?(?:not supported|unsupported|invalid)/i,
  fenceFailure: /(?:fence|sync_wait|synchronization).*?(?:fail|error|timeout|timed out)|(?:fail|error|timeout|timed out).*?(?:fence|sync_wait|synchronization)/i,
  waylandProtocolFailure: /(?:protocol error|wayland.*?(?:disconnect|fail|error)|client.*?protocol.*?(?:fail|error))/i,
  rendererFailure: /(?:egl|gles|gl renderer).*?(?:fail|error)|(?:fail|error).*?(?:egl|gles|gl renderer)/i
});
function summarize(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 2 * 1024 * 1024) throw new Error('Invalid journal size');
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (lines.length > 2000) throw new Error('Invalid journal record count');
  const processes = new Set();
  let records = 0, ignoredForeignCommands = 0;
  const counts = Object.fromEntries(Object.keys(categories).map(key => [key, 0]));
  let unclassifiedWarningsOrErrors = 0;
  for (const line of lines) {
    const row = JSON.parse(line);
    if (row._EXE !== '/usr/bin/weston' || row._UID !== '1000' ||
        typeof row.MESSAGE !== 'string' || row.MESSAGE.length > 16384 ||
        typeof row._PID !== 'string' || !/^[1-9]\d{0,9}$/.test(row._PID) || Number(row._PID) < 2 ||
        typeof row._CMDLINE !== 'string' || row._CMDLINE.length > 4096 ||
        typeof row._SYSTEMD_CGROUP !== 'string' ||
        !/^(?:\/user\.slice\/user-1000\.slice\/session-c\d+\.scope|\/system\.slice\/tesla-waydroid-compositor\.service)$/.test(row._SYSTEMD_CGROUP)) {
      throw new Error('Foreign or malformed compositor metadata');
    }
    if (!COMMAND.test(row._CMDLINE)) { ignoredForeignCommands++; continue; }
    records++;
    processes.add(row._PID);
    let classified = false;
    for (const [key, pattern] of Object.entries(categories)) {
      if (pattern.test(row.MESSAGE)) { counts[key]++; classified = true; }
    }
    if (!classified && /\b(?:warning|error|failed|failure|fatal)\b/i.test(row.MESSAGE)) unclassifiedWarningsOrErrors++;
  }
  return { version: 1, records, compositorProcesses: processes.size, ignoredForeignCommands, ...counts,
    unclassifiedWarningsOrErrors, rawMessagesSaved: false, settingsWrites: false,
    scope: 'Recent compositor journal only; may include startup and recovery. Zero counts do not prove correct buffer contents.' };
}
async function inspect({ run = execute } = {}) {
  const result = await run('/usr/bin/journalctl', ['_EXE=/usr/bin/weston', '_UID=1000', '--boot', '--since=-20min',
    '--lines=2000', '--no-pager', '--output=json', '--output-fields=_EXE,_UID,_PID,_CMDLINE,_SYSTEMD_CGROUP,MESSAGE'],
  { env: { ...process.env, SYSTEMD_COLORS: '0' }, timeoutMs: 10000 });
  return summarize(result.toString());
}
if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length !== 2) {
    console.error('Fixed root compositor metadata probe required'); process.exitCode = 1;
  } else inspect().then(result => console.log(JSON.stringify(result))).catch(() => {
    console.error('Compositor metadata unavailable; no settings changed'); process.exitCode = 1;
  });
}
module.exports = { summarize, inspect };
