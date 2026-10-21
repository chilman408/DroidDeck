// SPDX-License-Identifier: GPL-3.0-only
const { Transform } = require("node:stream");
const { createAudioSettings } = require("./audio-settings");
const AUDIO_SETTINGS_FILE = "/home/hilman/.config/teslaandroid-waydroid/browser-audio.json";
function validVolume(volume) {
  return Number.isInteger(volume) && volume >= 0 && volume <= 150;
}
class PcmGainStream extends Transform {
  constructor(volume = () => 100) {
    super({ highWaterMark: 64 * 1024 });
    this.volume = volume; this.pending = Buffer.alloc(0);
  }
  _transform(chunk, _encoding, callback) {
    try {
      if (chunk.length > 1024 * 1024) throw new Error("PCM gain chunk exceeded limit");
      const volume = this.volume();
      if (!validVolume(volume)) throw new Error("Invalid PCM gain volume");
      const bytes = this.pending.length ? Buffer.concat([this.pending, chunk]) : chunk;
      const complete = bytes.length - bytes.length % 4;
      // Keep at most one incomplete stereo frame, never a view of a large chunk.
      this.pending = Buffer.from(bytes.subarray(complete));
      if (complete) {
        const out = Buffer.allocUnsafe(complete);
        if (volume === 100) bytes.copy(out, 0, 0, complete);
        else for (let offset = 0; offset < complete; offset += 2) {
          const sample = Math.round(bytes.readInt16LE(offset) * volume / 100);
          out.writeInt16LE(Math.max(-32768, Math.min(32767, sample)), offset);
        }
        this.push(out);
      }
      callback();
    } catch (error) { callback(error); }
  }
  _flush(callback) { callback(this.pending.length ? new Error("Incomplete PCM stereo frame") : undefined); }
}
function createVolumeProvider({ filename = AUDIO_SETTINGS_FILE, clock = Date.now,
  readSettings = file => createAudioSettings(file).snapshot(), onError = () => {} } = {}) {
  let lastRead, volume = 0, failed = false;
  return () => {
    const now = clock();
    if (lastRead !== undefined && now >= lastRead && now - lastRead < 500) return volume;
    lastRead = now;
    try {
      const state = readSettings(filename);
      if (!state || typeof state.enabled !== "boolean" || !validVolume(state.volume)) throw new Error("Invalid audio preferences");
      volume = state.enabled ? state.volume : 0; failed = false;
    } catch {
      volume = 0;
      if (!failed) onError("Audio gain preferences unavailable; output muted");
      failed = true;
    }
    return volume;
  };
}
function gainEnabled(env) {
  if (env.TESLA_WAYDROID_AUDIO_PCM_GAIN !== undefined && env.TESLA_WAYDROID_AUDIO_PCM_GAIN !== "1") throw new Error("Invalid audio PCM gain flag");
  return env.TESLA_WAYDROID_AUDIO_PCM_GAIN === "1";
}
module.exports = { PcmGainStream, createVolumeProvider, gainEnabled, AUDIO_SETTINGS_FILE };
