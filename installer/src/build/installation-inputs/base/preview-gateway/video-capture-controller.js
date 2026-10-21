// SPDX-License-Identifier: GPL-3.0-only
const { validateProfile } = require('./video-settings');
function createVideoCaptureController({ readProfile, prepare, launch, onFailure, stopTimeoutMs = 5000 }) {
  let queue = Promise.resolve(), current, closing = false;
  const serialized = task => { const next = queue.then(task); queue = next.catch(() => {}); return next; };
  async function retire() {
    const previous = current;
    if (previous) previous.retiring = true;
    // prepare verifies the exact native lease before stopping escaped guest video.
    let preparationFailure;
    try { await prepare(); } catch (error) { preparationFailure = error; }
    if (!previous) { if (preparationFailure) throw preparationFailure; return; }
    const child = previous.child;
    if (child.exitCode === null && child.signalCode === null && !previous.exited) {
      const exited = new Promise(resolve => child.once('exit', resolve));
      child.kill('SIGTERM');
      let timer;
      const wait = async ms => {
        try { return await Promise.race([exited.then(() => true), new Promise(resolve => { timer = setTimeout(() => resolve(false), ms); })]); }
        finally { clearTimeout(timer); }
      };
      if (!await wait(stopTimeoutMs)) {
        child.kill('SIGKILL');
        if (!await wait(stopTimeoutMs)) throw new Error('Owned video launcher did not stop');
      }
    }
    current = undefined;
    if (preparationFailure) throw preparationFailure;
  }
  return {
    reconcile: () => serialized(async () => {
      if (closing) return;
      const requested = validateProfile(await readProfile());
      if (current && current.profile.revision === requested.revision &&
          current.profile.bitRate === requested.bitRate && current.profile.maxFps === requested.maxFps) return;
      await retire();
      if (closing) return;
      const profile = validateProfile(await readProfile());
      if (closing) return;
      const child = launch(profile), record = { child, profile, retiring: false, exited: false };
      current = record;
      child.once('error', error => { if (!record.retiring && !closing) onFailure(error); });
      child.once('exit', (code, signal) => {
        record.exited = true;
        if (!record.retiring && !closing) onFailure(new Error(`Video capture exited (${signal || code})`));
      });
    }),
    stop() { closing = true; return serialized(retire); },
  };
}
module.exports = { createVideoCaptureController };
