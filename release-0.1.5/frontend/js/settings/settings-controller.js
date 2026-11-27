(function (global) {
  "use strict";

  const TAHtml = global.TAHtml;
  const utils = TAHtml.utils;
  const constants = TAHtml.constants;

  class SettingsController {
    constructor(options) {
      this.apiClient = options.apiClient;
      this.displayController = options.displayController;
      this.gpsController = options.gpsController;
      this.audioController = options.audioController;
      this.flavor = options.flavor;
      this.onSystemConfigurationChanged = options.onSystemConfigurationChanged;

      this.sidebar = options.sidebar;
      this.content = options.content;
      this.banner = options.banner;
      this.bannerText = options.bannerText;
      this.bannerAction = options.bannerAction;

      this.sections = [
        { id: "display", title: "Display", icon: "monitor" },
        { id: "audio", title: "Audio", icon: "speaker" },
        { id: "gps", title: "GPS", icon: "gps_fixed" },
        { id: "browserConnection", title: "Browser", icon: "public" },
        { id: "network", title: "Network", icon: "wifi" },
        { id: "device", title: "Device", icon: "developer_board" },
      ];

      this.activeSectionId = "display";
      this._renderToken = 0;
      this._activeDropdown = null;

      this.systemConfigCache = null;
      this.networkPendingConfig = null;
      this.audioSettingsPending = false;
      this.audioSettingsError = null;
      this._bindBannerEvents();
      this._bindGlobalEvents();
    }

    async initialize() {
      this._renderSidebar();
      await this.setActiveSection(this.activeSectionId);
    }

    async setActiveSection(sectionId) {
      this.activeSectionId = sectionId === "rearDisplay" ? "display" : sectionId === "browserAccess" ? "browserConnection" : sectionId;
      this._renderSidebar();
      await this._renderActiveSection();
      this._refreshNetworkBanner();
    }

    async _renderActiveSection() {
      const token = ++this._renderToken;
      this._setActiveDropdown(null);
      this.content.innerHTML = "";

      if (this.content.dataset) delete this.content.dataset.vmDeviceInfo;
      if (this.content.dataset) delete this.content.dataset.vmNetworkInfo;
      if (this.content.dataset) delete this.content.dataset.vmDisplayControls;
      if (this.content.dataset) delete this.content.dataset.browserConnection;

      if (this.activeSectionId === "display") {
        await this._renderDisplaySection(token);
        return;
      }
      if (this.activeSectionId === "network") {
        await this._renderNetworkSection(token);
        return;
      }
      if (this.activeSectionId === "audio") {
        await this._renderAudioSection(token);
        return;
      }
      if (this.activeSectionId === "gps") {
        await this._renderGpsSection(token);
        return;
      }
      if (this.activeSectionId === "device") {
        await this._renderDeviceSection(token);
      }
      if (this.activeSectionId === "browserAccess") {
        await this._renderBrowserAccessSection(token);
      }
      if (this.activeSectionId === 'browserConnection') await this._renderBrowserSection(token);
    }

    async _renderBrowserSection(token) {
      const group = (title) => {
        this.content.appendChild(utils.createElement('h2', 'settings-browser-heading', title));
        const pane = utils.createElement('section', 'settings-browser-group', null);
        this.content.appendChild(pane);
        return pane;
      };
      const connection = group('Connection');
      const access = group('Browser access');
      await Promise.all([this._renderBrowserConnectionSection(token, connection),
        this._renderBrowserAccessSection(token, access)]);
    }

    _connectionCanEdit() {
      const state = this.displayController.remoteDisplayState;
      return this.activeSectionId === 'browserConnection' && !this.connectionSettingsPending &&
        state?.canControl === true && state.transactionStatus === 'idle' && !this.displayController.controlRequestInFlight;
    }

    _refreshConnectionAuthority() {
      if (this.activeSectionId !== 'browserConnection') return;
      const pane = this.content.querySelector('[data-browser-connection]');
      for (const input of pane?.querySelectorAll('input, button') || []) input.disabled = !this._connectionCanEdit();
    }

    async _renderBrowserConnectionSection(token, container = this.content) {
      container.dataset.browserConnection = '1';
      this._appendLoadingState(container); let state;
      try { state = await this.apiClient.fetchBrowserConnection(); }
      catch (_) {
        if (token === this._renderToken && this.activeSectionId === 'browserConnection') this._renderErrorState('Browser connection unavailable', container);
        return;
      }
      if (token !== this._renderToken || this.activeSectionId !== 'browserConnection') return;
      container.innerHTML = '';
      this._appendTile({ icon: 'public', title: 'Configured address', trailing: this._createTrailingText(state.origin), dense: false }, container);
      this._appendTile({ icon: 'verified_user', title: 'TLS certificate', trailing: this._createTrailingText('Managed by reverse proxy'), dense: false }, container);
      if (state.previousOrigin) this._appendTile({ icon: 'history', title: 'Previous address', trailing: this._createTrailingText(state.previousOrigin), dense: false }, container);
      const form = utils.createElement('form', 'settings-access-form', null);
      const label = utils.createElement('label', 'settings-access-label', 'Hostname');
      const hostname = utils.createElement('input', 'settings-access-input', null);
      hostname.type = 'text'; hostname.value = state.hostname; hostname.maxLength = 256; hostname.required = true;
      hostname.autocomplete = 'off'; hostname.spellcheck = false; hostname.id = 'connection-hostname'; label.htmlFor = hostname.id;
      label.appendChild(hostname); form.appendChild(label);
      const actions = utils.createElement('div', 'settings-connection-actions', null);
      const apply = utils.createElement('button', 'settings-access-save', 'Apply'); apply.type = 'submit';
      const defaults = utils.createElement('button', 'settings-access-save', 'Use default'); defaults.type = 'button';
      defaults.addEventListener('click', () => {
        if (token !== this._renderToken || !this._connectionCanEdit()) return;
        hostname.value = new URL(state.defaultOrigin).hostname;
      });
      actions.appendChild(apply); actions.appendChild(defaults); form.appendChild(actions);
      const status = utils.createElement('div', 'settings-access-status', null); status.setAttribute('role', 'status'); form.appendChild(status);
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (token !== this._renderToken || !this._connectionCanEdit() || !form.checkValidity()) return;
        this.connectionSettingsPending = true; this._refreshConnectionAuthority(); status.textContent = 'Saving';
        try {
          await this.apiClient.saveBrowserConnection({ revision: state.revision, hostname: hostname.value });
          if (token === this._renderToken && this.activeSectionId === 'browserConnection') await this._renderActiveSection();
        } catch (error) {
          if (token === this._renderToken && this.activeSectionId === 'browserConnection') status.textContent = error.status === 409 ? 'Connection changed; reopen settings' : 'Connection could not be saved';
        } finally { this.connectionSettingsPending = false; this._refreshConnectionAuthority(); }
      });
      container.appendChild(form); this._refreshConnectionAuthority();
    }

    async _renderBrowserAccessSection(token, container = this.content) {
      this._appendLoadingState(container);
      let state;
      try { state = await this.apiClient.fetchBrowserAccess(); }
      catch (_error) {
        if (token === this._renderToken && ["browserAccess", "browserConnection"].includes(this.activeSectionId)) this._renderErrorState("Browser access settings unavailable", container);
        return;
      }
      if (token !== this._renderToken || !["browserAccess", "browserConnection"].includes(this.activeSectionId)) return;
      container.innerHTML = "";
      const form = utils.createElement("form", "settings-access-form", null);
      form.id = "browser-access-form";
      const newFields = utils.createElement("fieldset", "settings-access-fields", null);
      const currentFields = utils.createElement("fieldset", "settings-access-fields", null);
      const field = (parent, id, title, type, autocomplete) => {
        const label = utils.createElement("label", "settings-access-label", title);
        label.htmlFor = id;
        const input = utils.createElement("input", "settings-access-input", null);
        input.id = id; input.type = type; input.autocomplete = autocomplete;
        input.maxLength = type === "password" ? 256 : 64;
        label.appendChild(input); parent.appendChild(label);
        return input;
      };
      const username = field(newFields, "access-username", "Username", "text", "username");
      username.value = state.username;
      username.pattern = "[A-Za-z0-9_.@-]{1,64}";
      const password = field(newFields, "access-password", state.enabled ? "New password" : "Password", "password", "new-password");
      password.minLength = 12;
      const confirm = field(newFields, "access-confirm-password", "Confirm password", "password", "new-password");
      let currentPassword;
      if (state.enabled) {
        currentPassword = field(currentFields, "access-current-password", "Current password", "password", "current-password");
        currentPassword.required = true;
      }
      let required = state.enabled;
      const synchronize = () => {
        newFields.hidden = !required;
        newFields.disabled = !required;
        username.required = required;
        password.required = required && !state.enabled;
        confirm.required = required && !state.enabled;
      };
      const loginSwitch = this._createSwitch(required, async (checked) => {
        required = checked; synchronize();
      });
      loginSwitch.querySelector("input").setAttribute("aria-label", "Require login");
      this._appendTile({ icon: "lock", title: "Require login", trailing: loginSwitch }, container);
      const status = utils.createElement("div", "settings-access-status", "");
      status.id = "access-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      const save = utils.createElement("button", "settings-access-save", null);
      save.type = "submit";
      save.appendChild(utils.createElement("span", "material-symbols-rounded", "save"));
      save.appendChild(utils.createElement("span", "", "Save"));
      form.appendChild(currentFields);
      form.appendChild(newFields);
      form.appendChild(save);
      form.appendChild(status);
      container.appendChild(form);
      synchronize();
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (save.disabled || token !== this._renderToken || !["browserAccess", "browserConnection"].includes(this.activeSectionId)) return;
        const payload = { enabled: required };
        if (required && (!state.enabled || password.value || username.value !== state.username)) {
          if (password.value.length < 12) { status.textContent = "Password must be at least 12 characters"; return; }
          if (password.value !== confirm.value) { status.textContent = "Passwords do not match"; return; }
          payload.username = username.value;
          payload.password = password.value;
        }
        save.disabled = true;
        status.textContent = "Saving...";
        try {
          let authorization;
          if (state.enabled) {
            const bytes = new TextEncoder().encode(state.username + ":" + currentPassword.value);
            authorization = "Basic " + btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));
          }
          await this.apiClient.saveBrowserAccess(payload, state.csrfToken, authorization);
          password.value = ""; confirm.value = "";
          if (currentPassword) currentPassword.value = "";
          if (token !== this._renderToken || !["browserAccess", "browserConnection"].includes(this.activeSectionId)) return;
          if (required) global.location.reload();
          else {
            await this._renderActiveSection();
            const saved = this.content.querySelector("#access-status");
            if (saved) saved.textContent = "Saved";
          }
        } catch (error) { status.textContent = error.message || "Could not save browser access settings"; }
        finally { save.disabled = false; }
      });
    }

    async _renderDisplaySection(token) {
      this._appendLoadingState();

      let config;
      try {
        config = this._normalizeDisplayState(await this.apiClient.fetchDisplayState());
      } catch (_error) {
        this._renderErrorState("Service error");
        return;
      }

      if (token !== this._renderToken || this.activeSectionId !== "display") {
        return;
      }

      this.content.innerHTML = "";

      if (config.displayControlSupported === 1) {
        if (this.content.dataset) this.content.dataset.vmDisplayControls = "";
        this._displayControlGeneration = config.generation;
        const resolution = this._createSelect(constants.DISPLAY_RESOLUTION_PRESETS, config.resolutionPreset,
          async value => { await this._updateDisplayConfig(draft => { draft.resolutionPreset = utils.toInt(value, config.resolutionPreset); }); });
        const responsive = this._createSwitch(config.isResponsive === 1,
          async checked => { await this._updateDisplayConfig(draft => { draft.isResponsive = checked ? 1 : 0; }); });
        resolution.dataset.displaySize = "1";
        responsive.dataset.displaySize = "1";
        this._appendTile({ icon: "display_settings", title: "Resolution", trailing: resolution });
        this._appendDivider();
        this._appendTile({ icon: "photo_size_select_large", title: "Dynamic aspect ratio", trailing: responsive });
        const enabled = config.canControl && config.transactionStatus === "idle";
        for (const input of this.content.querySelectorAll("button, input")) {
          if (!input.closest?.("[data-browser-renderer]")) input.disabled = !enabled ||
            (config.canResize === false && Boolean(input.closest?.("[data-display-size]")));
        }
        this._appendDivider();
        if (config.streamFormats?.includes("h264")) {
          const renderer = this._createSelect(TAHtml.browserRenderer.options(), TAHtml.browserRenderer.choice(), async value => {
            try { TAHtml.browserRenderer.select(value); }
            catch { this.displaySettingsError = "Renderer preference could not be saved"; }
          });
          renderer.dataset.browserRenderer = "1";
          this._appendTile({ icon: "texture", title: "Renderer", trailing: renderer });
        }
        if (config.videoControlSupported === 1 && config.videoTargets) {
          this._videoControlRevision = config.videoTargets.revision;
          const change = async (key, value) => {
            if (this.videoSettingsPending) return;
            this.videoSettingsPending = true;
            this.displaySettingsError = null;
            this.refreshDisplayControl(config);
            try {
              await this.apiClient.postVideoTargets({ revision: config.videoTargets.revision,
                bitRate: config.videoTargets.bitRate, maxFps: config.videoTargets.maxFps, [key]: Number(value) });
            } catch (error) {
              this.displaySettingsError = error.status === 409 ? "Video settings changed; try again" : "Video settings could not be saved";
            } finally { this.videoSettingsPending = false; }
          };
          const bitrate = this._createSelect([1, 2, 3, 4, 8, 12].map(value => ({ value: value * 1000000, label: value + " Mbps" })),
            config.videoTargets.bitRate, value => change("bitRate", value));
          bitrate.dataset.videoBitrate = "1";
          const rate = this._createSelect([30, 45, 60].map(value => ({ value, label: value + " fps" })),
            config.videoTargets.maxFps, value => change("maxFps", value));
          rate.dataset.videoFrameRate = "1";
          this._appendDivider();
          this._appendTile({ icon: "photo_size_select_actual_outlined", title: "Video bitrate", trailing: bitrate });
          this._appendDivider();
          this._appendTile({ icon: "monitor", title: "Target frame rate", trailing: rate });
          for (const input of this.content.querySelectorAll("button, input")) {
            if (!input.closest?.("[data-browser-renderer]")) input.disabled = !enabled || this.videoSettingsPending ||
              (config.canResize === false && Boolean(input.closest?.("[data-display-size]")));
          }
        } else {
          this._appendTile({ icon: "monitor", title: "Refresh rate", trailing: this._createTrailingText("30 Hz") });
        }
        this._renderSecondaryDisplaySettings(config);
        if (this.displaySettingsError) {
          const status = utils.createElement("div", "settings-error", this.displaySettingsError);
          status.setAttribute("role", "status"); this.content.appendChild(status);
        }
        return;
      }

      this._appendTile({
        icon: "texture",
        title: "Renderer",
        trailing: this._createSelect(constants.DISPLAY_RENDERERS, config.renderer, async (value) => {
          const renderer = utils.toInt(value, config.renderer);
          await this._updateDisplayConfig((draft) => {
            draft.renderer = renderer;
            draft.isH264 = renderer !== 0 ? 1 : 0;
            draft.density = utils.densityForResolutionPreset(draft.resolutionPreset, draft.isH264 === 1);
          });
        }),
      });
      this._appendDescription(
        "Tesla Android supports both h264 and MJPEG display compression. MJPEG has less visible compression artifacts but needs much more bandwidth.\n\nNOTE: WebCodecs may not work if your car is running Tesla Firmware older than 2025.32.",
      );
      this._appendDivider();

      this._appendTile({
        icon: "display_settings",
        title: "Resolution",
        trailing: this._createSelect(constants.DISPLAY_RESOLUTION_PRESETS, config.resolutionPreset, async (value) => {
          const preset = utils.toInt(value, config.resolutionPreset);
          await this._updateDisplayConfig((draft) => {
            draft.resolutionPreset = preset;
            draft.density = utils.densityForResolutionPreset(draft.resolutionPreset, draft.isH264 === 1);
          });
        }),
      });
      this._appendDescription(
        "Choosing a low resolution improves the display performance in Drive. It reduces the browser load, meant for cars equipped with MCU2. Resolutions lower than 720p only work with the MJPEG renderer.",
      );
      this._appendDivider();

      this._appendTile({
        icon: "photo_size_select_actual_outlined",
        title: "Image quality",
        trailing: this._createSelect(constants.DISPLAY_QUALITY_PRESETS, config.quality, async (value) => {
          const quality = utils.toInt(value, config.quality);
          await this._updateDisplayConfig((draft) => {
            draft.quality = quality;
          });
        }),
      });
      this._appendDescription(
        "Reducing the image quality can significantly improve performance when a higher resolution is used.",
      );
      this._appendDivider();

      this._appendTile({
        icon: "monitor",
        title: "Refresh rate",
        trailing: this._createSelect(constants.DISPLAY_REFRESH_RATE_PRESETS, config.refreshRate, async (value) => {
          const refreshRate = utils.toInt(value, config.refreshRate);
          await this._updateDisplayConfig((draft) => {
            draft.refreshRate = refreshRate;
          });
        }),
      });
      this._appendDescription(
        "By default Tesla Android is running in 30Hz. You can increase the frame rate with this setting. This feature is experimental.",
      );
      this._appendDivider();

      this._appendTile({
        icon: "photo_size_select_large",
        title: "Dynamic aspect ratio",
        trailing: this._createSwitch(config.isResponsive === 1, async (checked) => {
          await this._updateDisplayConfig((draft) => {
            draft.isResponsive = checked ? 1 : 0;
          });
        }),
      });
      this._appendDescription(
        "Advanced setting, Tesla Android can automatically resize the virtual display when the browser window size changes. If you disable this option, the display aspect will be locked on the current value.",
      );
      this._renderSecondaryDisplaySettings(config);
    }

    _rearSharedAllowed(state) {
      return Boolean(!this.rearSettingsPending && !this.videoSettingsPending && state?.canControl && state.transactionStatus === "idle" &&
        !this.displayController.controlRequestInFlight);
    }

    _renderControlledRearDisplay(config) {
      const rolePicker = utils.createElement("div", "rear-role-picker", null);
      rolePicker.setAttribute("data-rear-role", "");
      for (const [role, label] of [["primary", "Primary Display"], ["rear", "Secondary Display"]]) {
        const choice = document.createElement("label"), input = document.createElement("input");
        input.type = "radio"; input.name = "rear-display-role"; input.value = role;
        input.checked = config.displayRole === role;
        input.addEventListener("change", () => {
          if (input.checked) void this._changeControlledRear(config, { role });
        });
        input.addEventListener('click', () => {
          const state = this.displayController.remoteDisplayState;
          const preferred = state.isRearDisplayEnabled === 1 && state.isRearDisplayPrioritised === 1 ? 'rear' : 'primary';
          if (input.checked && state.displayRole === role && role === preferred && !state.canControl && state.displayRoleClaimSupported === 1) {
            void this._changeControlledRear(config, { role });
          }
        });
        choice.appendChild(input); choice.appendChild(utils.createElement("span", null, label)); rolePicker.appendChild(choice);
      }
      const roleTile = this._appendTile({ icon: "screenshot_monitor", title: "This Display", trailing: rolePicker });
      roleTile?.setAttribute("data-display-role-tile", "");
      const addSharedSwitch = (title, checked, patch) => {
        const switchControl = this._createSwitch(checked, value => this._changeControlledRear(config, patch(value)),
          () => this._rearSharedAllowed(this.displayController.remoteDisplayState));
        switchControl.setAttribute("data-rear-shared", "");
        this._appendTile({ icon: "monitor", title, trailing: switchControl });
      };
      addSharedSwitch("Secondary Display", config.isRearDisplayEnabled === 1, enabled => ({ enabled }));
      if (this.rearSettingsError) this._appendDescription(this.rearSettingsError);
      this._rearControlGeneration = config.generation;
      this.refreshDisplayControl(config);
    }

    async _changeControlledRear(config, change) {
      if (this.activeSectionId !== "display" || this.rearSettingsPending || this.videoSettingsPending ||
          this.displayController.controlRequestInFlight || this.displayController.remoteDisplayState.transactionStatus !== "idle") return;
      if (!("role" in change) && !this._rearSharedAllowed(this.displayController.remoteDisplayState)) return;
      this.rearSettingsPending = true; this.rearSettingsError = null;
      this.refreshDisplayControl(this.displayController.remoteDisplayState);
      try {
        if ("role" in change) await this.displayController.selectPrimaryDisplay(change.role === "primary");
        else await this.displayController.updateRearSettings({ revision: config.rearDisplayRevision,
          enabled: config.isRearDisplayEnabled === 1, prioritised: config.isRearDisplayPrioritised === 1, ...change });
      } catch (_error) { this.rearSettingsError = "Secondary display change failed. Reload settings and retry."; }
      finally {
        this.rearSettingsPending = false;
        if (this.activeSectionId === "display") await this._renderActiveSection();
      }
    }

    _renderSecondaryDisplaySettings(config) {
      this._appendDivider();
      if (config.rearDisplayControlSupported === 1) {
        this._renderControlledRearDisplay(config);
        return;
      }
      if (config.displayControlSupported === 1) {
        this._appendTile({ icon: "monitor", title: "Secondary Display", trailing: this._createTrailingText("Unavailable") });
        return;
      }

      this._appendTile({
        icon: "monitor",
        title: "Secondary Display",
        trailing: this._createSwitch(config.isRearDisplayEnabled === 1, async (checked) => {
          await this._updateDisplayConfig((draft) => {
            draft.isRearDisplayEnabled = checked ? 1 : 0;
          }, false);
          await this._renderActiveSection();
        }),
      });

      if (config.isRearDisplayEnabled === 1) {
        const isPrimaryDisplay = this._readPrimaryDisplayPreference();

        this._appendDivider();
        this._appendTile({
          icon: "screenshot_monitor",
          title: "Primary Display",
          trailing: this._createSwitch(
            isPrimaryDisplay === null ? true : isPrimaryDisplay,
            async (checked) => {
              this._storePrimaryDisplayPreference(checked);
            },
          ),
        });

      }
    }

    async _renderNetworkSection(token) {
      this._appendLoadingState();

      if (this.displayController.remoteDisplayState?.displayControlSupported === 1) {
        let info;
        try { info = await this.apiClient.fetchDeviceInfo(); }
        catch (_error) {
          if (token === this._renderToken && this.activeSectionId === "network") this._renderErrorState("Network details unavailable");
          return;
        }
        if (token !== this._renderToken || this.activeSectionId !== "network") return;
        if (info.networkInfoVersion !== 1) { this._renderErrorState("Network details unavailable"); return; }
        this.content.innerHTML = ""; this.content.dataset.vmNetworkInfo = "1";
        this.networkPendingConfig = null;
        const rows = [["public", "Browser connection", global.location.origin],
          ["lan", "LAN streaming", info.lan_stream_origin || "Unavailable"],
          ["route", "Android network", info.network_mode || "Unavailable"]];
        for (const address of info.android_addresses || []) rows.push(["lan", "Android IPv4 (" + address.interface + ")", address.address]);
        if (!info.android_addresses?.length) rows.push(["lan", "Android IPv4", "Unavailable"]);
        for (const [icon, title, value] of rows) this._appendTile({ icon, title, trailing: this._createTrailingText(value), dense: false });
        this._refreshNetworkBanner();
        return;
      }

      if (!this.systemConfigCache) {
        try { this.systemConfigCache = await this.apiClient.fetchSystemConfiguration(); }
        catch (_error) {
          if (token === this._renderToken && this.activeSectionId === "network") this._renderErrorState("Failed to fetch Wi-Fi configuration from your device.");
          return;
        }
      }

      if (!this.networkPendingConfig) {
        this.networkPendingConfig = this._networkPendingFromSystemConfig(this.systemConfigCache);
      }

      if (token !== this._renderToken || this.activeSectionId !== "network") {
        return;
      }

      this.content.innerHTML = "";

      this._appendTile({
        icon: "wifi_channel",
        title: "Frequency band and channel",
        trailing: this._createSelect(
          constants.SOFT_AP_BANDS.map((item) => ({ value: item.key, label: item.name })),
          this.networkPendingConfig.bandType.key,
          async (value) => {
            const selected = this._bandByKey(value);
            if (!selected) {
              return;
            }
            this.networkPendingConfig.bandType = selected;
            this._refreshNetworkBanner();
          },
        ),
      });

      this._appendDescription(
        "The utilization of the 5 GHz operation mode enhances the performance of the Tesla Android system while effectively resolving Bluetooth-related challenges. If your car does not see the Tesla Android network please change the channel, supported channels differ by region. This mode is anticipated to be designated as the default option in a future versions.\n\nConversely, when operating on the 2.4 GHz frequency, the allocation of resources between the hotspot and Bluetooth can lead to dropped frames, particularly when utilizing AD2P audio.",
      );
      this._appendDivider();

      this._appendTile({
        icon: "wifi_off",
        title: "Offline mode",
        subtitle: "Persistent Wi-Fi connection",
        trailing: this._createSwitch(this.networkPendingConfig.isOfflineModeEnabled, async (checked) => {
          this.networkPendingConfig.isOfflineModeEnabled = checked;
          this._refreshNetworkBanner();
        }),
      });
      this._appendDivider();

      this._appendDescription(
        "To ensure continuous internet access, your Tesla vehicle relies on Wi-Fi networks that have an active internet connection. However, if you encounter a situation where Wi-Fi connectivity is unavailable, there is a solution called \"offline mode\" to address this limitation. In offline mode, certain features like Tesla Mobile App access and other car-side functionalities that rely on internet connectivity will be disabled. To overcome this limitation, you can establish internet access in your Tesla Android setup by using an LTE Modem or enabling tethering.",
      );
      this._appendDivider();

      this._appendTile({
        icon: "data_thresholding_sharp",
        title: "Tesla Telemetry",
        subtitle: "Reduces data usage, uncheck to disable",
        trailing: this._createSwitch(this.networkPendingConfig.isOfflineModeTelemetryEnabled, async (checked) => {
          this.networkPendingConfig.isOfflineModeTelemetryEnabled = checked;
          this._refreshNetworkBanner();
        }),
      });
      this._appendDivider();

      this._appendTile({
        icon: "update",
        title: "Tesla Software Updates",
        subtitle: "Reduces data usage, uncheck to disable",
        trailing: this._createSwitch(this.networkPendingConfig.isOfflineModeTeslaFirmwareDownloadsEnabled, async (checked) => {
          this.networkPendingConfig.isOfflineModeTeslaFirmwareDownloadsEnabled = checked;
          this._refreshNetworkBanner();
        }),
      });
      this._appendDescription(
        "Your car will still be able to check the availability of new updates. With this option enabled they won't immediately start downloading",
      );

      this._refreshNetworkBanner();
    }

    async _renderAudioSection(token) {
      this._appendLoadingState();

      const systemConfiguration = await this._fetchSystemConfigurationSafe();
      if (!systemConfiguration) {
        this._renderErrorState("Service error");
        return;
      }

      if (token !== this._renderToken || this.activeSectionId !== "audio") {
        return;
      }

      const isEnabled =
        utils.toInt(systemConfiguration["persist.tesla-android.browser_audio.is_enabled"], 1) === 1;
      const volume = utils.toInt(
        systemConfiguration["persist.tesla-android.browser_audio.volume"],
        100,
      );

      this.content.innerHTML = "";

      this._appendTile({
        icon: "speaker",
        title: "Browser audio",
        subtitle: "Disable if you intend to use Bluetooth audio",
        trailing: this._createSwitch(isEnabled, async (checked) => {
          await this._saveAudioSettings(checked ? 1 : 0, 100);
        }),
      });
      this._appendDivider();

      const sliderWrap = utils.createElement("div", "settings-volume-wrap", null);
      const sliderValue = utils.createElement("span", "settings-volume-value", String(volume) + " %");
      const slider = utils.createElement("input", "settings-volume-slider", null);
      slider.type = "range";
      slider.min = "0";
      slider.max = "150";
      slider.step = "10";
      slider.value = String(volume);
      slider.addEventListener("input", () => {
        sliderValue.textContent = String(slider.value) + " %";
      });
      slider.addEventListener("change", async () => {
        await this._saveAudioSettings(1, utils.toInt(slider.value, volume));
      });
      sliderWrap.appendChild(sliderValue);
      sliderWrap.appendChild(slider);

      this._appendTile({
        icon: "volume_down",
        title: "Volume",
        trailing: sliderWrap,
        dense: false,
      });

        if (typeof global.getAudioDelayMs === "function" && typeof global.setAudioDelayMs === "function") {
          this._appendDivider();
          const delayWrap = utils.createElement("div", "settings-volume-wrap", null);
          const delayValue = utils.createElement("span", "settings-volume-value", String(global.getAudioDelayMs()) + " ms");
          const delay = utils.createElement("input", "settings-audio-delay-slider", null);
          delay.type = "range"; delay.min = "-750"; delay.max = "750"; delay.step = "25";
          delay.style.width = "180px"; delay.style.accentColor = "var(--accent)";
          delay.value = String(global.getAudioDelayMs()); delay.dataset.localAudioDelay = "true";
          delay.setAttribute("aria-label", "A/V sync");
          delay.addEventListener("input", () => { delayValue.textContent = String(delay.value) + " ms"; });
          delay.addEventListener("change", () => {
            if (token !== this._renderToken || this.activeSectionId !== "audio") return;
            try { global.setAudioDelayMs(Number(delay.value)); } catch (_) {}
            delay.value = String(global.getAudioDelayMs()); delayValue.textContent = delay.value + " ms";
          });
          delayWrap.appendChild(delayValue); delayWrap.appendChild(delay);
          this._appendTile({ icon: "av_timer", title: "A/V sync", trailing: delayWrap, dense: false });
        }

      if (this.apiClient.browserAudioSupported) this.refreshDisplayControl(this.displayController.remoteDisplayState);
      if (this.audioSettingsError) {
        const status = utils.createElement("div", "settings-error", this.audioSettingsError);
        status.setAttribute("role", "status"); this.content.appendChild(status);
      }

      this._appendDescription(
        "If you plan to use browser audio continuously in conjunction with video playback, it's essential to note that it can be bandwidth-intensive. To optimize your experience, you may want to consider pairing your car with the Tesla Android device over Bluetooth, particularly if your Tesla is equipped with MCU2.",
      );
      this._appendDescription(
        "In case you encounter a situation where the browser in your Tesla fails to produce sound, a simple reboot of the vehicle should resolve the issue. Please note that this is a known issue with the browser itself.",
      );
    }

    _audioSettingsAllowed(state = this.displayController.remoteDisplayState) {
      return !this.audioSettingsPending && (!this.apiClient.browserAudioSupported ||
        (state?.canControl && state.transactionStatus === "idle" && !this.displayController.controlRequestInFlight));
    }

    async _saveAudioSettings(enabled, volume) {
      if (!this._audioSettingsAllowed()) return;
      this.audioSettingsPending = true;
      this.audioSettingsError = null;
      if (this.apiClient.browserAudioSupported) this.refreshDisplayControl(this.displayController.remoteDisplayState);
      try {
        await this.apiClient.setBrowserAudioState(enabled);
        await this.apiClient.setBrowserAudioVolume(volume);
        await this._handleSystemConfigurationChanged();
      } catch (_error) {
        this.audioSettingsError = "Audio settings could not be saved";
      } finally { this.audioSettingsPending = false; }
      if (this.activeSectionId === "audio") await this._renderActiveSection();
    }

    async _renderGpsSection(token) {
      this._appendLoadingState();

      const systemConfiguration = await this._fetchSystemConfigurationSafe();
      if (!systemConfiguration) {
        this._renderErrorState("Service error");
        return;
      }

      if (token !== this._renderToken || this.activeSectionId !== "gps") {
        return;
      }

      const isEnabled =
        utils.toInt(systemConfiguration["persist.tesla-android.gps.is_active"], 1) === 1;

      this.content.innerHTML = "";

      this._appendTile({
        icon: "gps_fixed",
        title: "GPS",
        subtitle: "Disable if you don't use Android navigation apps",
        trailing: this._createSwitch(isEnabled, async (checked) => {
          if (this.gpsSettingsPending) return;
          this.gpsSettingsPending = true;
          if (this.apiClient.browserGpsManaged) this.refreshDisplayControl(this.displayController.remoteDisplayState);
          try {
            await this.apiClient.setGpsState(checked ? 1 : 0);
            await this._handleSystemConfigurationChanged();
            if (!this.apiClient.browserGpsManaged) this.gpsController.setEnabled(checked);
          } catch (_error) {
            this._renderErrorState("Service error");
            return;
          } finally {
            this.gpsSettingsPending = false;
          }
          if (this.activeSectionId === "gps") await this._renderActiveSection();
        }),
      });

      if (this.apiClient.browserGpsManaged) {
        const state = this.displayController.remoteDisplayState;
        const allowed = !this.gpsSettingsPending && this.apiClient.browserGpsSupported && state?.canControl && state.transactionStatus === "idle" &&
          !this.displayController.controlRequestInFlight;
        for (const input of this.content.querySelectorAll("input, button")) input.disabled = !allowed;
      }

      this._appendDescription(
        this.apiClient.browserGpsManaged
          ? "Browser GPS uses an Android mock location provider. Some apps may not accept mock locations."
          : "NOTE: GPS via Browser can cause crashes on Tesla Software 2024.14 or newer, the integration is disabled by default until this issue is solved by Tesla. Please be assured that your location data never leaves your car. The real-time location updates sent to your Tesla Android device are solely utilized to emulate a hardware GPS module in the Android OS.",
      );
    }

    async _renderDeviceSection(token) {
      this._appendLoadingState();

      let deviceInfo;
      try {
        deviceInfo = await this.apiClient.fetchDeviceInfo();
      } catch (_error) {
        this._renderErrorState("Service error");
        return;
      }

      if (token !== this._renderToken || this.activeSectionId !== "device") {
        return;
      }

      if (deviceInfo.deviceInfoVersion === 1) {
        this.content.innerHTML = "";
        this._renderVmDeviceInfo(deviceInfo);
        return;
      }

      const deviceModel = utils.mapDeviceModelName(deviceInfo.device_model);

      this.content.innerHTML = "";

      this._appendTile({
        icon: "device_thermostat",
        title: "CPU Temperature",
        trailing: this._createTrailingText(String(utils.toInt(deviceInfo.cpu_temperature, 0)) + "\u00B0C"),
      });
      this._appendDescription(
        "CPU temperature should not exceed 80\u00B0C. Make sure the device is actively cooled and proper ventilation is provided.",
      );
      this._appendDivider();

      this._appendTile({
        icon: "perm_device_info",
        title: "Model",
        trailing: this._createTrailingText(deviceModel),
        dense: false,
      });
      this._appendTile({
        icon: "developer_board_rounded",
        title: "Serial Number",
        trailing: this._createTrailingText(String(deviceInfo.serial_number || "undefined")),
        dense: false,
      });
      this._appendTile({
        icon: "broadcast_on_home_sharp",
        title: "CarPlay Module",
        trailing: this._createTrailingText(
          utils.toInt(deviceInfo.is_carplay_detected, 0) === 1 ? "Connected" : "Not connected",
        ),
        dense: false,
      });
      this._appendTile({
        icon: "cell_tower",
        title: "LTE Modem",
        trailing: this._createTrailingText(
          utils.toInt(deviceInfo.is_modem_detected, 0) === 1 ? "Detected" : "Not detected",
        ),
        dense: false,
      });
      this._appendDescription(
        "The LTE modem is considered detected when it is properly connected, and the gateway is reachable by Android. IP address 192.168.(0/8).1 is used for this check (Default for E3372 and Alcatel modems).",
      );
      this._appendTile({
        icon: "update",
        title: "Release type",
        trailing: this._createTrailingText(String(deviceInfo.release_type || "undefined").toUpperCase()),
        dense: false,
      });
      this._appendDescription(
        "No support is provided for devices that are running pre-release (beta) software. You can switch your desired release type on https://beta.teslaandroid.com",
      );
    }

    _renderVmDeviceInfo(info) {
      if (this.content.dataset) this.content.dataset.vmDeviceInfo = "1";
      const number = value => Number.isFinite(value) && value >= 0;
      const memory = value => number(value) ? (value / (1024 ** 3)).toFixed(1) + " GiB" : "Unavailable";
      const value = input => input === null || input === undefined || input === "" ? "Unavailable" : String(input);
      let uptime = "Unavailable";
      if (number(info.uptime_seconds)) {
        const minutes = Math.floor(info.uptime_seconds / 60), hours = Math.floor(minutes / 60), days = Math.floor(hours / 24);
        uptime = (days ? days + "d " : "") + (hours ? hours % 24 + "h " : "") + minutes % 60 + "m";
      }
      const rows = [
        ["perm_device_info", "Model", value(info.device_model)],
        ["developer_board", "VM CPU", value(info.cpu_model)],
        ["memory", "CPU cores", value(info.cpu_cores)],
        ["memory", "VM memory", memory(info.memory_total_bytes)],
        ["memory", "Free VM memory", memory(info.memory_free_bytes)],
        ["schedule", "Host uptime", uptime],
        ["terminal", "Host kernel", value(info.kernel_release)],
        ["computer", "Host name", value(info.host_name)],
      ];
      if (number(info.cpu_temperature)) rows.push(["device_thermostat", "CPU Temperature", info.cpu_temperature + "\u00B0C"]);
      for (const [icon, title, text] of rows) {
        this._appendTile({ icon, title, trailing: this._createTrailingText(text), dense: false });
      }
    }

    async applyNetworkConfiguration() {
      if (!this.systemConfigCache || !this.networkPendingConfig) {
        return;
      }

      try {
        await this.apiClient.setSoftApBand(this.networkPendingConfig.bandType.band);
        await this.apiClient.setSoftApChannelWidth(this.networkPendingConfig.bandType.channelWidth);
        await this.apiClient.setSoftApChannel(this.networkPendingConfig.bandType.channel);
        await this.apiClient.setSoftApState(this.networkPendingConfig.isSoftApEnabled ? 1 : 0);
        await this.apiClient.setOfflineModeState(this.networkPendingConfig.isOfflineModeEnabled ? 1 : 0);
        await this.apiClient.setOfflineModeTelemetryState(
          this.networkPendingConfig.isOfflineModeTelemetryEnabled ? 1 : 0,
        );
        await this.apiClient.setOfflineModeTeslaFirmwareDownloads(
          this.networkPendingConfig.isOfflineModeTeslaFirmwareDownloadsEnabled ? 1 : 0,
        );

        await this._handleSystemConfigurationChanged();
        this.networkPendingConfig = this._networkPendingFromSystemConfig(this.systemConfigCache);
        this._refreshNetworkBanner();
      } catch (_error) {
        this._renderErrorState("Failed to save your new Wi-Fi configuration");
      }
    }

    _bindBannerEvents() {
      this.bannerAction.addEventListener("click", async () => {
        await this.applyNetworkConfiguration();
      });
    }

    _bindGlobalEvents() {
      if (!global.document || typeof global.document.addEventListener !== "function") {
        return;
      }

      global.document.addEventListener("pointerdown", (event) => {
        this._handleDocumentPointerDown(event);
      });
      global.document.addEventListener("keydown", (event) => {
        this._handleDocumentKeyDown(event);
      });
    }

    _handleDocumentPointerDown(event) {
      if (!this._activeDropdown) {
        return;
      }
      if (this._activeDropdown.contains(event.target)) {
        return;
      }
      this._setActiveDropdown(null);
    }

    _handleDocumentKeyDown(event) {
      if (!this._activeDropdown) {
        return;
      }
      if (event.key === "Escape") {
        this._setActiveDropdown(null);
      }
    }

    _setActiveDropdown(dropdown) {
      if (this._activeDropdown && this._activeDropdown !== dropdown) {
        this._activeDropdown.classList.remove("open");
        const previousTrigger = this._activeDropdown.querySelector(".settings-dropdown-trigger");
        if (previousTrigger) {
          previousTrigger.setAttribute("aria-expanded", "false");
        }
      }

      if (!dropdown) {
        this._activeDropdown = null;
        return;
      }

      if (this._activeDropdown === dropdown) {
        return;
      }

      dropdown.classList.add("open");
      const trigger = dropdown.querySelector(".settings-dropdown-trigger");
      if (trigger) {
        trigger.setAttribute("aria-expanded", "true");
      }
      this._activeDropdown = dropdown;
    }

    _renderSidebar() {
      this.sidebar.innerHTML = "";

      for (let index = 0; index < this.sections.length; index += 1) {
        const section = this.sections[index];
        const button = utils.createElement("button", "settings-menu-item", null);
        button.type = "button";
        button.setAttribute('aria-current', section.id === this.activeSectionId ? 'page' : 'false');
        if (section.id === this.activeSectionId) {
          button.classList.add("active");
        }

        const icon = utils.createElement(
          "span",
          "material-symbols-rounded settings-menu-icon",
          this._iconName(section.icon),
        );
        const label = utils.createElement("span", "settings-menu-label", section.title);

        button.appendChild(icon);
        button.appendChild(label);
        button.addEventListener("click", async () => {
          await this.setActiveSection(section.id);
        });
        this.sidebar.appendChild(button);
      }
    }

    refreshDisplayControl(state) {
      if (this.activeSectionId === 'browserConnection') { this._refreshConnectionAuthority(); return; }
      if (this.activeSectionId === "display" && state.rearDisplayControlSupported === 1) {
        const busy = this.rearSettingsPending || this.videoSettingsPending || this.displayController.controlRequestInFlight || state.transactionStatus !== "idle";
        for (const input of this.content.querySelectorAll("[data-rear-role] input")) {
          input.disabled = Boolean(busy);
          if (!this.rearSettingsPending) input.checked = state.displayRole === input.value;
        }
        for (const input of this.content.querySelectorAll("[data-rear-shared] input")) input.disabled = !this._rearSharedAllowed(state);
      }
      if (this.activeSectionId === "audio" && this.apiClient.browserAudioSupported) {
        const allowed = this._audioSettingsAllowed(state);
        for (const input of this.content.querySelectorAll("input, button")) {
          input.disabled = input.dataset?.localAudioDelay === "true" ? false : !allowed;
        }
        return;
      }
      if (this.activeSectionId === "gps" && this.apiClient.browserGpsManaged) {
        const allowed = !this.gpsSettingsPending && this.apiClient.browserGpsSupported && state.canControl && state.transactionStatus === "idle" &&
          !this.displayController.controlRequestInFlight;
        for (const input of this.content.querySelectorAll("input, button")) input.disabled = !allowed;
        return;
      }
      if (this.activeSectionId !== "display" || state.displayControlSupported !== 1) return;
      const enabled = !this.videoSettingsPending && !this.rearSettingsPending && state.canControl && state.transactionStatus === "idle" &&
        !this.displayController.controlRequestInFlight;
      for (const input of this.content.querySelectorAll("button, input")) {
        if (!input.closest?.("[data-browser-renderer], [data-rear-role], [data-rear-shared]")) input.disabled = !enabled ||
          (state.canResize === false && Boolean(input.closest?.("[data-display-size]")));
      }
      if (enabled && (this._displayControlGeneration !== state.generation ||
          (state.videoTargets && this._videoControlRevision !== state.videoTargets.revision))) {
        this._displayControlGeneration = state.generation;
        this._videoControlRevision = state.videoTargets?.revision;
        void this._renderActiveSection();
      }
    }

    async _updateDisplayConfig(mutator, showRebootBanner) {
      if (this.displayController.remoteDisplayState?.displayControlSupported === 1) {
        for (const input of this.content.querySelectorAll("button, input")) {
          if (!input.closest?.("[data-browser-renderer]")) input.disabled = true;
        }
        this.displaySettingsError = null;
        try { await this.displayController.updateControlledSettings(mutator); await this._renderActiveSection(); }
        catch (_error) {
          this.displaySettingsError = "Display change failed";
          await this._renderActiveSection();
        }
        return;
      }
      const shouldShowRebootBanner = showRebootBanner !== false;

      try {
        const config = this._normalizeDisplayState(await this.apiClient.fetchDisplayState());
        const draft = { ...config };
        mutator(draft);
        await this.apiClient.postDisplayState(draft);
        if (shouldShowRebootBanner) {
          global.alert(
            "Display configuration has been updated. Please restart the device in case of any issues.",
          );
        }
        this.displayController.triggerResizeFromViewport();
      } catch (_error) {
        this._renderErrorState("Service error");
      }
    }

    _normalizeDisplayState(raw) {
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
      };
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

    _networkPendingFromSystemConfig(config) {
      return {
        bandType: utils.mapSoftApBandFromConfig(config),
        isSoftApEnabled:
          utils.toInt(config["persist.tesla-android.softap.is_enabled"], 1) === 1,
        isOfflineModeEnabled:
          utils.toInt(config["persist.tesla-android.offline-mode.is_enabled"], 0) === 1,
        isOfflineModeTelemetryEnabled:
          utils.toInt(config["persist.tesla-android.offline-mode.telemetry.is_enabled"], 0) === 1,
        isOfflineModeTeslaFirmwareDownloadsEnabled:
          utils.toInt(
            config["persist.tesla-android.offline-mode.tesla-firmware-downloads"],
            0,
          ) === 1,
      };
    }

    _bandByKey(key) {
      for (let index = 0; index < constants.SOFT_AP_BANDS.length; index += 1) {
        if (constants.SOFT_AP_BANDS[index].key === key) {
          return constants.SOFT_AP_BANDS[index];
        }
      }
      return null;
    }

    _isNetworkPendingChanged() {
      if (!this.systemConfigCache || !this.networkPendingConfig) {
        return false;
      }
      const original = this._networkPendingFromSystemConfig(this.systemConfigCache);
      const pending = this.networkPendingConfig;

      return (
        original.bandType.key !== pending.bandType.key ||
        original.isSoftApEnabled !== pending.isSoftApEnabled ||
        original.isOfflineModeEnabled !== pending.isOfflineModeEnabled ||
        original.isOfflineModeTelemetryEnabled !== pending.isOfflineModeTelemetryEnabled ||
        original.isOfflineModeTeslaFirmwareDownloadsEnabled !==
          pending.isOfflineModeTeslaFirmwareDownloadsEnabled
      );
    }

    _refreshNetworkBanner() {
      const visible = this.activeSectionId === "network" && this._isNetworkPendingChanged();
      this.banner.hidden = !visible;
      this.bannerText.textContent =
        "System configuration has been updated. Apply it during the next system startup?";
    }

    async _handleSystemConfigurationChanged() {
      this.systemConfigCache = await this.apiClient.fetchSystemConfiguration();
      this.networkPendingConfig = null;
      if (typeof this.onSystemConfigurationChanged === "function") {
        this.onSystemConfigurationChanged(this.systemConfigCache);
      }
    }

    async _fetchSystemConfigurationSafe() {
      try {
        this.systemConfigCache = await this.apiClient.fetchSystemConfiguration();
        return this.systemConfigCache;
      } catch (_error) {
        return null;
      }
    }

    _appendTile(options, container = this.content) {
      const dense = options.dense !== false;
      const tile = utils.createElement(
        "div",
        dense ? "settings-tile settings-tile-dense" : "settings-tile settings-tile-wide",
        null,
      );

      const leading = utils.createElement("div", "settings-tile-leading", null);
      const leadingIcon = utils.createElement(
        "span",
        "material-symbols-rounded settings-tile-icon",
        this._iconName(options.icon || ""),
      );
      leading.appendChild(leadingIcon);
      const content = utils.createElement("div", "settings-tile-content", null);
      const title = utils.createElement("div", "settings-tile-title", options.title || "");
      content.appendChild(title);
      if (options.subtitle) {
        content.appendChild(utils.createElement("div", "settings-tile-subtitle", options.subtitle));
      }

      const trailing = utils.createElement("div", "settings-tile-trailing", null);
      if (options.trailing) {
        trailing.appendChild(options.trailing);
      }

      tile.appendChild(leading);
      tile.appendChild(content);
      tile.appendChild(trailing);
      container.appendChild(tile);
      return tile;
    }

    _appendDescription(text) {
      const description = utils.createElement("p", "settings-description", text);
      this.content.appendChild(description);
    }

    _appendDivider() {
      this.content.appendChild(utils.createElement("hr", "settings-divider", null));
    }

    _appendLoadingState(container = this.content) {
      container.innerHTML = "";
      container.appendChild(utils.createElement("div", "settings-loading", "Loading..."));
    }

    _renderErrorState(message, container = this.content) {
      container.innerHTML = "";
      container.appendChild(utils.createElement("div", "settings-error", message));
    }

    _createSelect(options, value, onChange) {
      const selectedOption = this._findOptionByValue(options, value);

      const root = utils.createElement("div", "settings-dropdown", null);
      const trigger = utils.createElement("button", "settings-dropdown-trigger", null);
      trigger.type = "button";
      trigger.setAttribute("aria-haspopup", "listbox");
      trigger.setAttribute("aria-expanded", "false");

      const valueLabel = utils.createElement(
        "span",
        "settings-dropdown-value",
        selectedOption ? selectedOption.label : "",
      );
      const arrow = utils.createElement(
        "span",
        "material-symbols-rounded settings-dropdown-arrow",
        this._iconName("arrow_drop_down"),
      );
      trigger.appendChild(valueLabel);
      trigger.appendChild(arrow);

      const menu = utils.createElement("div", "settings-dropdown-menu", null);
      menu.setAttribute("role", "listbox");

      for (let index = 0; index < options.length; index += 1) {
        const option = options[index];
        const item = utils.createElement(
          "button",
          "settings-dropdown-option",
          String(option.label),
        );
        item.type = "button";
        item.dataset.value = String(option.value);
        if (String(option.value) === String(value)) {
          item.classList.add("active");
        }

        item.addEventListener("click", async () => {
          if (trigger.disabled) {
            return;
          }
          this._setActiveDropdown(null);
          root.classList.add("is-busy");
          trigger.disabled = true;
          try {
            await onChange(item.dataset.value || "");
            if (this.activeSectionId) {
              await this._renderActiveSection();
            }
          } finally {
            trigger.disabled = false;
            root.classList.remove("is-busy");
          }
        });

        menu.appendChild(item);
      }

      trigger.addEventListener("click", () => {
        if (this._activeDropdown === root) {
          this._setActiveDropdown(null);
          return;
        }
        this._setActiveDropdown(root);
      });

      root.appendChild(trigger);
      root.appendChild(menu);
      return root;
    }

    _createSwitch(value, onChange, canEnable) {
      const label = utils.createElement("label", "settings-switch", null);
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      const slider = utils.createElement("span", "settings-switch-slider", null);

      input.addEventListener("change", async () => {
        input.disabled = true;
        try {
          await onChange(Boolean(input.checked));
        } finally {
          input.disabled = canEnable ? !canEnable() : false;
        }
      });

      label.appendChild(input);
      label.appendChild(slider);
      return label;
    }

    _createTrailingText(text) {
      return utils.createElement("span", "settings-trailing-text", text);
    }

    _findOptionByValue(options, value) {
      for (let index = 0; index < options.length; index += 1) {
        if (String(options[index].value) === String(value)) {
          return options[index];
        }
      }
      return options.length > 0 ? options[0] : null;
    }

    _iconName(iconName) {
      const source = String(iconName || "");
      switch (source) {
        case "monitor":
          return "desktop_windows";
        case "speaker":
          return "volume_up";
        case "developer_board":
          return "memory";
        case "display_settings":
          return "settings_overscan";
        case "photo_size_select_actual_outlined":
          return "photo_size_select_actual";
        case "photo_size_select_large":
          return "aspect_ratio";
        case "screenshot_monitor":
          return "desktop_windows";
        case "wifi_channel":
          return "wifi";
        case "data_thresholding_sharp":
          return "data_usage";
        case "device_thermostat":
          return "thermostat";
        case "developer_board_rounded":
          return "developer_board";
        case "broadcast_on_home_sharp":
          return "phonelink";
        case "cell_tower":
          return "network_cell";
        default:
          return source;
      }
    }
  }

  TAHtml.SettingsController = SettingsController;
})(window);
