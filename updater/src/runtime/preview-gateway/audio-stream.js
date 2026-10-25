// SPDX-License-Identifier: GPL-3.0-only
const { Transform } = require("node:stream");
const MAX_ATOM = 512 * 1024;
class Mp4Fragments {
  constructor(receive) { this.receive = receive; this.pending = Buffer.alloc(0); this.ftyp = null; this.ready = false; this.moof = null; }
  push(bytes) {
    if (this.pending.length + bytes.length > 2 * MAX_ATOM) throw new Error("MP4 buffer limit exceeded");
    this.pending = Buffer.concat([this.pending, bytes]);
    while (this.pending.length >= 8) {
      const length = this.pending.readUInt32BE(), type = this.pending.toString("ascii", 4, 8);
      if (length < 8 || length > MAX_ATOM) throw new Error("MP4 atom length exceeded");
      if (this.pending.length < length) return;
      const atom = Buffer.from(this.pending.subarray(0, length)); this.pending = this.pending.subarray(length);
      if (!this.ready) {
        if (type === "ftyp" && !this.ftyp) this.ftyp = atom;
        else if (type === "moov" && this.ftyp) {
          this.ready = true; this.receive("init", Buffer.concat([this.ftyp, atom])); this.ftyp = null;
        } else throw new Error("MP4 initialization order invalid");
      } else if (type === "moof" && !this.moof) this.moof = atom;
      else if (type === "mdat" && this.moof) {
        this.receive("fragment", Buffer.concat([this.moof, atom])); this.moof = null;
      } else if (type !== "mfra" || this.moof) throw new Error("MP4 fragment order invalid");
    }
  }
}
class RawPcmStream extends Transform {
  constructor() { super({ highWaterMark: 64 * 1024 }); this.header = Buffer.alloc(0); }
  _transform(bytes, _encoding, callback) {
    if (bytes.length > 1024 * 1024) { callback(new Error("PCM chunk limit exceeded")); return; }
    if (this.header.length < 4) {
      const count = Math.min(4 - this.header.length, bytes.length);
      this.header = Buffer.concat([this.header, bytes.subarray(0, count)]); bytes = bytes.subarray(count);
      if (this.header.length === 4 && this.header.readUInt32BE() !== 0x00726177) {
        callback(new Error("Audio capture disabled or unexpected codec")); return;
      }
    }
    if (this.header.length === 4 && bytes.length) this.push(bytes);
    callback();
  }
  _flush(callback) { callback(this.header.length === 4 ? null : new Error("Truncated PCM codec header")); }
}
module.exports = { Mp4Fragments, RawPcmStream };
