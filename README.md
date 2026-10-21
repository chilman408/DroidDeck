# DroidDeck

Android streamed to your browser from an Ubuntu virtual machine.

![DroidDeck logo](installer/src/runtime/branding/droiddeck-logo.png)

## Pilot Release

**0.1.0-rc.1 is an early source release candidate for a small technical pilot.**
It is not a prebuilt VM image, an Android distribution, or an in-place upgrader.

Use the five assets attached to the `v0.1.0-rc.1` GitHub prerelease. Verify
`SHA256SUMS`, then follow the [installation guide](docs/INSTALL.md). Use the
named release asset, not GitHub's automatically generated repository source ZIP.
The release TAR is the exact immutable package used for installation testing.

The source installer downloads pinned inputs from their publishers, builds and
compares two artifact sets, and installs the completed services. Community
Android images, APKs, SDK tools and third-party libraries are not bundled here.
Review [pilot requirements](docs/PILOT.md) and
[known limitations](docs/KNOWN_LIMITATIONS.md) before installing.

## Tested Scope

- Fresh Ubuntu 26.04.1 x86_64 on Unraid KVM, 128 GiB virtual disk and 6 GiB RAM.
- Working VirGL 3D graphics, with native Android GPU verification.
- Clean installation through all 35 phases, independently reproduced source
  archives on Windows and Linux, and two matching native/Java/library builds.
- Automatic full-display recovery after a killed Android session, original GPU
  profile restoration, and service startup after reboot before interactive login.
- Desktop/mobile browser H264 video and AAC audio, native touch after idle,
  and two-display role handoff with continuing audio.

Android rendering uses the GPU. Software media decoding with codec4/RGBX/AAC is
intentional; these are different parts of the pipeline. Legacy H264 support is
retained. This is not a claim that every app, browser, GPU or DRM service works.

## Defaults and Safety

Aurora Store is installed by default. Initial browser-route media volume is
100%, and the initial Android lockscreen is disabled. Later user choices are
preserved. Do not replace the image's Google Play Services through Aurora.

Optional browser authentication defaults **off**. Keep DroidDeck on a trusted
LAN; do not forward its port to the Internet. Explicitly verify and trust the
VM's public TLS certificate, or use an authenticated trusted reverse proxy.
Never upload Android data, passwords, private keys, account tokens or DRM files.
Use a separate disposable VM for installer tests, not a configured daily-use VM.
Test vehicle browser behavior only while parked.

## Feedback

Open an issue with your host/guest versions, GPU mode, browser version, exact
reproduction steps, and the failing installation phase if applicable. Redact
logs first. Do not attach VM disks, Android data, full environment dumps or TLS
private keys. Report security concerns as described in [SECURITY.md](SECURITY.md).

## Source and License

`installer/` contains the unmodified contents of the accepted source TAR. Its
internal candidate flags are frozen build-time metadata; the release asset
`release.json` records the later acceptance for those exact bytes. The public
repository starts with a clean history and excludes private development logs.

DroidDeck source is GPL-3.0-only; see [LICENSE](LICENSE). Upstream components
retain their own licenses. Project names identify integrations, not endorsement
or app-store, account, certification or DRM compatibility guarantees. This pilot
is checksum-verified but is not digitally signed.
