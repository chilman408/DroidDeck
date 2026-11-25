(function (global) {
  "use strict";

  global.TAHtml.releaseNotesData = {
    versions: [{
      versionName: "0.1.0 Preview",
      changelogItems: [
        {
          title: "Waydroid VM runtime",
          shortDescription: "Changes from the base Waydroid image",
          descriptionMarkdown: "DroidMate adds a supervised browser-streaming runtime to Waydroid on an Ubuntu x86 VM, including unattended headless startup and recovery."
        },
        {
          title: "GPU rendering",
          shortDescription: "VirGL and Mesa",
          descriptionMarkdown: "Android and compositor GPU rendering are enabled on the verified VM graphics path, with independent health-based recovery. Compatibility settings retain software video decoding."
        },
        {
          title: "Browser video",
          shortDescription: "Compositor-backed H264",
          descriptionMarkdown: "Compositor capture, legacy H264 browser rendering and bounded decode queues replace the earlier low-frame-rate preview path. Automatic rendering does not fall back to a PNG stream."
        },
        {
          title: "Audio and A/V sync",
          shortDescription: "Browser-local synchronization",
          descriptionMarkdown: "Live browser audio, host-side volume gain and per-browser A/V sync from -750 to +750 ms are implemented. Zero remains the default; shared audio settings retain owner controls."
        },
        {
          title: "Display and input",
          shortDescription: "Five supported aspect ratios",
          descriptionMarkdown: "Dynamic sizing selects from 4:3, 1:1, 16:9, 9:16 and 3:4. Touch coordinates, browser input queues and three-button Android navigation have been corrected and verified."
        },
        {
          title: "Primary and Secondary Display",
          shortDescription: "Combined Display settings",
          descriptionMarkdown: "Primary Display and Secondary Display roles share one Android display, with controlled ownership transfer and configurable geometry priority. These are not independent Android desktops."
        },
        {
          title: "Location and browser access",
          shortDescription: "GPS and optional login",
          descriptionMarkdown: "Browser GPS forwarding, optional browser login defaulting off, same-origin streaming and reverse-proxy access are implemented. Viewer and owner authority remain separate."
        },
        {
          title: "DroidMate preview",
          shortDescription: "Project identity and diagnostics",
          descriptionMarkdown: "DroidMate replaces inherited product branding and release history. Network and Device pages show VM diagnostics without Pi-only placeholders. Unrelated Raspberry Pi downloads no longer trigger an update badge."
        },
        {
          title: "Known limits",
          shortDescription: "Preview status",
          descriptionMarkdown: "The portable installer and project-specific update channel are not yet implemented. Hardware video decoding remains disabled for compatibility. The Apple TV opening-logo transition issue remains deferred."
        }
      ]
    }]
  };
})(window);
