// SPDX-License-Identifier: GPL-3.0-only
// Run ONLY in a private mount namespace with an isolated tmpfs /run.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const E=require('./video-encoding');
test('native root status reports ready encoder and rejects stale or substituted telemetry', {skip:process.platform!=='linux'||process.getuid?.()!==0||process.env.DROIDMATE_ISOLATED_STATUS_TEST!=='1'},()=>{
 const mounts=fs.readFileSync('/proc/self/mountinfo','utf8');assert.ok(mounts.split('\n').some(l=>l.split(' ')[4]==='/run'&&l.includes(' - tmpfs ')),'isolated /run tmpfs required');assert.equal(fs.existsSync('/run/droidmate-encoding'),false,'new private namespace required');
 const write=E.createStatusWriter({gpuAvailable:true}),file='/run/droidmate-encoding/status.json';write('gpu',3);const ready=E.readStatus();assert.equal(ready.effective,'gpu');assert.equal(ready.preferenceRevision,3);assert.equal(ready.pid,process.pid);
 assert.throws(()=>E.readStatus({now:()=>ready.updatedAt+10001}),/Stale/);assert.throws(()=>E.readStatus({now:()=>ready.updatedAt-1}),/Stale/);
 write('starting',4);assert.equal(E.readStatus().effective,'starting');write('cpu',4,'cpu');assert.equal(E.readStatus().effective,'cpu');assert.equal(E.readStatus().preference,'cpu');
 const original=fs.readFileSync(file);const mutate=change=>{const state=JSON.parse(original);Object.assign(state,change);fs.writeFileSync(file,JSON.stringify(state));assert.throws(()=>E.readStatus());fs.writeFileSync(file,original);};
 mutate({preference:'gpu'});mutate({birth:'1'});mutate({boot:'0'.repeat(36)});mutate({effective:'vaapi'});mutate({command:'reboot'});
 fs.chmodSync(file,0o666);assert.throws(()=>E.readStatus(),/Unsafe/);assert.throws(()=>write('cpu',4),/Foreign/);fs.chmodSync(file,0o644);
 fs.linkSync(file,file+'.link');assert.throws(()=>E.readStatus(),/Unsafe/);assert.throws(()=>write('cpu',4),/Foreign/);fs.unlinkSync(file+'.link');
 fs.renameSync(file,file+'.real');fs.symlinkSync(file+'.real',file);assert.throws(()=>E.readStatus());assert.throws(()=>write('cpu',4),/Foreign/);fs.unlinkSync(file);fs.renameSync(file+'.real',file);
 write('unavailable',4);assert.equal(E.readStatus().effective,'unavailable');
});
