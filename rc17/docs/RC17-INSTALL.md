# DroidMate RC17 installation and recovery

Use the authenticated RC17 pilot installer and the qualification evidence supplied
with its release. Component archives and development checkpoints are not installers.
The commands below apply to the authenticated aggregate asset.

## Choose an installation path

Use a clean Ubuntu 26.04.1 VM for a new installation. Keep VirtIO 3D graphics,
VNC, working networking, at least 6 GB RAM and a 128 GB virtual disk. Complete
Ubuntu setup first and use its normal administrative account. Android images
are obtained directly from their publisher during installation, not included
in the release. Publisher downloads and Ubuntu dependencies require internet.

For an existing supported installation, use the preserving update path. It must
retain Android apps, accounts, private-app PIN requirements, browser preferences,
TLS material and the Ubuntu desktop. Lock screen disabled is a new-install
default; it is not imposed on an installation that needs a PIN. Do not reinstall
Ubuntu or erase its disk to apply a preserving update.

RC17 is the aggregate release name. The already signed core runtime stays at
`0.1.1-rc.20`; the aggregate does not rename or downgrade that core, or manufacture
new base receipts for a legacy installation.

## Verify before installing

Obtain the final installer and its SHA256 from the repository's RC17 release.
Use the repository release page as the source of the expected hash, then compare
the downloaded bytes. The Linux entry also verifies the embedded authorization
with the existing pilot public signing key before importing payload code.
Never substitute an unsigned development capsule or ignore a verification error.

```sh
sha256sum droidmate-rc17.pyz
python3 -I -B droidmate-rc17.pyz verify
sudo python3 -I -B droidmate-rc17.pyz plan
```

The plan must recognize the actual installation and state its required guest and
selected-VM host changes. A differing or ambiguous installation is refused.
Do not delete an incomplete journal, replace receipts, or replay an old private
GPU profile to get past a refusal.

## Shared GPU host preparation

GPU H264 encoding needs the selected VM's private GPU addon as well as the guest
runtime. The qualified pilot host contract is Unraid 7.1.4 / QEMU 9.2.3 with the
tested AMD shared render node and render group. A different executable, wrapper,
device or group contract is refused; compatibility is not inferred from a model
name alone. CPU fallback remains required when GPU admission fails.

The addon keeps VirtIO 3D and VNC. It does not replace global QEMU, shared graphics
libraries or drivers, change global GPU permissions, or add an Unraid startup hook.
It prepares a new protected profile for exactly one VM. The VM must be shut down
gracefully before its emulator field is changed; its disks and other settings are
preserved. Other VMs and containers continue using their existing setup.

The final release must include the addon and its native source/build provenance.
Follow the authenticated addon's `source/docs/GPU-ADDON.md` for `plan`, `prepare`,
`install`, `status` and `undo`, recording the locally produced profile hash. Its
host commands run locally in the Unraid terminal; no host password, private key,
VM XML or local receipt belongs in a distributed installer or issue report.

## Guest installation

For an existing recognized installation:

```sh
sudo python3 -I -B droidmate-rc17.pyz install-existing
```

For a clean Ubuntu VM, replace both example arguments with the Ubuntu account and
the VM's own LAN IPv4 address:

```sh
sudo python3 -I -B droidmate-rc17.pyz install-new YOUR_UBUNTU_USER YOUR_VM_IPV4
```

The aggregate records its own transaction before changing workers. Recovery must
be registered before CPU capture restoration or the runtime restart. The browser
uses a complete service-private public runtime/frontend view; the original runtime
and signed base remain available for restoration. No configured Android directory,
credentials, DRM material or TLS private key is copied into source or artifacts.

After installation, validate browser video, touch, audio, settings, reconnect and
reboot. Use the hostname/reverse proxy that your browser can reach. A private LAN
IP URL is not generally reachable through cellular. Retain your existing TLS and
reverse-proxy configuration when applying an existing-install update.

## Interrupted installation and explicit Undo

Automatic recovery restores an incomplete owned operation. A healthy accepted
installation has no testing expiry. Recovery does not replay an installation or
claim a foreign file merely because its contents match.

Keep the exact installer used for the operation:

```sh
sudo python3 -I -B droidmate-rc17.pyz recover
sudo python3 -I -B droidmate-rc17.pyz undo
```

`recover` handles an interrupted owned operation; `undo` explicitly restores an
accepted one. Both preserve apps and data. A modified or foreign mapping is
preserved and reported, rather than overwritten or deleted. Keep the original
journal and retained ownership anchors for diagnosis.

Guest restoration comes before host Undo: restore capture, release the native
owner/updater lease, then gracefully stop only the selected VM, undo its exact
private host profile, and restart it with its original emulator. Disabling a
preference service alone does not restore an already running GPU worker.

## Release validation

The release notes identify the tested host contract and the completed clean-install,
preserving-update, GPU/CPU fallback, reboot and Undo/recovery checks. A different
host contract is refused before a selected-VM profile is installed. Keep the exact
installer used for an operation with its journals so recovery can verify its owner.

## Fresh-install package compatibility

New installations use the qualified Waydroid 1.6.2 package from its signed
publisher repository. A narrowly scoped owned APT preference keeps that application
version compatible with the original runtime's Android service protocol. Other
Ubuntu package/security updates remain enabled. Existing installations are not
downgraded by this fresh-only step. The pin belongs to the installed foundation
and remains when the aggregate browser/GPU update is undone.

The Java build uses an authenticated private compiler snapshot in a temporary
read-only mount namespace; the VM's installed Java remains current. Original
runtime binaries, build receipts and signed source checks are retained.

## Local certificate maintenance

A legacy preview can stop recovering after reboot when its self-signed LAN TLS
certificate expires: the runtime correctly refuses its own expired certificate.
The included `tools/renew-local-certificate.py` runs as the account owning the
private TLS directory, renews only an expiring self-signed certificate using its
existing private key, and preserves its IP/DNS names. It keeps a public-certificate
backup and leaves healthy certificates unchanged. It refuses CA-issued certificates;
those must use their original renewal provider. Renewal changes the public certificate
fingerprint, so a directly connected browser may require a new local trust exception.

The qualified legacy repair installs this check before runtime startup. Portable
TLS initialization has separate ownership receipts; automatic portable renewal is
not yet qualified. Do not modify those receipts or copy a private key to repair it.
