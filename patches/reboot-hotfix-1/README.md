# Pilot Reboot Hotfix 1

Required update for **DroidDeck 0.1.0-rc.1**. This is a narrowly scoped source
patch, not a replacement installer, VM image, or general in-place upgrader.
The original rc.1 release assets and tag remain unchanged.

## What It Fixes

An extra shutdown during Android startup could leave the GPU guard in a previous
boot's `starting` state. The next boot treated that unfinished startup as expired,
restored the CPU profile and left Android stopped. Later reboots stayed stopped.
Configured Android systems could also exceed the original 60-second session limit.

The patch resumes only post-mutation GPU startup states on a new boot, checks
the original profile and native GPU rendering, and allows a bounded 180-second
Android session startup. The caller waits 210 seconds and single-use activation
is bounded at 450 seconds. Healthy services have no testing expiry. Uncertain
mutation, foreign state and failed activation are never silently replayed.

## Apply to an Existing rc.1 Installation

Keep the configured VM and its Android data. **Do not rerun the clean installer,
wipe Android, replay an old activation token, or delete recovery journals.**
Use the VM's normal administrative account and authenticated terminal connection.

1. Download `droiddeck-0.1.0-rc.1-hotfix.1.tar`, `INSTALL.md`, and `SHA256SUMS`
   from the [hotfix release](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.0-rc.1-hotfix.1)
   into one directory on the VM. Verify them before root extraction:

   ```sh
   sha256sum -c SHA256SUMS
   ```

   Continue only when every file reports `OK`. Obtain checksums through the
   trusted release page; this pilot is checksum-verified, not digitally signed.

2. Extract into a **new root-owned directory**:

   ```sh
   sudo mkdir -m700 /usr/local/share/droiddeck-reboot-hotfix-1 &&
   sudo tar --no-same-owner --no-same-permissions \
     -xf droiddeck-0.1.0-rc.1-hotfix.1.tar \
     -C /usr/local/share/droiddeck-reboot-hotfix-1 &&
   sudo chmod 600 /usr/local/share/droiddeck-reboot-hotfix-1/*.py
   ```

3. Keep the terminal connected and apply the repair once:

   ```sh
   sudo python3 -I -B /usr/local/share/droiddeck-reboot-hotfix-1/pilot-reboot-repair.py --apply
   ```

   The command identifies the original enabled watcher, retains journals and
   source backups, applies exact hash-pinned changes, and verifies native GPU
   readiness. If the original guard already restored the CPU profile, it obtains
   a **new** original-owned GPU transaction before patching, without reinstalling
   Android. An existing healthy GPU transaction is not reactivated.

4. Verify, then reboot normally and reconnect to the existing browser URL:

   ```sh
   sudo python3 -I -B /usr/local/share/droiddeck-reboot-hotfix-1/pilot-reboot-repair.py --verify
   sudo systemctl reboot
   ```

   Android should return automatically without an Ubuntu login. Check video,
   audio after a browser gesture, installed apps and existing account state.
   The VM's TLS certificate and browser address are unchanged.

For a **new pilot install**, complete the original rc.1 installation and apply
this hotfix **before the first reboot**. Do not substitute this TAR for the
original named source-installer TAR. There is no rebuilt full-installer release
in this hotfix.

## If the Repair Stops

Do not repeatedly run `--apply`. Preserve `/var/lib/droiddeck/pilot-reboot-repair-1`,
`/var/lib/droiddeck/reboot-hotfix-2`, original GPU transaction/admission journals,
and `/root/droiddeck-installation`. `--resume` skips completed repair steps but
refuses an uncertain started-without-complete admission or activation. Such a
failure requires inspection, not deletion/adoption of the journal or token replay.
The source-only updater also has its own explicit `--resume` operation.

Report selected redacted errors. Never upload these private journal directories,
Android data, VM disks, passwords, account tokens, or TLS private keys.

## Verification Scope

The configured pilot VM recovered with native VirGL rendering, intentional
codec4/RGBX/software media decoding, retained Android data-directory identity,
and an unchanged public TLS certificate. A normal reboot and a reboot deliberately
interrupted during Android startup recovered to `ready` at about 36 seconds and
35 seconds respectively, before any interactive guest login. Desktop and mobile
legacy H264 video and AAC playback passed afterward without saving screen/media
content. Focused Linux state-machine and durable-update tests passed.

This does not extend app/DRM support or the tested hardware scope. Optional
browser authentication remains off by default; keep the service on a trusted LAN.
The patch is GPL-3.0-only; see `COPYING`. Third-party components are unchanged.
