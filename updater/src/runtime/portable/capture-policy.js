// SPDX-License-Identifier: GPL-3.0-only
// Root-owned, optional host policy. Kept outside runtime.json for old-release rollback.
const fs=require('node:fs');
const {readBoundedRegular}=require('./read-bounded');
const POLICY_FILE='/etc/droiddeck/capture-policy.json',MAX_BYTES=256;
function normalizeCapturePolicy(value){
 if(value&&typeof value==='object'&&!Array.isArray(value)&&value.version===2&&Object.keys(value).sort().join(',')==='encoder,preprocessing,version'&&['cpu','gpu'].includes(value.preprocessing)&&value.encoder==='vaapi')return Object.freeze({version:2,preprocessing:value.preprocessing,encoder:'vaapi'});
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!=='preprocessing,version'||value.version!==1||!['cpu','gpu'].includes(value.preprocessing))throw Error('Invalid capture policy');
 return Object.freeze({version:1,preprocessing:value.preprocessing});
}
function parseCapturePolicy(data){
 if(!Buffer.isBuffer(data)||!data.length||data.length>MAX_BYTES)throw Error('Invalid capture policy bytes');
 let text,policy;
 try{text=new TextDecoder('utf-8',{fatal:true}).decode(data);policy=normalizeCapturePolicy(JSON.parse(text))}catch{throw Error('Invalid capture policy bytes')}
 // The closed two-key schema accepts ordinary whitespace and either key order,
 // while rejecting duplicate keys, alternate numeric forms and escaped aliases.
 const compact=text.replace(/[ \t\r\n]/g,'');
 const orders=policy.version===2?['version,preprocessing,encoder','version,encoder,preprocessing','preprocessing,version,encoder','preprocessing,encoder,version','encoder,version,preprocessing','encoder,preprocessing,version']:['version,preprocessing','preprocessing,version'];
 const forms=orders.map(order=>'{'+order.split(',').map(key=>JSON.stringify(key)+':'+JSON.stringify(policy[key])).join(',')+'}');
 if(!forms.includes(compact))throw Error('Ambiguous capture policy bytes');
 return policy;
}
function protectedStat(stat,directory){
 if(stat.uid!==0||stat.mode&0o022||!(directory?stat.isDirectory():stat.isFile())||(!directory&&(stat.nlink!==1||stat.size<1||stat.size>MAX_BYTES)))throw Error('Unsafe capture policy metadata');
}
function readCapturePolicy({io=fs,read=readBoundedRegular}={}){
 for(const directory of ['/','/etc'])protectedStat(io.lstatSync(directory),true);
 let directory;
 try{directory=io.lstatSync('/etc/droiddeck')}catch(e){if(e.code==='ENOENT')return null;throw e}
 protectedStat(directory,true);
 let before;
 try{before=io.lstatSync(POLICY_FILE)}catch(e){if(e.code==='ENOENT')return null;throw e}
 protectedStat(before,false);
 const policy=parseCapturePolicy(read(POLICY_FILE,MAX_BYTES)),after=io.lstatSync(POLICY_FILE);
 protectedStat(after,false);
 if(['ino','dev','size','mtimeMs','ctimeMs'].some(k=>before[k]!==after[k]))throw Error('Capture policy changed during read');
 return policy;
}
function resolveCaptureSettings(environment=process.env,{read=readCapturePolicy}={}){
 const mode=environment.DROIDDECK_EXPERIMENTAL_GPU_CAPTURE,pacing=environment.DROIDDECK_EXPERIMENTAL_FRAME_PACING;
 if(mode!==undefined&&!['cpu','hybrid'].includes(mode)||pacing!==undefined&&!['legacy','bounded'].includes(pacing))throw Error('Invalid experimental capture setting');
 const policy=read();
 if(policy!==null){
  if(policy.version===2){
   const checked=normalizeCapturePolicy(policy),captureMode=checked.preprocessing==='gpu'?'hybrid':'cpu';
   if(mode!==undefined&&mode!==captureMode||pacing!==undefined&&pacing!=='bounded'||environment.DROIDMATE_EXPERIMENTAL_H264_ENCODER!==undefined&&environment.DROIDMATE_EXPERIMENTAL_H264_ENCODER!=='vaapi')throw Error('Encoder policy conflicts with environment');
   return Object.freeze({captureMode,framePacing:'bounded',encoder:'vaapi',source:'protected-policy'});
  }
  const value=normalizeCapturePolicy(policy),captureMode=value.preprocessing==='gpu'?'hybrid':'cpu',framePacing=value.preprocessing==='gpu'?'bounded':'legacy';
  if(mode!==undefined&&mode!==captureMode||pacing!==undefined&&pacing!==framePacing)throw Error('Capture policy conflicts with environment');
  return Object.freeze({captureMode,framePacing,source:'protected-policy'});
 }
 return Object.freeze({captureMode:mode??'cpu',framePacing:pacing??'legacy',source:mode!==undefined||pacing!==undefined?'development-environment':'default'});
}
function main(args){
 if(process.platform!=='linux'||process.getuid?.()!==0||args.length!==1||args[0]!=='inspect')throw Error('Root Linux capture-policy inspect required');
 console.log(JSON.stringify({...resolveCaptureSettings(),experimental:true,installable:false}));
}
if(require.main===module){try{main(process.argv.slice(2))}catch{console.error('Capture policy rejected');process.exitCode=1}}
module.exports={POLICY_FILE,MAX_BYTES,normalizeCapturePolicy,parseCapturePolicy,readCapturePolicy,resolveCaptureSettings};
