// SPDX-License-Identifier: GPL-3.0-only
const { setTimeout: delay } = require("node:timers/promises");

async function waitForSession({ registered, state, unfreeze, booted, showUI,
  bootUnavailable = () => false, sleep = () => delay(250) }, signal) {
  for (;;) {
    signal?.throwIfAborted();
    if (await registered()) {
      let current = await state();
      if (current === "FROZEN") {
        await unfreeze();
        current = await state();
      }
      let ready = false;
      if (current === "RUNNING") {
        try { ready = await booted(); }
        catch (error) {
          signal?.throwIfAborted();
          if (!bootUnavailable(error)) throw error;
        }
      }
      if (ready) {
        await showUI();
        signal?.throwIfAborted();
        return;
      }
    }
    await sleep(signal);
  }
}

module.exports = { waitForSession };
