// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const { parseAudioOutputs, compareAudioProgress } = require('./audio-progress');

function dump({ frames = 1831936, age = 14660781, active = 27, tid = 390, standby = 'no' } = {}) {
  return `Output thread 0x123, name AudioOut_D, tid ${tid}, type 0 (MIXER):\n` +
    `  Standby: ${standby}\n  Last write occurred (msecs): ${age}\n` +
    `  Frames written: ${frames}\n  27 Tracks of which ${active} are active\n` +
    '- Output thread 0x456, name AudioOut_95, tid 46054, type 0 (MIXER):\n' +
    '-   Frames written: 150528\n-   5 Tracks of which 0 are active\n';
}

test('ignores archived output threads and preserves actual playback counters', () => {
  assert.deepEqual(parseAudioOutputs(dump()), [{ tid: 390, standby: false,
    lastWriteMs: 14660781, frames: 1831936, activeTracks: 27 }]);
});
test('reports the observed blocked mixer without restarting anything', () => {
  assert.equal(compareAudioProgress(dump(), dump({ age: 14670781 }), 10000).status, 'stalled');
});
test('advancing frame counters establish output progress', () => {
  assert.equal(compareAudioProgress(dump(), dump({ frames: 2311936, age: 14 }), 10000).status, 'progressing');
});
test('idle output, short samples, replacement and counter reset cannot establish a stall', () => {
  assert.equal(compareAudioProgress(dump({ active: 0 }), dump({ active: 0 }), 10000).status, 'idle');
  assert.equal(compareAudioProgress(dump(), dump({ standby: 'yes' }), 10000).status, 'idle');
  assert.equal(compareAudioProgress(dump(), dump(), 1000).status, 'unknown');
  assert.equal(compareAudioProgress(dump(), dump({ tid: 55774 }), 10000).status, 'unknown');
  assert.equal(compareAudioProgress(dump(), dump({ frames: 100 }), 10000).status, 'unknown');
  assert.equal(compareAudioProgress(dump(), dump({ age: 5 }), 10000).status, 'unknown');
});
test('missing, malformed and oversized snapshots fail closed', () => {
  assert.equal(compareAudioProgress('', '', 10000).status, 'unknown');
  assert.throws(() => parseAudioOutputs(dump().replace('Frames written: 1831936', 'Frames written: nope')));
  assert.throws(() => parseAudioOutputs('x'.repeat(1048577)));
  assert.throws(() => compareAudioProgress(dump(), dump(), NaN));
});
