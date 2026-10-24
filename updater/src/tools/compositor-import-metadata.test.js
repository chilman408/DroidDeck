// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { summarize, inspect } = require('./compositor-import-metadata');
const command = '/usr/bin/weston --backend=pipewire --renderer=gl --socket=wayland-teslaandroid --width=1280 --height=720 --idle-time=0 --no-config --shell=kiosk-shell.so';
const row = (MESSAGE, extra = {}) => JSON.stringify({ _EXE: '/usr/bin/weston', _UID: '1000', _PID: '123',
  _CMDLINE: command, _SYSTEMD_CGROUP: '/user.slice/user-1000.slice/session-c43.scope', MESSAGE, ...extra });
test('counts import, format, fence, protocol and renderer failures without returning messages', () => {
  const text = ['failed to import dmabuf PRIVATE', 'unsupported buffer format PRIVATE',
    'fence wait timed out PRIVATE', 'Wayland protocol error PRIVATE', 'EGL image creation failed PRIVATE',
    'warning PRIVATE'].map(message => row(message)).join('\n');
  const value = summarize(text);
  for (const key of ['dmaBufImportFailure', 'unsupportedBufferFormat', 'fenceFailure', 'waylandProtocolFailure',
    'rendererFailure', 'unclassifiedWarningsOrErrors']) assert.equal(value[key], 1);
  assert.equal(value.records, 6);
  assert.equal(value.compositorProcesses, 1);
  assert.equal(value.rawMessagesSaved, false);
  assert.ok(!JSON.stringify(value).includes('PRIVATE'));
});
test('normal capability announcements are not import errors', () => {
  const value = summarize([row('dmabuf formats advertised'), row('EGL initialized', { _PID: '456' })].join('\n'));
  assert.equal(value.dmaBufImportFailure, 0);
  assert.equal(value.rendererFailure, 0);
  assert.equal(value.compositorProcesses, 2);
  assert.match(value.scope, /Zero counts do not prove/);
});
test('rejects foreign, oversized or malformed records without exposing contents', () => {
  for (const text of [row('PRIVATE', { _EXE: '/usr/bin/other' }), row(['PRIVATE']),
    row('PRIVATE', { _PID: 'PRIVATE' }), row('PRIVATE', { _UID: '0' }),
    row('PRIVATE', { _SYSTEMD_CGROUP: '/user.slice/user-999.slice/session-c43.scope' }), '{', 'x'.repeat(2 * 1024 * 1024 + 1),
    Array(2001).fill(row('ok')).join('\n')]) assert.throws(() => summarize(text));
});
test('fixed journal read is bounded and cannot write settings or select foreign units', async () => {
  const value = await inspect({ run: async (file, args, options) => {
    assert.equal(file, '/usr/bin/journalctl');
    assert.deepEqual(args, ['_EXE=/usr/bin/weston', '_UID=1000', '--boot', '--since=-20min',
      '--lines=2000', '--no-pager', '--output=json', '--output-fields=_EXE,_UID,_PID,_CMDLINE,_SYSTEMD_CGROUP,MESSAGE']);
    assert.equal(options.timeoutMs, 10000);
    return Buffer.from(row('ok'));
  } });
  assert.equal(value.settingsWrites, false);
  assert.equal(value.records, 1);
});
test('PAM session attribution is accepted but unrelated Weston command is not counted', () => {
  const value = summarize([row('ok'), row('failed to import dmabuf PRIVATE', {
    _CMDLINE: command.replace('wayland-teslaandroid', 'wayland-other') })].join('\n'));
  assert.equal(value.records, 1);
  assert.equal(value.ignoredForeignCommands, 1);
  assert.equal(value.dmaBufImportFailure, 0);
  assert.ok(!JSON.stringify(value).includes('PRIVATE'));
});
