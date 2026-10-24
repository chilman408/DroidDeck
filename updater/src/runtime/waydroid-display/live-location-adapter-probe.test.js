// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { probe } = require("./live-location-adapter-probe");
function fixture({ existing = false, failVerification = false } = {}) {
  let owner = null, fixes = [], closed = 0;
  const adapter = {
    async inspect(token) { return { present: existing || owner !== null, owned: owner === token }; },
    async open(token) { owner = token; return this.inspect(token); },
    async close(token) { if (owner !== token) throw new Error("Conflict"); owner = null; closed++; return this.inspect(token); },
    async deliver(token, fix) { if (owner !== token) throw new Error("Conflict"); fixes.push(fix); },
  };
  return { adapter, fixes, get closed() { return closed; },
    verify: async (_token, motion) => {
      if (failVerification) throw new Error("Verification failed");
      assert.equal(fixes.at(-1).speed, motion ? 2 : null);
      assert.equal(fixes.at(-1).bearing, motion ? 90 : null);
      return { version: 1, verified: true, mock: true };
    } };
}
test("live GPS probe verifies motion removal and always cleans its owned provider", async () => {
  const f = fixture(); let nativeChecked = false;
  const result = await probe({ ...f, rejectNative: async () => { nativeChecked = true; } });
  assert.equal(result.motionCleared, true); assert.equal(result.nativeValidationChecked, true);
  assert.equal(nativeChecked, true); assert.equal(f.closed, 1); assert.equal(f.fixes.length, 2);
});
test("live GPS probe cleans up a failed verification but never touches a preexisting provider", async () => {
  const failed = fixture({ failVerification: true });
  await assert.rejects(probe(failed), /Verification failed/); assert.equal(failed.closed, 1);
  const existing = fixture({ existing: true });
  await assert.rejects(probe(existing), /Existing GPS provider/); assert.equal(existing.closed, 0);
});
