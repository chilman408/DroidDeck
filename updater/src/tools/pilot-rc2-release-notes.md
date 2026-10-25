# Integrated Pilot Installer

**One clean installer, including the reboot recovery fix.** New installations
use `droiddeck-0.1.0-rc.2-source.tar`; no separate rc.1 hotfix step is needed.
Download all five named assets, verify `SHA256SUMS`, and follow `INSTALL.md`.
Do not substitute GitHub's automatically generated source-code downloads.

The exact immutable source package passed a complete clean Ubuntu installation,
native VirGL rendering, desktop/mobile TLS video and AAC playback, full-display
crash recovery, browser touch after idle and two-display handoff. Actual reboot
tests include startup longer than the old 60-second limit and reboot deliberately
interrupted while Android is starting, with automatic recovery before Ubuntu
login. `release.json` records acceptance for the exact tested package bytes.

Already configured on rc.1? Keep your apps/accounts and use the
[dedicated rc.1 hotfix](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.0-rc.1-hotfix.1).
This clean installer refuses populated or partially installed systems. Do not
rerun it over an existing installation, delete partial journals or replay a GPU
activation token. Existing release tags and downloadable assets are unchanged.

Tested scope: Ubuntu 26.04.1 x86_64 on Unraid KVM with working VirGL 3D, 128 GiB
disk, 6 GiB RAM and the first regular administrative user (UID/GID 1000).
External Android images, APKs and build inputs come directly from their pinned
publishers; they are not bundled or mirrored. No Android account data, passwords,
private TLS keys or DRM material are included. Aurora Store, initial media volume
100% and no initial lockscreen are included; later user choices are preserved.

Early technical pilot, checksum-verified but not digitally signed. Trust only the
VM's verified public TLS certificate. Browser authentication defaults off: use a
trusted LAN, no Internet port forwarding. This is not a universal GPU/app/DRM
compatibility claim. Test vehicle browsers only while parked.
