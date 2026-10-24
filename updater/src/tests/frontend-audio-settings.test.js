// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched frontend');
function fixture() {
  const window = {}, context = vm.createContext({ window, console });
  for (const file of ['js/core/shared.js', 'js/settings/settings-controller.js'])
    vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  const inputs = [{ disabled: false }, { disabled: false }], settings = Object.assign(Object.create(window.TAHtml.SettingsController.prototype), {
    activeSectionId: 'audio', audioSettingsPending: false, apiClient: { browserAudioSupported: true },
    displayController: { controlRequestInFlight: false, remoteDisplayState: { canControl: true, transactionStatus: 'idle' } },
    content: { querySelectorAll: () => inputs }, _renderActiveSection: async () => {},
  });
  return { settings, inputs };
}
test('shared Audio Settings disables viewers, resize/recovery and pending saves without disabling listening', () => {
  const { settings, inputs } = fixture();
  for (const state of [{ canControl: false, transactionStatus: 'idle' }, { canControl: true, transactionStatus: 'resizing' },
    { canControl: false, transactionStatus: 'recovery' }]) {
    settings.refreshDisplayControl(state); assert.equal(inputs.every(input => input.disabled), true);
  }
  const idle = { canControl: true, transactionStatus: 'idle' };
  settings.audioSettingsPending = true; settings.refreshDisplayControl(idle); assert.equal(inputs.every(input => input.disabled), true);
  settings.audioSettingsPending = false; settings.refreshDisplayControl(idle); assert.equal(inputs.every(input => !input.disabled), true);
  settings.activeSectionId = 'display'; settings.refreshDisplayControl(idle); assert.equal(inputs.every(input => !input.disabled), true);
});
test('audio setting saves cannot overlap, and polling cannot unlock controls before save settles', async () => {
  const { settings, inputs } = fixture(), calls = []; let release;
  settings.apiClient.setBrowserAudioState = async value => { calls.push(['state', value]); await new Promise(resolve => { release = resolve; }); };
  settings.apiClient.setBrowserAudioVolume = async value => calls.push(['volume', value]);
  settings._handleSystemConfigurationChanged = async () => calls.push(['refresh']);
  const first = settings._saveAudioSettings(1, 150), second = settings._saveAudioSettings(0, 100);
  assert.equal(settings.audioSettingsPending, true); settings.refreshDisplayControl(settings.displayController.remoteDisplayState);
  assert.equal(inputs.every(input => input.disabled), true); release(); await first; await second;
  assert.deepEqual(calls, [['state', 1], ['volume', 150], ['refresh']]); assert.equal(settings.audioSettingsPending, false);
});
test('viewer callbacks cannot write and failed late saves cannot repaint another section', async () => {
  const { settings } = fixture(); let writes = 0, rendered = 0, release;
  settings.apiClient.setBrowserAudioState = async () => { writes++; await new Promise(resolve => { release = resolve; }); throw new Error('private'); };
  settings.displayController.remoteDisplayState.canControl = false;
  await settings._saveAudioSettings(1, 150); assert.equal(writes, 0);
  settings.displayController.remoteDisplayState.canControl = true;
  settings._renderActiveSection = async () => { rendered++; };
  const pending = settings._saveAudioSettings(1, 150); settings.activeSectionId = 'device'; release(); await pending;
  assert.equal(rendered, 0); assert.equal(settings.audioSettingsPending, false);
  assert.equal(settings.audioSettingsError, 'Audio settings could not be saved');
});

test('local audio-delay controls stay available without shared write authority', () => {
  const { settings, inputs } = fixture();
  const local = { disabled: true, dataset: { localAudioDelay: 'true' } };
  inputs.push(local);
  settings.refreshDisplayControl({ canControl: false, transactionStatus: 'idle' });
  assert.equal(local.disabled, false);
  assert.equal(inputs[0].disabled, true); assert.equal(inputs[1].disabled, true);
});
