# Signed pilot update: 0.1.1-rc.16

For an existing working RC2 installation or an older signed gateway candidate.
This update preserves Android apps, accounts, configuration and HTTPS identity.
This cumulative update includes Settings → Updates, faster Automatic quality response,
bounded live audio recovery, AAC-LC audio by default, the Settings control cleanup,
and DroidDeck Audio metadata/artwork. It retains both browser-local renderer choices
and the Tesla-tested AUD compatibility fix.
Automatic still uses legacy. See [browser compatibility](docs/BROWSER-RENDERER.md).

Use Ubuntu 26.04.1 x86_64 with the accepted Unraid KVM/VirGL setup. For a new VM,
install the separate RC2 clean installer first. Never run a clean installer over
an existing signed-in Android setup. Require `release.json` acceptance before use.

## Download and verify

Download `droiddeck-0.1.1-rc.16-update.pyz` and `SHA256SUMS` from the official
[DroidDeck pilot release](https://github.com/chilman408/DroidDeck/releases).
The `.pyz` is the complete updater: it contains the payload, public source archive,
release statement, detached signature and installer. It needs no separate hotfix.

Check the downloaded file against the release's published checksum:

```sh
sha256sum -c SHA256SUMS --ignore-missing
python3 -I -B ./droiddeck-0.1.1-rc.16-update.pyz verify
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
sudo python3 -I -B ./droiddeck-0.1.1-rc.16-update.pyz \
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

## Update from Settings

After this initial signed updater installation, open **Settings → Updates**:

1. Check the installed version and select **Check for updates**.
2. Select the offered version, then confirm **Install**. The picture can briefly
   disconnect while the gateway is replaced. Apps and Android data stay intact.
3. Keep the page open or reconnect to your usual DroidDeck address. The independent
   helper retains the operation and reports its progress across browser reloads.
4. To restore the original gateway, select **Roll back update** and confirm.
   Completion requires the original recovery verifier, rather than service startup.

Only the primary display can check, install or request rollback. Updates uses the
same browser access settings and display-session protections as other shared
Settings. Browser login remains optional; no Linux or VM password is entered in
the Updates page. The helper accepts only official DroidDeck pilot releases and
verifies the existing Ed25519 signature before executing downloaded code.

Rollback restores **the original RC2 gateway**, not the immediately preceding
candidate. That original version may not include Updates in Settings; reopen the
usual DroidDeck address to use it. The independent helper and recovery records
remain retained. Install a strictly newer signed updater using the command above
to return to the newer interface. A rolled-back version is consumed and is not
advertised for reinstallation. Do not delete its protected history.

The command-line status/rollback interface below remains available for diagnosis.

## Automatic quality

In **Settings → Display**, select the desired bitrate and frame rate, then enable
**Automatic quality**. It is off by default. These selections are upper limits.
The shared encoder responds only to the current primary display's delivery
feedback; a slow secondary viewer cannot lower quality for everyone.

Sustained moderate congestion lowers bitrate at intervals of at least six
seconds. Severe backlog can cut bitrate more sharply at intervals of at least
four seconds and cap the target at 30 fps. These are controller intervals, not
a guarantee that every connection recovers within that time. At the minimum
bitrate, higher frame rates can reduce toward 30 fps. Sustained healthy active delivery restores quality gradually, with at
least 45 seconds of healthy evidence per increase. Quiet screens and missing
feedback do not count as recovered bandwidth. Automatic quality never changes
screen resolution, Android volume or the chosen H264 decoder.

Choosing a bitrate or frame rate manually turns Automatic quality off. Turning
its switch off restores the selected upper limits. The mode and limits survive
restarts. Connection loss or primary-display handoff discards prior measurements.
The existing encoder must reconnect while a target changes, so brief pauses can
occur. This is a conservative delivery-feedback pilot, not a guarantee of smooth
playback on every network or vehicle browser.

The last effective bitrate/frame rate remains in the original encoder settings
format. Rolling back to RC2 stops automatic adaptation; its older interface can
retain those last effective manual targets. Select the desired manual values
before rollback if needed. No media content or network trace is recorded.

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

Use Settings → Updates for later official releases. For administrator-managed
installation, download and verify the next signed `.pyz`, then run its
`install completed` command with the same original RC2 package. This pilot uses cumulative gateway
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

## Browser audio and Settings

**Settings → Audio → Audio format** offers AAC-LC at a target of128 kbps stereo
by default, with FLAC(lossless) as a manual browser-local option. AAC reduces
network usage compared with lossless audio; transport and container overhead are
additional. The FLAC option adds `?audio=flac`; choosing AAC removes that override.
An existing FLAC URL remains explicit. Unsupported or unsuccessful AAC startup
can fall back to FLAC. No Android codec, account or DRM change is required.

Audio keeps bounded live queues and discards stale sessions when a connection
stalls, so reconnection starts from the current stream rather than minutes of old
audio. Poor links can still cause audible gaps. This is not a promise of seamless
playback on every network. The existing volume and A/V sync preferences are kept.

The Automatic quality circular control now sits above Video bitrate. Audio format
uses the same dropdown style as the other controls and sits below Browser audio.
The media title is **DroidDeck Audio** and artwork uses the DroidDeck logo. Browser
or vehicle media-card caches may keep earlier text/artwork until the page reloads.

User-created secure lockscreens and private apps remain supported. Lock-screen
None is a fresh-install default, not an ongoing requirement; this gateway update
never clears a PIN or changes an existing Android credential.

## Pilot scope

- Gateway updates only; Android images, native/Java libraries and frontend are
  preserved. Changing the independent updater itself needs a reviewed migration.
- Explicit browser or command-line installation; no unattended update scheduling.
- The first signed update installs a separate root-owned UI helper. The original
  privileged updater/recovery authority is retained; replacing that authority or
  migrating an existing helper protocol requires separate reviewed work.
- Up to 32 retained cumulative release attempts per enrolled controller. Nothing
  silently deletes previous attempts or adopts replacement files.
- Tested Ubuntu/Unraid/VirGL configuration; other platforms need verification.
- Automatic quality and the audio/Settings improvements above are included.
  Additional GPU video encoding and a larger Settings redesign remain future work.
  VirtIO 3D Android rendering and intentional software codec4/RGBX/AAC Android
  decoding are retained. No passthrough GPU or replacement VM is required.
- Legacy previews without original RC2 installation records are not eligible for
  this updater. Do not fabricate enrollment or run the clean installer over them.
- The exact signed RC16 update was installed and checked in the supported
  environment, and the user accepted the Settings/media changes. Earlier releases
  supplied reboot/recovery coverage; a new RC16 reboot was not performed for this
  browser/gateway cleanup. Edge/Apple TV investigations remain deferred.
