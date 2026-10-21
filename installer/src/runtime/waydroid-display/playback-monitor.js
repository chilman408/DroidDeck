// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const { constants } = require('node:fs');
const { performance } = require('node:perf_hooks');
const { inspectAppleTransition, createAppleTransitionInspector } = require('./playback-probe');
const DIRECTORY = '/var/lib/teslaandroid-waydroid/apple-transition-diagnostics-20261010i';
const OUTPUT = DIRECTORY + '/transition-metadata.jsonl';

async function sampleAppleTransition({ inspect = inspectAppleTransition, write,
  now = () => performance.now(), wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
  const deadline = now() + 180000, maximum = 2 * 1024 * 1024;
  let samples = 0, bytes = 0, previous, truncated = false;
  while (samples < 90 && now() < deadline) {
    let record;
    try { record = { available: true, metadata: await inspect() }; }
    catch { record = { available: false }; }
    samples++;
    const signature = JSON.stringify(record);
    if (signature !== previous) {
      const text = JSON.stringify({ elapsedMs: Math.max(0, Math.round(now() - (deadline - 180000))), ...record }) + '\n';
      if (Buffer.byteLength(text) > 256 * 1024 || bytes + Buffer.byteLength(text) > maximum - 1024) {
        truncated = true; break;
      }
      await write(text); bytes += Buffer.byteLength(text); previous = signature;
    }
    if (samples < 90 && now() < deadline) await wait(2000);
  }
  await write(JSON.stringify({ phase: truncated ? 'truncated' : 'complete', samples }) + '\n');
  return { samples, truncated };
}

async function main() {
  for (const path of ['/var', '/var/lib', '/var/lib/teslaandroid-waydroid', DIRECTORY]) {
    const stat = await fs.lstat(path);
    if (!stat.isDirectory() || stat.uid !== 0 || (stat.mode & 0o022)) throw new Error('Unprotected diagnostics directory');
  }
  const file = await fs.open(OUTPUT, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try {
    const inspect = await createAppleTransitionInspector();
    await sampleAppleTransition({ inspect, write: async text => { await file.writeFile(text); } });
    await file.sync();
  } finally { await file.close(); }
}

if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length !== 2) {
    console.error('Fixed root metadata sampler required'); process.exitCode = 1;
  } else main().catch(() => {
    console.error('Metadata sampling failed; no playback settings changed'); process.exitCode = 1;
  });
}
module.exports = { sampleAppleTransition, DIRECTORY, OUTPUT };
