// SPDX-License-Identifier: GPL-3.0-only
(function (global) {
  'use strict';
  const prototype = global.TAHtml?.SettingsController?.prototype;
  if (!prototype) throw new Error('Settings controls unavailable');
  const initialize = prototype.initialize, render = prototype._renderActiveSection, refresh = prototype.refreshDisplayControl;
  const phases = { queued: 'Preparing update…', downloading: 'Downloading update…', verifying: 'Verifying release signature…',
    installing: 'Installing update. The picture may reconnect…', 'rolling-back': 'Restoring the original version…',
    complete: 'Operation completed.', failed: 'Operation failed. Recovery records have been preserved.',
    interrupted: 'Operation was interrupted. Check status before trying another update.' };
  const busy = state => ['queued', 'downloading', 'verifying', 'installing', 'rolling-back'].includes(state?.job?.phase);
  const text = (tag, value) => { const node = global.document.createElement(tag); node.textContent = value; return node; };
  prototype.initialize = async function (...args) {
    if (!this.sections.some(section => section.id === 'updates')) this.sections.push({ id: 'updates', title: 'Updates', icon: 'system_update' });
    return initialize.apply(this, args);
  };
  prototype.refreshDisplayControl = function (state, ...args) {
    const result = refresh.call(this, state, ...args);
    if (this.activeSectionId === 'updates' && this._updateState && this._updatesPrimary !== (state?.canControl === true)) {
      this._paintUpdates(this._updateState);
    }
    return result;
  };
  prototype._updateRequest = async function (payload) {
    const session = this.apiClient.displaySession;
    if (!session) throw new Error('Reconnect the browser to manage updates.');
    const response = await global.fetch('/api/updates', { method: payload ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      signal: global.AbortSignal.timeout(40000), headers: { 'X-Display-Session': session.token,
        ...(payload ? { 'Content-Type': 'application/json', 'X-Display-CSRF': session.csrfToken } : {}) },
      ...(payload ? { body: JSON.stringify(payload) } : {}) });
    if (!response.ok) throw new Error(response.status === 403 ? 'Use the primary display to manage updates.' :
      response.status === 503 ? 'Updates are unavailable. The connection may be restarting; check again shortly.' : 'Updates are not enabled on this installation.');
    return response.json();
  };
  prototype._renderActiveSection = async function (...args) {
    global.clearTimeout(this._updateTimer);
    if (this.activeSectionId !== 'updates') return render.apply(this, args);
    const generation = ++this._renderToken;
    this._setActiveDropdown(null); this.content.replaceChildren(text('h2', 'DroidDeck updates'));
    this.content.dataset.updates = '';
    try {
      const state = await this._updateRequest();
      if (this.activeSectionId !== 'updates' || generation !== this._renderToken) return;
      this._updateState = state; this._paintUpdates(state);
    } catch (error) {
      if (generation !== this._renderToken) return;
      this.content.appendChild(text('p', error.message));
      const retry = text('button', 'Check status'); retry.type = 'button';
      retry.onclick = () => this._renderActiveSection(); this.content.appendChild(retry);
      if (this._updateState && busy(this._updateState)) this._updateTimer = global.setTimeout(() => this._renderActiveSection(), 3000);
    }
  };
  prototype._paintUpdates = function (state) {
    this.content.replaceChildren(text('h2', 'DroidDeck updates'), text('p', 'Installed version: ' + state.installedVersion));
    this.content.dataset.updates = '';
    this.content.appendChild(text('p', 'Pilot channel · Official signed releases. Android apps and data are preserved.'));
    const primary = this.displayController?.remoteDisplayState?.canControl === true;
    this._updatesPrimary = primary;
    const controls = [], status = text('p', this._updateChecked && !busy(state) ?
      (state.available ? 'Available version: ' + state.available.version : 'No newer pilot release is available.') : state.job ? phases[state.job.phase] || 'Check update status.' :
      state.available ? 'Available version: ' + state.available.version : 'Check for a newer pilot release.');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); this.content.appendChild(status);
    const button = (label, payload, confirmation) => {
      const node = text('button', label); node.type = 'button'; node.disabled = !primary || busy(state);
      node.style.cssText = 'margin:8px 12px 8px 0;padding:12px 18px;min-height:44px'; controls.push(node);
      node.onclick = async () => {
        if (confirmation && !global.confirm(confirmation)) return;
        controls.forEach(item => { item.disabled = true; });
        try {
          const request = typeof payload === 'function' ? payload() : payload;
          this._updateChecked = false;
          // A lost reply never creates another operation automatically. The
          // independent helper persists its accepted request before execution.
          this._updateState = await this._updateRequest(request);
          this._updateChecked = request.action === 'check';
          if (this.activeSectionId === 'updates') this._paintUpdates(this._updateState);
        } catch (error) { status.textContent = error.message; }
        finally { if (this.activeSectionId === 'updates') this._updateTimer = global.setTimeout(() => this._renderActiveSection(), 2000); }
      };
      this.content.appendChild(node);
    };
    button('Check for updates', { action: 'check' });
    const id = () => Array.from(global.crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
    if (state.available) button('Install ' + state.available.version, () => ({ action: 'install', token: state.available.token, requestId: id() }),
      'Install DroidDeck ' + state.available.version + '? Streaming will briefly disconnect. Android apps and data will be preserved.');
    if (state.rollback) button('Roll back update', () => ({ action: 'rollback', token: state.rollback, requestId: id() }),
      'Restore the original RC2 gateway? Android apps and data will be preserved. The original version may not have Updates in Settings.');
    if (!primary) this.content.appendChild(text('p', 'Use the primary display to check, install or roll back updates.'));
    this.content.appendChild(text('p', 'Streaming reconnects during installation. If the original version returns after rollback, reopen your usual DroidDeck address.'));
    if (busy(state)) this._updateTimer = global.setTimeout(() => this._renderActiveSection(), 2500);
  };
})(window);
