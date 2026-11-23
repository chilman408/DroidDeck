(function (global) {
  "use strict";

  const TAHtml = global.TAHtml;
  const utils = TAHtml.utils;
  const constants = TAHtml.constants;

  const dynamicAspectRatios = [[4, 3], [1, 1], [16, 9], [9, 16], [3, 4]];

  function supportedDynamicSize(area, preset, committed) {
    let selected = null, bestCoverage = -1;
    for (const [a, b] of dynamicAspectRatios) {
      const fit = Math.min(area.width / a, area.height / b);
      const coverage = fit * fit * a * b;
      // Integer multiples retain the exact ratio and even H264 dimensions.
      const unitWidth = a * 2, unitHeight = b * 2;
      const hardCap = Math.floor(Math.min(1920 / unitWidth, 1920 / unitHeight,
        Math.sqrt(1600000 / (unitWidth * unitHeight))));
      let units = Math.floor(Math.min(hardCap, area.width / unitWidth, area.height / unitHeight,
        [832, 720, 640, 544, 480][preset] / Math.min(unitWidth, unitHeight)));
      // Preserve the pinned image's working landscape navigation layout.
      if (a > b && committed?.landscapeNavigationMinHeight === 676) {
        const floor = Math.ceil(676 / unitHeight);
        if (floor <= hardCap) units = Math.max(units, floor);
      }
      const width = unitWidth * units, height = unitHeight * units;
      if (width >= 96 && height >= 96 && coverage > bestCoverage) {
        selected = { width, height }; bestCoverage = coverage;
      }
    }
    return selected;
  }

  function calculateDynamicSize(area, preset, committed, dynamic) {
    if (!area || ![area.width, area.height].every(v => Number.isFinite(v) && v >= 96) ||
        !Number.isInteger(preset) || preset < 0 || preset > 4) return null;
    if (dynamic) return supportedDynamicSize(area, preset, committed);
    const source = dynamic ? area : committed;
    if (!source || ![source.width, source.height].every(v => Number.isFinite(v) && v > 0)) return null;
    let scale = Math.min(dynamic ? 1 : Infinity, area.width / source.width, area.height / source.height,
      1920 / source.width, 1920 / source.height, Math.sqrt(1600000 / (source.width * source.height)),
      [832, 720, 640, 544, 480][preset] / Math.min(source.width, source.height));
    // The pinned natural-landscape Android image clips its phone navigation bar.
    // Use its working tablet layout without changing density or viewport aspect.
    const minimum = committed?.landscapeNavigationMinHeight;
    const navigationFloor = source.width > source.height && minimum === 676 &&
      minimum / source.height <= Math.min(1920 / source.width, 1920 / source.height,
        Math.sqrt(1600000 / (source.width * source.height)));
    if (navigationFloor) scale = Math.max(scale, minimum / source.height);
    let width = Math.min(1920, navigationFloor ? Infinity : Math.floor(area.width / 2) * 2,
      Math.round(source.width * scale / 2) * 2);
    let height = Math.min(1920, navigationFloor ? Infinity : Math.floor(area.height / 2) * 2,
      Math.round(source.height * scale / 2) * 2);
    while (width * height > 1600000) { if (width >= height) width -= 2; else height -= 2; }
    return width >= 96 && height >= 96 ? { width, height } : null;
  }

  class DisplayController {
    constructor(options) {
      this.apiClient = options.apiClient;
      this.rendererManager = options.rendererManager;
      this.flavor = options.flavor;
      this.onResizeSessionRefresh = options.onResizeSessionRefresh || null;
      this.onResizeModeChange =
        typeof options.onResizeModeChange === "function" ? options.onResizeModeChange : null;

      this.dialog = options.dialog;
      this.dialogMainButton = options.dialogMainButton;
      this.dialogRearButton = options.dialogRearButton;

      this.remoteDisplayState = null;

      this.display = {
        viewSize: { width: 1, height: 1 },
        adjustedSize: { width: 1, height: 1 },
      };

      this.resize = {
        mode: "initial",
        token: 0,
        timerId: null,
        pendingViewSize: null,
        cooldownState: null,
      };

      this._dialogWired = false;
      this._viewportWatcherStarted = false;
      this.isPaused = false;
      this.controlRequestInFlight = false;
      this.onControlStateChange = null;

      this._notifyResizeMode(this.resize.mode);
    }

    async initialize() {
      this._wireDialogEvents();
      if (this.apiClient.openDisplaySession) await this.apiClient.openDisplaySession();

      let remoteDisplayState = this._normalizeRemoteDisplayState(
        await this.apiClient.fetchDisplayState(),
      );
      try {
        remoteDisplayState = await this._registerStoredRole(remoteDisplayState);
      } catch (error) {
        if (![409, 503].includes(error.status)) throw error;
        // A temporary authority handoff must not prevent view-only video startup.
        // Normal control polling retries registration; no unconfirmed input rights.
        remoteDisplayState = { ...remoteDisplayState, canControl: false, canResize: false };
      }
      this.remoteDisplayState = remoteDisplayState;

      this._setNormalState({
        viewSize: utils.getViewportSize(),
        adjustedSize: {
          width: remoteDisplayState.width,
          height: remoteDisplayState.height,
        },
        remoteDisplayState,
      });

      await this.rendererManager.ensureDisplaySocket({
        displayWebSocket: this._displaySocketUrl(),
        remoteDisplayState,
      });

      this._setupViewportWatcher();
      this.triggerResizeFromViewport();
      if (this.remoteDisplayState.displayControlSupported === 1) {
        this._startControlPolling();
        global.addEventListener("pagehide", () => {
          this.pageSuspended = true;
          this._clearResizeTimer();
          global.clearInterval(this.controlPollTimer); this.controlPollTimer = null;
          this.rendererManager.closeDisplaySocket();
          if (this.onControlStateChange) this.onControlStateChange({ canControl: false });
        });
        global.addEventListener("pageshow", event => {
          if (!event.persisted) return;
          this.pageSuspended = false;
          this._startControlPolling();
          void this.refreshControlState().then(() => this.triggerResizeFromViewport());
        });
      }
    }

    _startControlPolling() {
      if (this.controlPollTimer == null) {
        this.controlPollTimer = global.setInterval(() => { void this.refreshControlState(); }, 1000);
      }
    }

    _displaySocketUrl() {
      return this.apiClient.socketUrl ? this.apiClient.socketUrl(this.flavor.displayWebSocket) : this.flavor.displayWebSocket;
    }

    _availableArea() {
      if (document.hidden || this.isPaused || this.pageSuspended) return null;
      const active = document.activeElement;
      if (active && (active.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName))) return null;
      if (global.visualViewport && global.visualViewport.height < global.innerHeight * 0.8) return null;
      const rect = document.getElementById("home-stage")?.getBoundingClientRect();
      return rect ? { width: rect.width, height: rect.height } : utils.getViewportSize();
    }

    _adoptControlledState(state, viewSize = this.display.viewSize) {
      this.remoteDisplayState = this._normalizeRemoteDisplayState(state);
      this._setNormalState({ viewSize, adjustedSize: state, remoteDisplayState: this.remoteDisplayState });
      if (this.onControlStateChange) this.onControlStateChange(this.pageSuspended ?
        { ...this.remoteDisplayState, canControl: false } : this.remoteDisplayState);
    }

    async refreshControlState() {
      if (this.pageSuspended || this.controlPollInFlight || this.controlRequestInFlight || this.remoteDisplayState?.displayControlSupported !== 1) return;
      this.controlPollInFlight = true;
      const serial = this.controlOperationSerial || 0;
      try {
        const old = this.remoteDisplayState;
        let next = await this.apiClient.fetchDisplayState();
        if (this.pageSuspended || this.controlRequestInFlight || serial !== (this.controlOperationSerial || 0)) return;
        if (next.transactionStatus !== "idle") {
          if (this.onControlStateChange) this.onControlStateChange({ ...next, canControl: false });
          return;
        }
        next = await this._registerStoredRole(next);
        if (this.pageSuspended || this.controlRequestInFlight || serial !== (this.controlOperationSerial || 0)) return;
        if (next.generation !== old.generation || next.canControl !== old.canControl || next.canResize !== old.canResize ||
            next.displayRole !== old.displayRole || next.rearDisplayRevision !== old.rearDisplayRevision) {
          this._adoptControlledState(next); this.triggerResizeFromViewport();
        } else {
          this.remoteDisplayState = this._normalizeRemoteDisplayState(next);
          if (this.onControlStateChange) this.onControlStateChange(next);
        }
        await this.rendererManager.ensureDisplaySocket({ displayWebSocket: this._displaySocketUrl(), remoteDisplayState: next });
      } catch (error) {
        if (this.pageSuspended || this.controlRequestInFlight || serial !== (this.controlOperationSerial || 0)) return;
        if ([401, 403].includes(error.status)) {
          this.rendererManager.closeDisplaySocket();
          if (this.onControlStateChange) this.onControlStateChange({ canControl: false });
          this.apiClient.setDisplaySession?.(null);
          try {
            await this.apiClient.openDisplaySession();
            let renewed = await this.apiClient.fetchDisplayState();
            if (this.pageSuspended) return;
            renewed = await this._registerStoredRole(renewed);
            if (this.pageSuspended || this.controlRequestInFlight || serial !== (this.controlOperationSerial || 0)) return;
            this._adoptControlledState(renewed);
            await this.rendererManager.ensureDisplaySocket({ displayWebSocket: this._displaySocketUrl(), remoteDisplayState: renewed });
          } catch (_error) { /* Keep control disabled until authentication succeeds. */ }
        }
      } finally { this.controlPollInFlight = false; }
    }

    _scheduleControlledResize(viewSize) {
      this._clearResizeTimer();
      this.resize.pendingViewSize = viewSize;
      if (this.controlRequestInFlight) return;
      this.resize.timerId = global.setTimeout(() => { this.resize.timerId = null; void this._sendControlledResize(); }, 1000);
    }

    async _sendControlledResize() {
      if (!this._availableArea() || this.controlRequestInFlight) return;
      this.controlRequestInFlight = true;
      const viewSize = this.resize.pendingViewSize;
      this.resize.pendingViewSize = null;
      try {
        const state = await this.apiClient.fetchDisplayState();
        if (!this._availableArea() || (this.resize.pendingViewSize && !utils.sameSize(this.resize.pendingViewSize, viewSize))) return;
        if (!state.canControl || state.canResize === false || state.transactionStatus !== "idle") return;
        const size = calculateDynamicSize(viewSize, state.resolutionPreset, state, state.isResponsive === 1);
        if (!size) return;
        if (utils.sameSize(size, state)) { this._adoptControlledState(state, viewSize); return; }
        this._setResizeMode("resize_in_progress");
        if (this.onControlStateChange) this.onControlStateChange({ ...state, canControl: false });
        const committed = await this.apiClient.postDisplayState({ ...size, density: state.density,
          resolutionPreset: state.resolutionPreset, isResponsive: state.isResponsive, generation: state.generation });
        this._adoptControlledState(committed, viewSize);
      } catch (error) {
        TAHtml.log("Controlled resize failed: " + String(error));
        try { this._adoptControlledState(await this.apiClient.fetchDisplayState()); } catch (_error) {}
        if (error.status === 409) this.resize.pendingViewSize = this._availableArea();
      } finally {
        this.controlRequestInFlight = false; this._setResizeMode("normal");
        if (this.resize.pendingViewSize && !this.isPaused) this._scheduleControlledResize(this.resize.pendingViewSize);
      }
    }

    async updateControlledSettings(mutator) {
      if (this.controlRequestInFlight) throw new Error("Display resize in progress");
      this._clearResizeTimer(); this.controlRequestInFlight = true;
      try {
        const state = await this.apiClient.fetchDisplayState();
        if (!state.canControl || state.canResize === false || state.transactionStatus !== "idle") throw new Error("Display is view-only or busy");
        const draft = { ...state }; mutator(draft);
        const size = calculateDynamicSize(this.display.viewSize, draft.resolutionPreset, state,
          draft.isResponsive === 1);
        if (!size) throw new Error("Display area is too small");
        if (this.onControlStateChange) this.onControlStateChange({ ...state, canControl: false });
        this._adoptControlledState(await this.apiClient.postDisplayState({ ...size, density: state.density,
          resolutionPreset: draft.resolutionPreset, isResponsive: draft.isResponsive, generation: state.generation }));
      } catch (error) {
        try { this._adoptControlledState(await this.apiClient.fetchDisplayState()); } catch (_error) {}
        throw error;
      } finally { this.controlRequestInFlight = false; }
    }

    getTouchscreenSize() {
      return {
        width: this.display.adjustedSize.width,
        height: this.display.adjustedSize.height,
      };
    }

    async _registerStoredRole(state) {
      if (state.rearDisplayControlSupported !== 1) return state;
      if (state.displayRoleClaimSupported === 1) {
        if (['primary', 'rear'].includes(state.displayRole)) {
          const primary = state.displayRole === 'primary';
          if (this.controlledPrimaryPreference !== primary) {
            this.controlledPrimaryPreference = primary;
            try { this._storePrimaryDisplayPreference(primary); } catch (_error) { /* Accepted server role remains tab-local. */ }
          }
        }
        // The server assigns a role when the display connects; cached roles cannot preempt it.
        return state;
      }
      // Read browser preference once; another tab cannot silently change this role.
      if (this.controlledPrimaryPreference === undefined) {
        try { this.controlledPrimaryPreference = this._readPrimaryDisplayPreference(); }
        catch (_error) { this.controlledPrimaryPreference = null; }
      }
      if (this.controlledPrimaryPreference === null) {
        if (state.isRearDisplayEnabled === 1 && !this.isPaused) this._showDisplayTypeSelectionDialog();
        return state;
      }
      const role = this.controlledPrimaryPreference ? "primary" : "rear";
      if (state.displayRole === role) return state;
      if (this.onControlStateChange) this.onControlStateChange({ ...state, canControl: false });
      const registration = this.apiClient.postDisplayRole(role);
      this.roleRegistration = registration;
      try { return await registration; }
      finally { if (this.roleRegistration === registration) this.roleRegistration = null; }
    }

    async selectPrimaryDisplay(primary) {
      if (typeof primary !== "boolean" || this.controlRequestInFlight || this.pageSuspended) throw new Error("Display role is busy");
      this._clearResizeTimer(); this.controlRequestInFlight = true;
      this.controlOperationSerial = (this.controlOperationSerial || 0) + 1;
      try {
        if (this.roleRegistration) await this.roleRegistration;
        const state = await this.apiClient.fetchDisplayState();
        if (state.rearDisplayControlSupported !== 1 || state.transactionStatus !== "idle") throw new Error("Display role unavailable");
        if (this.onControlStateChange) this.onControlStateChange({ ...state, canControl: false });
        const role = primary ? 'primary' : 'rear';
        const preferred = state.isRearDisplayEnabled === 1 && state.isRearDisplayPrioritised === 1 ? 'rear' : 'primary';
        const claim = state.displayRoleClaimSupported === 1 && role === preferred ? { claim: true, generation: state.generation } : undefined;
        const next = await this.apiClient.postDisplayRole(role, claim);
        this.controlledPrimaryPreference = primary;
        try { this._storePrimaryDisplayPreference(primary); } catch (_error) { /* This tab still retains its accepted role. */ }
        this._adoptControlledState(next); this._hideDisplayTypeSelectionDialog();
      } finally {
        this.controlRequestInFlight = false;
        await this.refreshControlState(); this.triggerResizeFromViewport();
      }
    }

    async updateRearSettings(payload) {
      if (this.controlRequestInFlight || this.pageSuspended) throw new Error("Rear display is busy");
      this._clearResizeTimer(); this.controlRequestInFlight = true;
      this.controlOperationSerial = (this.controlOperationSerial || 0) + 1;
      try {
        if (this.roleRegistration) await this.roleRegistration;
        const state = await this.apiClient.fetchDisplayState();
        if (state.rearDisplayControlSupported !== 1 || !state.canControl || state.transactionStatus !== "idle") throw new Error("Rear settings are view-only or busy");
        if (this.onControlStateChange) this.onControlStateChange({ ...state, canControl: false });
        this._adoptControlledState(await this.apiClient.postRearDisplaySettings(payload));
      } finally {
        this.controlRequestInFlight = false;
        await this.refreshControlState(); this.triggerResizeFromViewport();
      }
    }

    setResizeSessionRefreshHandler(handler) {
      this.onResizeSessionRefresh = typeof handler === "function" ? handler : null;
    }

    setResizeModeChangeHandler(handler) {
      this.onResizeModeChange = typeof handler === "function" ? handler : null;
      this._notifyResizeMode(this.resize.mode);
    }

    triggerResizeFromViewport() {
      this.triggerResize(this.remoteDisplayState?.displayControlSupported === 1 ? this._availableArea() : utils.getViewportSize());
    }

    triggerResize(viewSize) {
      if (this.isPaused) {
        return;
      }
      if (this.remoteDisplayState?.displayControlSupported === 1) {
        if (viewSize && this._availableArea()) this._scheduleControlledResize(viewSize);
        return;
      }
      const normalizedViewSize = utils.normalizeSize(viewSize, {
        fallback: { width: 1, height: 1 },
      });
      void this._startResize(normalizedViewSize);
    }

    async _startResize(viewSize) {
      if (this.isPaused) {
        return;
      }
      if (!this.flavor) {
        return;
      }

      if (this.resize.mode === "resize_cooldown") {
        const scheduled = this.resize.cooldownState;
        if (scheduled && utils.sameSize(scheduled.viewSize, viewSize)) {
          return;
        }
        this._clearResizeTimer();
      }

      const token = ++this.resize.token;
      this.resize.pendingViewSize = viewSize;

      let remoteDisplayState;
      try {
        remoteDisplayState = this._normalizeRemoteDisplayState(
          await this.apiClient.fetchDisplayState(),
        );
      } catch (error) {
        TAHtml.log("Unable to fetch display state for resize: " + String(error));
        return;
      }

      if (token !== this.resize.token) {
        return;
      }

      this.remoteDisplayState = remoteDisplayState;

      if (remoteDisplayState.fixedDisplay === 1) {
        this._setNormalState({
          viewSize,
          adjustedSize: {
            width: remoteDisplayState.width,
            height: remoteDisplayState.height,
          },
          remoteDisplayState,
        });
        return;
      }

      const isRearDisplayEnabled = remoteDisplayState.isRearDisplayEnabled === 1;
      const isRearDisplayPrioritised = remoteDisplayState.isRearDisplayPrioritised === 1;
      const isPrimaryDisplay = isRearDisplayEnabled
        ? this._readPrimaryDisplayPreference()
        : true;

      if (isPrimaryDisplay === null && isRearDisplayEnabled) {
        this._setResizeMode("display_type_selection");
        this._showDisplayTypeSelectionDialog();
        return;
      }

      this._hideDisplayTypeSelectionDialog();

      let desiredSize;

      if (isPrimaryDisplay === true || (isPrimaryDisplay === false && isRearDisplayPrioritised)) {
        const sizeForCalculation = remoteDisplayState.isResponsive === 1
          ? viewSize
          : constants.DEFAULT_NON_RESPONSIVE_SIZE;

        desiredSize = this._calculateOptimalSize(sizeForCalculation, {
          resolutionPreset: remoteDisplayState.resolutionPreset,
          isH264: remoteDisplayState.isH264 === 1,
          isHeadless: (remoteDisplayState.isHeadless ?? 1) === 1,
        });
      } else {
        desiredSize = {
          width: remoteDisplayState.width,
          height: remoteDisplayState.height,
        };

        this._setNormalState({
          viewSize,
          adjustedSize: desiredSize,
          remoteDisplayState,
        });
        return;
      }

      this._setResizeMode("resize_cooldown");
      this.resize.cooldownState = {
        token,
        viewSize,
        adjustedSize: desiredSize,
        resolutionPreset: remoteDisplayState.resolutionPreset,
        renderer: remoteDisplayState.renderer,
        isResponsive: remoteDisplayState.isResponsive === 1,
        quality: remoteDisplayState.quality,
        refreshRate: remoteDisplayState.refreshRate,
        isRearDisplayEnabled,
        isRearDisplayPrioritised,
      };

      this.resize.timerId = global.setTimeout(() => {
        void this._sendResizeRequest(token);
      }, constants.RESIZE_COOLDOWN_MS);
    }

    async _sendResizeRequest(token) {
      const cooldown = this.resize.cooldownState;
      if (!cooldown || this.resize.mode !== "resize_cooldown" || cooldown.token !== token) {
        return;
      }

      this._setResizeMode("resize_in_progress");
      this.resize.timerId = null;

      const isH264 = cooldown.renderer !== 0;
      const density = this._densityForResolutionPreset(cooldown.resolutionPreset, isH264);

      const payload = {
        width: cooldown.adjustedSize.width,
        height: cooldown.adjustedSize.height,
        density,
        resolutionPreset: cooldown.resolutionPreset,
        renderer: cooldown.renderer,
        isResponsive: cooldown.isResponsive ? 1 : 0,
        isH264: isH264 ? 1 : 0,
        quality: cooldown.quality,
        refreshRate: cooldown.refreshRate,
        isRearDisplayEnabled: cooldown.isRearDisplayEnabled ? 1 : 0,
        isRearDisplayPrioritised: cooldown.isRearDisplayPrioritised ? 1 : 0,
      };

      try {
        await this.apiClient.postDisplayState(payload);
      } catch (error) {
        TAHtml.log("Resize request failed: " + String(error));
        this._setResizeMode("normal");
        this.resize.cooldownState = null;
        return;
      }

      if (token !== this.resize.token) {
        return;
      }

      this.remoteDisplayState = {
        ...this.remoteDisplayState,
        ...payload,
      };

      await utils.delay(constants.RESIZE_COOLDOWN_MS);

      if (token !== this.resize.token) {
        return;
      }

      if (!this.isPaused) {
        this.rendererManager.closeDisplaySocket();
        if (typeof this.onResizeSessionRefresh === "function") {
          try {
            this.onResizeSessionRefresh({
              width: cooldown.adjustedSize.width,
              height: cooldown.adjustedSize.height,
            });
          } catch (_error) {
            // no-op
          }
        }
      }

      this._setNormalState({
        viewSize: cooldown.viewSize,
        adjustedSize: cooldown.adjustedSize,
        remoteDisplayState: this.remoteDisplayState,
      });
    }

    _setNormalState(options) {
      const remoteDisplayState = options.remoteDisplayState || this.remoteDisplayState;
      if (!remoteDisplayState) {
        return;
      }

      this._clearResizeTimer();
      this._setResizeMode("normal");
      this.resize.cooldownState = null;

      this.display.viewSize = utils.normalizeSize(options.viewSize, {
        fallback: utils.getViewportSize(),
      });

      this.display.adjustedSize = utils.normalizeSize(options.adjustedSize, {
        fallback: {
          width: remoteDisplayState.width,
          height: remoteDisplayState.height,
        },
      });

      global.displayWidth = this.display.adjustedSize.width;
      global.displayHeight = this.display.adjustedSize.height;

      this.rendererManager.applyDisplaySize(
        this.display.adjustedSize.width,
        this.display.adjustedSize.height,
      );

      if (!this.isPaused && !this.pageSuspended) {
        void this.rendererManager.ensureDisplaySocket({
          displayWebSocket: this._displaySocketUrl(),
          remoteDisplayState,
        });
      }
    }

    setPaused(paused) {
      const nextPaused = Boolean(paused);
      if (nextPaused === this.isPaused) {
        return;
      }

      this.isPaused = nextPaused;
      if (this.onControlStateChange && this.remoteDisplayState?.displayControlSupported === 1) {
        this.onControlStateChange({ ...this.remoteDisplayState,
          canControl: Boolean(this.remoteDisplayState.canControl && !nextPaused &&
            !this.pageSuspended && !this.controlRequestInFlight &&
            this.remoteDisplayState.transactionStatus === "idle") });
      }

      if (nextPaused) {
        this._hideDisplayTypeSelectionDialog();
        this._clearResizeTimer();
        if (this.remoteDisplayState?.displayControlSupported !== 1) this.rendererManager.closeDisplaySocket();
      } else {
        if (this.remoteDisplayState) {
          void this.rendererManager.ensureDisplaySocket({
            displayWebSocket: this._displaySocketUrl(),
            remoteDisplayState: this.remoteDisplayState,
          });
        }
        this.triggerResizeFromViewport();
      }
    }

    _setupViewportWatcher() {
      if (this._viewportWatcherStarted) {
        return;
      }
      this._viewportWatcherStarted = true;

      let lastViewport = utils.getViewportSize();

      const onPotentialResize = () => {
        if (this.isPaused) {
          return;
        }

        const current = this.remoteDisplayState?.displayControlSupported === 1 ? this._availableArea() : utils.getViewportSize();
        if (!current) return;
        if (!utils.sameSize(current, lastViewport)) {
          lastViewport = current;

          if (this.display.adjustedSize.width > 0 && this.display.adjustedSize.height > 0) {
            this.rendererManager.applyDisplaySize(
              this.display.adjustedSize.width,
              this.display.adjustedSize.height,
            );
          }

          this.triggerResize(current);
        }
      };

      const rafCheck = () => {
        onPotentialResize();
        global.requestAnimationFrame(rafCheck);
      };

      global.addEventListener("resize", onPotentialResize, { passive: true });
      global.addEventListener("orientationchange", onPotentialResize, { passive: true });
      if (this.remoteDisplayState?.displayControlSupported === 1) {
        document.addEventListener?.("visibilitychange", () => this.triggerResizeFromViewport());
        document.addEventListener?.("focusout", () => this.triggerResizeFromViewport());
      }

      if (global.visualViewport) {
        global.visualViewport.addEventListener("resize", onPotentialResize, {
          passive: true,
        });
        global.visualViewport.addEventListener("scroll", onPotentialResize, {
          passive: true,
        });
      }

      global.requestAnimationFrame(rafCheck);
    }

    _wireDialogEvents() {
      if (this._dialogWired) {
        return;
      }
      this._dialogWired = true;

      if (this.dialogMainButton) {
        this.dialogMainButton.addEventListener("click", () => {
          this._onDisplayTypeSelectionFinished(true);
        });
      }

      if (this.dialogRearButton) {
        this.dialogRearButton.addEventListener("click", () => {
          this._onDisplayTypeSelectionFinished(false);
        });
      }
    }

    _onDisplayTypeSelectionFinished(isPrimaryDisplay) {
      if (this.remoteDisplayState?.rearDisplayControlSupported === 1) {
        if (this.controlRequestInFlight) return;
        void this.selectPrimaryDisplay(isPrimaryDisplay).catch(error => TAHtml.log("Display role selection failed: " + String(error)));
        return;
      }
      this._storePrimaryDisplayPreference(isPrimaryDisplay);
      this._hideDisplayTypeSelectionDialog();

      const pendingViewSize = this.resize.pendingViewSize || utils.getViewportSize();
      this.triggerResize(pendingViewSize);
    }

    _showDisplayTypeSelectionDialog() {
      if (this.dialog) {
        this.dialog.hidden = false;
      }
    }

    _hideDisplayTypeSelectionDialog() {
      if (this.dialog) {
        this.dialog.hidden = true;
      }
    }

    _readPrimaryDisplayPreference() {
      for (let index = 0; index < constants.DISPLAY_PREF_KEYS.length; index += 1) {
        const key = constants.DISPLAY_PREF_KEYS[index];
        const raw = global.localStorage.getItem(key);
        if (raw === null) {
          continue;
        }

        const parsed = utils.parseBoolean(raw);
        if (parsed !== null) {
          return parsed;
        }

        try {
          const parsedJson = JSON.parse(raw);
          const parsedFromJson = utils.parseBoolean(parsedJson);
          if (parsedFromJson !== null) {
            return parsedFromJson;
          }
        } catch (_error) {
          // no-op
        }
      }

      return null;
    }

    _storePrimaryDisplayPreference(value) {
      const encoded = value ? "true" : "false";
      for (let index = 0; index < constants.DISPLAY_PREF_KEYS.length; index += 1) {
        global.localStorage.setItem(constants.DISPLAY_PREF_KEYS[index], encoded);
      }
    }

    _normalizeRemoteDisplayState(raw) {
      return {
        ...raw,
        width: Math.max(1, utils.toInt(raw.width, 1024)),
        height: Math.max(1, utils.toInt(raw.height, 768)),
        density: utils.toInt(raw.density, 200),
        resolutionPreset: utils.toInt(raw.resolutionPreset, 0),
        renderer: utils.toInt(raw.renderer, 0),
        isHeadless:
          raw.isHeadless === undefined || raw.isHeadless === null
            ? 1
            : utils.toInt(raw.isHeadless, 1),
        isResponsive: utils.toInt(raw.isResponsive, 1),
        isH264: utils.toInt(raw.isH264, 0),
        refreshRate: utils.toInt(raw.refreshRate, 30),
        quality: utils.toInt(raw.quality, 90),
        isRearDisplayEnabled: utils.toInt(raw.isRearDisplayEnabled, 0),
        isRearDisplayPrioritised: utils.toInt(raw.isRearDisplayPrioritised, 0),
        fixedDisplay: utils.toInt(raw.fixedDisplay, 0),
      };
    }

    _calculateOptimalSize(viewSize, options) {
      const isHeadless = Boolean(options.isHeadless);
      if (!isHeadless) {
        return { width: 1024, height: 768 };
      }

      const resolutionPreset = utils.toInt(options.resolutionPreset, 0);
      const isH264 = Boolean(options.isH264);

      let adjustedResolutionPreset;
      if (isH264) {
        adjustedResolutionPreset = resolutionPreset === 0 ? 0 : 1;
      } else {
        adjustedResolutionPreset = resolutionPreset;
      }

      const maxWidth = 1920;
      const maxHeight = 1088;
      const minSide = 320;

      const safeView = utils.normalizeSize(viewSize, {
        fallback: { width: 1024, height: 768 },
      });
      const aspectRatio = safeView.width / safeView.height;

      let width = utils.clamp(safeView.width, minSide, maxWidth);
      let height = width / aspectRatio;

      if (height > maxHeight) {
        height = maxHeight;
        width = height * aspectRatio;
      }

      const maxShortest = this._maxHeightForResolutionPreset(adjustedResolutionPreset);
      const shortest = Math.min(width, height);
      if (shortest > maxShortest) {
        const scale = maxShortest / shortest;
        width *= scale;
        height *= scale;
      }

      width = utils.alignUp(width, 64);
      height = utils.alignUp(height, 32);

      if (height <= 480) {
        height = 512;
        width = utils.alignUp(height * aspectRatio, 64);
      }

      if (width < minSide) {
        width = utils.alignUp(minSide, 64);
      }
      if (height < minSide) {
        height = utils.alignUp(minSide, 32);
      }

      width = Math.min(width, maxWidth);
      height = Math.min(height, maxHeight);

      return {
        width: Math.round(width),
        height: Math.round(height),
      };
    }

    _maxHeightForResolutionPreset(preset) {
      switch (utils.toInt(preset, 0)) {
        case 0:
          return 832;
        case 1:
          return 720;
        case 2:
          return 640;
        case 3:
          return 544;
        case 4:
          return 480;
        default:
          return 832;
      }
    }

    _densityForResolutionPreset(preset, isH264) {
      const normalizedPreset = utils.toInt(preset, 0);

      if (isH264) {
        switch (normalizedPreset) {
          case 0:
            return 200;
          case 1:
            return 175;
          default:
            return 175;
        }
      }

      switch (normalizedPreset) {
        case 0:
          return 200;
        case 1:
          return 175;
        case 2:
          return 155;
        case 3:
          return 130;
        case 4:
          return 115;
        default:
          return 200;
      }
    }

    _clearResizeTimer() {
      if (this.resize.timerId !== null) {
        global.clearTimeout(this.resize.timerId);
        this.resize.timerId = null;
      }
    }

    _setResizeMode(mode) {
      if (this.resize.mode === mode) {
        return;
      }
      this.resize.mode = mode;
      this._notifyResizeMode(mode);
    }

    _notifyResizeMode(mode) {
      if (typeof this.onResizeModeChange === "function") {
        try {
          this.onResizeModeChange(String(mode || "initial"));
        } catch (_error) {
          // no-op
        }
      }
    }
  }

  TAHtml.DisplayController = DisplayController;
  TAHtml.calculateDynamicSize = calculateDynamicSize;
})(window);
