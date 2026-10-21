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

## Installation

### 1. Prepare a Clean VM

Use a **fresh Ubuntu 26.04.1 x86_64 VM** with working **VirGL 3D graphics**,
128 GiB disk, 6 GiB RAM, and Internet access. Create a regular administrative
user (the tested layout is UID/GID 1000) and reserve the VM's LAN IPv4 address.
Run the commands below inside that Ubuntu VM, not on the Unraid host.

**Do not install over an existing DroidDeck or Android setup.** This pilot is
not an upgrader and refuses populated or partially installed targets.

### 2. Download and Verify

Download these five assets from the
[0.1.0-rc.1 release](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.0-rc.1)
into the same directory on the VM:

- `droiddeck-0.1.0-rc.1-source.tar`
- `INSTALL.md`
- `COPYING`
- `release.json`
- `SHA256SUMS`

Do not use GitHub's automatic **Source code (zip)** or **Source code (tar.gz)**
downloads. In a terminal, change to your download directory and run:

```sh
sha256sum -c SHA256SUMS
```

**Continue only if every listed file reports `OK`.** Obtain the checksums from
the trusted release page; a checksum does not authenticate its own source.

### 3. Install

Extract the verified source into a new, root-owned directory:

```sh
sudo mkdir -m755 /usr/local/share/droiddeck-0.1.0-rc.1 &&
sudo tar --no-same-owner --no-same-permissions \
  -xf droiddeck-0.1.0-rc.1-source.tar \
  -C /usr/local/share/droiddeck-0.1.0-rc.1
```

After successful extraction, replace `YOUR_UBUNTU_USER` with the administrative
username and `YOUR_VM_IPV4` with the VM's reserved private LAN address. Leave
`droiddeck.invalid` unchanged:

```sh
sudo python3 -I -B \
  /usr/local/share/droiddeck-0.1.0-rc.1/droiddeck-source-candidate/droiddeck-install \
  --install-machine YOUR_UBUNTU_USER YOUR_VM_IPV4 droiddeck.invalid
```

The installer downloads publisher-hosted dependencies, checks their pins,
compares two builds, verifies Android GPU rendering, and starts the services.
Keep the terminal or SSH connection open until all installation phases finish.
If a phase fails, **do not rerun the installer or delete partial state**; retain
the journals and follow [recovery guidance](docs/INSTALL.md#recovery-and-scope).

### 4. Connect from Your Browser

Open `https://YOUR_VM_IPV4:8443/` after installation succeeds. Verify the VM's
public certificate fingerprint through your authenticated VM connection:

```sh
openssl x509 -in ~/.config/droiddeck/cert.pem -noout -sha256 -fingerprint
```

Run that command as the selected Ubuntu user. Explicitly trust that **public**
certificate on each client, or use an authenticated trusted reverse proxy.
Never export `key.pem` or disable TLS verification globally. Browser login is
**off by default**: keep the VM on a trusted LAN and do not port-forward it.

Check video, audio after a browser gesture, touch, and reconnect after reboot.
See the [full installation guide](docs/INSTALL.md) and
[pilot validation checklist](docs/PILOT.md#install-and-validate) for details.

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
