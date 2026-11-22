# DroidMate 0.1.1

This first official distribution combines the signed runtime, five-section
Settings, adaptive video/audio quality and reversible shared-GPU support.
The product version is separate from immutable signed core runtime 0.1.1-rc.20.
Existing release assets and component receipts remain unchanged.

- Live, translucent Settings with Display, Audio, Connectivity, Device Info
  and Updates. A second Settings tap closes every menu level.
- 28px icons inside39px controls,49% ink/backing transparency,11-second idle fade
  and safe touch reveal. Startup fades the logo after a rendered frame.
- Tesla defaults to720p. Automatic Quality preserves selected resolution and
  ceilings, with faster recovery and320/450kbps at10fps for severe congestion.
- AAC-LC default, optional64/128/256kbps automatic audio, persistent playback
  identity/artwork and bounded live audio. FLAC and both H264 renderers remain.
- GPU-preferred H264 with CPU fallback and automatic retry on an idle screen.
  CPU Only is available. Choosing Automatic again requests an immediate retry.
- Resizing keeps the browser audio and video connections open and verifies the
  new picture while the touch controls are remapped.
- Signed preserving portable updates and clean Ubuntu installation, with reboot
  recovery and explicit Undo. Native sources and publisher build inputs included.

Qualification is recorded for exact download hashes. Until all required checks
pass, these files are private release candidates and must not be published.
The GPU addon is per VM; stock QEMU, shared libraries and VNC are preserved.
No whole-host CPU-saving percentage or universal host/browser support is claimed.
