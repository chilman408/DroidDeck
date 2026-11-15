// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {createGateway}=require('./server'),{createDisplayControl}=require('./display-control'),{createEncodingControl}=require('./video-encoding');
test('encoding HTTP controls require owner, exact origin/CSRF/revision and bounded closed fields',async t=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'dm-encoding-http-'));fs.chmodSync(directory,0o700);fs.writeFileSync(path.join(directory,'index.html'),'test');
 const store=createEncodingControl(path.join(directory,'encoding.json'),{status:()=>({gpuAvailable:true,effective:'gpu',preferenceRevision:0})});
 const display=createDisplayControl({helper:{inspect:async()=>({width:640,height:480,density:180,resolutionPreset:4,isResponsive:1,recoveryRequired:false})}});await display.initialize();
 const gateway=createGateway({frontendDir:directory,displayControl:display,videoEncodingControl:store});await new Promise(r=>gateway.listen(0,'127.0.0.1',r));
 t.after(async()=>{display.close();await new Promise(r=>gateway.close(r));assert.equal(path.dirname(directory),path.resolve(os.tmpdir()));fs.rmSync(directory,{recursive:true,force:true});});
 const origin='http://127.0.0.1:'+gateway.address().port,owner=display.createSession(),viewer=display.createSession();display.connect(owner.token,'display');display.connect(viewer.token,'display');
 const request=(value,session=owner,headers={},route='/api/videoEncoding')=>fetch(origin+route,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json','X-Display-Session':session.token,'X-Display-CSRF':session.csrfToken,...headers},body:typeof value==='string'?value:JSON.stringify(value)});
 const payload={revision:0,preference:'cpu'};
 for(const [session,headers]of [[viewer,{}],[owner,{Origin:'https://foreign.example'}],[owner,{'X-Display-CSRF':'wrong'}],[owner,{Origin:''}]])assert.equal((await request(payload,session,headers)).status,403);
 assert.equal((await request(payload,owner,{'Content-Type':'text/plain'})).status,415);assert.equal((await request(' '.repeat(257))).status,413);
 for(const value of ['{bad',null,[],{...payload,command:'reboot'},{...payload,preference:'vaapi'},{...payload,revision:-1}])assert.equal((await request(value)).status,400);
 for(const route of ['/api/%76ideoEncoding','/api/videoEncoding?x=1'])assert.equal((await request(payload,owner,{},route)).status,400);
 assert.equal(store.snapshot().revision,0);
 const read=await fetch(origin+'/api/videoEncoding',{headers:{'X-Display-Session':viewer.token}});assert.equal(read.status,200);assert.equal((await read.json()).effective,'gpu');
 assert.equal((await request(payload)).status,200);assert.equal((await request(payload)).status,409);assert.equal(store.snapshot().preference,'cpu');
 display.revokeAll();assert.equal((await request({revision:1,preference:'auto'})).status,403);
});
