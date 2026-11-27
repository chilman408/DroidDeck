(function (global) {
  "use strict";

  const TAHtml = global.TAHtml;
  const utils = TAHtml.utils;

  const rendererKey = "TeslaAndroid_browserH264Renderer_v1";
  const recoveryKey = "TeslaAndroid_autoH264Fallback_v1";
  let rendererRecoveryPending = false;
  const rendererChoices = ["auto", "webcodecs", "legacy"];
  const hasWebCodecs = () => global.isSecureContext && typeof global.VideoDecoder === "function";
  TAHtml.browserRenderer = {
    choice() {
      const query = new URLSearchParams(global.location.search).get("video");
      let value = query;
      if (!rendererChoices.includes(value)) {
        try { value = global.localStorage.getItem(rendererKey); } catch { value = null; }
      }
      if (!rendererChoices.includes(value) || (value === "webcodecs" && !hasWebCodecs())) return "auto";
      return value;
    },
    effectiveChoice() {
      const value = this.choice();
      if (value === "auto") {
        // API presence is not playback proof; legacy is the accepted vehicle path.
        return "legacy";
      }
      return value;
    },
    recover() {
      if (rendererRecoveryPending) return true;
      if (this.choice() !== "auto" || this.effectiveChoice() === "legacy") return false;
      rendererRecoveryPending = true;
      // OffscreenCanvas cannot be transferred twice; one browser reload switches decoders.
      try {
        global.sessionStorage.setItem(recoveryKey, "legacy");
        if (global.sessionStorage.getItem(recoveryKey) !== "legacy") throw new Error("Storage unavailable");
        global.location.reload();
      } catch {
        // A URL override prevents a reload loop even when browser storage is blocked.
        const url = new URL(global.location.href);
        url.searchParams.set("video", "legacy");
        global.location.replace(url.href);
      }
      return true;
    },
    options() {
      return [{ value: "auto", label: "Automatic" },
        ...(hasWebCodecs() ? [{ value: "webcodecs", label: "h264 (WebCodecs)" }] : []),
        { value: "legacy", label: "h264 (legacy)" }];
    },
    select(value) {
      if (!rendererChoices.includes(value) || (value === "webcodecs" && !hasWebCodecs())) {
        throw new Error("Unsupported browser renderer");
      }
      // Decoder scripts own the OffscreenCanvas; reload only this browser to switch.
      global.localStorage.setItem(rendererKey, value);
      try { global.sessionStorage.removeItem(recoveryKey); } catch {}
      rendererRecoveryPending = false;
      const url = new URL(global.location.href);
      url.searchParams.delete("video");
      if (url.href !== global.location.href) global.history.replaceState(global.history.state, "", url.href);
      global.location.reload();
    },
  };

  class DisplayRendererManager {
    constructor(options) {
      this.displayRoot = options.displayRoot;
      this.onFrameReceived = typeof options.onFrameReceived === "function" ? options.onFrameReceived : null;
      this.onSocketOpen = typeof options.onSocketOpen === "function" ? options.onSocketOpen : null;
      this.onSocketClose = typeof options.onSocketClose === "function" ? options.onSocketClose : null;
      this.onSocketError = typeof options.onSocketError === "function" ? options.onSocketError : null;

      this.displaySocket = null;
      this.displaySocketConfig = {
        url: null,
        binaryType: null,
      };

      this.renderer = {
        id: null,
        scriptPath: null,
        loadPromise: null,
      };

      this.lastDisplaySize = {
        displayWidth: 1,
        displayHeight: 1,
        windowWidth: 1,
        windowHeight: 1,
      };
      global.requestDisplayStreamReconnect = () => this.displaySocket?.refresh();
    }

    applyDisplaySize(width, height) {
      const safeWidth = Math.max(1, Math.round(width));
      const safeHeight = Math.max(1, Math.round(height));

      const viewport = utils.getViewportSize();
      const scale = Math.min(viewport.width / safeWidth, viewport.height / safeHeight);

      const cssWidth = Math.max(1, Math.floor(safeWidth * scale));
      const cssHeight = Math.max(1, Math.floor(safeHeight * scale));

      this.displayRoot.style.width = String(cssWidth) + "px";
      this.displayRoot.style.height = String(cssHeight) + "px";
      this.displayRoot.style.aspectRatio = String(safeWidth) + " / " + String(safeHeight);

      this.lastDisplaySize = {
        displayWidth: safeWidth,
        displayHeight: safeHeight,
        windowWidth: cssWidth,
        windowHeight: cssHeight,
      };

      this.updateRendererRuntimeConfig(this.lastDisplaySize);
      return this.lastDisplaySize;
    }

    async ensureDisplaySocket(options) {
      const remoteDisplayState = options.remoteDisplayState;
      let socketUrl = options.displayWebSocket;
      const rendererInfo = this._rendererInfoForId(remoteDisplayState.renderer, remoteDisplayState);
      if (rendererInfo.packetFormat === "scrcpy") {
        const videoUrl = new URL(socketUrl);
        videoUrl.searchParams.set("format", "h264");
        socketUrl = videoUrl.href;
      }

      if (this.renderer.scriptPath && this.renderer.scriptPath !== rendererInfo.scriptPath) {
        global.location.reload();
        return;
      }

      await this._ensureRendererScript(rendererInfo);

      if (
        this.displaySocket &&
        this.displaySocketConfig.url === socketUrl &&
        this.displaySocketConfig.binaryType === rendererInfo.binaryType
      ) {
        this.updateRendererRuntimeConfig();
        return;
      }

      this._closeDisplaySocket();

      this.displaySocketConfig = {
        url: socketUrl,
        binaryType: rendererInfo.binaryType,
      };

      const liveWindow = rendererInfo.packetFormat === "scrcpy";
      let packetReceipt = null;
      const socket = new ReconnectingWebSocket(socketUrl, liveWindow ? "droiddeck-live-v1" : null, {
        binaryType: rendererInfo.binaryType,
        reconnectInterval: 150,
        maxReconnectInterval: 1000,
        reconnectDecay: 1.2,
        timeoutInterval: 900,
      });

      socket.onopen = () => {
        packetReceipt = null;
        TAHtml.log("Display websocket connected");
        if (typeof this.onSocketOpen === "function") {
          try {
            this.onSocketOpen();
          } catch (_error) {
            // no-op
          }
        }
        this.updateRendererRuntimeConfig();
      };
      socket.onclose = () => {
        packetReceipt = null;
        TAHtml.log("Display websocket closed");
        if (typeof this.onSocketClose === "function") {
          try {
            this.onSocketClose();
          } catch (_error) {
            // no-op
          }
        }
      };
      socket.onerror = (error) => {
        TAHtml.log("Display websocket error: " + String(error));
        if (typeof this.onSocketError === "function") {
          try {
            this.onSocketError(error);
          } catch (_error) {
            // no-op
          }
        }
      };
      socket.onmessage = (event) => {
        if (this.displaySocket !== socket) return;
        if (liveWindow && typeof event.data === "string") {
          let metadata;
          try { metadata = JSON.parse(event.data); } catch {}
          packetReceipt = null;
          if (metadata?.type === "scrcpy-flow-frame") {
            if (Number.isSafeInteger(metadata.id) && metadata.id > 0) packetReceipt = metadata.id;
            return;
          }
        }
        if (typeof this.onFrameReceived === "function") {
          try {
            this.onFrameReceived();
          } catch (_error) {
            // no-op
          }
        }
        if (typeof global.drawDisplayFrame === "function") {
          global.drawDisplayFrame(event.data);
        }
        if (liveWindow && packetReceipt !== null && event.data instanceof ArrayBuffer && socket.readyState === 1) {
          const id = packetReceipt; packetReceipt = null;
          socket.send(JSON.stringify({ type: "scrcpy-flow-ack", id }));
        }
      };

      this.displaySocket = socket;
    }

    updateRendererRuntimeConfig(config) {
      if (typeof global.updateDisplayRendererConfig !== "function") {
        return;
      }

      if (config) {
        global.updateDisplayRendererConfig(config);
        return;
      }

      const width = Math.max(1, Math.round(this.displayRoot.clientWidth || this.lastDisplaySize.windowWidth));
      const height = Math.max(1, Math.round(this.displayRoot.clientHeight || this.lastDisplaySize.windowHeight));

      global.updateDisplayRendererConfig({
        displayWidth: this.lastDisplaySize.displayWidth,
        displayHeight: this.lastDisplaySize.displayHeight,
        windowWidth: width,
        windowHeight: height,
      });
    }

    closeDisplaySocket() {
      this._closeDisplaySocket();
    }

    _closeDisplaySocket() {
      if (this.displaySocket) {
        this.displaySocket.close();
        this.displaySocket = null;
      }
    }

    _rendererInfoForId(rendererId, state = {}) {
      const canvas = document.getElementById("canvas");
      const videoChoice = new URLSearchParams(global.location.search).get("video");
      if (state.streamFormats && state.streamFormats.includes("h264") && videoChoice !== "png") {
        if (!canvas || typeof canvas.transferControlToOffscreen !== "function") {
          throw new Error("H264 canvas rendering is unavailable in this browser");
        }
        const useWebCodecs = TAHtml.browserRenderer.effectiveChoice() !== "legacy" && hasWebCodecs();
        return { id: useWebCodecs ? 1 : 2, packetFormat: "scrcpy",
          scriptPath: useWebCodecs ? "./js/display/renderers/h264-webcodecs/h264WebCodecs.js" :
            "./js/display/renderers/h264-broadway/h264Brodway.js",
          binaryType: "arraybuffer" };
      }
      switch (utils.toInt(rendererId, 0)) {
        case 2:
          return {
            id: 2,
            scriptPath: "./js/display/renderers/h264-broadway/h264Brodway.js",
            binaryType: "arraybuffer",
          };
        case 1:
          return {
            id: 1,
            scriptPath: "./js/display/renderers/h264-webcodecs/h264WebCodecs.js",
            binaryType: "arraybuffer",
          };
        case 0:
        default:
          return {
            id: 0,
            scriptPath: "./js/display/renderers/mjpeg/mjpeg.js",
            binaryType: "blob",
          };
      }
    }

    _ensureRendererScript(rendererInfo) {
      if (
        this.renderer.scriptPath === rendererInfo.scriptPath &&
        typeof global.drawDisplayFrame === "function"
      ) {
        return Promise.resolve();
      }

      if (this.renderer.loadPromise) {
        return this.renderer.loadPromise;
      }

      global.displayWidth = this.lastDisplaySize.displayWidth;
      global.displayHeight = this.lastDisplaySize.displayHeight;
      global.TAHtmlVideoPacketFormat = rendererInfo.packetFormat || "annexb";

      this.renderer.id = rendererInfo.id;
      this.renderer.scriptPath = rendererInfo.scriptPath;

      this.renderer.loadPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = rendererInfo.scriptPath;
        script.async = true;
        script.onload = () => {
          this.renderer.loadPromise = null;
          resolve();
        };
        script.onerror = (error) => {
          this.renderer.loadPromise = null;
          reject(error);
        };
        document.head.appendChild(script);
      });

      return this.renderer.loadPromise;
    }
  }

  TAHtml.DisplayRendererManager = DisplayRendererManager;
})(window);
