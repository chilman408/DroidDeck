# DroidMate RC17

Android apps streamed from an Ubuntu VM to your browser, with shared GPU H264 encoding and CPU fallback.

RC17 combines adaptive video quality, AAC-LC audio, expanded Display and Audio settings, Tesla WebCodecs compatibility, and the reversible per-VM GPU addon.

See the [qualification record](rc17/QUALIFICATION.json) and [release checksums](rc17/SHA256SUMS).

## Install or update

Use the RC17 release assets and [installation and Undo guide](rc17/INSTALL.md). The signed `droidmate-rc17.pyz` contains the guest installer and its public source, signatures and component inventory. Android images and third-party apps are fetched directly from their publishers.

- New VM: clean Ubuntu 26.04.1 x86_64, VirtIO 3D, VNC, at least 6 GB RAM and 128 GB disk. Run `install-new` with your Ubuntu user and VM address.
- Existing supported VM: run `plan`, then `install-existing`. Android apps, accounts, PINs, settings, desktop and TLS identity are preserved. Do not use the clean installer over an existing Android setup.
- GPU encoding: prepare the qualified private addon for the selected Unraid VM. Retain VirtIO 3D and VNC. No GPU passthrough or replacement of global QEMU, drivers, shared libraries or GPU permissions is required.
- Undo: restore the guest first, then gracefully shut down the selected VM and undo its exact host profile.

```sh
sha256sum -c SHA256SUMS --ignore-missing
python3 -I -B droidmate-rc17.pyz verify
sudo python3 -I -B droidmate-rc17.pyz plan
```

After checking the plan, choose exactly one installation path:

```sh
# Existing recognized installation:
sudo python3 -I -B droidmate-rc17.pyz install-existing

# Clean Ubuntu only; replace both arguments:
sudo python3 -I -B droidmate-rc17.pyz install-new YOUR_UBUNTU_USER YOUR_VM_IPV4
```

Keep the exact installer and original journals for recovery. If a check refuses the target, preserve the records; do not delete receipts or rerun a retired transaction.

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
compatibility is asserted. This pilot is checksum-verified, not digitally signed.
