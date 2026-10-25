# DroidDeck 0.1.0-rc.1 Pilot

**Required update:** apply [Pilot Reboot Hotfix 1](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.0-rc.1-hotfix.1)
after installing rc.1 and before rebooting, or to repair an existing configured
pilot VM without reinstalling Android. It fixes interrupted-startup recovery and
the short Android startup deadline. Original assets below are unchanged.

An early **source-only release candidate** for a small technical pilot of Android
streaming to a browser from an Ubuntu VM. Not a prebuilt VM, general updater,
digitally signed release, or guarantee of app/account/DRM compatibility.

## Start Here

Download all five named assets: `droiddeck-0.1.0-rc.1-source.tar`, `INSTALL.md`,
`COPYING`, `release.json`, and `SHA256SUMS`. Do not substitute GitHub's automatic
"Source code" ZIP/TAR for the named tested installer asset. Verify the checksums
before root extraction, then follow `INSTALL.md`.

Tested configuration: clean Ubuntu 26.04.1 x86_64 on Unraid KVM, first regular
user UID/GID 1000, 128 GiB disk, 6 GiB RAM, and working VirGL 3D graphics.
Use a separate disposable VM. Never run the clean installer over your configured
Android VM or delete partial installation journals to force a retry.

## Verified for This Candidate

- All 35 clean-installation operations completed from this exact package.
- Source archive reproduced identically on Windows and Linux; native/Java/library
  artifacts built twice and compared during installation.
- Native Android GPU rendering, original GPU profile restoration, automatic
  full-display recovery after a killed Android session, and reboot before login.
- Desktop/mobile browser legacy H264 video and AAC audio, generated-video motion
  and audio-tone checks, native touch after 75 seconds idle, and two-display
  handoff with continuous audio.
- Aurora Store installed, initial browser-route volume 100%, and initial
  lockscreen disabled. Later user choices are preserved.

Source TAR SHA256:
`ec5e233a186b2dc060c73bd2c974c37d5529992d245e63c621cadec3c553c929`

## Security and Limitations

Optional browser authentication defaults **off**. Keep the pilot on a trusted
LAN; do not port-forward it to the Internet. Verify/trust only the VM's public
TLS certificate. No global certificate-trust bypass is installed.

Android rendering is GPU-accelerated; software codec4/RGBX/AAC media decoding is
intentional. External inputs are fetched directly from pinned publisher sources.
Community images, APKs, signed-in data, passwords, private keys and DRM material
are not bundled. Development history and private validation transcripts are not
included in this public repository.

Apple TV and the Android Edge splash-screen issue remain deferred. Other hosts,
GPUs, user layouts and vehicle browsers need their own pilot testing. Test in a
vehicle only while parked. Review `docs/KNOWN_LIMITATIONS.md` and `SECURITY.md`.

Please report minimal reproduction steps with host/guest/GPU/browser versions.
Redact selected logs first. Never attach VM disks, account data, credentials,
TLS private keys or full system journals to public issues.
