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
    const row = global.document.createElement('div'); row.dataset.automaticQuality = '1'; row.className = 'settings-description';
    const label = global.document.createElement('label'), input = global.document.createElement('input');
    input.type = 'checkbox'; input.checked = state.policy.enabled; input.disabled = !allowed(this);
    label.append(input, global.document.createTextNode(' Automatic quality')); row.appendChild(label);
    const detail = global.document.createElement('p'); detail.setAttribute('role', 'status');
    detail.textContent = state.policy.enabled ?
      'Adjusting up to ' + state.policy.ceilingBitRate / 1000000 + ' Mbps / ' + state.policy.ceilingMaxFps + ' fps. Current: ' + state.profile.bitRate / 1000000 + ' Mbps / ' + state.profile.maxFps + ' fps.' :
      'Adjusts to the primary display’s connection. Your selected bitrate and frame rate are the upper limits. Choosing either manually turns this off.';
    row.appendChild(detail); this.content.appendChild(row);
    input.onchange = async () => {
      if (!allowed(this) || token !== this._renderToken || this.activeSectionId !== 'display') { input.checked = state.policy.enabled; return; }
      this._automaticPending = true; input.disabled = true;
      try {
        await this._automaticRequest({ revision: state.policy.revision, profileRevision: state.profile.revision, enabled: input.checked });
        if (token === this._renderToken && this.activeSectionId === 'display') await this._renderActiveSection();
      } catch (error) { if (token === this._renderToken) { input.checked = state.policy.enabled; detail.textContent = error.message; } }
      finally { this._automaticPending = false; if (input.isConnected) input.disabled = !allowed(this); }
    };
  };
})(window);
