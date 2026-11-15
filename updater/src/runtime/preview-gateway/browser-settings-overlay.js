// SPDX-License-Identifier: GPL-3.0-only
// Reuse the real controls/APIs while Settings overlays the unchanged live player.
(function(global){
 'use strict';
 const T=global.TAHtml,P=T?.SettingsController?.prototype;if(!P)throw Error('Settings controls unavailable');
 const D=global.document;
 const sections=[{id:'display',title:'Display',icon:'monitor'},{id:'audio',title:'Audio',icon:'speaker'},{id:'connectivity',title:'Connectivity',icon:'public'},{id:'deviceInfo',title:'Device Info',icon:'developer_board'},{id:'updates',title:'Updates',icon:'system_update'}];
 const original={render:P._renderActiveSection,set:P.setActiveSection,refresh:P.refreshDisplayControl,pointer:P._handleDocumentPointerDown,key:P._handleDocumentKeyDown};
 const aliases={gps:'connectivity',browserConnection:'connectivity',browserAccess:'connectivity',network:'deviceInfo',device:'deviceInfo',rearDisplay:'display'};
 const node=(tag,cls,text)=>{const n=D.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
 const mark='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M6 3h12a3 3 0 0 1 3 3v1H3V6a3 3 0 0 1 3-3z" fill="currentColor"/><path d="M8 12 6.5 9.5M16 12l1.5-2.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M5.5 18v-1a6.5 6.5 0 0 1 13 0v1zM9 14a.85.85 0 1 0 0 1.7.85.85 0 1 0 0-1.7zM15 14a.85.85 0 1 0 0 1.7.85.85 0 1 0 0-1.7z" fill="currentColor" fill-rule="evenodd"/></svg>';
 const speaker=playing=>'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4z'+(playing?'m12-1c3 2 3 6 0 8m3-11c5 4 5 10 0 14':'m13 0 5 6m0-6-5 6')+'" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
 class Overlay{
  constructor(controller){
   this.c=controller;this.epoch=0;this.animations=new Map();this.motion=global.matchMedia?.('(prefers-reduced-motion: reduce)');
   this.root=node('div','dm-settings-overlay');this.root.hidden=true;this.root.setAttribute('aria-label','Settings');
   this.list=node('aside','dm-menu-list');const heading=node('header','dm-menu-heading','Settings');heading.appendChild(this.button('Close Settings','×',()=>this.close()));
   this.list.append(heading,controller.sidebar);
   this.pane=node('section','dm-menu-detail');this.pane.hidden=true;
   const head=node('header','dm-detail-heading');this.back=this.button('Back to Settings','‹',()=>this.showRoot(true));this.back.classList.add('dm-back');this.title=node('h1','');head.append(this.back,this.title,this.button('Close Settings','×',()=>this.close()));
   this.pane.append(head);if(controller.banner)this.pane.append(controller.banner);this.pane.append(controller.content);this.root.append(this.list,this.pane);D.body.append(this.root);
   this.motion?.addEventListener?.('change',()=>{if(this.motion.matches)this.cancel();});
   D.addEventListener('keydown',event=>{if(event.key==='Escape'&&!this.root.hidden){if(controller._activeDropdown||(controller._dmChildren||[]).some(c=>c._activeDropdown))return;if(controller.activeSectionId)this.showRoot(true);else this.close();}},true);
  }
  button(label,text,click){const b=node('button','dm-menu-button',text);b.type='button';b.setAttribute('aria-label',label);b.title=label;b.onclick=click;return b;}
  cancel(){for(const a of this.animations.values())a.cancel();this.animations.clear();}
  animate(el,frames,duration){this.animations.get(el)?.cancel();if(this.motion?.matches||typeof el.animate!=='function')return Promise.resolve();const a=el.animate(frames,{duration,easing:'cubic-bezier(.22,.61,.36,1)'});this.animations.set(el,a);return a.finished.catch(()=>{}).then(()=>{if(this.animations.get(el)===a)this.animations.delete(el);});}
  open(){++this.epoch;this.cancel();this.root.hidden=false;this.root.inert=false;this.showRoot(false,true);D.getElementById('home-settings-button')?.setAttribute('aria-expanded','true');void this.animate(this.list,[{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'none'}],200);}
  close(){const epoch=++this.epoch;this.cancel();this.c._dmRetire();this.c.activeSectionId=null;const b=D.getElementById('home-settings-button');b?.setAttribute('aria-expanded','false');const finish=()=>{if(epoch!==this.epoch)return;this.root.hidden=true;this.root.inert=false;};if(this.root.hidden||this.motion?.matches||typeof this.root.animate!=='function'){finish();return;}this.root.inert=true;b?.focus();void this.animate(this.root,[{opacity:1,transform:'none'},{opacity:0,transform:'translateY(4px)'}],180).then(finish);}
  showRoot(focus=false,instant=false){
   const previous=this.c.activeSectionId,epoch=++this.epoch;this.cancel();this.c._dmRetire();this.c.activeSectionId=null;this.c._renderSidebar();
   const finish=()=>{if(epoch!==this.epoch)return;this.pane.hidden=true;this.pane.inert=false;this.root.classList.remove('dm-has-detail');if(!instant&&global.innerWidth<=760)void this.animate(this.list,[{opacity:0,transform:'translateX(-8px)'},{opacity:1,transform:'none'}],180);if(focus)this.c.sidebar.querySelector('[data-dm-section="'+previous+'"]')?.focus({preventScroll:true});};
   if(instant||this.pane.hidden||this.motion?.matches||typeof this.pane.animate!=='function'){finish();return;}
   this.pane.inert=true;void this.animate(this.pane,[{opacity:1,transform:'none'},{opacity:0,transform:'translateX(8px)'}],180).then(finish);
  }
  section(id){const wasOpen=!this.pane.hidden,old=this.list.getClientRects().length?this.list.getBoundingClientRect():null;++this.epoch;this.cancel();this.pane.hidden=false;this.pane.inert=false;this.root.classList.add('dm-has-detail');this.title.textContent=sections.find(s=>s.id===id)?.title||'Settings';
   if(!wasOpen){const next=this.list.getClientRects().length?this.list.getBoundingClientRect():null;if(old&&next&&old.left!==next.left)void this.animate(this.list,[{transform:'translateX('+(old.left-next.left)+'px)'},{transform:'none'}],260);void this.animate(this.pane,[{opacity:0,transform:'translateX('+(global.innerWidth<=760?12:-10)+'px)'},{opacity:1,transform:'none'}],260);}else void this.animate(this.c.content,[{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'none'}],160);
   if(global.innerWidth<=760&&D.activeElement?.closest('.dm-menu-list'))this.back.focus({preventScroll:true});
  }
 }
 P.initialize=async function(){this.sections=sections.map(s=>({...s}));this.activeSectionId=null;this._dmChildren=[];this._dmOverlay=new Overlay(this);this._renderSidebar();};
 const sidebar=P._renderSidebar;
 P._renderSidebar=function(){sidebar.call(this);if(this._dmChild)return;for(const [i,b]of [...this.sidebar.querySelectorAll('button')].entries()){b.dataset.dmSection=this.sections[i].id;b.setAttribute('aria-expanded',String(this.activeSectionId===this.sections[i].id));}};
 P._dmRetire=function(){++this._renderToken;this._setActiveDropdown(null);global.clearTimeout(this._updateTimer);global.clearTimeout(this._encodingTimer);if(this._audioFormatRefresh)global.removeEventListener('droiddeck-audio-format',this._audioFormatRefresh);this._dmRefreshEncoding=null;for(const child of this._dmChildren||[]){++child._renderToken;child.activeSectionId='retired';child._setActiveDropdown(null);global.clearTimeout(child._updateTimer);if(child._audioFormatRefresh)global.removeEventListener('droiddeck-audio-format',child._audioFormatRefresh);}this._dmChildren=[];};
 P.setActiveSection=async function(id){if(this._dmChild)return original.set.call(this,id);id=aliases[id]||id;if(!sections.some(s=>s.id===id))return;this.activeSectionId=id;this._renderSidebar();this._dmOverlay.section(id);await this._renderActiveSection();};
 P._dmChildFor=function(id,container){const child=Object.assign(Object.create(Object.getPrototypeOf(this)),this,{_dmChild:true,_dmParent:this,_dmReady:false,activeSectionId:id,content:container,sidebar:node('div',''),_renderToken:0,_activeDropdown:null,_updateTimer:null,_dmChildren:[],_audioFormatRefresh:null});this._dmChildren.push(child);return child;};
 P._dmGroup=function(title){const group=node('section','dm-settings-group');group.appendChild(node('h2','dm-group-title',title));this.content.append(group);return group;};
 P._renderActiveSection=async function(...args){
  if(this._dmChild){if(this._dmReady&&this.activeSectionId==='display'&&this._dmParent?.activeSectionId==='display'&&this._dmParent._dmChildren.includes(this))return this._dmParent._renderActiveSection();await original.render.apply(this,args);tidy(this);return;}
  this._dmRetire();const id=this.activeSectionId;this.content.replaceChildren();if(!id)return;
  if(id==='display'||id==='audio'){const container=node('div','');this.content.append(container);const child=this._dmChildFor(id,container);await child._renderActiveSection();if(this.activeSectionId!==id||!container.isConnected||this._dmChildren[0]!==child)return;child._dmReady=true;this._videoQualityState=child._videoQualityState;if(id==='display')await this._dmRenderEncoding();return;}
  const epoch=++this._renderToken;
  if(id==='connectivity'||id==='deviceInfo'){
   const groups=id==='connectivity'?[['Browser','browserConnection'],['GPS','gps']]:[['Device','device'],['Network','network']];
   await Promise.all(groups.map(async([title,legacy])=>{const group=this._dmGroup(title),container=node('div','');group.append(container);const child=this._dmChildFor(legacy,container);await child._renderActiveSection();}));
   if(epoch!==this._renderToken||id!==this.activeSectionId)return;this.refreshDisplayControl(this.displayController.remoteDisplayState);return;
  }
  if(id==='updates'){
   const updates=this._dmGroup('Updates'),container=node('div','');updates.append(container);const child=this._dmChildFor('updates',container);await child._renderActiveSection();
   if(epoch!==this._renderToken||id!==this.activeSectionId)return;
   for(const [title,selector]of [['About','#page-about .about-content'],['Donation','#page-donations .donation-content']]){const group=this._dmGroup(title),existing=D.querySelector(selector);if(existing){const copy=existing.cloneNode(true),image=copy.querySelector('img'),picture=image?.closest('a')||image;if(picture){const text=node('div','dm-illustrated-text');for(const child of [...copy.childNodes])if(child!==picture)text.append(child);copy.replaceChildren(picture,text);}group.append(copy);}else group.append(node('p','','Information unavailable.'));}
   const notes=this._dmGroup('Release Notes'),versions=T.releaseNotesData?.versions||[],installed=child._updateState?.installedVersion||T.constants.APP_VERSION,current=versions.find(v=>v.versionName.replace(/ Preview$/, '')===installed);
   if(current){notes.append(node('h3','',current.versionName));for(const item of current.changelogItems||[]){notes.append(node('h4','',item.title),node('p','dm-release-text',item.descriptionMarkdown||item.shortDescription||''));}}
   else notes.append(node('p','','Release notes for this version are not yet available.'));
   const link=node('a','dm-older-notes','Release Notes for Older Versions ↗');link.href='https://github.com/chilman408/DroidDeck/releases';link.target='_blank';link.rel='noopener noreferrer';notes.append(link);
  }
 };
 function tidy(c){
  const tiles=[...c.content.querySelectorAll('.settings-tile')],title=row=>row.querySelector('.settings-tile-title')?.textContent?.toLowerCase();
  if(c.activeSectionId==='audio')for(const row of tiles)if(title(row)==='browser audio')row.remove();
  if(c.activeSectionId==='display'){
   const role=c.content.querySelector('[data-rear-role]')?.closest('.settings-tile');if(role)c.content.prepend(role);
   for(const label of c.content.querySelectorAll('[data-rear-role] label')){const input=label.querySelector('input'),text=label.querySelector('span');if(text)text.textContent=input?.value==='primary'?'Master Display':'View-Only Display';}
   for(const row of tiles)if(title(row)==='secondary display'&&row.querySelector('[data-rear-shared]'))row.remove();
  }
  if(c.activeSectionId==='browserConnection'){
   for(const row of tiles)if(title(row)==='require login'){row.querySelector('.settings-tile-title').textContent='Browser Login';row.querySelector('input')?.setAttribute('aria-label','Browser Login');const group=row.closest('.settings-browser-group'),heading=group?.previousElementSibling;if(group){const first=c.content.firstElementChild;if(heading)c.content.insertBefore(heading,first);c.content.insertBefore(group,heading?.nextSibling||first);}}
  }
  if(c.activeSectionId==='gps'&&!c.content.querySelector('.dm-master-gps'))c.content.append(node('p','dm-master-gps','Location always follows the Master Display.'));
 }
 P.refreshDisplayControl=function(state,...args){if(this._dmChild)return original.refresh.call(this,state,...args);for(const child of this._dmChildren||[])child.refreshDisplayControl(state,...args);this._dmRefreshEncoding?.();};
 P._handleDocumentPointerDown=function(event){original.pointer.call(this,event);for(const child of this._dmChildren||[])original.pointer.call(child,event);};
 P._handleDocumentKeyDown=function(event){original.key.call(this,event);for(const child of this._dmChildren||[])original.key.call(child,event);};
 P._dmEncodingRequest=async function(payload){const session=this.apiClient.displaySession;if(!session)throw Error('Reconnect the Master Display.');const response=await global.fetch('/api/videoEncoding',{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:global.AbortSignal.timeout(10000),headers:{'X-Display-Session':session.token,...(payload?{'X-Display-CSRF':session.csrfToken,'Content-Type':'application/json'}:{})},...(payload?{body:JSON.stringify(payload)}:{})});if(!response.ok)throw Error('Encoding controls are unavailable.');return response.json();};
 P._dmRenderEncoding=async function(){
  const token=this._renderToken;let state;try{state=await this._dmEncodingRequest();}catch{state=null;}if(token!==this._renderToken||this.activeSectionId!=='display')return;
  const group=node('section','dm-encoding');group.append(node('h2','dm-group-title','GPU'));const status=node('p','dm-current');status.setAttribute('role','status');
  const paint=value=>{const q=this._videoQualityState,profile=q?.profile,display=this.displayController.remoteDisplayState,parts=[];
   if(display?.width&&display?.height)parts.push(display.width+' × '+display.height);
   const rate=n=>n<1000000?n/1000+' kbps':n/1000000+' Mbps';
   if(profile)parts.push(rate(profile.bitRate)+' · '+profile.maxFps+' fps');
   if(q?.policy.enabled)parts.push('Automatic: up to '+rate(q.policy.ceilingBitRate)+' · '+q.policy.ceilingMaxFps+' fps');
   parts.push(value?'Encoding: '+(value.effective==='gpu'?'GPU':value.effective==='cpu'?'CPU':value.effective==='starting'?'Starting':'Unavailable'):'Encoding information unavailable');status.textContent=parts.join('. ')+'.';};
  const control=this._createSelect(state?.gpuAvailable?[{value:'auto',label:'Automatic (GPU Preferred)'},{value:'cpu',label:'CPU Only'}]:[{value:'cpu',label:state?'CPU Only':'Unavailable'}],state?.gpuAvailable?state.preference:'cpu',async preference=>{
   const owner=this.displayController.remoteDisplayState;if(this._encodingPending||!state||!owner?.canControl||owner.transactionStatus!=='idle'||token!==this._renderToken)return;
   this._encodingPending=true;for(const b of control.querySelectorAll('button'))b.disabled=true;
   try{const next=await this._dmEncodingRequest({revision:state.revision,preference});if(token===this._renderToken){state=next;paint(next);}}
   catch(error){if(token===this._renderToken)status.textContent=error.message;}
   finally{this._encodingPending=false;if(token===this._renderToken)this._dmRefreshEncoding();}
  });
  control.dataset.dmEncoding='';
  this._dmRefreshEncoding=()=>{if(token!==this._renderToken||this.activeSectionId!=='display')return;const owner=this.displayController.remoteDisplayState;for(const b of control.querySelectorAll('button'))b.disabled=this._encodingPending||!state?.gpuAvailable||!owner?.canControl||owner.transactionStatus!=='idle'||this.displayController.controlRequestInFlight;};
  this._dmRefreshEncoding();
  const poll=async()=>{if(token!==this._renderToken||this.activeSectionId!=='display')return;try{const [next,quality]=await Promise.all([this._dmEncodingRequest(),this._automaticRequest().catch(()=>null)]);if(token!==this._renderToken)return;if(quality)this._videoQualityState=quality;state=next;paint(next);this._dmRefreshEncoding();}catch{}finally{if(token===this._renderToken&&this.activeSectionId==='display')this._encodingTimer=global.setTimeout(poll,2000);}};
  this._encodingTimer=global.setTimeout(poll,2000);
  this._appendTile({icon:'memory',title:'Video Encoding',trailing:control},group);
  const renderer=[...this.content.querySelectorAll('.settings-tile')].find(row=>row.querySelector('.settings-tile-title')?.textContent==='Renderer');if(renderer)renderer.after(group);else this.content.append(group);paint(state);const footer=node('footer','dm-display-status');for(const note of this.content.querySelectorAll('.dm-video-policy-status,.settings-error'))footer.append(note);footer.append(status);this.content.append(footer);
 };
 function attach(){
  const shell=T.ShellController?.prototype,home=T.HomeController?.prototype;if(!shell||!home)throw Error('Player controls unavailable');
  const setPage=shell.setPage;shell.setPage=function(id){if(['settings','about','donations','releaseNotes'].includes(id)){if(this.currentPage!=='home')setPage.call(this,'home');this.settingsController._dmOverlay.open();if(id!=='settings'&&id!=='about')void this.settingsController.setActiveSection('updates');return;}this.settingsController._dmOverlay?.close();return setPage.call(this,id);};
  const setIcon=home._setButtonIcon;home._setButtonIcon=function(button,name){if(button?.id==='home-audio-button'){button.innerHTML=speaker(name==='volume_up');return;}return setIcon.call(this,button,name);};
  const refresh=home._refreshAudioButton;home._refreshAudioButton=function(){refresh.call(this);const playing=this.audioController.getAudioState()==='playing';this.audioButton.title=playing?'Mute':'Unmute';this.audioButton.setAttribute('aria-label',this.audioButton.title);this.audioButton.setAttribute('aria-pressed',String(playing));};
  const settings=D.getElementById('home-settings-button');if(settings){settings.innerHTML=mark;settings.title='Settings';settings.setAttribute('aria-label','Settings');settings.setAttribute('aria-expanded','false');}
 }
 if(D.readyState==='loading'||!T.ShellController||!T.HomeController)D.addEventListener('DOMContentLoaded',attach,{once:true});else attach();
 const style=node('style','');style.id='droidmate-settings-overlay-style';style.textContent=`
 #home-controls{background:rgba(20,29,40,.68)}.home-control-button{color:rgba(255,255,255,.72)}.home-control-button svg{width:30px;height:30px;flex-shrink:0}.home-control-button:focus-visible{outline:2px solid #8bf4b9;outline-offset:-4px}
 .dm-settings-overlay{position:fixed;z-index:60;top:56px;right:12px;display:flex;align-items:flex-start;gap:10px;max-height:calc(100dvh - 70px);color:#f3f6f9;font-family:system-ui,sans-serif}.dm-settings-overlay[hidden],.dm-menu-detail[hidden]{display:none!important}
 .dm-settings-overlay{--accent:#76d7ab;--text-primary:#f3f6f9;--text-secondary:#acb9c7}.dm-menu-list,.dm-menu-detail{background:rgba(20,29,40,.89);border:1px solid #ffffff26;border-radius:18px;box-shadow:0 16px 50px #07131b4d}.dm-menu-list{width:226px;flex-shrink:0;padding:9px}.dm-menu-heading,.dm-detail-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:5px 8px;font-size:14px;font-weight:600}.dm-menu-button{width:36px;height:36px;min-width:36px;background:none;border:0;color:inherit;border-radius:9px;font-size:24px;cursor:pointer}.dm-menu-button:hover{background:#ffffff12}.dm-menu-button:focus-visible{outline:2px solid #8bf4b9;outline-offset:-2px}
 .dm-settings-overlay #settings-sidebar{display:block!important;grid-template-columns:none;border:0;background:none;padding:0;overflow:auto;border-radius:0;max-height:calc(100dvh - 132px)}.dm-settings-overlay .settings-menu-item{width:100%;min-height:55px;display:flex;align-items:center;gap:12px;border-radius:12px;padding:10px 11px;margin:3px 0;background:none;border:0;color:#d8e1ec;text-align:left}.dm-settings-overlay .settings-menu-item.active{background:#70e7a116;color:#a0f5c3}.dm-settings-overlay .settings-menu-label{font-size:15px}.dm-settings-overlay .settings-menu-icon{font-size:21px}.dm-menu-detail{width:440px;max-height:calc(100dvh - 70px);overflow:auto;scrollbar-width:thin}.dm-detail-heading{position:sticky;top:0;z-index:2;background:#182330f5;border-radius:18px 18px 0 0;padding:10px 14px;border-bottom:1px solid #ffffff16}.dm-detail-heading h1{font-size:22px;flex:1;margin:0}.dm-back{display:none}
 .dm-settings-overlay #settings-content{padding:8px 20px 20px;overflow:visible;font-size:14px}.dm-settings-overlay .settings-tile{padding:15px 0;gap:10px;min-height:0;border-bottom:1px solid #ffffff16}.dm-settings-overlay .settings-tile-title{font-size:14px;font-weight:500}.dm-settings-overlay .settings-trailing-text{font-size:14px;line-height:1.5;overflow-wrap:anywhere}.dm-settings-overlay .settings-tile-trailing{overflow-wrap:anywhere}.dm-settings-overlay .settings-tile-icon{display:none}.dm-settings-overlay .settings-tile-subtitle,.dm-settings-overlay .settings-description{font-size:12px;line-height:1.5}.dm-settings-overlay .settings-divider{display:none}.dm-settings-overlay .settings-tile-trailing{min-width:0;max-width:200px}.dm-settings-overlay .settings-dropdown-trigger{font-size:13px;min-height:40px;padding:8px 10px}.dm-settings-overlay .settings-dropdown-option{font-size:13px;min-height:40px}.dm-settings-overlay .settings-dropdown-menu{max-height:220px;overflow:auto}.dm-settings-overlay .settings-dropdown{max-width:195px}.dm-settings-overlay .rear-role-picker{font-size:12px;display:flex;flex-direction:column;gap:8px}.dm-settings-overlay .settings-volume-wrap{max-width:180px}.dm-settings-overlay input[type=range]{max-width:150px!important}.dm-settings-overlay .settings-volume-value{font-size:13px}.dm-settings-overlay .settings-description{padding:0;margin:9px 0;color:#acb9c7}
 .dm-group-title{font-size:12px;text-transform:uppercase;letter-spacing:1.3px;color:#8dabc0;margin:22px 0 8px}.dm-settings-group{margin:0 0 15px}.dm-settings-overlay .settings-browser-heading{font-size:14px;margin:12px 0}.dm-settings-overlay .settings-access-input{box-sizing:border-box;max-width:100%;font-size:14px}.dm-settings-overlay .settings-access-form{padding:0}.dm-current,.dm-master-gps{font-size:12px;line-height:1.5;color:#aec6d8}.dm-current{background:#90b7d50c;border-radius:10px;padding:12px}.dm-settings-overlay .about-content,.dm-settings-overlay .donation-content{display:grid;grid-template-columns:112px minmax(0,1fr);align-items:center;gap:16px;text-align:left;max-width:100%;padding:0}.dm-illustrated-text{min-width:0;overflow-wrap:anywhere}.dm-settings-overlay .droiddeck-name{font-size:16px;margin:0 0 3px}.dm-settings-overlay .about-version-value{margin:0}.dm-settings-overlay .about-message{margin-top:6px}.dm-settings-overlay .dm-video-policy-status:empty{display:none}.dm-settings-overlay .droiddeck-logo,.dm-settings-overlay .donation-qr{width:112px;height:112px;max-width:112px;object-fit:contain}.dm-settings-overlay .donation-text,.dm-settings-overlay .about-message,.dm-settings-overlay .about-version-value{font-size:13px;line-height:1.5}.dm-release-text{font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}.dm-older-notes{font-size:13px;color:#a0efbf}.dm-settings-overlay a:focus-visible{outline:2px solid #8bf4b9}
 .dm-settings-overlay #settings-content .settings-tile{grid-template-columns:minmax(0,1fr) minmax(0,195px)}.dm-settings-overlay #settings-content .settings-tile-leading{display:none}.dm-settings-overlay #settings-content .settings-tile-content{grid-column:1;grid-row:auto;min-width:0}.dm-settings-overlay #settings-content .settings-tile-trailing{grid-column:2;grid-row:auto}.dm-settings-overlay .settings-dropdown{min-width:0;width:100%}.dm-settings-overlay .settings-dropdown-value{font-size:13px}.dm-settings-overlay .rear-role-picker span{font-size:12px}.dm-settings-overlay .settings-switch input:checked + .settings-switch-slider{background:#368a64}
 @media(max-width:760px){.dm-settings-overlay{left:12px;right:12px}.dm-menu-list{margin-left:auto;width:244px}.dm-has-detail .dm-menu-list{display:none}.dm-menu-detail{margin-left:auto;width:min(440px,calc(100vw - 24px))}.dm-back{display:block}.dm-settings-overlay #settings-content{padding:5px 16px 16px}.dm-settings-overlay .settings-tile-trailing{max-width:178px}.dm-settings-overlay .settings-dropdown{max-width:174px}.dm-settings-overlay .settings-tile-title,.dm-settings-overlay .settings-trailing-text{font-size:13px}}
 @media(prefers-reduced-motion:reduce){.home-control-button{transition:none}}
 `;D.head.append(style);
})(window);
