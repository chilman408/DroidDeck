# DroidDeck 0.1.0-rc.1

Initial Ubuntu/Unraid installation release candidate. Not a signed binary release
or an in-place upgrader. Tested environment: fresh Ubuntu26.04.1 x86_64, first
regular user UID1000, Unraid KVM with working VirGL3D,128GiB disk and6GiB RAM.
GPU mode-access preflight and real Android GPU verification are mandatory.

## Install

Use a CLEAN disposable Ubuntu VM and an existing regular administrative user.
Existing Android, DroidDeck, installation journals or private preferences refuse
installation. Do not run this on a working preview or populated VM.

Verify SHA256SUMS obtained through a trusted release channel BEFORE root
extraction. Checksums alone do not authenticate their own download source.
The archive contains source only; external images/APKs/SDK tools/libraries are
downloaded directly from pinned publisher locations, then checked. Two complete
native/Java/library builds must match. No signed-in data, DRM or private TLS keys
are included. Community images are not bundled or mirrored.

```sh
sha256sum -c SHA256SUMS
sudo mkdir -m755 /usr/local/share/droiddeck-0.1.0-rc.1
sudo tar --no-same-owner --no-same-permissions -xf droiddeck-0.1.0-rc.1-source.tar -C /usr/local/share/droiddeck-0.1.0-rc.1
sudo python3 -I -B /usr/local/share/droiddeck-0.1.0-rc.1/droiddeck-source-candidate/droiddeck-install --install-machine YOUR_UBUNTU_USER YOUR_VM_IPV4 droiddeck.invalid
```

Use the VM's actual private LAN IPv4 address. A fixed/reserved address is needed
for its HTTPS identity. The command prints each installation phase. Normal sudo
authentication is required; no passwordless policy is installed. Do not close a
foreground SSH install. For unattended SSH operation, launch that same command
with systemd-run Type=exec/UMask=0077 and monitor its journal. Do not enable the
temporary installer unit at boot. Installation starts the completed services;
only the admitted GPU watcher is enabled to coordinate automatic boot startup.

## Connect

Open https://YOUR_VM_IPV4:8443/ after installation completes. A new private TLS
pair is generated on that VM. Explicitly trust its PUBLIC certificate on each
client, or use an authenticated trusted reverse proxy. Never export key.pem.
The public certificate is ~/.config/droiddeck/cert.pem for the selected user;
verify its fingerprint through your authenticated VM connection before trusting.
This package does not install global browser/OS trust or bypass TLS verification.

Aurora Store is required and installed only if missing. Initial browser-route
media volume is100%; initial lockscreen is None. Later volume/security choices
are preserved. Optional browser login defaults OFF: keep8443 on a trusted LAN
and configure supported authentication before any external exposure. Do not use
Aurora to replace the image's Google Play Services. No account, certification or
DRM spoofing is performed. Software codec4/RGBX/AAC decoding is intentional;
Android rendering remains GPU-accelerated. Legacy H264 and audio sync are kept.

## Recovery and Scope

Never rerun the whole command on an installed or partially installed target.
Preserve /root/droiddeck-installation and all original component journals under
/var/lib/droiddeck*. Started-without-complete markers identify uncertain phases.
Use the original component's explicit recovery operation after inspection, not
force/adoption/deletion of partial state. GPU activation tokens are single-use.
For a failed initial trial, retain its disk offline and use a new clean OS disk.
This is not a universal in-place upgrade or automatic pre-intent repair tool.

The source TAR is the exact immutable candidate that was tested. Its internal
build-time candidate warnings/false release flags are intentionally frozen, not
rewritten after testing. release.json carries the post-test acceptance for THESE
exact bytes. Acceptance is an engineering attestation, not a digital signature
or a claim of support for every GPU, Ubuntu version, app/account or DRM service.
Apple TV and the deferred Edge issue are outside this release's acceptance.
No healthy-service testing expiry or PNG video-transport fallback is installed.

## Source and Licenses

DroidDeck source headers identify GPL-3.0-only; COPYING supplies the unchanged GNU
GPLv3 text from https://www.gnu.org/licenses/gpl-3.0.txt. Upstream components keep
their own licenses. Installation fetches their publisher sources and notices;
this source distribution does not relicense their images, APKs or dependencies.
Project names identify integrations only; no third-party endorsement, account
compatibility, certification or redistribution clearance is asserted.
