// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../runtime/preview-gateway/node_modules/playwright-core');
const {styles}=require('../runtime/preview-gateway/browser-player-startup');
const frontend=process.env.TA_FRONTEND_DIR;if(!frontend)throw Error('Set TA_FRONTEND_DIR to verified frontend');
test('font-independent startup buttons precede JavaScript; logo waits for actual renderer draw and fades once',async t=>{
 const browser=await chromium.launch({executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});t.after(()=>browser.close());
 for(const viewport of [{width:640,height:480},{width:480,height:480}])for(const [decoder,file,reduced]of [['legacy','h264-broadway/h264Brodway.js',false],['webcodecs','h264-webcodecs/h264WebCodecs.js',false],['legacy','h264-broadway/h264Brodway.js',true]]){
  const page=await browser.newPage({viewport,reducedMotion:reduced?'reduce':'no-preference'});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://startup.test/**',route=>{
   const name=new URL(route.request().url()).pathname.slice(1);if(name.startsWith('fonts/'))return route.abort();
   if(name==='css/app.css')return route.fulfill({body:fs.readFileSync(path.join(frontend,name),'utf8')+'\n'+styles(),contentType:'text/css'});
   if(name==='assets/droiddeck-logo.png')return route.fulfill({body:fs.readFileSync(path.join(frontend,name)),contentType:'image/png'});
   return route.abort();
  });
  await page.evaluate(html=>{const doc=new DOMParser().parseFromString(html,'text/html');for(const script of doc.querySelectorAll('script'))script.remove();const base=doc.createElement('base');base.href='http://startup.test/';doc.head.prepend(base);document.open();document.write(doc.documentElement.outerHTML);document.close();},fs.readFileSync(path.join(frontend,'index.html'),'utf8'));
  await page.locator('#home-controls').waitFor();await page.waitForTimeout(250);
  for(const id of ['home-audio-button','home-settings-button']){
   const state=await page.locator('#'+id+' .home-control-icon').evaluate(e=>{const r=document.createRange();r.selectNodeContents(e);return {font:getComputedStyle(e).fontSize,textWidth:r.getBoundingClientRect().width,image:getComputedStyle(e,'::before').backgroundImage,button:e.parentElement.getBoundingClientRect().width};});
   assert.equal(state.font,'0px');assert.equal(state.textWidth,0);assert.match(state.image,/data:image\/svg\+xml/);assert.equal(state.button,39);
  }
  assert.equal(await page.locator('html.dm-startup-ready').count(),0,'CSS loading precedes any app JavaScript');
  assert.ok(await page.locator('#home-stage').evaluate(e=>Number(getComputedStyle(e,'::after').opacity)>0));
  if(process.env.SETTINGS_SCREENSHOT_DIR&&!reduced&&decoder==='legacy')await page.screenshot({path:path.join(process.env.SETTINGS_SCREENSHOT_DIR,'startup-before-frame-'+viewport.width+'x'+viewport.height+'.png')});
  await page.addScriptTag({path:path.resolve(__dirname,'../runtime/preview-gateway/browser-player-startup.js')});
  await page.evaluate(()=>{window.TAHtmlVideoPacketFormat='scrcpy';window.generatedWorkers=[];window.Worker=class{constructor(){generatedWorkers.push(this);}postMessage(){}terminate(){}};});
  await page.addScriptTag({path:path.join(frontend,'js/display/renderers',file)});
  await page.evaluate(()=>{canvas.dataset.videoState='ready';});await page.waitForTimeout(30);assert.equal(await page.locator('html.dm-startup-ready').count(),0,'socket/config readiness is not a rendered frame');
  await page.evaluate(()=>{canvas.dataset.videoFrames='0';canvas.dataset.videoWidth='640';canvas.dataset.videoHeight='480';});await page.waitForTimeout(30);assert.equal(await page.locator('html.dm-startup-ready').count(),0);
  await page.evaluate(()=>{generatedWorkers[0].onmessage({data:{frameRendered:true,width:640,height:480,pendingDecode:0}});});
  await page.waitForFunction(()=>document.documentElement.classList.contains('dm-startup-ready'));
  assert.equal(await page.locator('#home-stage').getAttribute('aria-busy'),'false');
  if(!reduced)await page.waitForTimeout(380);
  assert.equal(await page.locator('#home-stage').evaluate(e=>Number(getComputedStyle(e,'::after').opacity)),0);
  assert.equal(await page.locator('#home-stage').evaluate(e=>Number(getComputedStyle(e,'::before').opacity)),0);
  await page.evaluate(()=>{canvas.dataset.videoState='reconnecting';});assert.equal(await page.locator('html.dm-startup-ready').count(),1,'brief network loss must not repeatedly cover the stream');
  assert.deepEqual(errors,[]);await page.close();
 }
});
