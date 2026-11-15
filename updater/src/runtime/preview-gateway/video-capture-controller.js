// SPDX-License-Identifier: GPL-3.0-only
const { validateProfile } = require('./video-settings');
function createVideoCaptureController({ readProfile, prepare, launch, onFailure, stopTimeoutMs = 5000, preparationTimeoutMs, onRetire = () => {}, afterRetire }) {
  let queue = Promise.resolve(), current, closing = false;
  const serialized = task => { const next = queue.then(task); queue = next.catch(() => {}); return next; };
  async function retire() {
    const previous = current;
    if (previous) { previous.retiring = true; onRetire(previous.child); }
    // prepare verifies the exact native lease before stopping escaped guest video.
    let preparationFailure;
    let preparationTimer;
    try {
      const pending = Promise.resolve().then(prepare);
      if (preparationTimeoutMs === undefined) await pending;
      else await Promise.race([pending,new Promise((_,reject) => {
        preparationTimer=setTimeout(() => reject(new Error('Owned video preparation deadline exceeded')),preparationTimeoutMs);
      })]);
    } catch (error) { preparationFailure = error; }
    finally { clearTimeout(preparationTimer); }
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
    if(afterRetire){
      let timer;
      try{await Promise.race([Promise.resolve().then(()=>afterRetire(child)),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Owned video status drain deadline exceeded')),stopTimeoutMs)})]);}
      finally{clearTimeout(timer)}
    }
    current = undefined;
    if (preparationFailure) throw preparationFailure;
  }
  async function start() {
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
  }
  return {
    reconcile: () => serialized(async () => {
      if (closing) return;
      const requested = validateProfile(await readProfile());
      if (current && current.profile.revision === requested.revision &&
          current.profile.bitRate === requested.bitRate && current.profile.maxFps === requested.maxFps) return;
      await retire();
      await start();
    }),
    replace(child) {
      const previous=current;
      if(closing||!previous||previous.child!==child||previous.retiring)return Promise.resolve(false);
      // Suppress only this owned recovery's exit before queued work can observe it.
      previous.retiring=true;
      return serialized(async()=>{
        if(closing||current!==previous)return false;
        await retire();await start();return !closing;
      });
    },
    stop() { closing = true; return serialized(retire); },
  };
}
module.exports = { createVideoCaptureController };
