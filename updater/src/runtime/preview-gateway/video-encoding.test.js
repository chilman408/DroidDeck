// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const E=require('./video-encoding');
function fixture(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dm-encoding-'));fs.chmodSync(dir,0o700);t.after(()=>{assert.equal(path.dirname(dir),path.resolve(os.tmpdir()));fs.rmSync(dir,{recursive:true,force:true});});return path.join(dir,'browser-encoding.json');}
test('encoding preference persists independently, rejects stale and arbitrary commands and preserves old files',t=>{
 const file=fixture(t),store=E.createEncodingPreference(file);assert.deepEqual(store.snapshot(),{version:1,revision:0,preference:'auto'});assert.equal(fs.existsSync(file),false);
 assert.equal(store.update({revision:0,preference:'cpu'}).revision,1);assert.equal(E.createEncodingPreference(file).snapshot().preference,'cpu');
 const before=fs.readFileSync(file);for(const value of [null,[],{revision:0,preference:'auto'},{revision:1,preference:'gpu'},{revision:1,preference:'cpu',command:'reboot'},{revision:1.5,preference:'auto'}])assert.throws(()=>store.update(value));
 assert.deepEqual(fs.readFileSync(file),before);
 fs.writeFileSync(file,'{}');assert.throws(()=>E.readPreference(file));assert.equal(fs.readFileSync(file,'utf8'),'{}');
});
test('CPU preference cannot grant GPU authority; automatic retains independently admitted configuration',()=>{
 const cpu={captureMode:'cpu',framePacing:'legacy',source:'default',encoder:'cpu'},gpu={captureMode:'hybrid',framePacing:'bounded',source:'protected-policy',encoder:'vaapi'};
 for(const protectedSettings of [cpu,gpu]){
  assert.deepEqual(E.chooseEncoding(protectedSettings,{version:1,revision:0,preference:'auto'}),protectedSettings);
  assert.equal(E.chooseEncoding(protectedSettings,{version:1,revision:0,preference:'cpu'}).captureMode,'cpu');assert.equal(E.chooseEncoding(protectedSettings,{version:1,revision:0,preference:'cpu'}).encoder,'cpu');
 }
});
test('public status distinguishes pending request from measured encoder and fails closed on lost telemetry',t=>{
 const file=fixture(t);let actual={gpuAvailable:true,effective:'gpu',preferenceRevision:0};const control=E.createEncodingControl(file,{status:()=>{if(!actual)throw Error('No telemetry');return actual;}});
 assert.equal(control.snapshot().effective,'gpu');assert.equal(control.update({revision:0,preference:'cpu'}).effective,'starting');
 actual={...actual,effective:'cpu',preferenceRevision:1};assert.equal(control.snapshot().effective,'cpu');
 actual.gpuAvailable=false;assert.throws(()=>control.update({revision:1,preference:'auto'}),e=>e.status===409);assert.equal(control.snapshot().preference,'cpu');actual=null;assert.throws(()=>control.snapshot());
});
test('native preference reads reject links and shared permissions',{skip:process.platform==='win32'},t=>{
 const file=fixture(t),store=E.createEncodingPreference(file);store.update({revision:0,preference:'cpu'});fs.chmodSync(file,0o644);assert.throws(()=>E.readPreference(file));fs.chmodSync(file,0o600);fs.linkSync(file,file+'.link');assert.throws(()=>E.readPreference(file));fs.unlinkSync(file+'.link');fs.renameSync(file,file+'.real');fs.symlinkSync(file+'.real',file);assert.throws(()=>E.readPreference(file));
});
test('always-available audio migrates shared off flag without changing gain; view-only retains dormant layout preference',t=>{
 const dir=path.dirname(fixture(t)),audioFile=path.join(dir,'audio.json'),rearFile=path.join(dir,'rear.json');
 const {createAudioSettings}=require('./audio-settings'),{createRearDisplaySettings}=require('./rear-display-settings');
 const audio=createAudioSettings(audioFile);audio.update('enabled',0);audio.update('volume',150);const bytes=fs.readFileSync(audioFile);
 const available=createAudioSettings(audioFile,{alwaysEnabled:true});assert.deepEqual(available.snapshot(),{enabled:true,volume:150});assert.notDeepEqual(fs.readFileSync(audioFile),bytes);assert.deepEqual(createAudioSettings(audioFile).snapshot(),{enabled:true,volume:150});assert.throws(()=>available.update('enabled',0),e=>e.status===400);
 const rear=createRearDisplaySettings(rearFile);rear.update({revision:0,enabled:true,prioritised:true});const old=fs.readFileSync(rearFile),view=createRearDisplaySettings(rearFile,{alwaysAvailable:true});assert.deepEqual(view.snapshot(),{version:1,revision:1,enabled:true,prioritised:false});assert.deepEqual(fs.readFileSync(rearFile),old);assert.throws(()=>view.update({revision:1,enabled:false,prioritised:false}),e=>e.status===400);assert.throws(()=>view.update({revision:1,enabled:true,prioritised:true}),e=>e.status===400);
});
test('view-only cannot gain shared control after master disconnects and explicit master claim retires old authority',async t=>{
 const {createDisplayControl}=require('./display-control');let retire=0;let now=0;const timers=new Map();let next=0;
 const clock={now:()=>now,setTimeout:(fn,ms)=>{timers.set(++next,{fn,at:now+ms});return next;},clearTimeout:id=>timers.delete(id)};
 const control=createDisplayControl({masterViewOnly:true,clock,helper:{inspect:async()=>({width:640,height:480,density:180,resolutionPreset:4,isResponsive:1,recoveryRequired:false})},rearSettings:{snapshot:()=>({version:1,revision:0,enabled:true,prioritised:false})},retireAuthority:async()=>{retire++;}});t.after(()=>control.close());await control.initialize();
 const a=control.createSession(),b=control.createSession();control.connect(a.token,'display');await new Promise(r=>setImmediate(r));control.connect(b.token,'display');await new Promise(r=>setImmediate(r));assert.equal(control.snapshot(a.token).canControl,true);assert.equal(control.snapshot(b.token).canControl,false);
 control.disconnect(a.token,'display');now=6000;for(const [id,timer]of timers)if(timer.at<=now){timers.delete(id);timer.fn();}await new Promise(r=>setImmediate(r));assert.equal(control.snapshot(b.token).canControl,false);
 await control.registerRole(b.token,'primary',b.csrfToken,{claim:true,generation:control.snapshot(b.token).generation});assert.equal(control.snapshot(b.token).canControl,true);
 const c=control.createSession();control.connect(c.token,'display');await control.registerRole(c.token,'primary',c.csrfToken,{claim:true,generation:control.snapshot(c.token).generation});assert.ok(retire>=1);assert.equal(control.snapshot(c.token).canControl,true);assert.equal(control.snapshot(b.token).canControl,false);
});
