// SPDX-License-Identifier: GPL-3.0-only
// The existing frontend supplies a browser-local selector; show both decoders.
(function (global) {
  'use strict';
  const prototype = global.TAHtml?.SettingsController?.prototype;
  if (!prototype || typeof prototype._appendTile !== 'function') throw new Error('Settings controls unavailable');
  const append = prototype._appendTile;
  prototype._appendTile = function (tile, ...args) {
    if (tile?.title === 'Renderer' &&
        !global.TAHtml?.browserRenderer?.options().some(option => option.value === 'webcodecs')) return;
    return append.call(this, tile && typeof tile.title === 'string' ? {...tile,title:tile.title.replace(/\b[a-z]/g,letter=>letter.toUpperCase())} : tile, ...args);
  };
  const createSelect = prototype._createSelect;
  prototype._createSelect = function(options, value, onChange) {
    const numeric = options?.map(option => Number(option.value));
    const resolution = numeric?.length === 5 && numeric.every((v,i)=>v===i);
    if (this.activeSectionId === 'display' && resolution) options=[[2,1080],[1,900],[0,720],[3,600],[4,480]].map(([value,p])=>({value,label:p+'p'+(p===720?' (default)':'')}));
    const bitrate = numeric?.length >= 5 && numeric.includes(1000000) && numeric.includes(12000000);
    const fps = numeric?.length >= 3 && numeric.includes(30) && numeric.includes(60) && !bitrate;
    if (this.activeSectionId === 'display' && (bitrate || fps)) {
      const field = bitrate ? 'bitRate' : 'maxFps', state = this._videoQualityState;
      options = (bitrate ? [720000,1000000,2000000,3000000,4000000,6000000,8000000,10000000,12000000] : [15,30,45,60]).map(rate =>
        ({value:rate,label:bitrate ? (rate < 1000000 ? rate / 1000 + ' kbps' : rate / 1000000 + ' Mbps') : rate + ' fps'}));
      if (state?.policy.enabled) value = bitrate ? state.policy.ceilingBitRate : state.policy.ceilingMaxFps;
      const token = this._renderToken;
      onChange = async selected => {
        const owner = this.displayController.remoteDisplayState;
        if (token !== this._renderToken || this.activeSectionId !== 'display' || this.videoSettingsPending || this._automaticPending ||
            !owner?.canControl || owner.transactionStatus !== 'idle' || this.displayController.controlRequestInFlight) return;
        this.videoSettingsPending = true; this.displaySettingsError = null; this.refreshDisplayControl(owner);
        try {
          const current = await this._automaticRequest();
          if (token !== this._renderToken || this.activeSectionId !== 'display') return;
          await this.apiClient.postVideoTargets({revision:current.profile.revision,...(current.policy.enabled?{policyRevision:current.policy.revision}:{}),
            bitRate:current.policy.enabled ? current.policy.ceilingBitRate : current.profile.bitRate,
            maxFps:current.policy.enabled ? current.policy.ceilingMaxFps : current.profile.maxFps,[field]:Number(selected)});
        } catch (error) { this.displaySettingsError = error.message || 'Video settings could not be saved'; }
        finally { this.videoSettingsPending = false; if (token === this._renderToken && this.activeSectionId === 'display') await this._renderActiveSection(); }
      };
    }
    return createSelect.call(this, options, value, onChange);
  };
  const displayRender = prototype._renderDisplaySection, refresh = prototype.refreshDisplayControl;
  const aspectAllowed = c => { const s=c.displayController?.remoteDisplayState;return s?.canControl === true && s.canResize !== false &&
    s.isResponsive === 0 && s.transactionStatus === 'idle' && !c.displayController.controlRequestInFlight && !c.videoSettingsPending && !c.rearSettingsPending; };
  const aspectVisualState = controller => {
    const aspect=controller.content.querySelector('[data-manual-aspect]');
    if(!aspect)return;
    const disabled=!aspectAllowed(controller);
    aspect.setAttribute('aria-disabled',String(disabled));
    const tile=aspect.closest('.settings-tile');
    if(tile){tile.style.opacity=disabled?'0.45':'';tile.setAttribute('aria-disabled',String(disabled));}
    for(const input of aspect.querySelectorAll('button'))input.disabled=disabled;
    if(disabled && controller._activeDropdown===aspect)controller._setActiveDropdown(null);
  };
  prototype.refreshDisplayControl = function(...args) {
    const result=refresh.apply(this,args);
    aspectVisualState(this);
    return result;
  };
  prototype._renderDisplaySection = async function(token,...args) {
    await displayRender.call(this,token,...args);
    if(token!==this._renderToken || this.activeSectionId!=='display')return;
    const state=this.displayController.remoteDisplayState;
    if(state?.displayControlSupported!==1)return;
    const tiles=[...this.content.querySelectorAll('.settings-tile')];
    const findTitle=title=>tiles.find(tile=>tile.querySelector('.settings-tile-title')?.textContent.toLowerCase()===title);
    const renderer=findTitle('renderer'),frameRate=findTitle('target frame rate');
    if(renderer && frameRate){
      const divider=renderer.previousElementSibling?.matches('hr.settings-divider')?renderer.previousElementSibling:null;
      divider?.remove();this.content.insertBefore(renderer,frameRate.nextSibling);if(divider)this.content.insertBefore(divider,renderer);
    }
    const dynamic=findTitle('dynamic aspect ratio');
    if(!dynamic)return;

    const ratios=[['4:3',4,3],['16:9',16,9],['1:1',1,1],['9:16',9,16],['3:4',3,4]];
    const ratio=state.width/state.height,chosen=ratios.find(([,w,h])=>Math.abs(w/h-ratio)<.01)?.[0] || 'current';
    const options=ratios.map(([value])=>({value,label:value}));if(chosen==='current')options.unshift({value:'current',label:'Current'});
    const control=this._createSelect(options,chosen,async value=>{
      const selected=ratios.find(([name])=>name===value);
      if(!selected || !aspectAllowed(this) || token!==this._renderToken)return;
      const controller=this.displayController;controller.controlRequestInFlight=true;controller._clearResizeTimer?.();this.refreshDisplayControl(controller.remoteDisplayState);
      try {
        const current=await this.apiClient.fetchDisplayState();
        if(token!==this._renderToken || this.activeSectionId!=='display' || !current.canControl || current.canResize===false || current.isResponsive!==0 || current.transactionStatus!=='idle')return;
        const area=controller.display?.viewSize || {width:current.width,height:current.height};
        const size=global.TAHtml.calculateDynamicSize(area,current.resolutionPreset,{...current,width:selected[1]*100,height:selected[2]*100},false);
        if(!size)throw Error('Display area is too small');
        const next=await this.apiClient.postDisplayState({...size,density:current.density,resolutionPreset:current.resolutionPreset,isResponsive:0,generation:current.generation});
        controller._adoptControlledState(next);
      } catch(error) {this.displaySettingsError=error.message || 'Aspect ratio could not be saved';}
      finally {controller.controlRequestInFlight=false;if(token===this._renderToken && this.activeSectionId==='display')await this._renderActiveSection();}
    });
    control.dataset.manualAspect='1';control.dataset.displaySize='1';control.querySelector('.settings-dropdown-trigger').setAttribute('aria-label','Aspect Ratio');
    const row=this._appendTile({icon:'aspect_ratio',title:'Aspect ratio',trailing:control});
    this.content.insertBefore(row,dynamic.nextSibling);
    aspectVisualState(this);
  };
})(window);
