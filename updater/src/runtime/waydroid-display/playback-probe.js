// SPDX-License-Identifier: GPL-3.0-only
const { execute } = require('./host-adapter');
const APPS = Object.freeze(['com.amazon.avod.thirdpartyclient', 'com.apple.atve.androidtv.appletv']);
const TAGS = /^(?:CCodec|CCodecConfig|Codec2Client|Codec2InfoBuilder|ACodec|MediaCodec|MediaCodecList|MediaCodecVideoRenderer|MediaCodecRenderer|StagefrightPluginLoader|ExoPlayerImplInternal|NuPlayer|NuPlayerDecoder|OMXClient|OMXNodeInstance|MediaDrm|DrmHal|WVCdm|WVDrmPlugin|C2Soft\w+|C2FFMPEG\w+|Soft\w+|BufferQueueProducer|SurfaceFlinger)$/;
const CATEGORIES = Object.freeze({ codec: /codec|decoder|encoder|OMX|C2Soft/i,
  drm: /\bDRM\b|MediaDrm|license|HDCP|WVCdm|WVDrm/i,
  network: /network|HTTP|connection|timeout/i, surface: /surface|BufferQueue|graphic|buffer/i,
  unsupported: /unsupported|not supported|not implemented|not found|no suitable/i,
  failure: /fail|error|exception|fatal|abort|reject|denied|invalid/i });
const MIME = /\b(?:video\/(?:avc|hevc|av01|x-vnd\.on2\.vp[89])|audio\/(?:mp4a-latm|ac3|eac3(?:-joc)?|opus|vorbis))\b/g;
const DRM_OPERATIONS = Object.freeze(['InitializeAndCheckKeybox', 'Initialize', 'Terminate',
  'OpenSession', 'CloseSession', 'LoadKeys', 'RefreshKeys', 'QueryKeyControl', 'SelectKey',
  'DecryptCenc', 'DecryptCbcs', 'Decrypt', 'GetHdcpCapability', 'GetMetrics', 'GetProvisioningMethod']);

function playbackFacts(text, pids = []) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 8 * 1024 * 1024) throw new Error('Invalid playback diagnostic');
  const counts = {}, events = [], seen = new Map(), formatEvents = [];
  let previousFormat, formatTruncated = false;
  for (const line of text.split('\n').reverse()) {
    const brief = /^([VDIWEF])\/([^()\r\n]{1,100})\(\s*(\d+)\): (.*)$/.exec(line);
    const timed = /^(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+([^:\r\n]{1,100}): (.*)$/.exec(line);
    if (!brief && !timed) continue;
    const [priority, rawTag, rawPid, message] = brief ? brief.slice(1) : [timed[4], timed[5], timed[2], timed[6]];
    const pid = Number(rawPid), tid = timed ? Number(timed[3]) : null;
    if (!Number.isSafeInteger(pid) || pid < 1 || pid > 0x7fffffff ||
        (tid !== null && (!Number.isSafeInteger(tid) || tid < 1 || tid > 0x7fffffff))) continue;
    const tag = rawTag.trim();
    const knownTag = TAGS.test(tag), app = pids.find(item => item.pid === pid)?.app;
    if (!knownTag && !app) continue;
    const mime = [...new Set([...message.matchAll(MIME)].map(m => m[0]))].slice(0, 8);
    const format = {};
    for (const match of message.matchAll(/\b([a-z0-9_.-]*(?:width|height)|color-format|color-standard|color-transfer|color-range)\s*[:=]\s*(\d{1,10})\b/gi)) {
      const key = match[1].toLowerCase();
      if (/max[-.]|aspect|crop|stride|slice/.test(key)) continue;
      const name = key.split('.').at(-1), value = Number(match[2]);
      if (!['width', 'height', 'color-format', 'color-standard', 'color-transfer', 'color-range'].includes(name)) continue;
      if (tag === 'CCodecConfig' && ['width', 'height'].includes(key) && !/input.?format|output.?format/i.test(message)) continue;
      const maximum = name === 'width' ? 7680 : name === 'height' ? 4320 : 0xffffffff;
      if (value >= 0 && value <= maximum && (value >= 2 || !['width', 'height'].includes(name))) format[name] = value;
    }
    for (const match of message.matchAll(/(?<![a-z0-9_.-])(sample-rate|channel-count|raw\.sample-rate|raw\.channel-count)\s*[:=]\s*(\d{1,6})\b/gi)) {
      const key = match[1].toLowerCase().split('.').at(-1), value = Number(match[2]);
      if (key === 'sample-rate' ? value >= 8000 && value <= 192000 : value >= 1 && value <= 32) format[key] = value;
    }
    const transitions = Object.entries({ configure: /\bconfigur(?:e|ing)\b/i, flush: /\bflush(?:ing|ed)?\b/i,
      'output-format': /output.?format|onOutputFormatChanged/i, 'input-format': /input.?format/i,
      'surface-change': /setOutputSurface|surface.?chang/i, 'resolution-change': /resolution.?chang|size.?chang/i })
      .filter(([, pattern]) => pattern.test(message)).map(([name]) => name);
    if (tag === 'C2FFMPEGVideoDecodeComponent') {
      for (const [method, name] of [['onStop', 'decoder-stop'], ['onFlush_sm', 'decoder-flush'], ['onReset', 'decoder-reset']]) {
        if (message.trim() === method) transitions.push(name);
      }
    }
    if (Object.keys(format).length || transitions.length || mime.length) {
      const event = { time: timed?.[1] || null, pid, tid, source: knownTag ? tag : 'app', app: app || null,
        format, transitions, mime,
        codecs: [...message.matchAll(/\b(?:c2\.(?:android|google|ffmpeg)|OMX\.(?:google|android))\.[a-z0-9_.-]{1,80}\b/gi)].map(m => m[0]).slice(0, 8) };
      const key = JSON.stringify({ ...event, time: null });
      if (timed && previousFormat?.key === key) {
        previousFormat.event.occurrences = (previousFormat.event.occurrences || 1) + 1;
        previousFormat.event.firstTime = event.time;
      } else if (formatEvents.length < 128) {
        formatEvents.push(event); previousFormat = { key, event };
      } else { formatTruncated = true; previousFormat = null; }
    }
    const categories = Object.entries(CATEGORIES).filter(([, re]) => re.test(message)).map(([name]) => name);
    const discovery = tag === 'Codec2InfoBuilder' ? Object.entries({
      'interface-unavailable': /could not create interface/i, 'xml-parser-failure': /XML parser no good/i
    }).filter(([, pattern]) => pattern.test(message)).map(([reason]) => reason) : [];
    if (!categories.includes('failure') && !categories.includes('unsupported') && !/[WEF]/.test(priority) && !discovery.length) continue;
    for (const name of categories) counts[name] = (counts[name] || 0) + 1;
    // Only allowlisted metadata leaves this process; never emit messages, URLs or custom app tags.
    const codecs = [...message.matchAll(/\b(?:c2\.(?:android|google|ffmpeg)|OMX\.(?:google|android))\.[a-z0-9_.-]{1,80}\b/gi)].map(m => m[0]);
    const codes = [...message.matchAll(/\b(status|error(?:Code)?|err|result)\s*(?:[:=]|\s)\s*(-?\d{1,10}|0x[0-9a-f]{1,8})\b/gi)].map(m => ({ field: m[1].toLowerCase(), value: m[2] }));
    const markers = [...message.matchAll(/\b(?:SSLHandshakeException|SSLPeerUnverifiedException|UnknownHostException|ConnectException|SocketTimeoutException|DecoderInitializationException|ExoPlaybackException|PlaybackException|AudioSink\.(?:InitializationException|WriteException)|CryptoException|CodecException|UnsupportedSchemeException|NotProvisionedException|DeniedByServerException|MediaCodec\.error_neg_\d{1,10}|ERROR_(?:DRM|CODE|DECODING|DECODER|AUDIO|IO)_[A-Z0-9_]{1,64})\b/g)].map(m => m[0]);
    markers.push(...discovery);
    const drmOperations = tag === 'WVCdm' ? DRM_OPERATIONS.filter(name =>
      new RegExp('\\b(?:OEMCrypto_)?' + name + '\\b', 'i').test(message)) : [];
    if (tag === 'WVCdm') {
      if (/\bfalling back to L3\b/i.test(message)) markers.push('widevine-l3-fallback');
      if (/\bL1 not initialized\b/i.test(message)) markers.push('widevine-l1-unavailable');
    }
    if (tag === 'C2FFMPEGVideoDecodeComponent') {
      const stages = {
        'ffmpeg-codec-config-ignored': /^processCodecConfig: decoder is already opened, ignoring/i,
        'ffmpeg-graphic-fetch-failed': /^getOutputBuffer: failed to fetch graphic block/i,
        'ffmpeg-graphic-map-failed': /^getOutputBuffer: graphic view map failed/i,
        'ffmpeg-output-config-failed': /^outputFrame: config update failed/i,
        'ffmpeg-frame-download-failed': /^downloadFrame: failed to receive frame from HW decoder/i,
        'ffmpeg-eos-work-rejected': /^process: ignoring work while EOS reached/i
      };
      markers.push(...Object.entries(stages).filter(([, pattern]) => pattern.test(message)).map(([name]) => name));
    }
    const domains = markers.includes('UnknownHostException') ? [...message.matchAll(/\b(?:[a-z0-9-]+\.)*(?:amazon\.com|amazonaws\.com|amazonvideo\.com|media-amazon\.com|aiv-cdn\.net|aiv-delivery\.net|pv-cdn\.net|apple\.com|apple\.com\.akadns\.net)\b/gi)].map(m => m[0].toLowerCase()).filter(name => name.length <= 180) : [];
    const event = { time: timed?.[1] || null, pid, tid, priority, source: knownTag ? tag : 'app', app: app || null,
      categories, codecs: [...new Set(codecs)].slice(0, 8), mime: [...new Set(mime)].slice(0, 8),
      codes: codes.slice(0, 8), markers: [...new Set(markers)].slice(0, 8), drmOperations: drmOperations.slice(0, 8),
      publicDnsFailures: [...new Set(domains)].slice(0, 8) };
    // Keep the latest timestamp while coalescing identical repeated failures.
    const key = JSON.stringify({ ...event, time: null }), previous = seen.get(key);
    if (previous) previous.occurrences++;
    else if (events.length < 96) { event.occurrences = 1; seen.set(key, event); events.push(event); }
  }
  return { counts, events, truncated: events.length === 96, formatEvents: formatEvents.reverse(), formatTruncated };
}

const TRANSITION_TAGS = Object.freeze(['CCodec', 'CCodecConfig', 'Codec2Client', 'Codec2InfoBuilder',
  'ACodec', 'MediaCodec', 'MediaCodecList', 'MediaCodecVideoRenderer', 'MediaCodecRenderer',
  'ExoPlayerImplInternal', 'NuPlayer', 'NuPlayerDecoder', 'OMXClient', 'OMXNodeInstance',
  'C2FFMPEGVideoDecodeComponent', 'C2FFMPEGAudioDecodeComponent', 'C2SoftAvcDec', 'C2SoftAacDec',
  'MediaDrm', 'DrmHal', 'WVCdm', 'WVDrmPlugin', 'BufferQueueProducer', 'SurfaceFlinger']);

function transitionClock(value) {
  if (typeof value !== 'string' || !/^20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$/.test(value)) {
    throw new Error('Invalid transition clock');
  }
  const parsed = new Date(value.replace(' ', 'T') + 'Z');
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 23) !== value.replace(' ', 'T')) {
    throw new Error('Invalid transition clock');
  }
  return value;
}

async function guestContext(run) {
  const options = { env: { ...process.env, LD_LIBRARY_PATH: '/opt/teslaandroid-waydroid/libgbinder' }, timeoutMs: 10000 };
  const discover = async () => {
    const pid = (await run('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], options)).toString().trim();
    if (!/^[1-9]\d*$/.test(pid) || !Number.isSafeInteger(Number(pid)) || Number(pid) < 2) throw new Error('Invalid container identity');
    return pid;
  };
  const pid = await discover(), guest = async (file, args) => (await run('/usr/bin/nsenter',
    ['-t', pid, '-m', '-p', '-r', '--', '/system/bin/' + file, ...args], options)).toString();
  return { pid, guest, discover };
}

async function createAppleTransitionInspector(run = execute) {
  const { pid, guest, discover } = await guestContext(run);
  let since = transitionClock((await guest('date', ['+%Y-%m-%d %H:%M:%S.%3N'])).trim());
  if (await discover() !== pid) throw new Error('Container changed during diagnostic');
  return async () => {
    const result = await inspectAppleTransition(run, { since, containerPid: pid });
    since = result.window.nextSince;
    return result;
  };
}

async function inspectAppleTransition(run = execute, window = {}) {
  return inspectPlayback(run, true, window);
}

async function inspectPlayback(run = execute, appleOnly = false, { since = null, containerPid = null } = {}) {
  if (since !== null) transitionClock(since);
  const { pid, guest, discover } = await guestContext(run);
  if (containerPid !== null && containerPid !== pid) throw new Error('Container changed during diagnostic');
  const nextSince = since === null ? null : transitionClock((await guest('date', ['+%Y-%m-%d %H:%M:%S.%3N'])).trim());
  if (nextSince !== null && nextSince < since) throw new Error('Transition clock moved backward');
  const processes = [];
  for (const app of appleOnly ? [APPS[1]] : APPS) {
    const output = await guest('pidof', [app]).catch(() => '');
    if (appleOnly && !/^[1-9]\d{0,8}$/.test(output.trim())) throw new Error('Invalid Apple app identity');
    for (const item of output.trim().split(/\s+/)) {
      if (/^[1-9]\d{0,8}$/.test(item)) processes.push({ app, pid: Number(item) });
    }
  }
  const logArgs = ['-b', 'main', '-b', 'system', '-d', '-t', since || '10000', '-v', 'threadtime'];
  let text;
  if (appleOnly) {
    if (processes.length !== 1) throw new Error('Invalid Apple app identity');
    const appPid = String(processes[0].pid);
    // Independent filtered windows prevent another app's chatter hiding the transition.
    const appText = await guest('logcat', [...logArgs, '--pid=' + appPid]);
    const mediaText = await guest('logcat', [...logArgs, ...TRANSITION_TAGS.map(tag => tag + ':V'), '*:S']);
    if (Buffer.byteLength(appText) + Buffer.byteLength(mediaText) > 8 * 1024 * 1024) {
      throw new Error('Playback metadata too large');
    }
    if ((await guest('pidof', [APPS[1]])).trim() !== appPid) throw new Error('App changed during diagnostic');
    text = [...new Set((appText + '\n' + mediaText).split('\n'))].sort((a, b) =>
      a.slice(0, 18).localeCompare(b.slice(0, 18))).join('\n');
  } else text = await guest('logcat', logArgs);
  const facts = playbackFacts(text, processes);
  if (await discover() !== pid) throw new Error('Container changed during diagnostic');
  return { version: 1, activeApps: [...new Set(processes.map(p => p.app))], ...facts,
    ...(since === null ? {} : { window: { since, nextSince } }),
    scope: 'Recent bounded metadata only; no raw messages, media, credentials, licenses or app changes' };
}
if (require.main === module) {
  const appleOnly = process.argv.length === 3 && process.argv[2] === '--apple';
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || !(process.argv.length === 2 || appleOnly)) {
    console.error('Read-only probe requires Linux root and optional --apple'); process.exitCode = 1;
  } else (appleOnly ? inspectAppleTransition() : inspectPlayback()).then(result => console.log(JSON.stringify(result))).catch(() => {
    console.error('Playback inspection failed; no changes made'); process.exitCode = 1;
  });
}
module.exports = { playbackFacts, inspectPlayback, inspectAppleTransition, createAppleTransitionInspector, guestContext, APPS };
