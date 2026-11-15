// SPDX-License-Identifier: GPL-3.0-only
// Browser-local audio trial: AAC is default; an explicit FLAC URL keeps a reversible browser-local choice.
(function (global) {
  'use strict';
  const prototype = global.TAHtml?.SettingsController?.prototype;
  if (!prototype || typeof prototype._renderAudioSection !== 'function') throw new Error('Audio controls unavailable');
  const render = prototype._renderAudioSection;
  prototype._renderAudioSection = async function (token, ...args) {
    await render.call(this, token, ...args);
    const audio = global.DroidDeckAudio;
    if (!audio || token !== this._renderToken || this.activeSectionId !== 'audio') return;
    const browserTile = [...this.content.querySelectorAll('.settings-tile')].find(tile =>
      tile.querySelector('.settings-tile-title')?.textContent?.toLowerCase() === 'browser audio');
    if (!browserTile) return;
    // Keep the existing stored0..150 gain and slider behavior; normalize only its label.
    const slider=this.content.querySelector?.('.settings-volume-slider'),label=this.content.querySelector?.('.settings-volume-value');
    if(slider && label){const update=()=>{const percent=Math.round(Number(slider.value)/1.5);label.textContent=percent+' %';slider.setAttribute('aria-valuetext',percent+'%');};slider.addEventListener('input',update);update();}
    for(const note of this.content.querySelectorAll('.settings-description'))if(/^(If you plan to use browser audio|In case you encounter a situation)/.test(note.textContent || ''))note.remove();
    const initial = audio.snapshot();
    const select = this._createSelect([
      { value: 'aac64', label: 'AAC-LC 64 kbps' }, { value: 'aac', label: 'AAC-LC 128 kbps (default)' },
      { value: 'aac256', label: 'AAC-LC 256 kbps' }, { value: 'flac', label: 'FLAC (lossless)' },
    ], initial.requested, value => {
      if (token !== this._renderToken || this.activeSectionId !== 'audio' || !select.isConnected) return;
      audio.select(value);
    });
    select.dataset.localAudioFormat = '1';
    select.querySelector('.settings-dropdown-trigger').setAttribute('aria-label', 'Audio Format');
    // The choice is browser-local, including on a secondary display.
    for (const input of select.querySelectorAll('button')) input.dataset.localAudioDelay = 'true';
    const row = this._appendTile({ icon: 'graphic_eq', title: 'Audio format', trailing: select }); row.dataset.audioFormat = '1';
    const status = global.document.createElement('p'); status.className = 'settings-description'; status.setAttribute('role', 'status');
    const next = browserTile.nextSibling; this.content.insertBefore(row, next);
    if(typeof audio.setAutomatic==='function'){
      const control=this._createSwitch(initial.automaticQuality===true,enabled=>{
        if(token!==this._renderToken || this.activeSectionId!=='audio')return;
        audio.setAutomatic(Boolean(enabled));
      });
      for(const input of control.querySelectorAll('input,button'))input.dataset.localAudioDelay='true';
      control.querySelector('input').setAttribute('aria-label','Automatic Audio Quality');
      const automatic=this._appendTile({icon:'network_check',title:'Automatic quality',trailing:control});automatic.dataset.automaticAudioQuality='1';
      this.content.insertBefore(automatic,row);
    }
    this.content.insertBefore(status,next);
    const refresh = () => {
      if (!row.isConnected || token !== this._renderToken || this.activeSectionId !== 'audio') {
        global.removeEventListener('droiddeck-audio-format', refresh); return;
      }
      const state = audio.snapshot();
      status.textContent = state.fallbackReason || (state.effective === 'aac' ?
        (state.automaticQuality ? 'Automatic: ' : '') + 'AAC-LC '+ (state.targetBitRate || 128000)/1000 + ' kbps stereo. ' + (state.automaticQuality ? 'Up to '+state.ceilingBitRate/1000+' kbps. ' : '') + (state.playing ? 'Playing.' : 'Waiting for playback.') : 'Lossless FLAC. Variable bitrate.');
    };
    if (this._audioFormatRefresh) global.removeEventListener('droiddeck-audio-format', this._audioFormatRefresh);
    this._audioFormatRefresh = refresh;
    global.addEventListener('droiddeck-audio-format', refresh); refresh();
  };
})(window);
