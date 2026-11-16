// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../runtime/preview-gateway/node_modules/playwright-core');
const frontend=process.env.TA_FRONTEND_DIR;if(!frontend)throw Error('TA_FRONTEND_DIR must be the verified immutable frontend');
test('real Settings overlay keeps player alive and preserves controls, authority and navigation',async t=>{
 const browser=await chromium.launch({executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});t.after(()=>browser.close());
 const page=await browser.newPage({hasTouch:true});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://droidmate.test/**',route=>{const file=path.join(frontend,new URL(route.request().url()).pathname);if(!fs.existsSync(file)||!fs.statSync(file).isFile())return route.abort();return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.css')?'text/css':undefined});});
 await page.evaluate(html=>{const doc=new DOMParser().parseFromString(html,'text/html');for(const script of doc.querySelectorAll('script'))script.remove();const base=doc.createElement('base');base.href='http://droidmate.test/';doc.head.prepend(base);document.open();document.write(doc.documentElement.outerHTML);document.close();},fs.readFileSync(path.join(frontend,'index.html'),'utf8'));
 for(const file of ['js/core/shared.js','js/display/display-renderer-manager.js','js/settings/settings-controller.js','js/shell/shell-controller.js','js/home/home-controller.js','js/release-notes/release-notes-data.js'])await page.addScriptTag({path:path.join(frontend,file)});
 await page.evaluate(()=>{
  window.quality={policy:{enabled:true,revision:2,ceilingBitRate:10000000,ceilingMaxFps:60},profile:{revision:4,bitRate:450000,maxFps:10}};
  window.encoding={preference:'auto',effective:'gpu',gpuAvailable:true,revision:1};window.calls=[];window.pauseCalls=[];
  window.state={width:640,height:480,density:180,generation:1,resolutionPreset:4,isResponsive:1,displayControlSupported:1,canControl:true,canResize:true,transactionStatus:'idle',rearDisplayControlSupported:1,isRearDisplayEnabled:1,isRearDisplayPrioritised:0,rearDisplayRevision:0,displayRole:'primary',streamFormats:['h264'],videoControlSupported:1,videoTargets:quality.profile};
  window.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>url==='/api/videoAutomatic'?quality:url==='/api/videoEncoding'?encoding:{installedVersion:'0.1.1-rc.20',job:null,available:null}};};
  TAHtml.browserRenderer={options:()=>[{value:'webcodecs',label:'H264 (WebCodecs)'},{value:'legacy',label:'H264 (Legacy)'}],choice:()=> 'legacy',select:()=>{}};
  window.audioState={requested:'aac',effective:'aac',automaticQuality:true,targetBitRate:128000,ceilingBitRate:256000,playing:false};
  window.DroidDeckAudio={snapshot:()=>({...audioState}),setAutomatic:v=>audioState.automaticQuality=v,select:v=>{audioState.requested=v;}};
  window.getAudioDelayMs=()=>0;window.setAudioDelayMs=v=>calls.push({delay:v});
 });
 for(const name of ['browser-settings-policy','browser-update-policy','browser-adaptive-policy','browser-audio-policy','browser-settings-overlay'])await page.addScriptTag({path:path.resolve(__dirname,'../runtime/preview-gateway',name+'.js')});
 await page.evaluate(async()=>{
  window.api={displaySession:{token:'generated-session',csrfToken:'generated-csrf'},browserAudioSupported:true,browserGpsManaged:true,browserGpsSupported:true,
   fetchDisplayState:async()=>state,fetchSystemConfiguration:async()=>({'persist.tesla-android.browser_audio.is_enabled':1,'persist.tesla-android.browser_audio.volume':150,'persist.tesla-android.gps.is_active':1}),
   fetchDeviceInfo:async()=>({deviceInfoVersion:1,networkInfoVersion:1,android_addresses:[]}),fetchBrowserConnection:async()=>({revision:0,hostname:'generated.example',origin:'https://teslavm1.generated-long-hostname.example',previousOrigin:'https://previous.generated-long-hostname.example',defaultOrigin:'https://generated.example'}),fetchBrowserAccess:async()=>({enabled:false,username:'',csrfToken:'generated-csrf'}),postVideoTargets:async payload=>calls.push({video:payload})};
  window.display={remoteDisplayState:state,controlRequestInFlight:false,setPaused:value=>pauseCalls.push(value)};
  window.settings=new TAHtml.SettingsController({apiClient:api,displayController:display,sidebar:document.querySelector('#settings-sidebar'),content:document.querySelector('#settings-content'),banner:document.querySelector('#settings-banner'),bannerText:document.querySelector('#settings-banner-text'),bannerAction:document.querySelector('#settings-banner-action')});await settings.initialize();
  window.shell=new TAHtml.ShellController({apiClient:api,displayController:display,settingsController:settings,homeShell:document.querySelector('#home-shell'),panelShell:document.querySelector('#panel-shell'),navButtons:[],panelViews:{},appTitle:document.querySelector('#app-title')});shell.setPage('home');pauseCalls=[];shell.setPage('about');
 });
 assert.deepEqual(await page.locator('.settings-menu-label').allTextContents(),['Display','Audio','Connectivity','Device Info','Updates']);
 assert.equal(await page.locator('#home-shell').isVisible(),true);assert.deepEqual(await page.evaluate(()=>pauseCalls),[]);
 // Exercise the actual Settings launcher handler against the real Shell wrapper.
 await page.evaluate(()=>document.getElementById('home-settings-button').addEventListener('click',()=>shell.setPage('settings')));
 await page.locator('#home-settings-button').click();await page.waitForTimeout(220);
 assert.equal(await page.locator('.dm-settings-overlay').isVisible(),false,'second Settings tap closes root menu');
 assert.equal(await page.locator('#home-settings-button').getAttribute('aria-expanded'),'false');
 await page.locator('#home-settings-button').click();await page.evaluate(()=>settings.setActiveSection('display'));
 await page.locator('#home-settings-button').click();await page.waitForTimeout(220);
 assert.equal(await page.locator('.dm-settings-overlay').isVisible(),false,'second Settings tap closes a submenu too');
 assert.equal(await page.locator('.dm-menu-detail').isVisible(),false);
 await page.locator('#home-settings-button').click();
 assert.equal(await page.locator('.dm-settings-overlay').isVisible(),true);assert.equal(await page.locator('.dm-menu-detail').isVisible(),false,'reopen starts at root');
 await page.evaluate(()=>{shell.setPage('settings');shell.setPage('settings');});await page.waitForTimeout(240);
 assert.equal(await page.locator('.dm-settings-overlay').isVisible(),true,'tap during close animation reopens cleanly');

 for(const viewport of [{width:1280,height:720},{width:480,height:480},{width:640,height:480},{width:390,height:844}]){
  await page.setViewportSize(viewport);
  for(const id of ['display','audio','connectivity','deviceInfo','updates']){
   await page.evaluate(id=>settings.setActiveSection(id),id);await page.waitForTimeout(280);
   assert.equal(await page.locator('.dm-menu-detail').isVisible(),true);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${id}: horizontal fit`);
   assert.equal(await page.locator('.dm-menu-detail').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;}),true,`${id}: panel fit`);
   if(id==='display'){
    assert.equal(await page.locator('[data-video-bitrate] .settings-dropdown-value').textContent(),'10 Mbps');
    assert.deepEqual(await page.locator('[data-video-bitrate] .settings-dropdown-option').allTextContents(),['720 kbps','1 Mbps','2 Mbps','3 Mbps','4 Mbps','6 Mbps','8 Mbps','10 Mbps','12 Mbps']);
    assert.deepEqual(await page.locator('[data-video-frame-rate] .settings-dropdown-option').allTextContents(),['15 fps','30 fps','45 fps','60 fps']);
    assert.equal(await page.locator('[data-manual-aspect] button').first().isDisabled(),true);
    assert.deepEqual(await page.locator('[data-rear-role] label span').allTextContents(),['Master Display','View-Only Display']);
    assert.equal(await page.locator('[data-rear-shared]').count(),0);
    assert.match(await page.locator('.dm-current').textContent(),/GPU/);
    assert.equal(await page.locator('#settings-content .settings-tile-title').first().textContent(),'This Display');
    assert.equal(await page.locator('#settings-content').evaluate(e=>e.lastElementChild.matches('.dm-display-status')),true);
    assert.equal(await page.locator('.dm-current').evaluate(e=>(e.textContent.match(/450 kbps/g)||[]).length),1);
    assert.match(await page.locator('.dm-current').textContent(),/640 × 480.*450 kbps.*10 fps.*up to 10 Mbps.*60 fps.*GPU/);
    assert.equal(await page.getByText(/Resolution targets are constrained/).count(),0);
    assert.equal(await page.locator('.dm-video-policy-status').evaluate(e=>e.closest('.dm-display-status')!==null),true);
   }
   if(id==='audio'){
    assert.equal(await page.locator('[data-audio-format]').count(),1);assert.equal(await page.getByText('Browser Audio',{exact:true}).count(),0);
    assert.equal(await page.locator('.settings-audio-delay-slider').getAttribute('min'),'-750');assert.equal(await page.locator('.settings-audio-delay-slider').getAttribute('max'),'750');
    assert.equal(await page.locator('.settings-volume-value').first().textContent(),'100 %');
    assert.equal(await page.locator('.dm-audio-status').evaluate(e=>e===e.parentElement.lastElementChild),true);
    await page.evaluate(()=>{audioState.targetBitRate=64000;dispatchEvent(new Event('droiddeck-audio-format'));});
    assert.match(await page.locator('.dm-audio-status').textContent(),/64 kbps/);
   }
   if(id==='connectivity'){
    assert.equal(await page.getByRole('checkbox',{name:'Browser Login'}).count(),1);
    assert.equal(await page.locator('#settings-content .settings-tile-title').first().textContent(),'Browser Login');
    assert.equal(await page.locator('#connection-hostname').isDisabled(),false);assert.equal(await page.locator('.dm-master-gps').count(),1);
   }
   if(id==='deviceInfo')assert.deepEqual(await page.locator('.dm-settings-group > .dm-group-title').allTextContents(),['Device','Network']);
   if(id==='connectivity'||id==='deviceInfo'){
    assert.equal(await page.locator('#settings-content .settings-tile').evaluateAll(rows=>rows.filter(r=>r.querySelector('.settings-trailing-text')).every(r=>getComputedStyle(r.querySelector('.settings-trailing-text')).fontSize===getComputedStyle(r.querySelector('.settings-tile-title')).fontSize)),true,`${id}: value font matches heading`);
   }
   if(process.env.SETTINGS_SCREENSHOT_DIR&&viewport.width===640&&id==='display')await page.screenshot({path:path.join(process.env.SETTINGS_SCREENSHOT_DIR,'real-settings-640x480.png')});
   if(id==='updates'){
    assert.deepEqual(await page.locator('.dm-settings-group > .dm-group-title').allTextContents(),['Updates','About','Donation','Release Notes']);
    assert.equal(await page.locator('.dm-settings-overlay .donation-qr').count(),1);assert.equal(await page.locator('.dm-older-notes').count(),1);
    assert.match(await page.locator('.dm-settings-group').last().textContent(),/not yet available/);
    assert.equal(await page.locator('.dm-settings-overlay .about-content,.dm-settings-overlay .donation-content').evaluateAll(rows=>rows.every(row=>{const a=row.firstElementChild.getBoundingClientRect(),b=row.lastElementChild.getBoundingClientRect();return b.left>=a.right&&b.top<a.bottom&&a.top<b.bottom;})),true,'logo/QR beside copy');
   }
  }
 }
 assert.equal(await page.locator('#home-settings-button').evaluate(e=>getComputedStyle(e).color),'rgba(255, 255, 255, 0.51)');
 await page.evaluate(async()=>{quality.profile={revision:5,bitRate:720000,maxFps:15};await settings.setActiveSection('display');await settings._dmChildren[0]._renderActiveSection();});
 assert.match(await page.locator('.dm-current').textContent(),/720 kbps.*15 fps/);
 await page.evaluate(()=>{quality.profile={revision:6,bitRate:2000000,maxFps:30};});await page.waitForTimeout(2200);
 assert.match(await page.locator('.dm-current').textContent(),/2 Mbps.*30 fps/);assert.equal(await page.locator('[data-dm-encoding]').count(),1);
 await page.evaluate(async()=>{state.canControl=false;await settings.setActiveSection('connectivity');});
 assert.equal(await page.locator('#connection-hostname').isDisabled(),true);assert.equal(await page.locator('#browser-access-form button[type=submit]').isDisabled(),false);
 await page.evaluate(async()=>{await settings.setActiveSection('display');settings.refreshDisplayControl(state);});assert.equal(await page.locator('[data-dm-encoding] .settings-dropdown-trigger').isDisabled(),true);
 await page.evaluate(async()=>{settings._dmOverlay.showRoot();await settings.setActiveSection('audio');settings._dmOverlay.close();settings._dmOverlay.open();});await page.waitForTimeout(300);
 assert.equal(await page.locator('.dm-settings-overlay').isVisible(),true);assert.equal(await page.locator('.dm-menu-detail').isVisible(),false);
 await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(async()=>{await settings.setActiveSection('display');settings._dmOverlay.showRoot();});assert.equal(await page.locator('.dm-menu-detail').isVisible(),false);
 await page.evaluate(async()=>{state.canControl=true;encoding.gpuAvailable=false;await settings.setActiveSection('display');settings.refreshDisplayControl(state);});assert.equal(await page.locator('[data-dm-encoding] .settings-dropdown-trigger').isDisabled(),true);
 await page.evaluate(async()=>{const original=api.fetchDisplayState;api.fetchDisplayState=()=>new Promise((_resolve,reject)=>window.failSlowDisplay=reject);window.slowDisplay=settings.setActiveSection('display');while(!window.failSlowDisplay)await new Promise(r=>setTimeout(r,0));await settings.setActiveSection('audio');failSlowDisplay(Error('Delayed display failure'));await slowDisplay;api.fetchDisplayState=original;});
 assert.equal(await page.locator('[data-audio-format]').count(),1);assert.equal(await page.getByText('Service error',{exact:true}).count(),0);
 // Actual touch hit-testing must still reach Android when the corner is hidden.
 await page.evaluate(()=>settings._dmOverlay.close());await page.clock.install({time:new Date('2030-01-01T00:00:00Z')});await page.clock.pauseAt(new Date('2030-01-01T00:00:01Z'));await page.emulateMedia({reducedMotion:'no-preference'});
 await page.evaluate(()=>{settings._dmOverlay.close();TAHtml.HomeController.prototype._setButtonIcon.call({},document.getElementById('home-audio-button'),'volume_off');document.activeElement?.blur();document.documentElement.classList.add('dm-startup-ready');window.controlClicks=0;window.touchTargets=[];document.getElementById('home-audio-button').addEventListener('click',()=>++controlClicks);document.addEventListener('pointerdown',e=>{if(e.pointerType==='touch')touchTargets.push({id:e.target.id,cancelled:e.defaultPrevented});});document.dispatchEvent(new Event('droidmate-controls-activity'));});
 assert.equal(await page.locator('#home-controls').evaluate(e=>getComputedStyle(e).backgroundColor),'rgba(20, 29, 40, 0.51)');
 assert.equal(await page.locator('#home-controls').evaluate(e=>getComputedStyle(e).transitionDuration),'0.32s');
 for(const id of ['home-settings-button','home-audio-button'])assert.deepEqual(await page.locator('#'+id).evaluate(e=>({w:e.offsetWidth,h:e.offsetHeight,iw:e.querySelector('svg').getBoundingClientRect().width,ih:e.querySelector('svg').getBoundingClientRect().height})),{w:39,h:39,iw:28,ih:28});
 await page.clock.runFor(400);await page.evaluate(()=>document.dispatchEvent(new Event('droidmate-controls-activity')));
 await page.clock.fastForward(10999);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),false);
 await page.clock.fastForward(1);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),true,JSON.stringify(await page.evaluate(()=>({hidden:document.hidden,ready:document.documentElement.className,menus:[...document.querySelectorAll('.dm-settings-overlay')].map(e=>({hidden:e.hidden,inert:e.inert})),focus:document.querySelector('#home-controls :focus-visible')?.id,active:document.activeElement?.id,bar:document.getElementById('home-controls').className}))));
 assert.equal(await page.locator('#home-controls').evaluate(e=>getComputedStyle(e).pointerEvents),'none');
 const box=await page.locator('#home-audio-button').boundingBox();await page.touchscreen.tap(box.x+box.width/2,box.y+box.height/2);
 assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),false);assert.equal(await page.evaluate(()=>controlClicks),0,'reveal touch must not accidentally unmute');
 assert.equal(await page.evaluate(()=>touchTargets.length>0&&touchTargets.every(e=>!e.cancelled&&e.id!=='home-audio-button')),true,'original Android touch target passes through');
 await page.clock.fastForward(10000);await page.touchscreen.tap(100,150);await page.clock.fastForward(10000);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),false,'touch restarts all eleven seconds');
 await page.clock.fastForward(1001);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),true);
 // A dragged/cancelled reveal has no synthesized click; it unlocks afterward.
 const cdp=await page.context().newCDPSession(page);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+box.width/2,y:box.y+box.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:100,y:150}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});
 assert.equal(await page.locator('#home-controls').evaluate(e=>e.classList.contains('dm-controls-waking')),true);
 await page.clock.fastForward(300);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+box.width/2,y:box.y+box.height/2}]});
 await page.clock.fastForward(500);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),true,'previous fallback must not unlock a new touch');
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await page.clock.fastForward(400);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),false);assert.equal(await page.evaluate(()=>controlClicks),0);
 await page.clock.fastForward(11000);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),true);
 await page.keyboard.press('Tab');await page.locator('#home-audio-button').focus();await page.clock.fastForward(15000);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),false,'keyboard focus stays accessible');
 await page.evaluate(()=>{document.activeElement?.blur();settings._dmOverlay.open();});await page.clock.fastForward(15000);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),false,'open Settings retains controls');
 await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(()=>settings._dmOverlay.close());await page.clock.runFor(1);await page.clock.fastForward(11500);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),true);
 await page.evaluate(()=>dispatchEvent(new PageTransitionEvent('pagehide')));await page.clock.fastForward(30000);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),false);
 await page.evaluate(()=>dispatchEvent(new PageTransitionEvent('pageshow')));await page.clock.fastForward(11000);assert.equal(await page.locator('#home-controls').evaluate(e=>e.inert),true);
 await page.emulateMedia({reducedMotion:'reduce'});await page.touchscreen.tap(100,150);assert.equal(await page.locator('#home-controls').evaluate(e=>getComputedStyle(e).transitionDuration),'0s');
 assert.deepEqual(await page.evaluate(()=>pauseCalls),[]);assert.deepEqual(errors,[]);
});


test('deferred production script order attaches player hooks after Home and Shell load',async t=>{
 const browser=await chromium.launch({executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});t.after(()=>browser.close());
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const scripts=['js/core/shared.js','js/display/display-renderer-manager.js','js/settings/settings-controller.js','OVERLAY','js/home/home-controller.js','js/shell/shell-controller.js'];
 await page.route('http://deferred.test/**',route=>{const name=new URL(route.request().url()).pathname.slice(1);if(name==='')return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head>'+scripts.map((n,i)=>'<script defer src="/'+i+'.js"></script>').join('')+'</head><body><button id="home-settings-button"><span>settings</span></button></body></html>'});const i=Number(name.split('.')[0]);const body=scripts[i]==='OVERLAY'?fs.readFileSync(path.resolve(__dirname,'../runtime/preview-gateway/browser-settings-overlay.js')):fs.readFileSync(path.join(frontend,scripts[i]));return route.fulfill({contentType:'text/javascript',body});});
 await page.goto('http://deferred.test/',{waitUntil:'load'});
 assert.equal(await page.evaluate(()=>TAHtml.ShellController.prototype.setPage.toString().includes('overlay.close();return;')),true);
 assert.equal(await page.locator('#home-settings-button svg').count(),1);assert.deepEqual(errors,[]);
});
