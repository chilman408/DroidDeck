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
      tile.querySelector('.settings-tile-title')?.textContent === 'Browser audio');
    if (!browserTile) return;
    const initial = audio.snapshot();
    const select = this._createSelect([
      { value: 'aac', label: 'AAC-LC 128 kbps (default)' }, { value: 'flac', label: 'FLAC (lossless)' },
    ], initial.requested, value => {
      if (token !== this._renderToken || this.activeSectionId !== 'audio' || !select.isConnected) return;
      audio.select(value);
    });
    select.dataset.localAudioFormat = '1';
    select.querySelector('.settings-dropdown-trigger').setAttribute('aria-label', 'Audio format');
    // The choice is browser-local, including on a secondary display.
    for (const input of select.querySelectorAll('button')) input.dataset.localAudioDelay = 'true';
    const row = this._appendTile({ icon: 'graphic_eq', title: 'Audio format', trailing: select }); row.dataset.audioFormat = '1';
    const status = global.document.createElement('p'); status.className = 'settings-description'; status.setAttribute('role', 'status');
    const next = browserTile.nextSibling; this.content.insertBefore(row, next); this.content.insertBefore(status, next);
    const refresh = () => {
      if (!row.isConnected || token !== this._renderToken || this.activeSectionId !== 'audio') {
        global.removeEventListener('droiddeck-audio-format', refresh); return;
      }
      const state = audio.snapshot();
      status.textContent = state.fallbackReason || (state.effective === 'aac' ?
        'AAC-LC, 128 kbps stereo. ' + (state.playing ? 'Playing.' : 'Waiting for playback.') : 'Lossless FLAC. Variable bitrate.');
    };
    global.addEventListener('droiddeck-audio-format', refresh); refresh();
  };
})(window);
