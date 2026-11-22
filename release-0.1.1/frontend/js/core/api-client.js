(function (global) {
  "use strict";

  const TAHtml = global.TAHtml;

  class ApiClient {
    constructor(apiBaseUrl) {
      this.apiBaseUrl = String(apiBaseUrl || "").replace(/\/$/, "");
      this.githubApiBaseUrl = "https://api.github.com";
      this.displaySession = null;
      this.displayState = null;
      this.browserAudioSupported = false;
      this.browserAudioGainSupported = false;
      this.browserGpsManaged = false;
      this.browserGpsSupported = false;
    }

    async fetchSystemConfiguration() {
      const configuration = await this._fetchJson(this.apiBaseUrl + "/configuration");
      this.browserAudioSupported = configuration.browserAudioSupported === 1;
      this.browserAudioGainSupported = this.browserAudioSupported && configuration.browserAudioGainSupported === 1;
      this.browserGpsManaged = configuration.browserGpsMock === 1;
      this.browserGpsSupported = configuration.browserGpsSupported === 1;
      return configuration;
    }

    fetchBrowserAccess() {
      return this._fetchJson(this.apiBaseUrl + "/browserAccess");
    }

    fetchBrowserConnection() {
      return this._fetchJson(this.apiBaseUrl + '/browserConnection', {
        headers: this.displaySession ? { 'X-Display-Session': this.displaySession.token } : {} });
    }

    saveBrowserConnection(payload) {
      if (!this.displaySession) throw new Error('Browser connection session required');
      return this._fetchJson(this.apiBaseUrl + '/browserConnection', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Display-Session': this.displaySession.token,
          'X-Display-CSRF': this.displaySession.csrfToken }, body: JSON.stringify(payload) });
    }

    async saveBrowserAccess(payload, csrfToken, authorization) {
      const response = await fetch(this.apiBaseUrl + "/browserAccess", {
        method: "POST", cache: "no-store", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken,
          ...(authorization ? { Authorization: authorization } : {}) },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        if (response.status === 401) throw new Error("Current password is incorrect");
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || "Could not save browser access settings");
      }
      return response.json();
    }

    async openDisplaySession() {
      const response = await fetch(this.apiBaseUrl + "/displaySession", { method: "POST", cache: "no-store",
        credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: "{}" });
      if ([404, 501].includes(response.status)) return null;
      if (!response.ok) throw Object.assign(new Error("Display session unavailable"), { status: response.status });
      this.setDisplaySession(await response.json());
      return this.displaySession;
    }

    setDisplaySession(session) {
      if (session && (!/^[a-f0-9]{64}$/.test(session.token) || !/^[a-f0-9]{64}$/.test(session.csrfToken))) {
        throw new Error("Invalid display session");
      }
      this.displaySession = session;
    }

    socketUrl(url) {
      if (!this.displaySession) return url;
      const parsed = new URL(url);
      parsed.searchParams.set("session", this.displaySession.token);
      return parsed.toString();
    }

    gpsSocketUrl(url) {
      if (!this.displaySession) throw new Error("GPS display session required");
      const parsed = new URL(this.socketUrl(url));
      parsed.searchParams.set("csrf", this.displaySession.csrfToken);
      return parsed.toString();
    }

    async fetchDisplayState() {
      this.displayState = await this._fetchJson(this.apiBaseUrl + "/displayState", {
        headers: this.displaySession ? { "X-Display-Session": this.displaySession.token } : {} });
      return this.displayState;
    }

    async postDisplayState(payload) {
      if (!this.displaySession) return this._postJson(this.apiBaseUrl + "/displayState", payload);
      this.displayState = await this._fetchJson(this.apiBaseUrl + "/displayState", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Display-Session": this.displaySession.token,
          "X-Display-CSRF": this.displaySession.csrfToken },
        body: JSON.stringify({ ...payload, generation: payload.generation ?? this.displayState?.generation }) });
      return this.displayState;
    }

    postVideoTargets(payload) {
      if (!this.displaySession) throw new Error("Display session required for video settings");
      return this._fetchJson(this.apiBaseUrl + "/videoState", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Display-Session": this.displaySession.token,
          "X-Display-CSRF": this.displaySession.csrfToken }, body: JSON.stringify(payload) });
    }

    async postRearDisplaySettings(payload) {
      return this._postDisplayAuthority("rearDisplayState", payload);
    }

    async postDisplayRole(role, claim) {
      return this._postDisplayAuthority("displayRole", { role, ...(claim || {}) });
    }

    async _postDisplayAuthority(route, payload) {
      if (!this.displaySession) throw new Error("Display session required");
      this.displayState = await this._fetchJson(this.apiBaseUrl + "/" + route, { method: "POST",
        headers: { "Content-Type": "application/json", "X-Display-Session": this.displaySession.token,
          "X-Display-CSRF": this.displaySession.csrfToken }, body: JSON.stringify(payload) });
      return this.displayState;
    }

    fetchDeviceInfo() {
      return this._fetchJson(this.apiBaseUrl + "/deviceInfo");
    }

    openUpdater() {
      return this._fetch(this.apiBaseUrl + "/openUpdater");
    }

    fetchHealthCheck() {
      return this._fetch(this.apiBaseUrl + "/health");
    }

    fetchLatestRelease() {
      // No VM project release channel is published yet. Pi releases are not VM updates.
      return Promise.resolve(null);
    }

    setSoftApBand(value) {
      return this._postPlain(this.apiBaseUrl + "/softApBand", value);
    }

    setSoftApChannel(value) {
      return this._postPlain(this.apiBaseUrl + "/softApChannel", value);
    }

    setSoftApChannelWidth(value) {
      return this._postPlain(this.apiBaseUrl + "/softApChannelWidth", value);
    }

    setSoftApState(value) {
      return this._postPlain(this.apiBaseUrl + "/softApState", value);
    }

    setOfflineModeState(value) {
      return this._postPlain(this.apiBaseUrl + "/offlineModeState", value);
    }

    setOfflineModeTelemetryState(value) {
      return this._postPlain(this.apiBaseUrl + "/offlineModeTelemetryState", value);
    }

    setOfflineModeTeslaFirmwareDownloads(value) {
      return this._postPlain(this.apiBaseUrl + "/offlineModeTeslaFirmwareDownloads", value);
    }

    setBrowserAudioState(value) {
      return this._postAudio("browserAudioState", value);
    }

    setBrowserAudioVolume(value) {
      return this._postAudio("browserAudioVolume", value);
    }

    async _postAudio(route, value) {
      if (!this.browserAudioSupported) return this._postPlain(this.apiBaseUrl + "/" + route, value);
      if (!this.displaySession) throw new Error("Display session required for sound settings");
      await this._fetch(this.apiBaseUrl + "/" + route, { method: "POST", body: String(value), headers: {
        "Content-Type": "text/plain", "X-Display-Session": this.displaySession.token,
        "X-Display-CSRF": this.displaySession.csrfToken } });
    }

    setGpsState(value) {
      if (this.browserGpsManaged) {
        if (!this.displaySession) throw new Error("GPS display session required");
        return this._fetch(this.apiBaseUrl + "/gpsState", { method: "POST", body: String(value), headers: {
          "Content-Type": "text/plain", "X-Display-Session": this.displaySession.token,
          "X-Display-CSRF": this.displaySession.csrfToken } });
      }
      return this._postPlain(this.apiBaseUrl + "/gpsState", value);
    }

    async _fetch(url, options) {
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        ...(options || {}),
      });
      if (!response.ok) {
        throw Object.assign(new Error("HTTP " + response.status + " for " + url), { status: response.status });
      }
      return response;
    }

    async _fetchJson(url, options) {
      const response = await this._fetch(url, options);
      return response.json();
    }

    async _postJson(url, payload) {
      await this._fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
    }

    async _postPlain(url, value) {
      await this._fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "text/plain",
        },
        body: String(value),
      });
    }
  }

  TAHtml.ApiClient = ApiClient;
})(window);
