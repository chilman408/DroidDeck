// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { prepareCapture } = require("./capture-lease");
const owner = "a".repeat(32);

test('portable capture constructs native state with a validated snapshot only after lease validation', async () => {
  const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }, calls = [];
  const createNative = options => {
    assert.equal(options.configuration.uid, 1200); assert.equal(Object.isFrozen(options.configuration), true);
    calls.push('create');
    return { adopt: async value => { assert.equal(value, owner); calls.push('adopt'); }, stop: async role => { assert.equal(role, 'video'); calls.push('stop'); } };
  };
  const environment = { TESLA_WAYDROID_RUNTIME_OWNER: owner, TESLA_WAYDROID_RUNTIME_ROLE: 'video' };
  assert.equal(await prepareCapture('video', { configuration, environment, createNative }), owner);
  assert.deepEqual(calls, ['create', 'adopt', 'stop']);
  calls.length = 0;
  assert.equal(await prepareCapture('video', { configuration, environment: {}, createNative }), undefined);
  await assert.rejects(prepareCapture('video', { configuration, environment: { ...environment, TESLA_WAYDROID_RUNTIME_ROLE: 'audio' }, createNative }));
  await assert.rejects(prepareCapture('video', { configuration: { ...configuration, uid: 0 }, environment, createNative }));
  assert.deepEqual(calls, []);
});
test("capture restart verifies the journal before retiring only its fixed role", async () => {
  const calls = [];
  const native = { adopt: async value => calls.push(["adopt", value]), stop: async role => calls.push(["stop", role]) };
  for (const role of ["video", "audio"]) {
    calls.length = 0;
    assert.equal(await prepareCapture(role, { native, environment: {
      TESLA_WAYDROID_RUNTIME_OWNER: owner, TESLA_WAYDROID_RUNTIME_ROLE: role,
    } }), owner);
    assert.deepEqual(calls, [["adopt", owner], ["stop", role]]);
  }
});
test("legacy manual capture does not claim ownership or stop unowned guest workers", async () => {
  const native = { adopt: async () => assert.fail(), stop: async () => assert.fail() };
  assert.equal(await prepareCapture("video", { native, environment: {} }), undefined);
  for (const environment of [{ TESLA_WAYDROID_RUNTIME_ROLE: "video" },
    { TESLA_WAYDROID_RUNTIME_OWNER: owner },
    { TESLA_WAYDROID_RUNTIME_OWNER: owner, TESLA_WAYDROID_RUNTIME_ROLE: "audio" }]) {
    await assert.rejects(prepareCapture("video", { native, environment }), /lease/);
  }
});
test("journal or cleanup failure cannot start a replacement capture", async () => {
  const environment = { TESLA_WAYDROID_RUNTIME_OWNER: owner, TESLA_WAYDROID_RUNTIME_ROLE: "video" };
  await assert.rejects(prepareCapture("video", { environment, native: {
    adopt: async () => { throw new Error("ownership changed"); }, stop: async () => assert.fail(),
  } }), /ownership changed/);
  await assert.rejects(prepareCapture("video", { environment, native: {
    adopt: async () => {}, stop: async () => { throw new Error("cleanup failed"); },
  } }), /cleanup failed/);
});
