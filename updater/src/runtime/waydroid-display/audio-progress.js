// SPDX-License-Identifier: GPL-3.0-only
const { createHostAdapter } = require('./host-adapter');

function parseAudioOutputs(text) {
  if (typeof text !== 'string' || text.includes('\0') || Buffer.byteLength(text) > 1048576) {
    throw new Error('Invalid audio output snapshot');
  }
  const outputs = [];
  // AudioFlinger also prints archived threads with a "- " prefix. They are not live outputs.
  const blocks = text.split(/(?=^Output thread )/m);
  for (const block of blocks) {
    const header = /^Output thread [^\n]*\btid (\d+), type 0 \(MIXER\):/m.exec(block);
    if (!header) continue;
    const live = block.split(/^- /m)[0];
    const number = pattern => {
      const match = pattern.exec(live);
      const value = match ? Number(match[1]) : NaN;
      if (!Number.isSafeInteger(value) || value < 0) throw new Error('Incomplete audio output snapshot');
      return value;
    };
    const standby = /^\s+Standby: (yes|no)\s*$/m.exec(live)?.[1];
    if (!standby) throw new Error('Missing output standby state');
    outputs.push({ tid: Number(header[1]), standby: standby === 'yes',
      lastWriteMs: number(/^\s+Last write occurred \(msecs\): (\d+)\s*$/m),
      frames: number(/^\s+Frames written: (\d+)\s*$/m),
      activeTracks: number(/^\s+\d+ Tracks of which (\d+) are active\s*$/m) });
  }
  if (outputs.some(output => !Number.isSafeInteger(output.tid) || output.tid < 1) ||
      new Set(outputs.map(output => output.tid)).size !== outputs.length) throw new Error('Invalid output identity');
  return outputs;
}

function compareAudioProgress(beforeText, afterText, elapsedMs) {
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) throw new Error('Invalid audio sample duration');
  const before = parseAudioOutputs(beforeText), after = parseAudioOutputs(afterText);
  if (!after.length) return { status: 'unknown', outputs: after };
  const active = after.filter(output => !output.standby && output.activeTracks > 0);
  if (!active.length) return { status: 'idle', outputs: after };
  let stalled = false, progressing = false, unknown = false;
  for (const output of active) {
    const previous = before.find(item => item.tid === output.tid);
    if (!previous || previous.standby || !previous.activeTracks || output.frames < previous.frames) unknown = true;
    else if (output.frames > previous.frames) progressing = true;
    else if (elapsedMs >= 10000 && previous.lastWriteMs >= 10000 && output.lastWriteMs >= 10000) stalled = true;
    else unknown = true;
  }
  return { status: stalled ? 'stalled' : unknown ? 'unknown' : progressing ? 'progressing' : 'unknown', outputs: after };
}

async function sampleAudioProgress(guest, sleep = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  const args = ['/system/bin/dumpsys', 'media.audio_flinger'];
  const before = (await guest(args)).toString();
  const start = performance.now();
  await sleep(10000);
  const after = (await guest(args)).toString();
  return compareAudioProgress(before, after, performance.now() - start);
}

if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid() !== 0 || process.argv.length !== 2) {
    process.stderr.write('Read-only audio progress probe requires Linux root and no arguments\n'); process.exitCode = 1;
  } else {
    sampleAudioProgress(createHostAdapter({ hostLibs: '/opt/teslaandroid-waydroid/libgbinder' }).guest)
      .then(result => { process.stdout.write(JSON.stringify(result) + '\n'); if (result.status === 'stalled') process.exitCode = 2; })
      .catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
  }
}
module.exports = { parseAudioOutputs, compareAudioProgress, sampleAudioProgress };
