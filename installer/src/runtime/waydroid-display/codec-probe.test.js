// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs');
const { inspectCodecs, inspectGenerated, JAR, DECODE_ROOT, TRANSITION_ROOT, AUDIO_TRANSITION_ROOT, AUDIO_SINK_ROOT } = require('./codec-probe');
const protectedIo = { lstat: async path => ({ uid: 0, mode: 0o644, nlink: 1, size: 1024,
  isFile: () => /\.(jar|mp4|m4a)$/.test(path), isDirectory: () => !/\.(jar|mp4|m4a)$/.test(path) }) };
const metadata = { version: 1, decoders: [], selected: Array(7).fill({ mime: 'video/avc', decoder: null }) };
const runner = calls => async (file, args) => {
  calls.push({ file, args });
  return Buffer.from(file.endsWith('lxc-info') ? '2036\n' : args.includes('cat') ?
    'export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /apex/com.android.art/javalib/core-oj.jar\n' : JSON.stringify(metadata));
};
test('codec inspection uses live boot classpaths, protected helper and fixed read-only commands', async () => {
  const calls = [];
  assert.deepEqual(await inspectCodecs(runner(calls), protectedIo), metadata);
  assert.equal(calls.length, 4);
  assert.ok(calls[2].args.includes('CLASSPATH=' + JAR));
  assert.ok(calls[2].args.includes('ANDROID_ART_ROOT=/apex/com.android.art'));
  assert.equal(calls[2].args.at(-1), 'TeslaCodecProbe');
});
test('generated decode uses a separate protected jar and only the selected fixed fixture', async () => {
  const calls = [], result = { fixture: 'avc', decoder: 'c2.android.avc.decoder', decodedFrames: 30, changedFrames: 29 };
  const base = runner(calls), paths = [];
  const run = async (file, args) => {
    if (args.includes('TeslaCodecProbe')) { calls.push({ file, args }); return Buffer.from(JSON.stringify(result)); }
    return base(file, args);
  };
  assert.deepEqual(await inspectGenerated('avc', 'android', run, { lstat: async path => {
    paths.push(path); return protectedIo.lstat(path);
  } }), result);
  assert.ok(paths.includes('/proc/2036/root' + DECODE_ROOT + '/avc.mp4'));
  assert.ok(calls[2].args.includes('CLASSPATH=' + DECODE_ROOT + '/decode-probe.jar'));
  assert.equal(calls[2].args.at(-1), '--decode-avc');
});
test('generated decode rejects arbitrary paths, stalled results and unsafe fixtures', async () => {
  await assert.rejects(inspectGenerated('../movie'), /Invalid generated fixture/);
  await assert.rejects(inspectGenerated('avc', 'arbitrary'), /Invalid generated fixture/);
  const run = async (file, args) => args.includes('TeslaCodecProbe') ? Buffer.from(JSON.stringify({
    fixture: 'hevc', decoder: 'c2.android.hevc.decoder', decodedFrames: 1, changedFrames: 0 })) : runner([])(file, args);
  await assert.rejects(inspectGenerated('hevc', 'android', run, protectedIo), /Invalid generated decoding result/);
  await assert.rejects(inspectGenerated('hevc', 'android', run, { lstat: async path => ({
    ...(await protectedIo.lstat(path)), mode: path.endsWith('.mp4') ? 0o666 : 0o644 }) }), /Unprotected/);
});
test('FFmpeg is explicit and cannot report a different decoder family', async () => {
  const calls = [], base = runner(calls);
  let decoder = 'c2.ffmpeg.hevc.decoder';
  const run = async (file, args) => {
    if (!args.includes('TeslaCodecProbe')) return base(file, args);
    calls.push({ file, args });
    return Buffer.from(JSON.stringify({ fixture: 'hevc10', decoder, decodedFrames: 30, changedFrames: 29 }));
  };
  assert.equal((await inspectGenerated('hevc10', 'ffmpeg', run, protectedIo)).decoder, decoder);
  assert.deepEqual(calls[2].args.slice(-2), ['--decode-hevc10', '--ffmpeg']);
  decoder = 'c2.android.hevc.decoder';
  await assert.rejects(inspectGenerated('hevc10', 'ffmpeg', run, protectedIo), /Invalid generated decoding result/);
});

test('generated size transition uses an isolated fixture and requires both output sizes', async () => {
  const calls = [], paths = [], base = runner(calls);
  const result = { fixture: 'avc-transition', decoder: 'c2.ffmpeg.h264.decoder', decodedFrames: 240,
    changedFrames: 239, outputSizes: ['864x486', '864x432'] };
  const run = async (file, args) => args.includes('TeslaCodecProbe') ?
    (calls.push({ file, args }), Buffer.from(JSON.stringify(result))) : base(file, args);
  assert.deepEqual(await inspectGenerated('avc-transition', 'ffmpeg', run, { lstat: async path => {
    paths.push(path); return protectedIo.lstat(path);
  } }), result);
  assert.ok(paths.includes('/proc/2036/root' + TRANSITION_ROOT + '/avc-transition.mp4'));
  assert.deepEqual(calls[2].args.slice(-2), ['--decode-avc-transition', '--ffmpeg']);
  await assert.rejects(inspectGenerated('avc-transition', 'android', run, protectedIo), /explicit FFmpeg/);
  result.outputSizes = ['864x486'];
  await assert.rejects(inspectGenerated('avc-transition', 'ffmpeg', run, protectedIo), /transition not verified/);
  result.outputSizes = ['864x486', '864x432']; result.decodedFrames = 120;
  await assert.rejects(inspectGenerated('avc-transition', 'ffmpeg', run, protectedIo), /Invalid generated/);
});

test('AAC sample-rate transition requires stock reuse, exact output rates and both protected inputs', async () => {
  const paths = [], calls = [], base = runner(calls);
  const result = { fixture: 'aac-transition', decoder: 'c2.android.aac.decoder', configurations: 2,
    decodedFrames: 184, outputRates: [48000, 44100] };
  const run = async (file, args) => args.includes('TeslaCodecProbe') ? Buffer.from(JSON.stringify(result)) : base(file, args);
  assert.deepEqual(await inspectGenerated('aac-transition', 'android', run, { lstat: async path => {
    paths.push(path); return protectedIo.lstat(path);
  } }), result);
  assert.ok(paths.includes('/proc/2036/root' + AUDIO_TRANSITION_ROOT + '/aac-48000.m4a'));
  assert.ok(paths.includes('/proc/2036/root' + AUDIO_TRANSITION_ROOT + '/aac-44100.m4a'));
  await assert.rejects(inspectGenerated('aac-transition', 'ffmpeg', run, protectedIo), /stock decoder/);
  for (const change of [{ outputRates: [48000, 48000] }, { configurations: 1 }, { decodedFrames: 0 }, { decoder: 'c2.ffmpeg.aac.decoder' }]) {
    const bad = async (file, args) => args.includes('TeslaCodecProbe') ? Buffer.from(JSON.stringify({ ...result, ...change })) : base(file, args);
    await assert.rejects(inspectGenerated('aac-transition', 'android', bad, protectedIo), /audio transition not verified/);
  }
});

test('muted AAC output trial is isolated and cannot claim a sink pass from decode-only metadata', async () => {
  const paths = [], base = runner([]);
  const result = { fixture: 'aac-sink-transition', decoder: 'c2.android.aac.decoder', configurations: 2,
    decodedFrames: 181, outputRates: [48000, 44100], audioSink: 'muted-pcm',
    consumedPcmFrames: [96256, 89088] };
  const run = async (file, args) => args.includes('TeslaCodecProbe') ? Buffer.from(JSON.stringify(result)) : base(file, args);
  assert.equal((await inspectGenerated('aac-sink-transition', 'android', run, { lstat: async path => {
    paths.push(path); return protectedIo.lstat(path);
  } })).audioSink, 'muted-pcm');
  assert.ok(paths.includes('/proc/2036/root' + AUDIO_SINK_ROOT + '/decode-probe.jar'));
  await assert.rejects(inspectGenerated('aac-sink-transition', 'ffmpeg', run, protectedIo), /stock decoder/);
  delete result.audioSink;
  await assert.rejects(inspectGenerated('aac-sink-transition', 'android', run, protectedIo), /PCM sink not verified/);
  result.audioSink = 'muted-pcm'; delete result.consumedPcmFrames;
  await assert.rejects(inspectGenerated('aac-sink-transition', 'android', run, protectedIo), /PCM sink consumption not verified/);
});

test('generated PCM sink uses bounded nonblocking writes rather than bypassing its deadline', () => {
  const source = fs.readFileSync(require('node:path').join(__dirname, 'TeslaCodecProbe.java'), 'utf8');
  assert.match(source, /int\.class, int\.class, int\.class \}, pcm, offset, size - offset, 1\)/);
  assert.match(source, /while \(offset < size && System\.nanoTime\(\) < writeDeadline\)/);
  assert.match(source, /if \(offset != size\) throw new IllegalStateException\("PCM sink stalled"\)/);
  assert.doesNotMatch(source, /\}, pcm, 0, size\)/);
  assert.match(source, /decodeAudioTransition\(args\[0\]\.equals\("--decode-aac-sink-transition"\)\);\s+System\.exit\(0\);/);
});
test('successful audio cleanup preserves the original failure stage', () => {
  const source = fs.readFileSync(require('node:path').join(__dirname, 'TeslaCodecProbe.java'), 'utf8');
  assert.match(source, /String previousStage = stage;\s+stage = "audio-codec-release";\s+call\(codec, "release", new Class<\?>\[0\]\);\s+stage = previousStage;/);
});

test('unprotected helpers, malformed metadata and changed identity fail closed', async () => {
  await assert.rejects(inspectCodecs(runner([]), { lstat: async path => ({ ...(await protectedIo.lstat(path)), uid: 1000 }) }), /Unprotected/);
  const calls = [], run = runner(calls);
  await assert.rejects(inspectCodecs(async (file, args) => calls.length === 3 ? Buffer.from('2037') : run(file, args), protectedIo), /Container changed/);
  await assert.rejects(inspectCodecs(async (file, args) => args.includes('TeslaCodecProbe') ? Buffer.from('{}') : runner([])(file, args), protectedIo), /Invalid codec/);
});
