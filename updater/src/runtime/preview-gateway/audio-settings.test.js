// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
test("audio preferences validate values, persist privately and do not reset corrupt state", t => {
  assert.ok(fs.existsSync(__dirname + "/audio-settings.js"), "audio settings not implemented");
  const { createAudioSettings } = require("./audio-settings");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-audio-")); t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, "audio.json"), settings = createAudioSettings(file);
  assert.deepEqual(settings.snapshot(), { enabled: true, volume: 100 });
  settings.update("enabled", 0); settings.update("volume", 150);
  assert.deepEqual(createAudioSettings(file).snapshot(), { enabled: false, volume: 150 });
  for (const [key, value] of [["enabled", 2], ["volume", 151], ["volume", -1], ["volume", 1.5], ["other", 1]]) {
    assert.throws(() => settings.update(key, value), /Invalid/);
  }
  fs.writeFileSync(file, "{}", { mode: 0o600 });
  assert.throws(() => createAudioSettings(file), /Invalid/);
});
