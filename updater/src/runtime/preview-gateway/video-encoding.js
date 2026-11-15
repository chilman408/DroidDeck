// SPDX-License-Identifier: GPL-3.0-only
// User preference can only reduce acceleration. Protected capture admission remains authoritative.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {readVideoSettings}=require('./video-settings');
const STATUS_DIRECTORY='/run/droidmate-encoding';
const STATUS_FILE=STATUS_DIRECTORY+'/status.json';
const initial=Object.freeze({version:1,revision:0,preference:'auto'});
const failure=(message,status=400)=>Object.assign(Error(message),{status});
function validatePreference(v){
 if(!v||Array.isArray(v)||Object.keys(v).sort().join(',')!=='preference,revision,version'||v.version!==1||!['auto','cpu'].includes(v.preference)||!Number.isSafeInteger(v.revision)||v.revision<0||v.revision>=Number.MAX_SAFE_INTEGER)throw failure('Invalid encoding preference');
 return {...v};
}
function preferenceFile(videoFile){return path.join(path.dirname(videoFile),'browser-encoding.json');}
function readPreference(file,{uid=process.getuid?.()}={}){return readVideoSettings(file,{uid,validate:validatePreference,missing:initial});}
function createEncodingPreference(file){
 const snapshot=()=>readPreference(file);snapshot();
 return {snapshot,update(payload){
  if(!payload||Array.isArray(payload)||Object.keys(payload).sort().join(',')!=='preference,revision')throw failure('Invalid encoding request');
  validatePreference({version:1,...payload});const current=snapshot();
  if(payload.revision!==current.revision)throw failure('Encoding preference changed; reload settings',409);
  if(payload.preference===current.preference)return current;
  const next=validatePreference({version:1,...payload,revision:current.revision+1});const temporary=file+'.'+crypto.randomBytes(8).toString('hex')+'.tmp';let fd;
  try{fd=fs.openSync(temporary,'wx',0o600);fs.writeFileSync(fd,JSON.stringify(next)+'\n');fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temporary,file);}finally{if(fd!==undefined)fs.closeSync(fd);if(fs.existsSync(temporary))fs.unlinkSync(temporary);}
  return next;
 }};
}
function chooseEncoding(protectedSettings,preference){
 validatePreference(preference);
 if(preference.preference==='cpu')return {captureMode:'cpu',framePacing:protectedSettings.framePacing,encoder:'cpu',source:'manual-cpu'};
 return {...protectedSettings};
}
function privateRootDirectory(directory){const stat=fs.lstatSync(directory);if(!stat.isDirectory()||stat.uid!==0||stat.mode&0o022)throw Error('Unsafe encoding status directory');}
function processBirth(pid){const value=fs.readFileSync('/proc/'+pid+'/stat','utf8');return value.slice(value.lastIndexOf(')')+2).split(' ')[19];}
function validateStatus(v){
 if(!v||Array.isArray(v)||Object.keys(v).sort().join(',')!=='birth,boot,effective,gpuAvailable,pid,preference,preferenceRevision,updatedAt,version'||v.version!==1||!['cpu','gpu','starting','unavailable'].includes(v.effective)||typeof v.gpuAvailable!=='boolean'||!['auto','cpu'].includes(v.preference)||!Number.isSafeInteger(v.pid)||v.pid<2||!Number.isSafeInteger(v.preferenceRevision)||v.preferenceRevision<0||!Number.isSafeInteger(v.updatedAt)||typeof v.birth!=='string'||!/^\d+$/.test(v.birth)||!/^[-a-f0-9]{36}$/.test(v.boot))throw Error('Invalid encoder status');
 return v;
}
function readStatus({now=Date.now}={}){
 privateRootDirectory('/');privateRootDirectory('/run');privateRootDirectory(STATUS_DIRECTORY);
 const fd=fs.openSync(STATUS_FILE,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
 try{
  const info=fs.fstatSync(fd);if(!info.isFile()||info.uid!==0||info.nlink!==1||info.mode&0o022||info.size>2048)throw Error('Unsafe encoding status');
  const buffer=Buffer.alloc(2049),count=fs.readSync(fd,buffer,0,buffer.length,0);if(count>2048)throw Error('Oversized encoder status');
  const value=validateStatus(JSON.parse(buffer.subarray(0,count)));
  if(value.boot!==fs.readFileSync('/proc/sys/kernel/random/boot_id','utf8').trim()||processBirth(value.pid)!==value.birth||fs.statSync('/proc/'+value.pid).uid!==0||now()<value.updatedAt||now()-value.updatedAt>10000)throw Error('Stale encoder status');
  return value;
 }finally{fs.closeSync(fd);}
}
function createStatusWriter({gpuAvailable}){
 if(process.platform!=='linux'||process.getuid?.()!==0||typeof gpuAvailable!=='boolean')throw Error('Privileged encoder status writer required');
 privateRootDirectory('/');privateRootDirectory('/run');
 try{fs.mkdirSync(STATUS_DIRECTORY,{mode:0o755});}catch(error){if(error.code!=='EEXIST')throw error;}
 privateRootDirectory(STATUS_DIRECTORY);
 const identity=fs.lstatSync(STATUS_DIRECTORY),boot=fs.readFileSync('/proc/sys/kernel/random/boot_id','utf8').trim(),pid=process.pid,birth=processBirth(pid);
 return (effective,preferenceRevision,preference='auto')=>{
  privateRootDirectory(STATUS_DIRECTORY);const current=fs.lstatSync(STATUS_DIRECTORY);if(current.dev!==identity.dev||current.ino!==identity.ino)throw Error('Encoder status directory changed');
  const existing=fs.lstatSync(STATUS_FILE,{throwIfNoEntry:false});if(existing&&(!existing.isFile()||existing.uid!==0||existing.nlink!==1||existing.mode&0o022||existing.size>2048))throw Error('Foreign encoder status preserved');
  const value=validateStatus({version:1,boot,pid,birth,effective,gpuAvailable,preference,preferenceRevision,updatedAt:Date.now()});
  const temporary=STATUS_DIRECTORY+'/.status-'+crypto.randomBytes(16).toString('hex');let fd;
  try{fd=fs.openSync(temporary,'wx',0o644);fs.writeFileSync(fd,JSON.stringify(value)+'\n');fs.closeSync(fd);fd=undefined;fs.renameSync(temporary,STATUS_FILE);}finally{if(fd!==undefined)fs.closeSync(fd);if(fs.existsSync(temporary))fs.unlinkSync(temporary);}
 };
}
function createEncodingControl(file,{status=readStatus}={}){
 const store=createEncodingPreference(file);
 const snapshot=()=>{const preference=store.snapshot(),actual=status();return {revision:preference.revision,preference:preference.preference,effective:actual.preferenceRevision===preference.revision?actual.effective:'starting',gpuAvailable:actual.gpuAvailable};};
 return {snapshot,update(payload){const before=snapshot();if(payload?.preference==='auto'&&!before.gpuAvailable)throw failure('GPU encoding is unavailable',409);store.update(payload);return snapshot();}};
}
module.exports={validatePreference,readPreference,preferenceFile,createEncodingPreference,chooseEncoding,createEncodingControl,readStatus,createStatusWriter};
