// SPDX-License-Identifier: GPL-3.0-only
// Optional shared encoder policy, controlled only by the current primary display.
(function (global) {
  'use strict';
  const prototype = global.TAHtml?.SettingsController?.prototype;
  if (!prototype) throw new Error('Settings controls unavailable');
  const render = prototype._renderDisplaySection, refresh = prototype.refreshDisplayControl;
  const allowed = controller => {
    const state = controller.displayController?.remoteDisplayState;
    return state?.canControl === true && state.transactionStatus === 'idle' &&
      !controller.videoSettingsPending && !controller._automaticPending && !controller.displayController.controlRequestInFlight;
  };
  prototype.refreshDisplayControl = function (...args) {
    const result = refresh.apply(this, args);
    if (this.activeSectionId === 'display') {
      const input = this.content.querySelector('[data-automatic-quality] input');
      if (input) input.disabled = !allowed(this);
    }
    return result;
  };
  prototype._automaticRequest = async function (payload) {
    const session = this.apiClient.displaySession;
    if (!session) throw new Error('Reconnect the primary display.');
    const response = await global.fetch('/api/videoAutomatic', { method: payload ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      signal: global.AbortSignal.timeout(5000), headers: { 'X-Display-Session': session.token,
        ...(payload ? { 'X-Display-CSRF': session.csrfToken, 'Content-Type': 'application/json' } : {}) },
      ...(payload ? { body: JSON.stringify(payload) } : {}) });
    if (!response.ok) throw new Error(response.status === 409 ? 'Video settings changed. Reopen Display settings.' : 'Automatic quality is unavailable.');
    return response.json();
  };
  prototype._renderDisplaySection = async function (token, ...args) {
    await render.call(this, token, ...args);
    if (token !== this._renderToken || this.activeSectionId !== 'display' || !this.content.querySelector('[data-video-bitrate]')) return;
    let state;
    try { state = await this._automaticRequest(); } catch { return; }
    if (token !== this._renderToken || this.activeSectionId !== 'display') return;
    const bitrateTile = this.content.querySelector('[data-video-bitrate]')?.closest('.settings-tile');
    if (!bitrateTile) return;
    const detail = global.document.createElement('p'); detail.className = 'settings-description'; detail.setAttribute('role', 'status');
    detail.textContent = state.policy.enabled ?
      'Adjusting up to ' + state.policy.ceilingBitRate / 1000000 + ' Mbps / ' + state.policy.ceilingMaxFps + ' fps. Current: ' + state.profile.bitRate / 1000000 + ' Mbps / ' + state.profile.maxFps + ' fps.' :
      'Adjusts to the primary display’s connection. Your selected bitrate and frame rate are the upper limits. Choosing either manually turns this off.';
    // Use the same circular on/off control as Browser audio, including keyboard behavior.
    let input;
    const control = this._createSwitch(state.policy.enabled, checked => change(checked), () => allowed(this));
    input = control.querySelector('input'); input.setAttribute('aria-label', 'Automatic quality'); input.disabled = !allowed(this);
    const tile = this._appendTile({ icon: 'network_check', title: 'Automatic quality', trailing: control });
    tile.dataset.automaticQuality = '1';
    this.content.insertBefore(tile, bitrateTile); this.content.insertBefore(detail, bitrateTile);
    const change = async checked => {

      if (!allowed(this) || token !== this._renderToken || this.activeSectionId !== 'display') { input.checked = state.policy.enabled; return; }
      this._automaticPending = true; input.disabled = true;
      try {
        await this._automaticRequest({ revision: state.policy.revision, profileRevision: state.profile.revision, enabled: checked });
        if (token === this._renderToken && this.activeSectionId === 'display') await this._renderActiveSection();
      } catch (error) { if (token === this._renderToken) { input.checked = state.policy.enabled; detail.textContent = error.message; } }
      finally { this._automaticPending = false; if (input.isConnected) input.disabled = !allowed(this); }
    };
  };
})(window);
