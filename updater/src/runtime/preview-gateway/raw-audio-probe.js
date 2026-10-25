// SPDX-License-Identifier: GPL-3.0-only
const net = require("node:net");
const SOCKET = "\0scrcpy_54410002";
const MAX_BYTES = 2 * 1024 * 1024;
class RawAudioStats {
  constructor(onFrame) {
    if (onFrame !== undefined && typeof onFrame !== 'function') throw new Error('Frame observer must be a function');
    this.onFrame = onFrame; this.total = 0; this.header = Buffer.alloc(0); this.pending = Buffer.alloc(0); this.bytes = 0; this.nonzeroSamples = 0; this.peak = 0;
  }
  push(chunk) {
    this.total += chunk.length;
    if (this.total > MAX_BYTES + 4) throw new Error("Audio probe byte limit exceeded");
    if (this.header.length < 4) {
      const count = Math.min(4 - this.header.length, chunk.length);
      this.header = Buffer.concat([this.header, chunk.subarray(0, count)]); chunk = chunk.subarray(count);
      if (this.header.length < 4) return;
      if (this.header.readUInt32BE() !== 0x00726177) throw new Error("Audio capture disabled or unexpected codec");
    }
    const bytes = this.pending.length ? Buffer.concat([this.pending, chunk]) : chunk;
    const complete = bytes.length - bytes.length % 4;
    for (let i = 0; i < complete; i += 4) {
      const left = bytes.readInt16LE(i), right = bytes.readInt16LE(i + 2);
      if (left) this.nonzeroSamples++;
      if (right) this.nonzeroSamples++;
      this.peak = Math.max(this.peak, Math.abs(left), Math.abs(right));
      this.onFrame?.(left, right);
    }
    this.bytes += complete;
    this.pending = Buffer.from(bytes.subarray(complete));
  }
  finish() {
    if (this.header.length !== 4 || !this.bytes || this.pending.length) throw new Error("Empty or truncated PCM capture");
    return { bytes: this.bytes, frames: this.bytes / 4, seconds: this.bytes / 4 / 48000,
      nonzeroSamples: this.nonzeroSamples, peak: this.peak };
  }
}
function runProbe(connect = () => net.connect({ path: SOCKET }), durationMs = 8000, stats = new RawAudioStats()) {
  return new Promise((resolve, reject) => {
    const source = connect(); let done = false;
    const finish = error => {
      if (done) return; done = true; clearTimeout(timer); source.destroy();
      try { if (error) throw error; resolve(stats.finish()); } catch (e) { reject(e); }
    };
    const timer = setTimeout(() => finish(), durationMs);
    source.on("data", bytes => { try { stats.push(bytes); } catch (error) { finish(error); } });
    source.once("error", finish);
    source.once("end", () => finish(new Error("Audio capture ended before deadline")));
    source.once("close", () => finish(new Error("Audio capture closed before deadline")));
  });
}
if (require.main === module) {
  if (process.getuid?.() !== 0 || process.argv.length !== 2) throw new Error("Audio probe requires root in container network namespace, no arguments");
  runProbe().then(stats => console.log(JSON.stringify(stats))).catch(error => { console.error(error.message); process.exitCode = 1; });
}
module.exports = { RawAudioStats, runProbe };
