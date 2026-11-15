// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const profile = () => require('./video-settings');
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ta-video-settings-'));
  fs.chmodSync(dir, 0o700);
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return path.join(dir, 'video.json');
}
test('video targets default to current encoder values, persist atomically and reject stale writes', t => {
  const filename = fixture(t), { createVideoSettings, readVideoSettings } = profile();
  const store = createVideoSettings(filename);
  assert.deepEqual(store.snapshot(), { version: 1, revision: 0, bitRate: 4000000, maxFps: 30 });
  const next = store.update({ revision: 0, bitRate: 2000000, maxFps: 45 });
  assert.deepEqual(next, { version: 1, revision: 1, bitRate: 2000000, maxFps: 45 });
  assert.deepEqual(readVideoSettings(filename), next);
  assert.deepEqual(createVideoSettings(filename).snapshot(), next);
  assert.throws(() => store.update({ revision: 0, bitRate: 4000000, maxFps: 30 }), error => error.status === 409);
  assert.deepEqual(store.update({ revision: 1, bitRate: 2000000, maxFps: 45 }), next);
  assert.equal(fs.readdirSync(path.dirname(filename)).length, 1);
});
test('only bounded integer encoder targets are allowed; corrupt files never fall back silently', t => {
  const filename = fixture(t), { createVideoSettings } = profile(), store = createVideoSettings(filename);
  for (const change of [{ bitRate: 0 }, { maxFps: 120 }, { bitRate: '4000000' }, { maxFps: 30.1 },
    { command: 'sh' }, { revision: -1 }, { revision: Number.MAX_SAFE_INTEGER }, { bitRate: 5000000 }]) {
    assert.throws(() => store.update({ revision: 0, bitRate: 4000000, maxFps: 30, ...change }));
  }
  assert.equal(fs.existsSync(filename), false);
  fs.writeFileSync(filename, JSON.stringify({ version: 1, revision: 0, bitRate: 0, maxFps: 30 }), { mode: 0o600 });
  assert.throws(() => createVideoSettings(filename), /video/i);
});

test('3 Mbps persists across store recreation without changing defaults or frame rate', t => {
  const filename = fixture(t), { createVideoSettings, readVideoSettings, BIT_RATES } = profile();
  assert.deepEqual(BIT_RATES, [320000,450000,720000,1000000,2000000,3000000,4000000,6000000,8000000,10000000,12000000]);
  const store = createVideoSettings(filename);
  assert.equal(store.snapshot().bitRate, 4000000);
  const next = store.update({ revision: 0, bitRate: 3000000, maxFps: 30 });
  assert.equal(next.bitRate, 3000000);
  assert.equal(next.maxFps, 30);
  assert.deepEqual(readVideoSettings(filename), next);
  assert.deepEqual(createVideoSettings(filename).snapshot(), next);
});
test('profile reader rejects links and insecure permissions at the privilege boundary', { skip: process.platform === 'win32' }, t => {
  const filename = fixture(t), { readVideoSettings, createVideoSettings } = profile();
  createVideoSettings(filename).update({ revision: 0, bitRate: 8000000, maxFps: 60 });
  const other = filename + '.other'; fs.renameSync(filename, other); fs.symlinkSync(other, filename);
  assert.throws(() => readVideoSettings(filename)); fs.unlinkSync(filename);
  fs.linkSync(other, filename); assert.throws(() => readVideoSettings(filename)); fs.unlinkSync(filename);
  fs.renameSync(other, filename); fs.chmodSync(filename, 0o644); assert.throws(() => readVideoSettings(filename));
  fs.chmodSync(filename, 0o600); fs.chmodSync(path.dirname(filename), 0o777);
  assert.throws(() => readVideoSettings(filename));
});


test('automatic ceilings at 1 Mbps retain selected fps but cap effective delivery at 15 fps',t=>{
 const store=profile().createVideoSettings(fixture(t));store.update({revision:0,bitRate:3000000,maxFps:30});
 store.configureAutomatic({revision:0,profileRevision:1,enabled:true});
 const policy=store.automaticSnapshot();
 const next=store.update({revision:store.snapshot().revision,policyRevision:policy.revision,bitRate:1000000,maxFps:30});
 assert.equal(next.maxFps,15);assert.equal(store.automaticSnapshot().ceilingMaxFps,30);
});


test('automatic-only320/450 and10fps cannot be selected manually;6/10Mbps persist',t=>{
 const store=profile().createVideoSettings(fixture(t));
 for(const bitRate of [320000,450000])assert.throws(()=>store.update({revision:0,bitRate,maxFps:15}),e=>e.status===400);
 assert.throws(()=>store.update({revision:0,bitRate:720000,maxFps:10}),e=>e.status===400);
 for(const bitRate of [6000000,10000000])store.update({revision:store.snapshot().revision,bitRate,maxFps:30});
 const policy=store.configureAutomatic({revision:0,profileRevision:store.snapshot().revision,enabled:true});
 for(const bitRate of [450000,320000]){const next=store.adapt({revision:store.snapshot().revision,bitRate,maxFps:10},policy.revision);assert.equal(next.maxFps,10);}
 assert.equal(store.automaticSnapshot().ceilingBitRate,10000000);assert.equal(store.automaticSnapshot().ceilingMaxFps,30);
 assert.throws(()=>store.adapt({revision:store.snapshot().revision,bitRate:1000000,maxFps:10},policy.revision));
 store.configureAutomatic({revision:policy.revision,profileRevision:store.snapshot().revision,enabled:false});
 assert.equal(store.snapshot().bitRate,10000000);assert.equal(store.snapshot().maxFps,30);
});


test('new effective video targets preserve immutable predecessor profile and automatic ceiling bytes', t => {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'video-version-'));fs.chmodSync(dir,0o700);t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const legacy=path.join(dir,'browser-video.json'),legacyAuto=path.join(dir,'browser-video-automatic.json'),file=path.join(dir,'browser-video-v2.json');
 const profile={version:1,revision:5,bitRate:4000000,maxFps:30},automatic={version:1,revision:2,enabled:true,ceilingBitRate:8000000,ceilingMaxFps:60};
 for(const [name,value] of [[legacy,profile],[legacyAuto,automatic]])fs.writeFileSync(name,JSON.stringify(value)+'\n',{mode:0o600});
 const before=fs.readFileSync(legacy),autoBefore=fs.readFileSync(legacyAuto),store=require('./video-settings').createVideoSettings(file);
 assert.deepEqual(store.snapshot(),profile);assert.deepEqual(store.automaticSnapshot(),automatic);assert.equal(fs.existsSync(file),false);
 store.adapt({revision:5,bitRate:320000,maxFps:10},2);
 assert.equal(store.snapshot().bitRate,320000);assert.equal(store.snapshot().maxFps,10);
 store.update({revision:6,bitRate:10000000,maxFps:60,policyRevision:2});assert.equal(store.automaticSnapshot().ceilingBitRate,10000000);
 assert.deepEqual(fs.readFileSync(legacy),before);assert.deepEqual(fs.readFileSync(legacyAuto),autoBefore);
 const {readVersionedVideoSettings}=require('./video-settings');assert.equal(readVersionedVideoSettings(file).maxFps,10);
 if(process.platform!=='win32'){fs.renameSync(file,file+'.real');fs.symlinkSync(file+'.real',file);assert.throws(()=>readVersionedVideoSettings(file),'unsafe v2 must not fall through to predecessor');}
});
