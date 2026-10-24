// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fixture = { hostname: () => 'waydroid-vm', cpus: () => [{ model: 'Virtual CPU' }, { model: 'Virtual CPU' }],
  totalmem: () => 8 * 1024 ** 3, freemem: () => 3 * 1024 ** 3, uptime: () => 93784.6, release: () => '7.0.0-test' };
test('VM diagnostics report allocated host resources, never invented temperatures or peripheral detection', () => {
  const provider = require('./device-info').createDeviceInfoProvider({ system: fixture });
  const info = provider();
  assert.equal(info.deviceInfoVersion, 1); assert.equal(info.cpu_cores, 2); assert.equal(info.cpu_model, 'Virtual CPU');
  assert.equal(info.memory_total_bytes, 8 * 1024 ** 3); assert.equal(info.memory_free_bytes, 3 * 1024 ** 3);
  assert.equal(info.uptime_seconds, 93784); assert.equal(info.kernel_release, '7.0.0-test');
  assert.equal(info.host_name, 'waydroid-vm'); assert.equal(info.cpu_temperature, null); assert.equal(info.serial_number, null);
  assert.equal(info.is_modem_detected, null); assert.equal(info.is_carplay_detected, null);
  assert.equal(Object.isFrozen(info), true);
});
test('diagnostics cache bounded snapshots and represent unavailable or invalid resources as null', () => {
  let now = 0, calls = 0;
  const system = { ...fixture, cpus: () => { calls++; return []; }, totalmem: () => NaN, freemem: () => Infinity,
    hostname: () => '\u0000' + 'x'.repeat(500), uptime: () => -1 };
  const provider = require('./device-info').createDeviceInfoProvider({ system, clock: () => now });
  const first = provider(); assert.equal(first.cpu_model, null); assert.equal(first.cpu_cores, null);
  assert.equal(first.memory_total_bytes, null); assert.equal(first.memory_free_bytes, null); assert.equal(first.uptime_seconds, null);
  assert.equal(first.host_name.length, 160); assert.equal(first.host_name.includes('\u0000'), false);
  now = 1000; assert.equal(provider(), first); assert.equal(calls, 1);
  now = 2000; assert.notEqual(provider(), first); assert.equal(calls, 2);
});
