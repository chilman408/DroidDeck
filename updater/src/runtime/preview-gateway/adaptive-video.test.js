// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {createVideoSettings,readVideoSettings}=require('./video-settings');
const {createAdaptiveVideo}=require('./adaptive-video');
function fixture(t,targets={bitRate:4000000,maxFps:60}){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dm-responsive-'));fs.chmodSync(dir,0o700);t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const file=path.join(dir,'video.json'),store=createVideoSettings(file);store.update({revision:0,...targets});
 let now=0,packets=0,bytes=0;const controller=createAdaptiveVideo({store,clock:()=>now});
 const sample=(changes={},authority='primary',step=1000,byteDelta=750000)=>{now+=step;packets+=20;bytes+=byteDelta;return controller.observe({available:true,flow:true,streamId:'a'.repeat(16),ackedPackets:packets,acknowledgedBytes:bytes,pendingFrames:0,pendingBytes:0,oldestMs:0,ackDelayMs:50,...changes},authority);};
 const enable=()=>store.configureAutomatic({revision:store.automaticSnapshot().revision,profileRevision:store.snapshot().revision,enabled:true});
 const reduce=(bitRate=1000000,maxFps=15)=>store.adapt({revision:store.snapshot().revision,bitRate,maxFps},store.automaticSnapshot().revision);
 return{file,store,controller,sample,enable,reduce};
}
const bad={oldestMs:500,ackDelayMs:350,pendingFrames:4,pendingBytes:200000};
const severe={oldestMs:2000,ackDelayMs:1600,pendingFrames:10,pendingBytes:500000};

test('manual mode is unchanged; sustained congestion cuts within two samples and preserves ceilings',t=>{
 const f=fixture(t);for(let i=0;i<4;i++)f.sample(severe);assert.equal(f.store.snapshot().bitRate,4000000);f.enable();f.sample();
 assert.equal(f.sample(bad),null);const down=f.sample(bad);assert.equal(down.bitRate,3000000);assert.equal(down.maxFps,30);
 assert.equal(f.store.automaticSnapshot().ceilingBitRate,4000000);assert.equal(f.store.automaticSnapshot().ceilingMaxFps,60);
});
test('severe loaded congestion may jump to320kbps/10fps within the next poll; saved targets remain restorable',t=>{
 const f=fixture(t,{bitRate:12000000,maxFps:60});const policy=f.enable();f.sample();const down=f.sample(severe,'primary',1000,60000);
 assert.deepEqual([down.bitRate,down.maxFps],[320000,10]);
 f.store.configureAutomatic({revision:policy.revision,profileRevision:down.revision,enabled:false});assert.deepEqual([f.store.snapshot().bitRate,f.store.snapshot().maxFps],[12000000,60]);
});
test('no-ACK backlog cuts repeatedly with a two-second cooldown; minimum is automatic320/10',t=>{
 const f=fixture(t,{bitRate:8000000,maxFps:60});f.enable();f.sample({ackedPackets:0,acknowledgedBytes:0});
 const stalled={...severe,ackedPackets:0,acknowledgedBytes:0};assert.equal(f.sample(stalled).bitRate,4000000);assert.equal(f.sample(stalled),null);
 assert.equal(f.sample(stalled).bitRate,2000000);for(let i=0;i<20;i++)f.sample(stalled);assert.deepEqual([f.store.snapshot().bitRate,f.store.snapshot().maxFps],[320000,10]);
});
test('quiet but fresh delivery probes after3 healthy seconds without a throughput ceiling or1Mbps deadlock',t=>{
 const f=fixture(t,{bitRate:3000000,maxFps:30});f.enable();f.reduce();f.sample({},'primary',1000,4096);
 for(let i=0;i<3;i++)f.sample({},'primary',1000,4096);assert.equal(f.store.snapshot().bitRate,1000000);
 const up=f.sample({},'primary',1000,4096);assert.deepEqual([up.bitRate,up.maxFps],[2000000,30]);
 for(let i=0;i<12;i++)f.sample({},'primary',1000,4096);assert.equal(f.store.snapshot().bitRate,3000000);
});
test('a failed upward probe rolls back promptly and cannot immediately oscillate upward',t=>{
 const f=fixture(t);f.enable();f.reduce();for(let i=0;i<5;i++)f.sample();assert.equal(f.store.snapshot().bitRate,2000000);
 const back=f.sample(bad);assert.equal(back.bitRate,1000000);assert.equal(back.maxFps,15);for(let i=0;i<8;i++)f.sample();assert.equal(f.store.snapshot().bitRate,1000000);
});
test('high baseline RTT alone is not congestion; genuine backlog relative to it still reduces',t=>{
 const f=fixture(t);f.enable();const high={ackDelayMs:650,oldestMs:650,pendingFrames:2,pendingBytes:32000};for(let i=0;i<7;i++)f.sample(high);assert.equal(f.store.snapshot().bitRate,4000000);
 const down=f.sample({...severe,oldestMs:2300,ackDelayMs:1900});assert.ok(down.bitRate<4000000);
});
test('idle, malformed feedback and authority changes cannot carry healthy evidence into a new owner',t=>{
 const f=fixture(t);f.enable();f.reduce();for(let i=0;i<3;i++)f.sample();f.sample({},'new-owner');f.sample({},'new-owner');f.sample({},'new-owner');assert.equal(f.store.snapshot().bitRate,1000000);
 for(let i=0;i<12;i++)f.sample({available:false});assert.equal(f.store.snapshot().bitRate,1000000);
 for(const changes of [{pendingBytes:NaN},{ackedPackets:-1},{streamId:'bad'},{flow:false}]){f.sample(changes);assert.equal(f.store.snapshot().bitRate,1000000);}
});
test('tiny receipt noise and intermittent delivery cannot manufacture a healthy window',t=>{
 const f=fixture(t);f.enable();f.reduce();for(let i=0;i<20;i++)f.sample({},'primary',1000,1);assert.equal(f.store.snapshot().bitRate,1000000);
 f.controller.reset();for(let i=0;i<15;i++)f.sample(i%2?{}:{available:false});assert.equal(f.store.snapshot().bitRate,1000000);
});
test('sub-window polling retains measurements and reaches a ceiling without changing its stored schema',t=>{
 const f=fixture(t,{bitRate:2000000,maxFps:30});f.enable();f.reduce();for(let i=0;i<14;i++)f.sample({},'primary',500,2048);
 assert.deepEqual([f.store.snapshot().bitRate,f.store.snapshot().maxFps],[2000000,30]);assert.deepEqual(Object.keys(readVideoSettings(f.file)).sort(),['bitRate','maxFps','revision','version']);
});
test('manual1Mbps can retain60fps but automatic caps15 and disabling restores its explicit ceiling',t=>{
 const f=fixture(t,{bitRate:1000000,maxFps:60});const p=f.enable();for(let i=0;i<20;i++)f.sample();assert.equal(f.store.snapshot().maxFps,15);
 assert.throws(()=>f.store.adapt({revision:f.store.snapshot().revision,bitRate:2000000,maxFps:30},p.revision),e=>e.status===409);
 f.store.configureAutomatic({revision:p.revision,profileRevision:f.store.snapshot().revision,enabled:false});assert.equal(f.store.snapshot().maxFps,60);
});
