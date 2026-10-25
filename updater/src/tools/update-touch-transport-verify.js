// SPDX-License-Identifier: GPL-3.0-only
// Actual browser-session protocol/native keepalive; no injected input or saved media.
const assert=require('node:assert/strict'),fs=require('node:fs'),crypto=require('node:crypto'),https=require('node:https');
const {WebSocket}=require('../runtime/preview-gateway/node_modules/ws');
async function main([origin,certPath]){
  const url=new URL(origin);assert.equal(url.protocol,'https:');
  const ca=fs.readFileSync(certPath),certificate=new crypto.X509Certificate(ca);
  assert.equal(certificate.checkIP(url.hostname),url.hostname);
  const session=await new Promise((resolve,reject)=>{
    const request=https.request(new URL('/api/displaySession',origin),{method:'POST',ca,
      headers:{Origin:origin,'Content-Type':'application/json','Content-Length':2},timeout:10000},response=>{
      let text='';response.on('data',raw=>{text+=raw;if(text.length>4096)request.destroy(new Error('Bounded session response required'));});
      response.on('end',()=>{try{assert.equal(response.statusCode,200);resolve(JSON.parse(text));}catch(error){reject(error);}});
    });
    request.on('error',reject);request.on('timeout',()=>request.destroy(new Error('Session request timeout')));request.end('{}');
  });
  assert.match(session.token,/^[a-f0-9]{64}$/);
  const socketUrl=(path)=>{const value=new URL(path,origin);value.protocol='wss:';value.searchParams.set('session',session.token);return value;};
  let display,touch,frames=0,pings=0,messages=0;
  try{
    await new Promise((resolve,reject)=>{
      display=new WebSocket(socketUrl('/sockets/display'),{ca,origin,handshakeTimeout:10000});
      display.on('message',()=>frames++);display.once('open',resolve);display.once('error',reject);
    });
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Two native idle keepalives did not verify')),35000);
      touch=new WebSocket(socketUrl('/sockets/touchscreen'),{ca,origin,handshakeTimeout:10000});
      touch.on('ping',()=>{if(++pings>=2){clearTimeout(timer);resolve();}});
      touch.on('message',()=>messages++);
      touch.on('error',error=>{clearTimeout(timer);reject(error);});
      touch.on('close',code=>{if(pings<2){clearTimeout(timer);reject(new Error('Native channel closed before keepalive verification: '+code));}});
    });
    assert.equal(touch.readyState,WebSocket.OPEN);assert.ok(frames>0);
    console.log(JSON.stringify({kind:'live-native-touch-transport',nativeIdlePings:pings,displayProtocolFrames:frames,
      applicationMessagesSent:0,applicationMessagesReceived:messages,certificatePinned:true,androidInputInjected:false,
      screenshotsSaved:false,mediaSaved:false,scope:'Actual browser-session protocol/proxy and sole-owner native keepalive; no assertion of touch latency or app response.'}));
  }finally{touch?.terminate();display?.terminate();}
}
main(process.argv.slice(2)).catch(error=>{console.error(error.message);process.exitCode=1;});
