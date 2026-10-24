# Update an Existing DroidDeck RC2 Installation

This pilot adds signed gateway updates without reinstalling Android. Your apps,
accounts, settings, Android data and existing HTTPS certificate stay in place.
Streaming reconnects during an update. GPU rendering and the existing software
codec4/RGBX/AAC and legacy H264 paths remain unchanged.

The amended RC5 draft removes **h264 (WebCodecs)** from Settings. Automatic and
legacy use the compatible H264 decoder; old WebCodecs selections recover without
clearing browser data. See [browser compatibility](docs/BROWSER-RENDERER.md).
The amended package is pending one additional configured-RC2 apply/browser check;
keep it as a draft candidate until `release.json` records acceptance.

Use this on an already working **DroidDeck 0.1.0-rc.2**, Ubuntu 26.04.1 x86_64,
Unraid KVM/VirGL installation. For a new VM, use the separate RC2 clean installer
first. Do not run that clean installer over an existing Android setup.

## Download and verify

Download `droiddeck-0.1.1-rc.5-update.pyz` and `SHA256SUMS` from the official
[DroidDeck pilot release](https://github.com/chilman408/DroidDeck/releases).
The `.pyz` is the complete updater: it contains the payload, public source archive,
release statement, detached signature and installer. It needs no separate hotfix.

Check the downloaded file against the release's published checksum:

```sh
sha256sum -c SHA256SUMS --ignore-missing
python3 -I -B ./droiddeck-0.1.1-rc.5-update.pyz verify
```

Require the updater file to report `OK`, followed by
`droiddeck-signed-updater-verified`. Obtain checksums from the trusted release
channel. The updater independently verifies its Ed25519 release signature before
loading its bundled installation code. Its public-key SHA256 fingerprint is:

```text
4e0cb3fe1781c97522154c0a33d9678873cfb837808331ba218ad9c70425b34f
```

No signing private key or VM credential is included or needed.

## Install the update

Keep the original RC2 installer package and original installation records. The
updater checks them to prove the running installation belongs to that baseline.
If you followed RC2's published instructions, set:

```sh
RC2_PACKAGE=/usr/local/share/droiddeck-0.1.0-rc.2/droiddeck-source-candidate
```

If you extracted RC2 elsewhere, use that original root-owned directory instead;
it contains `package-manifest.json`. Do not substitute a different source archive
or rebuild the baseline to make verification pass.

Inside Ubuntu, run:

```sh
sudo python3 -I -B ./droiddeck-0.1.1-rc.5-update.pyz \
  install completed "$RC2_PACKAGE" --accept-existing-policy
```

`--accept-existing-policy` explicitly enrolls the existing DroidDeck maintenance
restart policy, preserving its original file. A different policy is refused.
Where the policy is absent, the updater creates the scoped DroidDeck policy.
Ubuntu security updates remain enabled; coupled Android services are recovered
together rather than restarted individually during package maintenance.

The first run verifies the original installation, enrolls the published public
release key and independent recovery service, then applies the signed gateway.
Verification can take several minutes. Leave the process running. Completion
reports `gateway-chain-installed`; rerunning an already healthy identical update
reports `gateway-chain-already-installed` without replaying it.

Open your usual DroidDeck HTTPS address and check video, sound and controls. The
address, certificate, browser-login preference and Android configuration stay the
same. No Ubuntu desktop login is required after reboot.

## Status and rollback

Inspect the current attempt:

```sh
sudo python3 -I -B \
  /var/lib/droiddeck/gateway-update-pilot/sources/runtime/portable/gateway-update-release.py \
  inspect-current
```

To explicitly roll back, copy the exact `current` digest from that report into
`UPDATE_DIGEST` below. Run the request once, then restart its independent updater:

```sh
UPDATE_DIGEST=EXACT_CURRENT_DIGEST
sudo python3 -I -B \
  /var/lib/droiddeck/gateway-update-pilot/sources/runtime/portable/gateway-update-release.py \
  rollback-request "$UPDATE_DIGEST"
sudo systemctl restart "droiddeck-update@$UPDATE_DIGEST.service"
```

Check `inspect-current` until it includes `rolled-back.json`. Rollback restores the
original RC2 gateway directory and verifies native Android, HTTPS and the complete
original installation. Android data and the independent GPU recovery service are
preserved. A rolled-back attempt is retained and cannot be reapplied as a new one.

## Later releases and interrupted updates

Download and verify the next signed `.pyz`, then run its `install completed`
command with the same original RC2 package. This pilot uses cumulative gateway
updates: it first returns the preceding attempt to the verified RC2 baseline,
then applies the newer target. Android remains installed throughout. Rollback
returns to RC2, rather than an arbitrary earlier target release.

An interrupted apply always rolls back; it never resumes forward blindly. On
reboot, recovery restores a pending original code pair before streaming and GPU
startup, then completes the ownership and health checks. Healthy updates persist
across reboot without an expiry timer.

If a command refuses a changed file or incomplete enrollment, retain all original
records and report the refusal. Do not delete journals, overwrite retained source,
rerun the clean installer or replay old GPU activation commands. Keep a private VM
backup as usual; the updater does not create or upload a backup of Android data.

## Pilot scope

- Gateway updates only; Android images, native/Java libraries and frontend are
  preserved. Changing the independent updater itself needs a reviewed migration.
- Manual release installation; no unattended download or update scheduling.
- Up to 32 retained cumulative release attempts per enrolled controller. Nothing
  silently deletes previous attempts or adopts replacement files.
- Tested Ubuntu/Unraid/VirGL configuration; other platforms need verification.
- Network-adaptive streaming, additional GPU acceleration and the Settings redesign
  are separate follow-up work. Edge/Apple TV investigations remain deferred.
