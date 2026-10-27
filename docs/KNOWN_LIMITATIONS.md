# Known Limitations

- Technical pilot, not a prebuilt VM. RC2 is the clean installer; the signed
  0.1.1-rc.16 pilot supports cumulative gateway updates on an existing RC2 setup.
  Android images, frontend and native/Java libraries remain unchanged. See
  [the update guide](../UPDATE.md) for the original-RC2 rollback baseline and scope.
- rc.2 includes the reboot fix. Configured rc.1 VMs should use
  [their dedicated hotfix](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.0-rc.1-hotfix.1),
  not reinstall over their Android data.
- Tested Ubuntu 26.04.1 x86_64/Unraid KVM/VirGL configuration and first regular
  administrative user only. Other host/GPU/account layouts need separate testing.
- GPU preflight and real native Android verification are mandatory. Intentional
  software codec4/RGBX/AAC media decoding is separate from GPU rendering.
- Publisher input availability and independent pins are required. A changed
  input fails verification instead of silently selecting a different image.
- Local TLS requires explicit client trust. Browser authentication defaults off;
  use a trusted LAN and do not forward the port to the Internet.
- No certification, account or DRM spoofing. Google Play/account/app compatibility
  is not guaranteed. Do not replace Google Play Services through Aurora.
- Android Edge splash-screen and Apple TV investigations remain deferred.
- Browser audio can require a user gesture. Vehicle browsers need real parked
  testing; desktop/mobile checks do not certify every vehicle browser.
- Generated media tests do not establish numerical AV-sync accuracy or universal
  app compatibility. No PNG video fallback or healthy-service expiry is installed.
- RC2 uses published checksums and acceptance records. The gateway updater adds
  Ed25519 signatures binding its exact source and payload archives; its initial
  downloaded launcher still needs the trusted release channel and published
  checksum. Reproducible general prebuilt-VM distribution remains outside scope.

- Updates in Settings needs one initial signed updater installation. It supports
  explicit official pilot gateway releases, with primary-display permissions.
  Android images and libraries remain outside its scope. Rollback returns to RC2,
  whose older interface may not contain the Updates section; later releases must
  be strictly newer than every consumed attempt. See the update guide.

- Automatic quality is opt-in and uses the primary display's acknowledgement
  timing and queue backlog. It does not adapt resolution or measure decoder CPU.
  Quality changes restart the existing encoder and may briefly reconnect video.
  Generated-motion desktop tests cover both H264 decoders at a constrained
  1 Mbps delivery rate, recovery and manual takeover; real parked vehicle and
  wider Wi-Fi/cellular validation remain necessary before broader claims.

- AAC-LC128kbps is the browser audio default; FLAC is a manual browser-local
  option and fallback. Bounded live queues prevent stale-session replay, but
  poor links can still cause audible gaps. No universal network guarantee.
- VirtIO3D remains the supported rendering setup. Hardware video encoding is
  not enabled, and no GPU passthrough or replacement VM is required.
- Secure lockscreens required by private apps are supported. None is an initial
  default, not an update requirement; existing credentials are never cleared.
- RC16 browser/gateway cleanup was checked without another reboot. Earlier
  reboot/recovery evidence is retained; this is scoped engineering acceptance.
