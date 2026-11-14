# DroidMate RC17

Android apps streamed from an Ubuntu VM to your browser, with shared GPU H264 encoding and CPU fallback.

RC17 combines adaptive video quality, AAC-LC audio, expanded Display and Audio settings, Tesla WebCodecs compatibility, and the reversible per-VM GPU addon.

See the [qualification record](rc17/QUALIFICATION.json) and [release checksums](rc17/SHA256SUMS).

## Install or update

**Start here: [Install DroidMate RC17, step by step](rc17/INSTALL.md).** No Linux experience is assumed. The guide explains where to click, where to paste each command, and what a successful result looks like.

- **New installation:** follow Steps 1–15, from creating the Ubuntu VM to opening Android in your browser.
- **Updating an existing supported VM:** begin at Step 4. Keep your existing disk, apps, accounts, PINs and settings.
- **Already running RC17 successfully:** no reinstall is needed.

The guide includes download checks, the shared GPU addon, a reboot check and Undo instructions. Use files from the [RC17 release](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.1-rc.17). The signed guest installer contains the program and its source; Android images and third-party apps are downloaded from their publishers.

## Compatibility and defaults

The qualified GPU host contract is Unraid 7.1.4 / QEMU 9.2.3 with the tested shared AMD render device. Other host contracts are refused; CPU fallback remains available. GPU resize is used for beneficial same-aspect downscaling; other preprocessing stays on CPU. No whole-host CPU-saving percentage is claimed.

AAC-LC is the default browser audio format, with 64, 128 and 256 kbps choices and optional automatic quality. Display resolutions are 1080p, 900p, 720p, 600p and 480p, defaulting to 720p. Dynamic and manual aspect ratios, 15 fps, 450/720 kbps video and both H264 renderers are included. Automatic video quality preserves the selected ceilings and uses 15 fps at or below 1 Mbps.

Aurora Store is a new-install default. User-created secure lockscreens and private apps are supported. Test vehicle browsers while parked.

The public repository URL and existing internal service identifiers retain DroidDeck for compatibility. The application is DroidMate. Immutable signed core runtime `0.1.1-rc.20` is included under the aggregate RC17 release; it is not downgraded or renamed.

Never publish Android data, account tokens, passwords, TLS private keys or DRM material. See [security reporting](SECURITY.md). Earlier pilot installation instructions remain in [the RC2 guide](docs/INSTALL.md).

## Source and License

`installer/` holds the exact accepted source TAR contents. Frozen build-time
candidate flags are not rewritten after testing; release `release.json` records
acceptance for those exact bytes. Old release tags/assets remain immutable.
Private development history and operational checkpoints are not published.

DroidDeck source is GPL-3.0-only; see [LICENSE](LICENSE). Upstream components keep
their own licenses. No third-party endorsement, certification or account/DRM
compatibility is asserted. RC17's guest installer verifies its embedded release signature; the older RC2 pilot used checksums without a digital signature.
