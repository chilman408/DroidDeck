// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched frontend');
function fixture() {
  const window = {}, context = vm.createContext({ window, console });
  for (const file of ['js/core/shared.js', 'js/settings/settings-controller.js'])
    vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  return window.TAHtml.SettingsController.prototype;
}
test('VM Device page uses real resource fields and does not display null temperature as zero degrees', async () => {
  const proto = fixture(), tiles = [], settings = { _renderToken: 1, activeSectionId: 'device', content: { innerHTML: '' },
    apiClient: { fetchDeviceInfo: async () => ({ deviceInfoVersion: 1, device_model: 'Waydroid VM', cpu_model: 'Virtual CPU', cpu_cores: 2,
      memory_total_bytes: 8 * 1024 ** 3, memory_free_bytes: 3 * 1024 ** 3, uptime_seconds: 93784, kernel_release: '7-test',
      host_name: 'test-vm', cpu_temperature: null, serial_number: null, release_type: 'development' }) },
    _appendLoadingState() {}, _appendTile: tile => tiles.push(tile), _createTrailingText: value => value };
  settings._renderVmDeviceInfo = value => proto._renderVmDeviceInfo.call(settings, value);
  await proto._renderDeviceSection.call(settings, 1);
  const values = Object.fromEntries(tiles.map(tile => [tile.title, tile.trailing]));
  assert.equal(values['CPU cores'], '2'); assert.equal(values['VM memory'], '8.0 GiB'); assert.equal(values['Free VM memory'], '3.0 GiB');
  assert.equal(values['Host uptime'], '1d 2h 3m'); assert.equal(values['CPU Temperature'], undefined);
  for (const title of ['Serial Number', 'CarPlay Module', 'LTE Modem', 'Release type']) assert.equal(values[title], undefined);
  assert.equal(tiles.length, 8); assert.equal(tiles.every(tile => tile.dense === false), true);
});

test('a measured VM temperature remains visible rather than being mistaken for an unavailable placeholder', () => {
  const proto = fixture(), tiles = [], settings = { content: { dataset: {} },
    _appendTile: tile => tiles.push(tile), _createTrailingText: value => value };
  proto._renderVmDeviceInfo.call(settings, { cpu_temperature: 42 });
  assert.equal(tiles.find(tile => tile.title === 'CPU Temperature').trailing, '42\u00b0C');
});
test('late VM diagnostics never repaint a different Settings section', async () => {
  const proto = fixture(); let painted = false;
  const settings = { _renderToken: 2, activeSectionId: 'audio', _appendLoadingState() {},
    apiClient: { fetchDeviceInfo: async () => ({ deviceInfoVersion: 1 }) }, _renderVmDeviceInfo() { painted = true; } };
  await proto._renderDeviceSection.call(settings, 1); assert.equal(painted, false);
});
