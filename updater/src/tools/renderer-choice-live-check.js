// SPDX-License-Identifier: GPL-3.0-only
// Real browser-local choices only; block Android/settings writes, keep pixels in RAM.
const assert=require('node:assert/strict'),fs=require('node:fs'),crypto=require('node:crypto'),https=require('node:https');
const {chromium}=require('../runtime/preview-gateway/node_modules/playwright-core');
const {PNG}=require('../runtime/waydroid-display/node_modules/pngjs');
async function main([origin,cert='-',candidate]) {
 const args=[];
 if(cert!=='-') {
  const c=new crypto.X509Certificate(fs.readFileSync(cert));assert.equal(c.checkIP(new URL(origin).hostname),new URL(origin).hostname);
  args.push('--ignore-certificate-errors-spki-list='+crypto.createHash('sha256').update(c.publicKey.export({type:'spki',format:'der'})).digest('base64'));
 }
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,args});
 try {
  const page=await browser.newPage(),errors=[],blockedPaths=[];let writes=0;
  page.on('pageerror',e=>errors.push(e.name));
  await page.addInitScript(()=>{for(const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey','flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey'])localStorage.setItem(key,'true');});
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;
   if(req.method()==='GET'&&path==='/api/displayState'){
    try {
     const state=await new Promise((resolve,reject)=>{
      const request=https.get(req.url(),{...(cert!=='-'?{ca:fs.readFileSync(cert)}:{}),headers:req.headers(),timeout:5000},response=>{
       let body='';response.on('data',chunk=>{body+=chunk;if(body.length>65536)request.destroy(new Error('Bounded display state required'));});
       response.on('end',()=>{try{if(response.statusCode!==200)throw new Error('Display state unavailable');resolve(JSON.parse(body));}catch{reject(new Error('Display state unavailable'));}});
      });request.on('timeout',()=>request.destroy(new Error('Display state timeout')));request.on('error',()=>reject(new Error('Pinned display state request failed')));
     });
     return route.fulfill({status:200,contentType:'application/json',json:{...state,canControl:false,canResize:false}});
    } catch {return route.abort();}
   }
   if(req.method()!=='GET'&&!['/api/displaySession','/api/displayRole'].includes(path)){writes++;blockedPaths.push(path);return route.abort();}
   return route.continue();
  });
  if(candidate)for(const [asset,file]of [['/js/display/display-renderer-manager.js','manager'],['/js/settings/settings-controller.js','settings']]) {
   const raw=fs.readFileSync(candidate+'/droiddeck-'+file+'-choice-20261024if.js');
   await page.route(origin+asset,r=>r.fulfill({status:200,contentType:'text/javascript',body:raw}));
  }
  async function picture(expected) {
   await page.waitForFunction(value=>window.TAHtml?.browserRenderer.effectiveChoice()===value&&Number(document.getElementById('canvas')?.dataset.videoFrames)>=3,expected,{timeout:30000});
   const p=PNG.sync.read(await page.locator('#canvas').screenshot());let nonblack=0;
   for(let i=0;i<p.data.length;i+=4)if(p.data[i]>8||p.data[i+1]>8||p.data[i+2]>8)nonblack++;
   assert.ok(nonblack>p.width*p.height*.01);
   return {renderer:expected,frames:await page.locator('#canvas').evaluate(c=>Number(c.dataset.videoFrames)),nonblack};
  }
  async function choose(label) {
   await page.locator('#home-settings-button').click();await page.locator('[data-page="settings"]').click();
   await page.locator('#settings-sidebar button').filter({hasText:'Display',exact:true}).click();
   const row=page.locator('[data-browser-renderer]');await row.locator('button').first().click();
   assert.ok(await row.getByRole('button',{name:'H264 (WebCodecs)',exact:true}).isVisible());
   assert.ok(await row.getByRole('button',{name:'H264 (Legacy)',exact:true}).isVisible());
   await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),row.getByRole('button',{name:label,exact:true}).click()]);
  }
  assert.equal((await page.goto(origin+'/',{waitUntil:'domcontentloaded'})).status(),200);
  if(await page.locator('#main-display-button').isVisible())await page.locator('#main-display-button').click();
  const initial=await picture('legacy');await choose('H264 (WebCodecs)');const webcodecs=await picture('webcodecs');
  assert.equal(await page.evaluate(()=>localStorage.getItem('TeslaAndroid_browserH264Renderer_v1')),'webcodecs');
  await choose('H264 (Legacy)');const legacy=await picture('legacy');
  assert.equal(writes,0,JSON.stringify({blockedPaths}));assert.deepEqual(errors,[]);
  console.log(JSON.stringify({initial,webcodecs,legacy,settingsRowVisible:true,browserPreferencePersisted:true,globalWrites:writes,pageErrors:errors,screenshotsSaved:false,mediaSaved:false}));
 }finally{await browser.close();}
}
main(process.argv.slice(2)).catch(e=>{console.error(e.message);process.exitCode=1});
