// SPDX-License-Identifier: GPL-3.0-only
const { execute } = require('./host-adapter');
const { guestContext, APPS } = require('./playback-probe');
const FIELDS = Object.freeze({
  width: [2, 7680], height: [2, 4320], errcode: [-0x80000000, 0x7fffffff],
  channelCount: [1, 32], sampleRate: [8000, 192000],
  encoder: [0, 1], hardware: [0, 1], secure: [0, 1], tunneled: [0, 1],
  'output-surface': [0, 1], 'flush-count': [0, 0x7fffffff],
  'set-surface-count': [0, 0x7fffffff], 'resolution-change-count': [0, 0x7fffffff],
  'video.input.frames': [0, 0x7fffffff], lifetimeMs: [0, 86400000]
});
const STATES = Object.freeze(['UNINITIALIZED', 'INITIALIZING', 'INITIALIZED', 'CONFIGURING',
  'CONFIGURED', 'STARTING', 'STARTED', 'FLUSHING', 'FLUSHED', 'STOPPING', 'RELEASING']);
const CODEC = /^(?:c2\.(?:android|google|ffmpeg)|OMX\.(?:google|android|ffmpeg))\.(?:avc|h264|hevc|vp8|vp9|av1|aac|ac3|eac3|opus|vorbis)\.decoder$/;
const MIME = /^(?:video\/(?:avc|hevc|av01|x-vnd\.on2\.vp[89])|audio\/(?:mp4a-latm|ac3|eac3(?:-joc)?|opus|vorbis))$/;

function targets(profile) {
  if (profile === 'paid-apps') return APPS;
  if (profile === 'generated-jelly') return ['org.lineageos.jelly'];
  if (profile === 'youtube') return ['com.google.android.youtube'];
  throw new Error('Unknown fixed metrics profile');
}

function packageIdentities(text, profile) {
  const apps = targets(profile);
  if (typeof text !== 'string' || Buffer.byteLength(text) > 2 * 1024 * 1024) throw new Error('Invalid package metadata');
  const identities = [], counts = new Map();
  for (const line of text.split('\n')) {
    const match = /^package:([a-zA-Z0-9_.]{1,200}) uid:([1-9]\d{0,9})$/.exec(line.trim());
    if (!match) continue;
    const uid = Number(match[2]);
    if (uid < 10000 || uid > 0x7fffffff) continue;
    counts.set(uid, (counts.get(uid) || 0) + 1);
    if (apps.includes(match[1])) identities.push({ app: match[1], uid });
  }
  return identities.filter(item => counts.get(item.uid) === 1);
}

function codecMetricFacts(text, packages, profile = 'paid-apps') {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 8 * 1024 * 1024) throw new Error('Invalid codec metrics');
  const identities = packageIdentities(packages, profile), records = [];
  let matched = 0, sourceRecords = 0, parsedRecords = 0, attributedRecords = 0;
  for (const line of text.split('\n').reverse()) {
    if (/^\s*\d+: \{codec,/.test(line)) sourceRecords++;
    if (line.length > 128 * 1024) continue;
    const match = /^\s*\d+: \{codec, \((\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\), \(([^,()\r\n]{1,200}), (-1|0|[1-9]\d{0,9}), ([1-9]\d{0,9})\), \((.*)\)\}$/.exec(line);
    if (!match) continue;
    parsedRecords++;
    const [, time, pkg, rawPid, rawUid, properties] = match;
    const parsedTime = new Date('2000-' + time.replace(' ', 'T') + 'Z');
    if (!Number.isFinite(parsedTime.getTime()) || parsedTime.toISOString().slice(5, 23) !== time.replace(' ', 'T')) continue;
    const pid = Number(rawPid), uid = Number(rawUid);
    const identity = identities.find(item => item.uid === uid && (pkg === item.app || pkg === rawUid));
    if (!identity || pid > 0x7fffffff) continue;
    attributedRecords++;
    const values = new Map(); let ambiguous = false;
    for (const property of properties.split(', ')) {
      const field = /^android\.media\.mediacodec\.([a-zA-Z0-9_.-]{1,64})=(.*)$/.exec(property);
      if (!field || !(Object.hasOwn(FIELDS, field[1]) || ['codec', 'mime', 'errstate'].includes(field[1]))) continue;
      if (values.has(field[1])) { ambiguous = true; break; }
      values.set(field[1], field[2]);
    }
    if (ambiguous) continue;
    const fields = {};
    for (const [name, [minimum, maximum]] of Object.entries(FIELDS)) {
      const value = values.get(name);
      if (typeof value !== 'string' || !/^-?\d{1,10}$/.test(value)) continue;
      const number = Number(value);
      if (number >= minimum && number <= maximum) fields[name] = number;
    }
    const codec = values.get('codec'), mime = values.get('mime'), state = values.get('errstate');
    if (CODEC.test(codec || '')) fields.codec = codec;
    if (MIME.test(mime || '')) fields.mime = mime;
    if (STATES.includes(state)) fields.errstate = state;
    if (!Object.keys(fields).length) continue;
    matched++;
    if (records.length < 128) records.push({ time, app: identity.app, pid: pid > 0 ? pid : null, fields });
  }
  return { version: 1, records: records.reverse(), truncated: matched > 128,
    coverage: { installedTargets: identities.map(item => item.app), sourceRecords, parsedRecords, attributedRecords },
    scope: 'Recent stored codec metadata; no raw analytics, session IDs, media, account or license data' };
}

async function inspectCodecMetrics(run = execute, profile = 'paid-apps') {
  targets(profile);
  const { pid, guest, discover } = await guestContext(run);
  const packages = await guest('cmd', ['package', 'list', 'packages', '-U']);
  const metrics = await guest('dumpsys', ['media.metrics', '--prefix', 'codec', '--since', '-3600']);
  if (!/^Dump of the media\.metrics process:\r?$/m.test(metrics)) throw new Error('Codec metrics unavailable');
  const result = codecMetricFacts(metrics, packages, profile);
  // Fence both the container and package-to-UID mapping across the dump.
  const after = await guest('cmd', ['package', 'list', 'packages', '-U']);
  if (JSON.stringify(packageIdentities(packages, profile)) !== JSON.stringify(packageIdentities(after, profile))) {
    throw new Error('Package identity changed during diagnostic');
  }
  if (await discover() !== pid) throw new Error('Container changed during diagnostic');
  return result;
}

if (require.main === module) {
  const profile = process.argv.length === 2 ? 'paid-apps' :
    process.argv.length === 3 && process.argv[2] === '--generated-jelly' ? 'generated-jelly' :
    process.argv.length === 3 && process.argv[2] === '--youtube' ? 'youtube' : null;
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || !profile) {
    console.error('Fixed read-only root codec metrics probe required'); process.exitCode = 1;
  } else inspectCodecMetrics(execute, profile).then(result => console.log(JSON.stringify(result))).catch(() => {
    console.error('Codec metrics inspection failed; no changes made'); process.exitCode = 1;
  });
}
module.exports = { codecMetricFacts, inspectCodecMetrics };
