(function (global) {
  "use strict";
  const TAHtml = global.TAHtml;
  class GpsController {
    constructor(options) {
      this.socketUrl = options.socketUrl;
      this.isEnabled = Boolean(options.isEnabled);
      this.apiClient = options.apiClient;
      this.canControl = options.canControl || (() => true);
      this.gpsSocket = null;
      this.gpsRunning = false;
      this.gpsUpdateIntervalId = null;
      this.retryTimer = null;
      this.pendingPosition = null;
      this.epoch = 0;
      this.permissionBlocked = false;
    }
    get managed() { return this.apiClient?.browserGpsManaged === true; }
    _available() {
      if (!this.isEnabled || this.permissionBlocked) return false;
      if (!this.managed) return true;
      try { return this.apiClient.browserGpsSupported && Boolean(this.apiClient.displaySession) && this.canControl() === true; }
      catch (_error) { return false; }
    }
    initialize() { this.refreshAuthority(); }
    setEnabled(value) {
      const enabled = Boolean(value);
      if (enabled && !this.isEnabled) this.permissionBlocked = false;
      this.isEnabled = enabled;
      this.refreshAuthority();
    }
    refreshAuthority() {
      if (!this._available()) { this._stop(); return; }
      let url;
      try { url = this.managed ? this.apiClient.gpsSocketUrl(this.socketUrl) : this.socketUrl; }
      catch (_error) { this._stop(); return; }
      if (this.gpsSocket && this.connectedUrl !== url) this._stop();
      if (!this.gpsSocket && this.retryTimer === null) this._connectSocket(url);
    }
    _connectSocket(url) {
      if (this.gpsSocket || !this._available()) return;
      const socket = this.managed ? new global.WebSocket(url) : new ReconnectingWebSocket(url);
      this.gpsSocket = socket;
      this.connectedUrl = url;
      socket.onopen = () => {
        if (this.gpsSocket !== socket || !this._available()) return;
        if (!this.managed) this._startLocationUpdates();
      };
      socket.onmessage = event => {
        if (this.gpsSocket !== socket || !this._available() || !this.managed) return;
        try {
          const status = JSON.parse(event.data);
          if (status.version !== 1 || status.ready !== true || status.mock !== true || Object.keys(status).length !== 3) throw new Error();
          this._startLocationUpdates();
        } catch (_error) { socket.close(); }
      };
      socket.onerror = () => TAHtml.log("GPS connection unavailable.");
      socket.onclose = () => {
        if (this.gpsSocket !== socket) return;
        if (!this.managed) {
          this.gpsRunning = false; this.epoch++;
          if (this.gpsUpdateIntervalId !== null) global.clearInterval(this.gpsUpdateIntervalId);
          this.gpsUpdateIntervalId = null; return;
        }
        this._stop();
        if (this._available()) this.retryTimer = global.setTimeout(() => {
          this.retryTimer = null; this.refreshAuthority();
        }, 2000);
      };
    }
    _startLocationUpdates() {
      if (!this._available() || this.gpsUpdateIntervalId !== null) return;
      this.gpsRunning = true;
      this.gpsUpdateIntervalId = global.setInterval(() => this._requestPosition(), 1000);
      this._requestPosition();
    }
    _requestPosition() {
      if (!this._available() || !this.gpsRunning || !this.gpsSocket || this.pendingPosition) return;
      const request = { epoch: this.epoch, socket: this.gpsSocket };
      this.pendingPosition = request;
      const complete = (position, error) => {
        if (this.pendingPosition === request) this.pendingPosition = null;
        if (request.epoch !== this.epoch || request.socket !== this.gpsSocket || !this._available()) return;
        if (error) {
          TAHtml.log(error.code === 1 ? "Location access denied." : "GPS fix unavailable.");
          if (error.code === 1) { this.permissionBlocked = true; this._stop(); }
          return;
        }
        this._updateLocation(position);
      };
      try {
        navigator.geolocation.getCurrentPosition(position => complete(position), error => complete(null, error),
          { enableHighAccuracy: true, maximumAge: 0, timeout: 10000 });
      } catch (_error) { complete(null, { code: 2 }); }
    }
    _updateLocation(position) {
      if (!this._available() || !this.gpsRunning || this.gpsSocket?.readyState !== 1 || !position?.coords) return;
      try { this.gpsSocket.send(this._toLocationData(position)); }
      catch (_error) { TAHtml.log("GPS fix unavailable."); }
    }
    _stop() {
      this.epoch++; this.gpsRunning = false;
      if (this.retryTimer !== null) global.clearTimeout(this.retryTimer);
      this.retryTimer = null;
      if (this.gpsUpdateIntervalId !== null) global.clearInterval(this.gpsUpdateIntervalId);
      this.gpsUpdateIntervalId = null;
      const socket = this.gpsSocket; this.gpsSocket = null;
      if (socket && socket.readyState !== 3) { try { socket.close(); } catch (_error) { /* Already closed. */ } }
      // getCurrentPosition cannot be cancelled; keep its slot until its callback settles.
    }
    _toLocationData(position) {
      const c = position.coords;
      const data = { latitude: c.latitude, longitude: c.longitude, vertical_accuracy: c.accuracy,
        timestamp: position.timestamp, speed: c.speed ?? null, bearing: c.heading ?? null };
      if (![data.latitude, data.longitude, data.vertical_accuracy, data.timestamp].every(Number.isFinite) ||
          Math.abs(data.latitude) > 90 || Math.abs(data.longitude) > 180 || data.vertical_accuracy <= 0 ||
          !Number.isSafeInteger(data.timestamp) || data.timestamp < Date.now() - 30000 || data.timestamp > Date.now() + 5000 ||
          (data.speed !== null && (!Number.isFinite(data.speed) || data.speed < 0 || data.speed > 150)) ||
          (data.bearing !== null && (!Number.isFinite(data.bearing) || data.bearing < 0 || data.bearing >= 360))) throw new Error("Invalid GPS fix");
      return JSON.stringify(this.managed ? data : Object.fromEntries(Object.entries(data).map(([key, value]) => [key, String(value)])));
    }
  }
  TAHtml.GpsController = GpsController;
})(window);
