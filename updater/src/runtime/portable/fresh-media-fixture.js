// SPDX-License-Identifier: GPL-3.0-only
// Unprivileged, bridge-only fixture. Its generated clip is never a source artifact.
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const { generateArgs, mediaFacts, sha } = require('./generated-playback');
const { createServer } = require('./generated-audio-fixture');
function main() {
  if (process.platform !== 'linux' || process.getuid() !== 1000 || process.argv.length !== 2) throw Error('Fixed unprivileged fresh media proof required');
  process.umask(0o077);
  const directory = fs.mkdtempSync('/home/hilman/.local/share/droiddeck-fresh-media-');
  execFileSync('/usr/bin/ffmpeg', generateArgs(directory), { timeout: 60000, stdio: ['ignore', 'pipe', 'pipe'] });
  const facts = mediaFacts(JSON.parse(execFileSync('/usr/bin/ffprobe', ['-v', 'error', '-count_frames',
    '-show_entries', 'stream=codec_type,codec_name,width,height,pix_fmt,nb_read_frames,r_frame_rate,duration,sample_rate,channels',
    '-of', 'json', directory + '/fixture.mp4'], { timeout: 30000, maxBuffer: 65536 })));
  const clip = fs.readFileSync(directory + '/fixture.mp4');
  if (clip.length > 8 * 1024 * 1024) throw Error('Bounded generated clip required');
  const server = createServer(clip);
  server.listen(18187, '192.168.240.1', () => console.log(JSON.stringify({ ...facts, sha256: sha(clip), bridgeOnly: true })));
  const close = () => { server.closeAllConnections(); server.close(); };
  process.once('SIGTERM', close); process.once('SIGINT', close);
}
if (require.main === module) { try { main(); } catch (_) { console.error('Fresh generated media fixture failed'); process.exitCode = 1; } }
