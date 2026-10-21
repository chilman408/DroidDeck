// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const { codecMetricFacts, inspectCodecMetrics } = require('./codec-metrics-probe');
const APP = 'com.apple.atve.androidtv.appletv';
const PACKAGES = `package:${APP} uid:10100\npackage:private.other.app uid:10101\n`;
const item = (properties, pkg = APP, uid = 10100) =>
  `  1: {codec, (10-10 16:00:00.123), (${pkg}, 50, ${uid}), (${properties})}`;
const property = (name, value) => `android.media.mediacodec.${name}=${value}`;

test('stored codec metrics retain fixed numeric fields without session or private analytics', () => {
  const facts = codecMetricFacts(item([property('codec', 'c2.ffmpeg.h264.decoder'), property('mime', 'video/avc'),
    property('width', 864), property('height', 432), property('errcode', -22), property('errstate', 'STARTED'),
    property('flush-count', 2), property('log-session-id', 'private-session'), property('id', 'private-id'),
    'private.url=https://secret/token', property('maxheight', 1080)].join(', ')), PACKAGES);
  assert.deepEqual(facts.records, [{ time: '10-10 16:00:00.123', app: APP, pid: 50,
    fields: { width: 864, height: 432, errcode: -22, 'flush-count': 2,
      codec: 'c2.ffmpeg.h264.decoder', mime: 'video/avc', errstate: 'STARTED' } }]);
  assert.doesNotMatch(JSON.stringify(facts.records), /private|secret|token|maxheight|session/);
});

test('codec attribution requires unique installed package UID and exact sanitized package identity', () => {
  const fields = property('errcode', -22);
  assert.equal(codecMetricFacts(item(fields, '10100'), PACKAGES).records.length, 1);
  for (const [line, packages] of [[item(fields, 'private.other.app'), PACKAGES], [item(fields, APP, 10101), PACKAGES],
    [item(fields), PACKAGES + 'package:private.shared.app uid:10100\n'], [item(fields), '']]) {
    assert.equal(codecMetricFacts(line, packages).records.length, 0);
  }
});

test('generated browser profile is explicit, fixed and separate from paid-app attribution', async () => {
  const packages = PACKAGES + 'package:org.lineageos.jelly uid:10102\n';
  const browser = item(property('codec', 'c2.ffmpeg.h264.decoder'), 'org.lineageos.jelly', 10102);
  const apple = item(property('errcode', -22));
  assert.equal(codecMetricFacts(browser, packages).records.length, 0);
  const facts = codecMetricFacts(browser + '\n' + apple, packages, 'generated-jelly');
  assert.equal(facts.records.length, 1);
  assert.equal(facts.records[0].app, 'org.lineageos.jelly');
  assert.equal(codecMetricFacts(browser, packages + 'package:private.shared uid:10102\n', 'generated-jelly').records.length, 0);
  assert.throws(() => codecMetricFacts(browser, packages, 'private.app'), /Unknown fixed/);
  await assert.rejects(inspectCodecMetrics(() => { throw new Error('must not run'); }, 'private.app'), /Unknown fixed/);
});

test('YouTube profile reads only uniquely attributed YouTube codec metadata', async () => {
  const app = 'com.google.android.youtube';
  const packages = PACKAGES + `package:${app} uid:10103\n`;
  const youtube = item([property('codec', 'c2.ffmpeg.vp9.decoder'),
    property('mime', 'video/x-vnd.on2.vp9'), property('width', 1280),
    property('height', 720), property('log-session-id', 'secret')].join(', '), app, 10103);
  const apple = item(property('errcode', -22));
  assert.equal(codecMetricFacts(youtube, packages).records.length, 0);
  const facts = codecMetricFacts(youtube + '\n' + apple, packages, 'youtube');
  assert.deepEqual(facts.coverage.installedTargets, [app]);
  assert.deepEqual(facts.records[0]?.fields, { width: 1280, height: 720,
    codec: 'c2.ffmpeg.vp9.decoder', mime: 'video/x-vnd.on2.vp9' });
  assert.equal(facts.records.length, 1);
  assert.doesNotMatch(JSON.stringify(facts), /secret|appletv/);
  assert.equal(codecMetricFacts(youtube, packages + 'package:private.shared uid:10103\n', 'youtube').records.length, 0);
  assert.equal(codecMetricFacts(youtube, '', 'youtube').records.length, 0);
  const calls = [], run = async (file, args) => {
    calls.push({ file, args });
    if (file.endsWith('lxc-info')) return Buffer.from('1234');
    return Buffer.from(args.includes('/system/bin/cmd') ? packages :
      'Dump of the media.metrics process:\n' + youtube + '\n' + apple);
  };
  assert.equal((await inspectCodecMetrics(run, 'youtube')).records[0]?.app, app);
  assert.equal(calls.length, 5);
  assert.deepEqual(calls[2].args.slice(-6), ['/system/bin/dumpsys', 'media.metrics', '--prefix', 'codec', '--since', '-3600']);
});

test('unspecified metrics PID does not discard uniquely UID-attributed codec failures', () => {
  for (const unknown of ['0', '-1']) {
    const line = item(property('errcode', -22)).replace('50,', unknown + ',');
    const facts = codecMetricFacts(line, PACKAGES);
    assert.equal(facts.records[0]?.pid, null);
    assert.equal(facts.records[0]?.app, APP);
    assert.equal(facts.records[0]?.fields.errcode, -22);
    assert.equal(facts.coverage.attributedRecords, 1);
    assert.equal(codecMetricFacts(line, PACKAGES + 'package:private.shared.app uid:10100\n').records.length, 0);
  }
});

test('unsupported, ambiguous, oversized and malformed metrics do not escape allowlists', () => {
  const fields = [property('width', 99999999), property('height', 0), property('sampleRate', 1),
    property('errcode', 2147483648), property('codec', 'c2.android.private-token.decoder'),
    property('errstate', 'private-error'), property('mime', 'private/mime')].join(', ');
  assert.equal(codecMetricFacts(item(fields), PACKAGES).records.length, 0);
  assert.equal(codecMetricFacts(item(property('width', 864) + ', ' + property('width', 432)), PACKAGES).records.length, 0);
  assert.equal(codecMetricFacts(item(property('width', '864 token=secret')), PACKAGES).records.length, 0);
  assert.equal(codecMetricFacts(item(property('errcode', -22)).replace('50,', '2147483648,'), PACKAGES).records.length, 0);
  assert.equal(codecMetricFacts(item(property('errcode', -22)).replace('10-10 16:', '99-99 25:'), PACKAGES).records.length, 0);
  assert.throws(() => codecMetricFacts('x'.repeat(8 * 1024 * 1024 + 1), PACKAGES));
  assert.throws(() => codecMetricFacts('', 'x'.repeat(2 * 1024 * 1024 + 1)));
});

test('stored codec output is bounded and truncation reflects actual discarded records', () => {
  const line = item(property('errcode', -22));
  assert.equal(codecMetricFacts((line + '\n').repeat(128), PACKAGES).truncated, false);
  const facts = codecMetricFacts((line + '\n').repeat(129), PACKAGES);
  assert.equal(facts.records.length, 128); assert.equal(facts.truncated, true);
});

test('coverage distinguishes empty retention, unavailable attribution and unsupported record formatting', () => {
  assert.deepEqual(codecMetricFacts('', PACKAGES).coverage,
    { installedTargets: [APP], sourceRecords: 0, parsedRecords: 0, attributedRecords: 0 });
  assert.equal(codecMetricFacts(item(property('errcode', -22)), '').coverage.parsedRecords, 1);
  assert.equal(codecMetricFacts(item(property('errcode', -22)), '').coverage.attributedRecords, 0);
  const facts = codecMetricFacts('  1: {codec, unexpected private-format=secret}', PACKAGES);
  assert.equal(facts.coverage.sourceRecords, 1); assert.equal(facts.coverage.parsedRecords, 0);
  assert.doesNotMatch(JSON.stringify(facts), /private-format|secret/);
});

test('codec metrics use fixed read-only commands and fence installed package and container identities', async () => {
  const calls = [], run = async (file, args) => {
    calls.push({ file, args });
    if (file.endsWith('lxc-info')) return Buffer.from('1234');
    return Buffer.from(args.includes('/system/bin/cmd') ? PACKAGES : 'Dump of the media.metrics process:\n' + item(property('errcode', -22)));
  };
  assert.equal((await inspectCodecMetrics(run)).records[0].fields.errcode, -22);
  assert.equal(calls.length, 5);
  assert.deepEqual(calls[2].args.slice(-6), ['/system/bin/dumpsys', 'media.metrics', '--prefix', 'codec', '--since', '-3600']);
  let count = 0;
  await assert.rejects(inspectCodecMetrics(async (file, args) => Buffer.from(file.endsWith('lxc-info') ? '1234' :
    args.includes('/system/bin/cmd') ? (++count === 1 ? PACKAGES : '') : 'Dump of the media.metrics process:\n')), /Package identity changed/);
  let discovery = 0;
  await assert.rejects(inspectCodecMetrics(async (file, args) => Buffer.from(file.endsWith('lxc-info') ?
    (++discovery === 1 ? '1234' : '1235') : args.includes('/system/bin/cmd') ? PACKAGES : 'Dump of the media.metrics process:\n')), /Container changed/);
  await assert.rejects(inspectCodecMetrics(async (file, args) => Buffer.from(file.endsWith('lxc-info') ? '1234' :
    args.includes('/system/bin/cmd') ? PACKAGES : "Can't find service: media.metrics")), /Codec metrics unavailable/);
});
