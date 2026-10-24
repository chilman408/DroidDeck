// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const { execute, parseBootClasspaths } = require('./host-adapter');
const JAR = '/data/local/tmp/tesla-codec-probe-20261009/codec-probe.jar';
const DECODE_ROOT = '/data/local/tmp/tesla-codec-decode-20261010';
const TRANSITION_ROOT = '/data/local/tmp/tesla-codec-transition-20261010m';
const AUDIO_TRANSITION_ROOT = '/data/local/tmp/tesla-aac-transition-20261010n';
const AUDIO_SINK_ROOT = '/data/local/tmp/tesla-aac-sink-transition-20261010s';
const FIXTURES = Object.freeze({ avc: 'c2.android.avc.decoder', hevc: 'c2.android.hevc.decoder',
  hevc10: 'c2.android.hevc.decoder', av1: 'c2.android.av1-dav1d.decoder', 'avc-transition': null,
  'aac-transition': 'c2.android.aac.decoder', 'aac-sink-transition': 'c2.android.aac.decoder' });
const FFMPEG = Object.freeze({ avc: 'c2.ffmpeg.h264.decoder', hevc: 'c2.ffmpeg.hevc.decoder',
  hevc10: 'c2.ffmpeg.hevc.decoder', av1: 'c2.ffmpeg.av1.decoder', 'avc-transition': 'c2.ffmpeg.h264.decoder',
  'aac-transition': null, 'aac-sink-transition': null });
async function inspectCodecs(run = execute, io = fs) {
  return inspect(run, io);
}
async function inspectGenerated(fixture, family = 'android', run = execute, io = fs) {
  if (!Object.hasOwn(FIXTURES, fixture) || !['android', 'ffmpeg'].includes(family)) throw new Error('Invalid generated fixture');
  if (fixture === 'avc-transition' && family !== 'ffmpeg') throw new Error('Transition requires explicit FFmpeg');
  if (['aac-transition', 'aac-sink-transition'].includes(fixture) && family !== 'android') throw new Error('AAC transition requires stock decoder');
  return inspect(run, io, fixture, family);
}
async function inspect(run, io, fixture, family) {
  const transition = fixture === 'avc-transition', sink = fixture === 'aac-sink-transition';
  const audio = fixture === 'aac-transition' || sink;
  const options = { env: { ...process.env, LD_LIBRARY_PATH: '/opt/teslaandroid-waydroid/libgbinder' }, timeoutMs: transition || audio ? 45000 : 15000 };
  const discover = async () => {
    const pid = (await run('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], options)).toString().trim();
    if (!/^[1-9]\d*$/.test(pid) || !Number.isSafeInteger(Number(pid)) || Number(pid) < 2) throw new Error('Invalid container identity');
    return pid;
  };
  const pid = await discover();
  const root = sink ? AUDIO_SINK_ROOT : audio ? AUDIO_TRANSITION_ROOT : transition ? TRANSITION_ROOT : DECODE_ROOT;
  const jar = fixture ? root + '/decode-probe.jar' : JAR;
  const paths = [[jar, false], [jar.slice(0, jar.lastIndexOf('/')), true]];
  if (audio) paths.push([root + '/aac-48000.m4a', false], [root + '/aac-44100.m4a', false]);
  else if (fixture) paths.push([root + '/' + fixture + '.mp4', false]);
  for (const [filename, directory] of paths) {
    const info = await io.lstat('/proc/' + pid + '/root' + filename);
    if (info.uid !== 0 || (info.mode & 0o022) || !(directory ? info.isDirectory() : info.isFile() && info.nlink === 1 && info.size >= 100 && info.size < 1024 * 1024)) {
      throw new Error('Unprotected codec diagnostic');
    }
  }
  const guest = async args => (await run('/usr/bin/nsenter', ['-t', pid, '-m', '-p', '-r', '--', ...args], options)).toString();
  const boot = parseBootClasspaths(await guest(['/system/bin/toybox', 'cat', '/data/system/environ/classpath']));
  const output = await guest(['/system/bin/env', 'CLASSPATH=' + jar, 'ANDROID_ROOT=/system', 'ANDROID_DATA=/data',
    'ANDROID_ART_ROOT=/apex/com.android.art', 'ANDROID_I18N_ROOT=/apex/com.android.i18n', 'ANDROID_TZDATA_ROOT=/apex/com.android.tzdata',
    ...boot, '/system/bin/app_process', '/system/bin', 'TeslaCodecProbe',
    ...(fixture ? ['--decode-' + fixture, ...(family !== 'android' ? ['--' + family] : [])] : [])]);
  if (Buffer.byteLength(output) > 65536) throw new Error('Codec metadata too large');
  const result = JSON.parse(output);
  if (audio) {
    if (result.fixture !== fixture || result.decoder !== FIXTURES[fixture] || result.configurations !== 2 ||
        !Number.isInteger(result.decodedFrames) || result.decodedFrames < 60 || result.decodedFrames > 240 ||
        JSON.stringify(result.outputRates) !== JSON.stringify([48000, 44100])) throw new Error('Generated audio transition not verified');
    if (sink && result.audioSink !== 'muted-pcm') throw new Error('Generated muted PCM sink not verified');
    if (sink && (!Array.isArray(result.consumedPcmFrames) || result.consumedPcmFrames.length !== 2 ||
        !result.consumedPcmFrames.every(value => Number.isSafeInteger(value) && value >= 30000 && value <= 192000))) {
      throw new Error('Generated PCM sink consumption not verified');
    }
  } else if (fixture) {
    if (result.fixture !== fixture || result.decoder !== (family === 'ffmpeg' ? FFMPEG : FIXTURES)[fixture] ||
        !Number.isInteger(result.decodedFrames) || result.decodedFrames < (transition ? 230 : 8) || result.decodedFrames > (transition ? 300 : 120) ||
        !Number.isInteger(result.changedFrames) || result.changedFrames < 6 || result.changedFrames >= result.decodedFrames) {
      throw new Error('Invalid generated decoding result');
    }
    if (transition && JSON.stringify(result.outputSizes) !== JSON.stringify(['864x486', '864x432'])) {
      throw new Error('Generated decoder transition not verified');
    }
  } else if (result.version !== 1 || !Array.isArray(result.decoders) || result.decoders.length > 256 ||
      !Array.isArray(result.selected) || result.selected.length !== 7) throw new Error('Invalid codec metadata');
  if (await discover() !== pid) throw new Error('Container changed during diagnostic');
  return result;
}
if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length !== 2) {
    console.error('Fixed root codec diagnostic required'); process.exitCode = 1;
  } else inspectCodecs().then(result => console.log(JSON.stringify(result))).catch(() => {
    console.error('Codec inspection failed; no changes made'); process.exitCode = 1;
  });
}
module.exports = { inspectCodecs, inspectGenerated, JAR, DECODE_ROOT, TRANSITION_ROOT, AUDIO_TRANSITION_ROOT, AUDIO_SINK_ROOT };
