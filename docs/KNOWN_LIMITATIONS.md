# Known Limitations

- Source-only release candidate, not a prebuilt VM or general in-place updater.
- Acceptance is limited to the documented Ubuntu/Unraid/VirGL configuration and
  first regular user. Other GPU/host/account combinations need separate testing.
- A GPU preflight and real native Android verification are required. Software
  codec4/RGBX/AAC decoding is intentional and does not mean rendering lacks GPU
  acceleration. No PNG video transport fallback is provided.
- TLS certificates are local to each VM. Client trust is explicit, not installed
  globally or bypassed. Authentication defaults off: trusted LAN only.
- External publisher availability and pinned inputs are required. A changed
  download must fail verification rather than silently selecting another image.
- Google Play/account compatibility, certification and DRM are not guaranteed or
  spoofed. Avoid replacing bundled Google Play Services through Aurora.
- The Android Edge splash-screen issue and Apple TV investigation are deferred.
- Browser audio can require a user gesture. Vehicle browsers require real-world
  parked testing; desktop/mobile automated checks do not certify every vehicle.
- Generated-video motion and audio-tone tests passed; no new numerical AV-sync
  accuracy, universal app compatibility or production security claim is made.
- SHA256 checksums and acceptance metadata are provided, but digital signing and
  reproducible prebuilt binary distribution are not part of this pilot.
