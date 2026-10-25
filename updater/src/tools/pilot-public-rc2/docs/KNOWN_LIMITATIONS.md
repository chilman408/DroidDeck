# Known Limitations

- Source-only technical pilot, not a prebuilt VM or general in-place updater.
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
- Checksums and acceptance records are not digital signatures. Reproducible
  prebuilt binary distribution is outside this source pilot.
