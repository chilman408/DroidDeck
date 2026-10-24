// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const { playbackFacts, inspectPlayback, inspectAppleTransition, createAppleTransitionInspector } = require('./playback-probe');

test('Apple transition capture isolates app and codec windows, deduplicates and fences app identity', async () => {
  const calls = [], run = async (file, args) => {
    calls.push({ file, args });
    if (file.endsWith('lxc-info')) return Buffer.from('1234');
    if (args.includes('/system/bin/pidof')) return Buffer.from('50');
    if (args.includes('--pid=50')) return Buffer.from('10-10 14:00:01.123 50 51 E PrivateTag: PlaybackException errorCode=4003 token=secret\n' +
      '10-10 14:00:02.123 50 51 I CCodec: output-format width=864 height=432\n');
    return Buffer.from('10-10 14:00:02.123 50 51 I CCodec: output-format width=864 height=432\n' +
      '10-10 14:00:00.123 90 91 E C2FFMPEGVideoDecodeComponent: process: ignoring work while EOS reached\n');
  };
  const result = await inspectAppleTransition(run);
  assert.deepEqual(result.activeApps, ['com.apple.atve.androidtv.appletv']);
  assert.equal(result.formatEvents.length, 1);
  assert.deepEqual(result.events[0].markers, ['PlaybackException']);
  assert.deepEqual(result.events[1].markers, ['ffmpeg-eos-work-rejected']);
  assert.ok(calls.some(call => call.args.includes('--pid=50')));
  const media = calls.find(call => call.args.includes('*:S'));
  assert.ok(media.args.includes('C2FFMPEGVideoDecodeComponent:V'));
  assert.ok(media.args.includes('C2FFMPEGAudioDecodeComponent:V'));
  assert.doesNotMatch(JSON.stringify(result), /secret|PrivateTag|token/);
  let reads = 0;
  await assert.rejects(inspectAppleTransition(async (file, args) => Buffer.from(file.endsWith('lxc-info') ? '1234' :
    args.includes('/system/bin/pidof') ? (++reads === 1 ? '50' : '51') : '')), /App changed/);
});

test('Apple transition capture refuses absent, ambiguous or oversized app metadata', async () => {
  for (const pid of ['', '50 51', '50 unsafe', 'unsafe']) {
    let logRead = false;
    await assert.rejects(inspectAppleTransition(async (file, args) => {
      if (args.includes('/system/bin/logcat')) logRead = true;
      return Buffer.from(file.endsWith('lxc-info') ? '1234' : pid);
    }), /Apple app identity/);
    assert.equal(logRead, false);
  }
  await assert.rejects(inspectAppleTransition(async (file, args) => Buffer.from(file.endsWith('lxc-info') ? '1234' :
    args.includes('/system/bin/pidof') ? '50' : 'x'.repeat(8 * 1024 * 1024 + 1))), /metadata too large/);
});

test('fresh sampler anchors guest time before the app opens and advances only after a fenced read', async () => {
  let clock = 0, open = false, container = '1234';
  const reads = [], run = async (file, args) => {
    if (file.endsWith('lxc-info')) return Buffer.from(container);
    if (args.includes('/system/bin/date')) return Buffer.from(`2026-10-10 16:00:0${clock++}.123`);
    if (args.includes('/system/bin/pidof')) return Buffer.from(open ? '50' : '');
    reads.push(args); return Buffer.from('');
  };
  const inspect = await createAppleTransitionInspector(run);
  await assert.rejects(inspect(), /Apple app identity/);
  assert.equal(reads.length, 0);
  open = true;
  const first = await inspect();
  assert.deepEqual(first.window, { since: '2026-10-10 16:00:00.123', nextSince: '2026-10-10 16:00:02.123' });
  assert.ok(reads.every(args => args.includes('2026-10-10 16:00:00.123') && !args.includes('10000')));
  reads.length = 0;
  const next = await inspect();
  assert.equal(next.window.since, first.window.nextSince);
  assert.ok(reads.every(args => args.includes(first.window.nextSince)));
  container = '1235';
  await assert.rejects(inspect(), /Container changed/);
});

test('fresh capture rejects malformed guest clocks and backward clocks without advancing', async () => {
  for (const since of ['2026-02-30 16:00:00.123', '2026-10-10 25:00:00.123', 'unsafe token=secret', '2026-10-10 16:00:00.123\n']) {
    let called = false;
    await assert.rejects(inspectAppleTransition(async () => { called = true; }, { since }), /Invalid transition clock/);
    assert.equal(called, false);
  }
  let clock = '2026-10-10 16:00:02.123';
  const run = async (file, args) => Buffer.from(file.endsWith('lxc-info') ? '1234' :
    args.includes('/system/bin/date') ? clock : args.includes('/system/bin/pidof') ? '50' : '');
  const inspect = await createAppleTransitionInspector(run);
  clock = '2026-10-10 16:00:01.123';
  await assert.rejects(inspect(), /Transition clock moved backward/);
  clock = '2026-10-10 16:00:03.123';
  assert.equal((await inspect()).window.since, '2026-10-10 16:00:02.123');
  await assert.rejects(createAppleTransitionInspector(async (file) => Buffer.from(file.endsWith('lxc-info') ? '1234' : 'bad clock')), /Invalid transition clock/);
});

test('failed fresh reads retain their window and initialization fences the container', async () => {
  let fail = true, clock = 0;
  const run = async (file, args) => {
    if (file.endsWith('lxc-info')) return Buffer.from('1234');
    if (args.includes('/system/bin/date')) return Buffer.from(`2026-10-10 16:00:0${clock++}.123`);
    if (args.includes('/system/bin/pidof')) return Buffer.from('50');
    if (fail) throw new Error('read failed');
    return Buffer.from('');
  };
  const inspect = await createAppleTransitionInspector(run);
  await assert.rejects(inspect(), /read failed/);
  fail = false;
  assert.equal((await inspect()).window.since, '2026-10-10 16:00:00.123');
  let discovery = 0;
  await assert.rejects(createAppleTransitionInspector(async file => Buffer.from(file.endsWith('lxc-info') ?
    (++discovery === 1 ? '1234' : '1235') : '2026-10-10 16:00:00.123')), /Container changed/);
});
test('only playback metadata is emitted, including app-specific failures', () => {
  const result = playbackFacts('E/MediaCodec( 10): configure failed c2.android.avc.decoder video/avc status=-22 https://secret/token\n' +
    'E/PrivateAccountTag( 20): license errorCode=123 token=secret\n' +
    'E/Unrelated( 30): error account=email@example.com\n', [{ app: 'com.amazon.avod.thirdpartyclient', pid: 20 }]);
  assert.equal(result.events.length, 2); assert.equal(result.counts.failure, 2);
  assert.deepEqual(result.events[1].codecs, ['c2.android.avc.decoder']);
  assert.deepEqual(result.events[1].codes, [{ field: 'status', value: '-22' }]);
  assert.equal(result.events[0].source, 'app');
  assert.doesNotMatch(JSON.stringify(result), /secret|PrivateAccount|email|https/);
});

test('process and thread identities prevent unrelated decoder events being merged', () => {
  const facts = playbackFacts('10-10 16:00:00.123 50 51 E MediaCodec: error status=-22 token=secret\n' +
    '10-10 16:00:01.123 90 91 E MediaCodec: error status=-22 token=secret\n' +
    '10-10 16:00:02.123 90 92 E MediaCodec: error status=-22 token=secret\n' +
    '10-10 16:00:03.123 90 92 E MediaCodec: error status=-22 token=secret\n',
    [{ app: 'com.apple.atve.androidtv.appletv', pid: 50 }]);
  assert.deepEqual(facts.events.map(e => [e.pid, e.tid, e.app, e.occurrences]), [
    [90, 92, null, 2], [90, 91, null, 1], [50, 51, 'com.apple.atve.androidtv.appletv', 1]
  ]);
  const formats = playbackFacts('10-10 16:00:00.123 90 91 I CCodec: output-format width=864 height=432\n' +
    '10-10 16:00:00.124 90 92 I CCodec: output-format width=864 height=432\n');
  assert.deepEqual(formats.formatEvents.map(e => [e.pid, e.tid]), [[90, 91], [90, 92]]);
  const brief = playbackFacts('E/MediaCodec( 90): error status=-22');
  assert.equal(brief.events[0].pid, 90);
  assert.equal(brief.events[0].tid, null);
  assert.doesNotMatch(JSON.stringify(facts), /secret|token/);
});

test('malformed or out-of-range diagnostic identities are excluded', () => {
  const facts = playbackFacts('E/MediaCodec( 999999999999999999999999): error status=-22\n' +
    'E/MediaCodec( 0): error status=-22\n' +
    '10-10 16:00:00.123 50 2147483648 E MediaCodec: error status=-22\n' +
    '10-10 16:00:00.124 2147483648 51 E MediaCodec: error status=-22\n');
  assert.deepEqual(facts.events, []);
  assert.deepEqual(facts.formatEvents, []);
});
test('informational codec traffic is excluded and input/output are bounded', () => {
  assert.equal(playbackFacts('I/CCodec( 1): created c2.android.avc.decoder').events.length, 0);
  const result = playbackFacts('E/MediaDrm( 1): error status=0x8000\n'.repeat(120));
  assert.equal(result.events.length, 1); assert.equal(result.events[0].occurrences, 120);
  assert.equal(playbackFacts(Array.from({ length: 120 }, (_, n) => `E/MediaDrm( 1): error status=${n}`).join('\n')).truncated, true);
  assert.throws(() => playbackFacts('x'.repeat(8 * 1024 * 1024 + 1)));
});
test('probe uses fixed read-only commands and fences container identity', async () => {
  const calls = [], run = async (file, args, options) => {
    calls.push({ file, args }); assert.equal(options.timeoutMs, 10000);
    return Buffer.from(file.endsWith('lxc-info') ? '1234\n' : args.includes('pidof') ? '45' :
      'E/MediaCodec( 45): decoder failure status=-12');
  };
  const result = await inspectPlayback(run);
  assert.equal(result.events.length, 1); assert.equal(calls.length, 5);
  assert.deepEqual(calls[3].args.slice(-10), ['/system/bin/logcat', '-b', 'main', '-b', 'system', '-d', '-t', '10000', '-v', 'threadtime']);
  let reads = 0;
  await assert.rejects(inspectPlayback(async file => Buffer.from(file.endsWith('lxc-info') ? String(++reads === 1 ? 1234 : 1235) : '')), /Container changed/);
});
test('format transitions preserve chronological safe dimensions, not raw app data', () => {
  const facts = playbackFacts('10-10 05:01:02.123 40 41 I CCodecConfig: output-format width=1920 height=1080 color-transfer=6 token=secret\n' +
    '10-10 05:01:03.234 50 51 I PrivateTag: onOutputFormatChanged width: 1280 height: 720 https://private/movie\n' +
    '10-10 05:01:04.345 60 61 I Unrelated: output-format width=640 height=480\n',
    [{ app: 'com.apple.atve.androidtv.appletv', pid: 50 }]);
  assert.equal(facts.events.length, 0);
  assert.equal(facts.formatEvents.length, 2);
  assert.equal(facts.formatEvents[0].time, '10-10 05:01:02.123');
  assert.deepEqual(facts.formatEvents[0].format, { width: 1920, height: 1080, 'color-transfer': 6 });
  assert.deepEqual(facts.formatEvents[1].format, { width: 1280, height: 720 });
  assert.equal(facts.formatEvents[1].source, 'app');
  assert.doesNotMatch(JSON.stringify(facts), /secret|PrivateTag|https|movie/);
});
test('format values and transition observations are bounded independently from failures', () => {
  const facts = playbackFacts('I/CCodecConfig( 1): output-format width=9999999999 height=0\n'.repeat(200));
  assert.equal(facts.formatEvents.length, 128);
  assert.equal(facts.formatTruncated, true);
  assert.deepEqual(facts.formatEvents[0].format, {});
  const failure = playbackFacts('10-10 05:01:02.123 40 41 E MediaCodec: failure status=-22');
  assert.deepEqual(failure.events[0].codes, [{ field: 'status', value: '-22' }]);
});
test('pixel aspect ratio and maximum sizes are not mistaken for video dimension changes', () => {
  const facts = playbackFacts('I/CCodecConfig( 1): raw.pixel-aspect-ratio.width = 1\n' +
    'I/CCodecConfig( 1): raw.max-size.width = 320\n' +
    'I/CCodec( 1): max-height = 240\n' +
    'I/CCodecConfig( 1): width = 1\n' +
    'I/CCodecConfig( 1): height = 240\n' +
    'I/CCodecConfig( 1): raw.size.width = 864\n');
  assert.deepEqual(facts.formatEvents.map(event => event.format), [{ width: 864 }]);
});
test('FFmpeg component errors retain allowlisted numeric status without raw messages', () => {
  const facts = playbackFacts('E/C2FFMPEGVideoDecodeComponent( 33): failure err -22 token=secret\n');
  assert.deepEqual(facts.events[0].codes, [{ field: 'err', value: '-22' }]);
  assert.doesNotMatch(JSON.stringify(facts), /secret|token/);
});

test('FFmpeg lifecycle failures distinguish configuration reuse from graphic buffer failures', () => {
  const result = playbackFacts('W/C2FFMPEGVideoDecodeComponent( 33): processCodecConfig: decoder is already opened, ignoring... token=secret\n' +
    'E/C2FFMPEGVideoDecodeComponent( 33): getOutputBuffer: failed to fetch graphic block 864 x 486 err = -12 https://private\n' +
    'E/C2FFMPEGVideoDecodeComponent( 33): getOutputBuffer: graphic view map failed err = -22\n' +
    'E/C2FFMPEGVideoDecodeComponent( 33): downloadFrame: failed to receive frame from HW decoder err = -5\n' +
    'E/C2FFMPEGVideoDecodeComponent( 33): process: ignoring work while EOS reached\n' +
    'W/PrivateTag( 50): processCodecConfig: decoder is already opened, ignoring...\n',
    [{ app: 'com.apple.atve.androidtv.appletv', pid: 50 }]);
  assert.deepEqual(result.events.map(event => event.markers), [[], ['ffmpeg-eos-work-rejected'],
    ['ffmpeg-frame-download-failed'], ['ffmpeg-graphic-map-failed'],
    ['ffmpeg-graphic-fetch-failed'], ['ffmpeg-codec-config-ignored']]);
  assert.doesNotMatch(JSON.stringify(result), /secret|private|token|https/);
});

test('exact FFmpeg lifecycle methods and output reconfiguration failures stay bounded and tag gated', () => {
  const facts = playbackFacts('D/C2FFMPEGVideoDecodeComponent( 33): onStop\n' +
    'D/C2FFMPEGVideoDecodeComponent( 33): onFlush_sm\n' +
    'D/C2FFMPEGVideoDecodeComponent( 33): onReset\n' +
    'E/C2FFMPEGVideoDecodeComponent( 33): outputFrame: config update failed err = -22 token=secret\n' +
    'I/PrivateTag( 50): onStop account=private\n', [{ app: 'com.apple.atve.androidtv.appletv', pid: 50 }]);
  assert.deepEqual(facts.formatEvents.map(e => e.transitions), [['decoder-stop'], ['decoder-flush'], ['decoder-reset']]);
  assert.deepEqual(facts.events[0].markers, ['ffmpeg-output-config-failed']);
  assert.deepEqual(facts.events[0].codes, [{ field: 'err', value: '-22' }]);
  assert.doesNotMatch(JSON.stringify(facts), /secret|private|token|account/);
});
test('exception markers are allowlisted, repeated noise coalesces, latest errors take priority', () => {
  const result = playbackFacts('E/MediaCodec( 1): error status=-22\n'.repeat(200) +
    'E/MediaCodec( 1): DecoderInitializationException c2.ffmpeg.hevc.decoder https://secret\n');
  assert.deepEqual(result.events[0].markers, ['DecoderInitializationException']);
  assert.deepEqual(result.events[0].codecs, ['c2.ffmpeg.hevc.decoder']);
  assert.equal(result.events[1].occurrences, 200);
  assert.doesNotMatch(JSON.stringify(result), /secret/);
});
test('DNS evidence excludes private hosts, URL paths and query tokens', () => {
  const result = playbackFacts('W/MediaCodec( 1): UnknownHostException https://api.amazon.com/private?token=secret private.internal\n');
  assert.deepEqual(result.events[0].publicDnsFailures, ['api.amazon.com']);
  assert.doesNotMatch(JSON.stringify(result), /private|secret|token/);
});

test('audio transition metadata includes only fixed MIME and bounded numeric format fields', () => {
  const facts = playbackFacts('10-10 08:38:20.123 50 51 I PrivateTag: input-format audio/mp4a-latm sample-rate=48000 channel-count=2 token=secret\n' +
    '10-10 08:38:22.123 50 51 I CCodecConfig: media-type = audio/eac3-joc raw.sample-rate = 48000 raw.channel-count = 6 account=private\n' +
    'I/CCodecConfig( 50): raw.sample-rate = 999999 raw.channel-count = 99\n',
    [{ app: 'com.apple.atve.androidtv.appletv', pid: 50 }]);
  assert.equal(facts.formatEvents.length, 2);
  assert.deepEqual(facts.formatEvents[0].mime, ['audio/mp4a-latm']);
  assert.deepEqual(facts.formatEvents[1].mime, ['audio/eac3-joc']);
  assert.deepEqual(facts.formatEvents[1].format, { 'sample-rate': 48000, 'channel-count': 6 });
  assert.doesNotMatch(JSON.stringify(facts), /secret|private|token|account/);
});

test('maximum audio capabilities are never reported as active audio format', () => {
  const facts = playbackFacts('I/CCodec( 1): max-channel-count=8\n' +
    'I/CCodecConfig( 1): raw.max-channel-count=8 max-sample-rate=192000\n' +
    'I/CCodec( 1): channel-count=2 sample-rate=48000\n');
  assert.deepEqual(facts.formatEvents.map(e => e.format), [{ 'channel-count': 2, 'sample-rate': 48000 }]);
});

test('duplicate format observations do not crowd out a real transition', () => {
  const facts = playbackFacts('10-10 15:00:00.000 1 2 I CCodec: output-format width=864 height=486\n' +
    '10-10 15:00:01.000 1 2 I CCodec: output-format width=864 height=432\n' +
    '10-10 15:00:02.000 1 2 I CCodec: output-format width=864 height=432\n'.repeat(200));
  assert.equal(facts.formatEvents.length, 2);
  assert.equal(facts.formatTruncated, false);
  assert.equal(facts.formatEvents[0].format.height, 486);
  assert.equal(facts.formatEvents[1].format.height, 432);
  assert.equal(facts.formatEvents[1].occurrences, 201);
});

test('numeric hexadecimal error codes are not shortened to zero', () => {
  const facts = playbackFacts('E/MediaCodec( 1): error status=0x80001001 account=secret\n');
  assert.deepEqual(facts.events[0].codes, [{ field: 'status', value: '0x80001001' }]);
});

test('error timestamps survive noise coalescing and player/audio exception markers stay allowlisted', () => {
  const facts = playbackFacts('10-10 08:38:20.123 50 51 E PrivateTag: ExoPlaybackException AudioSink.WriteException errorCode=5002 audio/eac3-joc token=secret\n' +
    '10-10 08:38:22.123 50 51 E PrivateTag: ExoPlaybackException AudioSink.WriteException errorCode=5002 audio/eac3-joc token=secret\n',
    [{ app: 'com.apple.atve.androidtv.appletv', pid: 50 }]);
  assert.equal(facts.events.length, 1);
  assert.equal(facts.events[0].occurrences, 2);
  assert.equal(facts.events[0].time, '10-10 08:38:22.123');
  assert.deepEqual(facts.events[0].markers, ['ExoPlaybackException', 'AudioSink.WriteException']);
  assert.deepEqual(facts.events[0].mime, ['audio/eac3-joc']);
  assert.doesNotMatch(JSON.stringify(facts), /secret|PrivateTag|token/);
});

test('framework codec discovery failures retain only fixed reasons and known codec names', () => {
  const facts = playbackFacts('10-10 09:00:00.123 90 91 D Codec2InfoBuilder: could not create interface for c2.android.avc.decoder account=secret\n' +
    '10-10 09:00:00.124 90 91 D Codec2InfoBuilder: XML parser no good /private/path\n');
  assert.equal(facts.events.length, 2);
  assert.deepEqual(facts.events[0].markers, ['xml-parser-failure']);
  assert.deepEqual(facts.events[1].markers, ['interface-unavailable']);
  assert.deepEqual(facts.events[1].codecs, ['c2.android.avc.decoder']);
  assert.doesNotMatch(JSON.stringify(facts), /secret|account|private|path/);
});

test('Widevine initialization fallback is distinguished from decrypt failure without sensitive context', () => {
  const facts = playbackFacts('10-10 13:15:19.008 90 91 W WVCdm: [private.cpp(1549):OEMCrypto_InitializeAndCheckKeybox] Keybox error: 25. Falling back to L3. token=secret\n' +
    '10-10 13:15:19.009 90 91 E WVCdm: OEMCrypto_DecryptCenc error=4 session=private\n');
  assert.deepEqual(facts.events[0].drmOperations, ['DecryptCenc']);
  assert.deepEqual(facts.events[1].drmOperations, ['InitializeAndCheckKeybox']);
  assert.deepEqual(facts.events[1].markers, ['widevine-l3-fallback']);
  assert.deepEqual(facts.events[1].codes, [{ field: 'error', value: '25' }]);
  assert.doesNotMatch(JSON.stringify(facts), /secret|private|token|session|\.cpp/);
});

test('Widevine operation names and fallback reasons are exact-tag gated and allowlisted', () => {
  const facts = playbackFacts('W/PrivateTag( 50): OEMCrypto_DecryptCenc error=25 Falling back to L3\n' +
    'W/WVCdm( 90): PrivateAccountMethod error=26\n' +
    'E/WVCdm( 90): L1 not initialized. Falling back to L3\n' +
    'W/WVCdm( 90): Private_DecryptCencSuffix error=25\n',
    [{ app: 'com.apple.atve.androidtv.appletv', pid: 50 }]);
  assert.deepEqual(facts.events.map(event => event.drmOperations), [[], [], [], []]);
  assert.deepEqual(facts.events[1].markers, ['widevine-l3-fallback', 'widevine-l1-unavailable']);
  assert.deepEqual(facts.events[3].markers, []);
  assert.doesNotMatch(JSON.stringify(facts), /Private|AccountMethod|DecryptCencSuffix/);
});
