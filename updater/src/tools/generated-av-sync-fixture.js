// SPDX-License-Identifier: GPL-3.0-only
// Fixed generated flashes and tones; no capture of app/account/protected media.
const fs = require('node:fs/promises'), crypto = require('node:crypto');
const { execute } = require('../runtime/waydroid-display/host-adapter');
const { av1Facts } = require('../runtime/waydroid-display/generated-transition-fixture');
const DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av-sync-20261011s';
function generationArgs() {
  return ['-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=c=black:size=640x360:rate=30:duration=8',
    '-f', 'lavfi', '-i', "aevalsrc='0.05*sin(2*PI*1000*t)*gte(t,2)*lt(mod(t,1),0.15)':s=48000:d=8",
    '-vf', "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='gte(t,2)*lt(mod(t,1),0.15)'",
    '-t', '8', '-c:v', 'libaom-av1', '-cpu-used', '8', '-threads', '2', '-row-mt', '1',
    '-lag-in-frames', '0', '-crf', '38', '-b:v', '0', '-g', '30', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', DIRECTORY + '/fixture.mp4'];
}
function audioFacts(value) {
  const audio = value?.streams;
  if (!Array.isArray(audio) || audio.length !== 1 || audio[0].codec_name !== 'aac' ||
      audio[0].sample_rate !== '48000' || audio[0].channels !== 2 ||
      !/^0\.0+$/.test(audio[0].start_time || '') || !/^8\.0+$/.test(audio[0].duration || '')) {
    throw new Error('Generated audio alignment unavailable');
  }
  return { codec: 'aac', sampleRate: 48000, channels: 2, startSeconds: 0, durationSeconds: 8 };
}
async function main() {
  if (process.platform !== 'linux' || process.getuid?.() !== 1000 || process.argv.length !== 2) throw new Error('Fixed unprivileged generation required');
  try { await fs.mkdir(DIRECTORY, { mode: 0o700 }); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const info = await fs.lstat(DIRECTORY);
    if (!info.isDirectory() || info.uid !== 1000 || (info.mode & 0o077) || (await fs.readdir(DIRECTORY)).length) {
      throw new Error('Only an empty owned fixture directory may be retried');
    }
  }
  const oldMask = process.umask(0o077);
  try {
    const options = { env: process.env, timeoutMs: 60000 };
    await execute('/usr/bin/ffmpeg', generationArgs(), options);
    const video = av1Facts(JSON.parse((await execute('/usr/bin/ffprobe', ['-v', 'error', '-select_streams', 'v',
      '-count_frames', '-show_entries', 'stream=codec_name,width,height,pix_fmt,nb_read_frames,r_frame_rate,duration',
      '-of', 'json', DIRECTORY + '/fixture.mp4'], options)).toString()));
    const audio = audioFacts(JSON.parse((await execute('/usr/bin/ffprobe', ['-v', 'error', '-select_streams', 'a',
      '-show_entries', 'stream=codec_name,sample_rate,channels,start_time,duration', '-of', 'json',
      DIRECTORY + '/fixture.mp4'], options)).toString()));
    const bytes = await fs.readFile(DIRECTORY + '/fixture.mp4');
    if (bytes.length > 8 * 1024 * 1024) throw new Error('Generated fixture exceeded bound');
    const manifest = { version: 1, ...video, audio, bytes: bytes.length,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
      scope: 'Generated black/white flashes and1000Hz tones only, six events at2..7seconds' };
    await fs.writeFile(DIRECTORY + '/manifest.json', JSON.stringify(manifest) + '\n', { flag: 'wx', mode: 0o600 });
    console.log(JSON.stringify(manifest));
  } finally { process.umask(oldMask); }
}
if (require.main === module) main().catch(() => { console.error('Generated sync fixture unavailable; no runtime settings changed'); process.exitCode = 1; });
module.exports = { generationArgs, audioFacts };
