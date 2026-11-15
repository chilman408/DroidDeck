// SPDX-License-Identifier: GPL-3.0-only
// Render-blocking CSS supplies font-independent controls and the initial logo.
// Readiness comes only from the existing renderer's successful canvas draw report.
(function(global){
 'use strict';
 const mark='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3" fill="none" stroke="white" stroke-width="1.7"/><path d="M6 3h12a3 3 0 0 1 3 3v1H3V6a3 3 0 0 1 3-3zM5.5 18v-1a6.5 6.5 0 0 1 13 0v1zM9 14a.85.85 0 1 0 0 1.7.85.85 0 1 0 0-1.7zM15 14a.85.85 0 1 0 0 1.7.85.85 0 1 0 0-1.7z" fill="white" fill-rule="evenodd"/><path d="M8 12 6.5 9.5M16 12l1.5-2.5" fill="none" stroke="white" stroke-width="1.6" stroke-linecap="round"/></svg>';
 const speaker='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4zm13-1 5 8m0-8-5 8" fill="none" stroke="white" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
 const download='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4" fill="none" stroke="white" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
 const uri=svg=>'url("data:image/svg+xml,'+encodeURIComponent(svg)+'")';
 function styles(){return `
 /* Startup presentation does not depend on fonts or JavaScript execution. */
 html:not(.dm-startup-ready) #touch-layer{pointer-events:none!important}
 #home-controls .home-control-icon{font-size:0!important;line-height:0;width:30px;height:30px;display:block;opacity:.82}
 #home-controls .home-control-icon::before{content:"";display:block;width:30px;height:30px;background:center/contain no-repeat}
 #home-settings-button .home-control-icon::before{background-image:${uri(mark)}}
 #home-audio-button .home-control-icon::before{background-image:${uri(speaker)}}
 #home-update-button .home-control-icon::before{background-image:${uri(download)}}
 #home-stage::before{content:"";position:absolute;inset:0;z-index:25;pointer-events:none;background:#101820;opacity:1;transition:opacity 320ms ease,visibility 0s 320ms}
 #home-stage::after{content:"";position:absolute;z-index:26;pointer-events:none;left:50%;top:50%;width:clamp(128px,24vmin,200px);height:clamp(128px,24vmin,200px);transform:translate(-50%,-50%);background:url("../assets/droiddeck-logo.png") center/contain no-repeat;opacity:.96;animation:dm-logo-enter 520ms ease-out both;transition:opacity 320ms ease,visibility 0s 320ms}
 @keyframes dm-logo-enter{from{opacity:0}to{opacity:.96}}
 html.dm-startup-ready #home-stage::before,html.dm-startup-ready #home-stage::after{animation:none;opacity:0;visibility:hidden}
 .dm-startup-status{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
 @media(prefers-reduced-motion:reduce){#home-stage::before,#home-stage::after{animation:none;transition:none}}
 `;}
 function install(){
  const document=global.document,stage=document.getElementById('home-stage'),canvas=document.getElementById('canvas');
  if(!stage||!canvas)return;
  for(const [id,label] of [['home-audio-button','Unmute'],['home-settings-button','Settings'],['home-update-button','Open Updates']]){const button=document.getElementById(id);if(button){button.setAttribute('aria-label',label);button.querySelector('.home-control-icon')?.setAttribute('aria-hidden','true');}}
  let ready=false,timer;
  const status=document.createElement('span');status.className='dm-startup-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');status.textContent='Connecting to Android.';stage.append(status);stage.setAttribute('aria-busy','true');
  const validSize=v=>Number.isSafeInteger(v)&&v>=96&&v<=1920&&v%2===0;
  const observe=()=>{
   const count=Number(canvas.dataset.videoFrames),width=Number(canvas.dataset.videoWidth),height=Number(canvas.dataset.videoHeight);
   if(ready||canvas.dataset.videoState!=='ready'||!Number.isSafeInteger(count)||count<1||!validSize(width)||!validSize(height)||width*height>1600000)return;
   ready=true;observer.disconnect();global.clearTimeout(timer);stage.setAttribute('aria-busy','false');status.textContent='Android display ready.';document.documentElement.classList.add('dm-startup-ready');
  };
  const observer=new global.MutationObserver(observe);
  const watch=()=>{if(ready)return;observer.observe(canvas,{attributes:true,attributeFilter:['data-video-frames','data-video-state','data-video-width','data-video-height']});observe();if(!ready){global.clearTimeout(timer);timer=global.setTimeout(()=>{status.textContent='Waiting for Android. Settings and connection controls are available.';},10000);}};
  watch();global.addEventListener('pagehide',()=>{observer.disconnect();global.clearTimeout(timer);});global.addEventListener('pageshow',watch);
 }
 if(typeof module==='object'&&module.exports)module.exports={styles};
 else if(global.document.readyState==='loading')global.document.addEventListener('DOMContentLoaded',install,{once:true});
 else install();
})(typeof window==='object'?window:globalThis);
