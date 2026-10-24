// SPDX-License-Identifier: GPL-3.0-only
const RATE = 48000, FREQUENCY = 1000, WINDOW = 480, PERIOD = RATE / FREQUENCY;
const cosine = Array.from({ length: PERIOD }, (_, i) => Math.cos(2 * Math.PI * i / PERIOD));
const sine = Array.from({ length: PERIOD }, (_, i) => Math.sin(2 * Math.PI * i / PERIOD));
class ToneEnergy {
  constructor() { this.frames = 0; this.energy = 0; this.real = 0; this.imaginary = 0; this.windows = 0; this.matchingWindows = 0; }
  push(sample) {
    const index = this.frames % PERIOD;
    this.energy += sample * sample; this.real += sample * cosine[index]; this.imaginary += sample * sine[index]; this.frames++;
    if (this.frames !== WINDOW) return;
    // Orthogonal single-bin DFT energy over ten complete tone periods. Keep
    // only sums/counters, never recorded samples or per-window sound data.
    if (this.energy > 0 && 2 * (this.real ** 2 + this.imaginary ** 2) / (WINDOW * this.energy) >= 0.7) this.matchingWindows++;
    this.windows++; this.frames = 0; this.energy = 0; this.real = 0; this.imaginary = 0;
  }
  finish() { return { frequencyHz: FREQUENCY, sampleRate: RATE, windows: this.windows, matchingWindows: this.matchingWindows }; }
}
function createToneStats(Parser) {
  const tone = new ToneEnergy(), stats = new Parser((left, right) => tone.push((left + right) / 2));
  const finish = stats.finish.bind(stats);
  stats.finish = () => ({ ...finish(), tone: tone.finish() });
  return stats;
}
module.exports = { ToneEnergy, createToneStats };
