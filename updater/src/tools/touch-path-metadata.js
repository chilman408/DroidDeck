// SPDX-License-Identifier: GPL-3.0-only
const { performance } = require('node:perf_hooks');
const { execute } = require('../runtime/waydroid-display/host-adapter');
const { readContainerIdentity, readAndroidIdentity } = require('../runtime/waydroid-display/runtime-discovery');
const PORTS = { 9999: 'touch', 19999: 'touchRelay', 8443: 'gateway' };
function inputFacts(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 4 * 1024 * 1024) throw new Error('Invalid dispatcher response');
  const field = name => {
    const rows = text.split(/\r?\n/).filter(line => new RegExp('^\\s*' + name + ':').test(line));
    if (rows.length > 1) throw new Error('Ambiguous dispatcher field');
    return rows[0]?.slice(rows[0].indexOf(':') + 1).trim();
  };
  const boolean = name => {
    const value = field(name);
    if (!['true', 'false'].includes(value)) throw new Error('Missing dispatcher flag');
    return value === 'true';
  };
  const queue = field('InboundQueue'), pending = field('PendingEvent');
  const length = /^length=(\d+)$/.exec(queue || '');
  return { enabled: boolean('DispatchEnabled'), frozen: boolean('DispatchFrozen'),
    pending: pending === undefined ? null : pending !== '<none>',
    inboundLength: queue === '<empty>' ? 0 : length && Number.isSafeInteger(Number(length[1])) ? Number(length[1]) : null };
}
function socketFacts(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 1024 * 1024) throw new Error('Invalid socket response');
  const result = Object.fromEntries(Object.values(PORTS).map(role => [role, { connections: 0, receiveBytes: 0, sendBytes: 0 }]));
  const rows = text.split(/\r?\n/).filter(line => line.trim());
  if (rows.length > 4096) throw new Error('Invalid socket count');
  for (const row of rows) {
    const fields = row.trim().split(/\s+/);
    if (fields.length !== 5 || !/^(?:ESTAB|CLOSE-WAIT|FIN-WAIT-[12]|CLOSING|LAST-ACK|SYN-SENT|SYN-RECV|TIME-WAIT|CLOSE|UNCONN|LISTEN)$/.test(fields[0]) ||
        !fields.slice(1, 3).every(v => /^\d+$/.test(v) && Number.isSafeInteger(Number(v))) ||
        !fields.slice(3).every(v => /:\d+$/.test(v))) throw new Error('Invalid socket row');
    const role = PORTS[Number(/:(\d+)$/.exec(fields[3])[1])];
    if (!role) continue;
    const counters = result[role]; counters.connections++;
    counters.receiveBytes += Number(fields[1]); counters.sendBytes += Number(fields[2]);
    if (![counters.receiveBytes, counters.sendBytes].every(Number.isSafeInteger)) throw new Error('Invalid socket totals');
  }
  return result;
}
async function inspect({ run = execute, identity = readContainerIdentity,
  androidIdentity = (pid, options) => readAndroidIdentity(pid, options, { run }) } = {}) {
  const options = { env: { ...process.env, LD_LIBRARY_PATH: '/opt/teslaandroid-waydroid/libgbinder' }, timeoutMs: 10000 };
  const discover = async () => {
    const value = (await run('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], options)).toString().trim();
    if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < 2) throw new Error('Invalid container identity');
    return Number(value);
  };
  const pid = await discover(), before = await identity(pid), androidBefore = await androidIdentity(pid, options);
  const started = performance.now();
  const dispatcher = inputFacts((await run('/usr/bin/nsenter', ['-t', String(pid), '-m', '-p', '-r', '--',
    '/system/bin/dumpsys', 'input'], options)).toString());
  const inputInspectMs = Math.round(performance.now() - started);
  const sockets = socketFacts((await run('/usr/bin/nsenter', ['-t', String(pid), '-n', '--', '/usr/bin/ss', '-Htn',
    '( sport = :9999 or sport = :19999 or sport = :8443 )'], options)).toString());
  if (await discover() !== pid || await identity(pid) !== before || await androidIdentity(pid, options) !== androidBefore) {
    throw new Error('Container or Android service identity changed');
  }
  return { version: 1, guestPid: pid, dispatcher, inputInspectMs, sockets,
    inputInjected: false, settingsWrites: false, rawRecordsSaved: false,
    scope: 'One passive sample; empty queues or absent clients do not prove responsive controls or end-to-end latency.' };
}
if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length !== 2) {
    console.error('Fixed Linux root input metadata probe required'); process.exitCode = 1;
  } else inspect().then(result => console.log(JSON.stringify(result))).catch(() => {
    console.error('Input metadata unavailable; no input or settings changed'); process.exitCode = 1;
  });
}
module.exports = { inputFacts, socketFacts, inspect };
