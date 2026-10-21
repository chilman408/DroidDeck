// SPDX-License-Identifier: GPL-3.0-only
// Pinned scrcpy v4 protocol: codec, resize-session headers and complete access units.
const MAX_PACKET = 2 * 1024 * 1024;
class ScrcpyStream {
  constructor(onPacket) { this.onPacket = onPacket; this.pending = Buffer.alloc(0); this.codec = false; this.session = false; }
  push(bytes) {
    if (bytes.length > MAX_PACKET + 12) throw new Error("Video input size exceeded");
    this.pending = Buffer.concat([this.pending, bytes]);
    if (!this.codec) {
      if (this.pending.length < 4) return;
      if (this.pending.subarray(0, 4).toString() !== "h264") throw new Error("Unsupported video codec");
      this.pending = this.pending.subarray(4); this.codec = true;
    }
    while (this.pending.length >= 12) {
      const header = this.pending.subarray(0, 12);
      if (header[0] & 0x80) {
        if (header.readUInt32BE(0) !== 0x80000000 && header.readUInt32BE(0) !== 0x80000001) throw new Error("Invalid video session");
        const width = header.readUInt32BE(4), height = header.readUInt32BE(8);
        if (![width, height].every(v => v >= 96 && v <= 1920 && v % 2 === 0) || width * height > 1600000) throw new Error("Unsafe video dimensions");
        this.session = true; this.pending = this.pending.subarray(12);
        this.onPacket({ type: "session", width, height });
      } else {
        if (!this.session) throw new Error("Video frame before session");
        const size = header.readUInt32BE(8);
        if (!size || size > MAX_PACKET) throw new Error("Invalid video packet size");
        if (this.pending.length < size + 12) break;
        const packet = Buffer.from(this.pending.subarray(0, size + 12));
        this.pending = this.pending.subarray(size + 12);
        this.onPacket({ type: "frame", config: !!(header[0] & 0x40), key: !!(header[0] & 0x20), packet });
      }
    }
    if (this.pending.length > MAX_PACKET + 12) throw new Error("Video buffer size exceeded");
  }
  end() { if (this.pending.length) throw new Error("Truncated video stream"); }
}
module.exports = { ScrcpyStream, MAX_PACKET };
