// SPDX-License-Identifier: GPL-3.0-only
// Delivery feedback is measured by the relay, never supplied by a browser.
const { BIT_RATES } = require('./video-settings');
function createAdaptiveVideo({ store, clock = () => performance.now() }) {
  let previous = null, healthySince = null, congested = 0, changedAt = -Infinity;
  const reset = () => { previous = null; healthySince = null; congested = 0; };
  return {
    reset,
    observe(sample, authority) {
      const now = clock(), state = store.automaticSnapshot(), profile = store.snapshot();
      if (!state.enabled || !sample?.available || sample.flow !== true || !authority ||
          !/^[a-f0-9]{16}$/.test(sample.streamId || '')) { reset(); return null; }
      const fields = ['ackedPackets', 'acknowledgedBytes', 'pendingBytes', 'pendingFrames', 'oldestMs', 'ackDelayMs'];
      if (fields.some(key => !Number.isSafeInteger(sample[key]) || sample[key] < 0 || sample[key] > Number.MAX_SAFE_INTEGER)) {
        reset(); return null;
      }
      if (!previous || previous.authority !== authority || previous.streamId !== sample.streamId ||
          previous.revision !== profile.revision || previous.policyRevision !== state.revision || now < previous.at ||
          now - previous.at > 4000 || sample.ackedPackets < previous.ackedPackets || sample.acknowledgedBytes < previous.acknowledgedBytes) {
        reset(); previous = { ...sample, authority, revision: profile.revision, policyRevision: state.revision, at: now }; return null;
      }
      const elapsed = now - previous.at, received = sample.ackedPackets - previous.ackedPackets;
      const delivered = sample.acknowledgedBytes - previous.acknowledgedBytes;
      previous = { ...sample, authority, revision: profile.revision, policyRevision: state.revision, at: now };
      if (elapsed < 750) return null;
      // Empty/idle streams are not congestion, nor evidence that bandwidth improved.
      const bad = sample.oldestMs >= 900 || (received > 0 && (sample.ackDelayMs >= 750 ||
        sample.pendingFrames >= 8 && sample.ackDelayMs >= 250));
      const severe = sample.oldestMs >= 1800 || received > 0 && sample.ackDelayMs >= 1500;
      const good = received >= 8 && delivered >= 32768 && sample.oldestMs < 250 && sample.ackDelayMs < 250 && sample.pendingFrames <= 2;
      congested = bad ? congested + 1 : 0;
      if (good) { if (healthySince === null) healthySince = now; } else healthySince = null;
      let bitRate = profile.bitRate, maxFps = profile.maxFps;
      if ((severe || congested >= 2) && now - changedAt >= (severe ? 4000 : 6000)) {
        const index = BIT_RATES.indexOf(bitRate);
        if (index > 0) {
          // Cut sharply on a stalled link; measured delivery includes room for audio.
          const deliveredRate = received >= 8 && delivered >= 32768 ? delivered * 8000 / elapsed * .65 : bitRate / 2;
          const target = severe ? Math.min(bitRate / 2, deliveredRate) : BIT_RATES[index - 1];
          bitRate = BIT_RATES.filter(value => value < bitRate && value <= target).at(-1) || BIT_RATES[0];
          if (severe) maxFps = Math.min(maxFps, 30);
        }
        else if (maxFps > 30) maxFps = maxFps === 60 ? 45 : 30;
      } else if (healthySince !== null && now - healthySince >= 45000 && now - changedAt >= 45000) {
        if (maxFps < state.ceilingMaxFps) maxFps = maxFps === 30 ? Math.min(45, state.ceilingMaxFps) : state.ceilingMaxFps;
        else bitRate = BIT_RATES.find(value => value > bitRate && value <= state.ceilingBitRate) || bitRate;
      }
      if (bitRate === profile.bitRate && maxFps === profile.maxFps) return null;
      const next = store.adapt({ revision: profile.revision, bitRate, maxFps }, state.revision);
      changedAt = now; reset(); return next;
    },
  };
}
module.exports = { createAdaptiveVideo };
