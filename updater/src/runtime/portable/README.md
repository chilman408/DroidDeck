# Portable Components

## Reproducible Source Candidate

```sh
python3 runtime/portable/release-package.py build /absolute/repo /absolute/new-candidate.tar
python3 runtime/portable/release-package.py verify /absolute/repo /absolute/candidate.tar
```

The builder pins its reviewed `SOURCE_REVISION`, not HEAD or dirty checkout
files. Its receipt identifies that exact revision. It reads bounded regular committed blobs under fixed
runtime/tools/build/config roots, excludes binary inputs/private-key/data suffixes,
and admits only the approved logo/QR as artwork. No fetch, tool/revision override,
build-output bundling, service operation or image/APK download occurs. Git object
inputs are trusted source caches, never live VM data or credential-bearing repos.

Canonical uncompressed USTAR uses sorted entries, fixed modes/owners/mtime and
canonical per-file SHA256 metadata. This avoids zlib/zlib-ng compressed-byte
differences. Output must be new, never overwritten. Verification reconstructs the
ENTIRE archive from independently pinned Git sources; a rewritten self-manifest,
extra data, links or changed source cannot authorize a package. No extraction CLI
is exposed. An uncertain partial output remains for inspection.

The source candidate carries a fixed `droiddeck-install` dispatcher including
the experimental `--install-machine USER PRIVATE_IPV4 HOSTNAME` command and
explicit component operations. It requires a clean Ubuntu26.04/x86_64 target,
protected package source and an existing regular user. It composes35 operations,
but a command is not accepted for release until THAT exact candidate passes its
whole fresh-install/reboot/recovery/browser trial. Source manifests retain their
build-time false release flags. Post-test promotion never rewrites tested bytes;
separate bounded acceptance metadata identifies the accepted immutable archive.
No signatures, universal GPU/app support or redistribution clearance is implied.

Pre-registration recipe sources belong under `/root/droiddeck-installer-sources`.
The original preparation guard admits ONLY image-staging and waydroid-profile
under `/var/lib/droiddeck`. Do not put GPU recovery sources there before original
registration. `--stage-gpu-sources` publishes the fixed recovery tree separately
AFTER registration/runtime extensions. Both publications have independent
original intents, exclusive outputs and read-only verification; no adoption.

The finite unprivileged installation media primer verifies the new public TLS
certificate and drains H264/AAC sockets. It does not record media, inject input,
change global trust or impose an expiry on healthy runtime services. The root
default phase independently admits the active remote_submix route before mutation.

## Repeatable Registration Phase

Protected `install-registration.py --install-registration USER ASSEMBLY RECIPES -`
reads bounded configuration from stdin and composes the original owned payload/
nine-registration transaction, two session-recovery registrations and headless
reset registration. It never reloads, starts or enables services. The fixed
five-worker inactive gate and original ownership/source checks remain mandatory.
The parent-directory lock coordinates copies of this new entrypoint only; it
does not reserve the system against other root administrators or old CLIs.

After interruption use the SAME inputs with `--resume-registration`. Original
registration must already have its committed intent; the wrapper cannot adopt
or recreate pre-intent payloads. It verifies/resumes original registrations, then
prepares each genuinely absent recovery phase or resumes its original journal.
Existing empty/corrupt/foreign journals and conflicting targets refuse without
deletion or overwrite. Completed phases keep their original file objects.

This is the first composed installer phase, not a whole-machine installer. It
belongs BEFORE the browser/compatibility ownership extensions; do NOT run it on
the populated working .215/.90 installation or replay historical installation
helpers. Image preparation, build inputs, frontend/compatibility/TLS, live guest,
GPU activation and required defaults remain separate ordered integration phases.

## Combined Installation Defaults

The fresh-install workflow must finish its defaults phase with protected source
`installation-defaults.py --inspect` (exit0 only when Aurora AND the one-time
media AND lockscreen initialization pass). It admits the active browser music
route first and shares the generation-checked Android adapter across these checks. Inspection
does not install apps, create a journal or alter preferences.

`installation-defaults.py --initialize /absolute/protected/publisher.apk` composes
the existing install-missing Aurora policy with one-time media and lockscreen
initialization. Existing Aurora and subsequent user volume/security choices
remain unchanged on reruns.
Keep all sibling implementations alongside the entrypoint. If the app succeeds
but later initialization fails, preserve the app and journal; inspect/explicitly
resume that phase, then rerun the combined gate. No cross-phase rollback uninstalls
apps or resets preferences. This is one combined installer phase, NOT a released
whole-machine install/resume command or proof of TLS/GPU/browser acceptance.

## Required Lockscreen Default

Fresh Android starts with Screen Lock=None, not a swipe lockscreen at every boot.
Protected `default-lockscreen.py --initialize` uses only Android SDK36 user0
`locksettings set-disabled --user 0 true`, after a booted keyguard reports no
secure credential. It never clears/verifies/sets a PIN, pattern or password,
reads credential databases, suppresses SystemUI or dismisses a lockscreen to
simulate a successful boot. An existing secure lockscreen refuses initialization.

This reuses the protected journal primitive from default-media.py at the separate
`/var/lib/droiddeck/default-lockscreen` path. Original Android data identity and
synced intent/completion bind one-time initialization. `--inspect` never writes;
pending `--resume` confirms an already-disabled setting without replaying changes.
Once initialized, later choices to restore Swipe or add a credential are preserved
and remain valid; no boot-time default enforcement is installed. Browser optional
login is separate and unchanged. Keep source siblings together in protected input.

## Required Media Default

After the fresh browser audio capture route is active, run protected root-owned
`default-media.py --initialize` with its sibling `default-apps.py`. This sets only
Android music stream3 to its reported maximum (100%) and verifies Android saved
`volume_music_remote_submix` at that maximum. Browser gain remains unchanged at
its existing default100; headphone/call/alarm settings are not raised separately.

The root-private `/var/lib/droiddeck/default-media` journal binds initialization
to the original Android `/data` directory identity and reported volume range.
Intent is synced before the Android command, completion only after active AND
persisted route readback. Repeating a completed initialization preserves later
user choices, including mute. Never run a volume reset from service startup.
`--inspect` is read-only and returns2 if initialization is missing/incomplete;
it accepts later volume changes after recorded completion. An interrupted intent
requires `--resume`, which only confirms already-maximal persisted volume and
never replays the setter. Other pending/corrupt/foreign state requires inspection,
not deletion or automatic adoption. This phase is not a complete release installer.

## Required Default Apps

Aurora Store (`com.aurora.store`) is REQUIRED on every new DroidDeck build, not
an optional playback trial. Do not declare a fresh installation complete without
`default-apps.py --inspect` succeeding. This check fails with exit2 when absent.
Run the phase against the running fresh guest after baseline Android startup.

Acquire the pinned official Aurora4.8.4 release directly from the publisher:
`https://auroraoss.com/downloads/AuroraStore/Release/AuroraStore-4.8.4.apk`.
Expected9362660bytes/SHA256
`8a1ed9aa09631290da91cb793e0517b0f20dc70239ac94ae6682cd94f91a4bad`;
publisher signing certificateSHA256
`4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f`.
The APK is an external input, never bundled/mirrored with source. Retain its
license/source link. Signed Ubuntu `apksigner`/`aapt` are verification prerequisites.

From protected root-owned source, `default-apps.py --install-missing APK` checks
the root-private single-link artifact, exact bytes, signature and package/version,
then streams it to ordinary Android user0 package installation without replace,
downgrade, root-installer or permission-grant flags. Existing Aurora is inspected
and preserved, never updated/reset; a foreign signature fails closed. Guest
generation is checked before/after commands. No other app is modified. Installed
publisher signature and launcher resolution must pass before defaultsSatisfied.

Leave onboarding/login for the user, normal session installer, no automatic app
updates, no spoofing. Do not install Google Play Services or other system-app
updates through this default phase. Public browser/GPU/media tests alone do NOT
satisfy the separate required-default-app acceptance gate.

## Owned Registration Transaction (Not A Complete Installer)

`runtime-registration-transaction.py --install-new USER SOURCE RECIPES -` composes
the existing fresh-admitted owned-target copier with final root permissions and
the nine fixed service/PAM/tmpfiles registrations. Configuration is bounded stdin;
SOURCE is a freshly protected runtime/artifact assembly and RECIPES the separately
pinned, protected recipe sources. Execute only independently hash-checked root-owned
code from a protected directory, never sudo against a mutable checkout. No force,
target, journal, recipe or policy override is exposed. Existing targets refuse.

The owned-copy host guard admits only the original private root-owned preparation
state with exactly `image-staging` and `waydroid-profile` children. Existing runtime
state, extra names, nonprivate/replaced/symlinked directories refuse; `/run/droiddeck`
must remain absent. This namespace observation does not validate staging contents;
the profile plan independently holds and regenerates the three profile files.

The existing copier now returns its held read-only layout authority as `layout`.
The finalizer consumes its original ownership/absence context, verifies all250
owned files/four aliases/closed inventories, and stages nine files in their target
parents. Existing registration parents are never chmodded. Before changing either
private root to its reviewed final0755 mode or publishing a registration, it syncs
an immutable root600 intent in new root700
`/var/lib/droiddeck/registration-transaction`. The intent records the independently
derived plan hash and original owned/staged file and parent device/inode identities.
Only the existing no-replace rename primitive publishes service files. Original
sources, bytes/modes/objects and the fixed five-worker inactive gate are rechecked.

`--resume-registration USER SOURCE RECIPES -` is explicit, inode-bound recovery of
this committed registration intent, not adoption of an existing installation. It
independently regenerates the original placement authority and accepts each
registration only at its recorded staging OR final name with its original inode.
It can finish after process death or an interrupted parent sync, or verify the
already completed transaction without replacing file objects. Unknown/conflicting
objects, changed sources/accounts/parents, links, changed payloads or an incomplete
intent refuse and remain for inspection. All workers must remain inactive; the
command never stops, starts, enables or reloads services. Inactivity checks are
observations, not a reservation against other administrators starting services.

Recovery begins only AFTER complete private payload copying and committed intent.
An interruption before that commit still requires inspection and is NOT automated
whole-installer recovery. Original source/profile inputs must remain available.
The result distinguishes `registrationPublicationRecoveryVerified` from
`runtimeRecoveryVerified=false`: Android/GPU health/boot recovery, frontend/images,
TLS, effective units and fresh browser acceptance remain separate integration.
No healthy-runtime expiry, PNG fallback or account/DRM changes are introduced.
All installation/activation-ready flags remain false. Do not activate these
incomplete components on the working preview.

## Portable Headless Session Component

`runtime-live-guest.py --install-new USER ASSEMBLY RECIPES -` binds the original
owned-layout authority and committed object journal to current installed metadata,
NSS account and actual running Android data mount. It publishes only seven pinned
helpers at the current runtime's fixed compatibility path, with exclusive output
creation and no source/PID/data/hash overrides. The runtime supervisor must remain
inactive. Existing outputs/partials refuse and must be preserved; no recovery or
adoption CLI exists. This does not install a frontend/TLS or verify streaming.

Fresh sessions require the exact normalized Binder protocol baseline before
headless admission. The fixed `promote-images.py --waydroid-protocol-baseline`
command preserves the original file and refuses existing/changed generations.
It never starts services; do not replay an already completed exchange.

`runtime-session-recovery.py --install|--resume USER ASSEMBLY RECIPES -` adds the
two fixed failure-recovery registrations after verifying original owned install
objects. It has its own synced inode-bound intent and never reloads/enables/starts
services. A terminal headless failure queues a container restart to clear stale
Waydroid tracking; recovery is limited to two starts per300seconds. Healthy
sessions have no expiry. This is not GPU profile/allocator restoration or full
fresh browser installation verification.
After that original two-file registration, `--install-reset` adds the fixed
headless start-budget reset prestart with its own separate intent; `--resume-reset`
verifies/resumes that phase. Original recovery file inodes remain unchanged.
Both phases are required: restarting the container alone cannot clear an exhausted
headless start limit. The recovery unit's independent rate limit is not reset.

`node runtime/portable/headless-bundle.js build CONFIG WAYDROID_CONFIG NEW_OUTPUT`
prepares five payloads: a machine-bound entrypoint, unchanged accepted session
callback/host-adapter sources, a public hash-only policy and a notify-service
dropin. The CLI requires unprivileged Linux and a new output under the user's
private home. `verify CONFIG WAYDROID_CONFIG OUTPUT` checks the closed inventory
and regenerates expected bytes from local sources and the provided configuration/
profile, not a self-rewritten manifest. Only the Waydroid profile hash is copied.
No account data, TLS keys, credentials or images belong in this component.

The new entrypoint uses protected metadata generation, actual NSS UID/GID/home,
private desktop environment, exact Waydroid profile bytes and fixed accepted
protocol source pins. It preserves the SDK36 user callback, stable full-UI
property and systemd READY protocol. Unlike the historical fixed-UID wrapper,
it derives the runtime directory and account from the configured machine.
`--inspect` admits these inputs without importing Waydroid, starting a session,
changing Android properties or sending readiness. The normal session path is
fixed `/opt/droiddeck/headless-runtime/headless-runtime.py` under Python -I -B.

The generated notify dropin is inert in the bundle; no service registration,
reload, enable/start, LXC or Android operation occurs. No healthy runtime expiry
is added. This does not replace the populated .215/.90 setup. Protected component
copy/registration, complete GPU/allocator/boot/recovery graph integration and
actual new-entrypoint startup/fresh-browser acceptance still follow. Source
verification is a consistency check, not a signed installer release.

## Machine-Derived PID Startup Component

`promote-images.py --machine-pid-policy` is read-only: it validates protected
machine metadata/NSS via the existing pinned policy and derives a PID component
manifest for that configuration generation. The two executable scripts retain
their exact accepted size/hash pins. Only the small generation-bearing systemd
dropin changes; the recipe is checked against the existing JavaScript builder
and the accepted fixed-machine manifest pin. No new script generator executes.

`--machine-pid-compat SOURCE` copies a NEW component to fixed
`/opt/droiddeck/pid-compat`; `--register-machine-pid-compat` registers its one
dropin in a NEW fixed `/etc/systemd/system/waydroid-container.service.d` directory.
Both hold protected metadata descriptors/path identities and recheck exact bytes
and service inactivity through publication. Existing targets refuse; published
uncertainty is retained for inspection, not silently retried. Modified executable
bytes remain rejected even with a rewritten source manifest. No policy or target
override is exposed by CLI, and legacy PID modes retain their fixed defaults.

Neither command runs the startup script, rewrites LXC/PID settings, starts Android,
registers the automatic-session hook or reloads/enables/starts systemd services.
Private test callbacks do not claim NSS validation. Test only isolated root-private
destinations on the populated .215/.90 machines; do NOT replay install/register on
their live paths. Accepted GPU/allocator/headless/boot/recovery integration and
full fresh-browser verification still follow. Not activation-ready/released.

## Machine-Derived Service Registration

`promote-images.py --register-machine-services` registers the six base service,
PAM and tmpfiles records from protected `/etc/droiddeck/services` into fixed
`/etc` destinations. The expected manifest and metadata hashes are derived from
the protected machine policy, including actual NSS account validation. Held
metadata and source files are rechecked through publication; stale recipes,
changed generations, writable/linked metadata and existing targets refuse.
Partial publication is retained and reported as uncertain, not retried silently.

This opt-in mode retains the original registration vacancy checks and no-replace
file copier. It does NOT register the container automatic-session hook, reload
systemd, enable/start workers or perform Android operations. The original fixed
`--register-services` policy is unchanged. No destination or policy override is
available in the CLI. Private fixture callbacks are not account-validation proof.

Do not run registration on either populated development VM. Native tests use
root-private generated etc trees, including real pinned generator/NSS validation,
without changing the host registry. Generic PID startup and accepted GPU/allocator/
headless/boot/recovery integration remain separate; not activation-ready/released.

## Machine-Derived Protected Service Copy

The protected `promote-images.py --machine-service-policy` mode reads verified
root-owned runtime metadata, validates the current NSS account and regenerates
the expected service manifest from the independently pinned protected sibling
`service-plan.js`. It prints only public hashes/flags and changes no files.
The generator's existing LF-canonical pin is unchanged; CRLF source transports
normalize before pin admission, like the existing unprivileged service builder.

`--machine-services SOURCE` uses that regenerated manifest hash plus the held
protected metadata generation with the existing no-replace copier. It publishes
only a NEW `/etc/droiddeck/services` base component, not global registered units.
No register, daemon-reload, enable, start, network or Android operation occurs.
Existing/partial targets refuse, and changed metadata/policy refuses before
publication. Test-only validation never claims an actual NSS account check.
The original `--services` and configuration/registration modes keep their pins.
Only the opt-in machine policy admits the exact accepted repaired runtime receipt.

Root Linux only. First independently hash-check the helper/copier/generator in a
new root-owned protected directory; remove group/other write permissions from
the private source copy before execution. Never run sudo against mutable checkout
files. The current .215 and .90 already have services: do NOT replay installation
there. Native acceptance uses isolated root-private generated fixture directories
plus the real read-only policy command, not replacement of existing services.
Generic registration/PID and accepted GPU/allocator/headless/boot/recovery wiring
still follow. This base component is NOT activation-ready or a released installer.

## Per-Machine Install Profile

`promote-images.py --machine-gpu-profile-policy` now derives CPU/GPU configuration
hashes from the protected three-file fresh profile backup, normalized machine
metadata and real NSS rather than the old single-VM original.cfg hash. It verifies
accepted codec files read-only and emits hashes/flags only, with no service downtime.
`--machine-waydroid-gpu-profile` and `--restore-machine-waydroid-gpu-profile` reuse
the retained-file atomic exchange under held metadata/source guards and require
all Android/streaming workers inactive. Their destinations and input recipe are
fixed; no force/path/hash overrides. Legacy fixed-profile commands stay unchanged.

This changes the Waydroid configuration file ONLY, never starts/stops services,
regenerates Android properties, changes the saved-layer/allocator, or arms recovery.
Both displaced profiles remain for inspection and replay/foreign restore refuse.
Do not invoke modifying commands on populated .215/.90. Native evidence is from
root-private generated fixtures, not actual GPU activation. Full lifecycle wiring
must still coordinate allocator/saved-layer health recovery and headless admission
of both owned GPU and software configurations; the current single-hash headless
policy is not yet a complete GPU-fallback policy. All readiness flags remain false.

`node runtime/portable/unit-admission.js inspect ABSOLUTE_V2_PROFILE` is a
read-only, unprivileged Ubuntu/systemd base-session gate. It regenerates the
profile, checks real NSS, snapshots five fixed loaded services twice and rejects
stale/transient/masked units, unexpected fragments/dropins, altered identity,
generation environment, command flags, callback/stop behavior and missing declared
dependencies. The Python D-Bus collector loads metadata, never starts/reloads
units. Extended Exec*Ex properties retain privilege/ignore-failure flags; command
and environment contents appear only as hashes, not in reports. No arbitrary
unit, bus address, command or path override is accepted by the CLI.

Exit0 means baseSessionUnitsMatched only; exit2 is a well-formed mismatch report,
exit1 is failed/refused inspection. The gate expects the new headless notify
component and PID dropin, so the current historical GPU boot/recovery graph is
expected to refuse, not be rewritten. Ubuntu26 default90s unspecified start
timeouts/vendor fragment layout are currently expected; other host defaults
conservatively refuse. Implicit extra Requires/Wants/After edges are allowed,
while required edges must remain present and PartOf is exact. This is not a
complete service sandbox, source-ownership or protected Waydroid/GPU admission.
gpuRecoveryVerified/readyForActivation remain false even when settings match.
Future recovery/boot wiring needs its own explicit admitted graph; no current
service is changed to satisfy this preparation-only check.

Protected headless integration is a separate, new-only root operation:
`promote-images.py --machine-headless-policy` is read-only and derives the exact
accepted component manifest from protected machine metadata, real NSS and the
held Waydroid profile. `--machine-headless SOURCE` copies the five payloads plus
manifest to the fixed /opt/droiddeck/headless-runtime target after artifact,
frontend, runtime and PID staging. `--register-machine-headless` publishes only
its notify dropin under a NEW tesla-waydroid-headless-session.service.d directory.
Both modifying commands require the entire Android/worker graph inactive,
continuously recheck held metadata/profile, reject existing or partial targets
and preserve publication uncertainty. No force, destination or hash overrides.
They never reload, enable or start services, run the new entrypoint, modify the
container session hook, Android or the Waydroid profile. Existing by2/by3 isolated
inspection copies are not adopted. Do not run these modifying commands on either
populated VM; current evidence uses root-private generated fixtures only.
Boot/recovery wiring and effective-unit/new-startup verification still follow.

`node runtime/portable/install-profile.js build CONFIG NEW_OUTPUT` combines the
existing normalized runtime identity/network metadata, base service component
and PID-startup component into one private generation-bound profile. CLI builds
require unprivileged Linux and a new output below the user's private home.
CONFIG uses runtime-config.js's closed schema: username, UID, GID, private LAN
address and optional home, port and browser hostname. The hostname still defaults
to device.teslaandroid.com; no password, TLS key or Android/account data is accepted.

`verify OUTPUT` checks all thirteen payload files plus the final manifest. It
also regenerates service and PID recipes from the normalized configuration and
the local generator sources: changing files and rewriting their checksums does
not bypass the recipe/generation checks. Only the final synced manifest marks
successful preparation; existing/partial output is retained and never adopted.
This is a source-corruption/consistency check, not a signed-release verifier or
hostile same-UID security boundary.

`check-host OUTPUT` performs a read-only Linux NSS/getent check for the exact
configured username/UID/GID/home, then rechecks the profile for changes. It does
not create users, change groups or persist account validation into the profile.
Generation alone does not establish that another target machine has that account.
The profile has no credential, DNS, TLS-certificate or reverse-proxy provisioning.

All outputs remain installable=false/readyForActivation=false. These are the
existing BASE services, not a replacement for the accepted GPU/allocator/headless/
boot/recovery integration. The automatic-session container hook is merely staged.
Never copy this profile over the working VM or enable its units as a complete
installation. Generic protected installation and complete fresh-browser acceptance
still follow; this component starts no service and changes nothing under /etc.

`build-headless CONFIG WAYDROID_CONFIG NEW_OUTPUT` explicitly prepares version2
with the accepted headless-session component as well: nineteen payload files plus
the final manifest. Only the SHA256 of the supplied bounded regular Waydroid
configuration enters the profile; its raw bytes are never copied. Version1 base
profiles and the original build command remain unchanged. Both versions keep the
same false readiness/activation flags and new-directory/private-home requirements.

`verify OUTPUT` regenerates every component against the profile's stored public
configuration and Waydroid hash. It does not compare that hash with the current
host. `verify-headless OUTPUT WAYDROID_CONFIG` additionally hashes the supplied
file and rejects a mismatch or base-only profile; its ephemeral
waydroidProfileVerified=true means those supplied bytes matched, not protected
host admission or permission to activate. `check-host` still checks only NSS
identity; it never claims Waydroid admission. All command receipts stay non-ready.

The headless dropin is staged inside the component only. Protected no-replace
headless copying/registration use the separate fixed commands above. Accepted
GPU/allocator/boot/recovery wiring and actual new entrypoint startup remain.
No new entrypoint is executed by profile
preparation or verification, and no existing VM installation is replaced.

## Single-Command Artifact Build

`node runtime/portable/artifact-build.js build INPUTS_JSON NEW_OUTPUT` runs the
existing touch, API, diagnostic-display, GPS, logical-capture and Binder-library
builders in that order, as an unprivileged Linux x86_64 user. The JSON has exactly
seven absolute-path keys: `libwsRepo`, `apiRepo`, `gbinderRepo`, `glibutilRepo`,
`d8Jar`, `androidJar` and `scrcpyServer`. Use locally obtained publisher inputs;
their pinned revisions/tool hashes remain enforced by the existing builders.
The publisher scrcpy4 server is independently checked before any build begins.
No network/download, package installation, Android or service activation occurs.

The new output must be below the user's private home. Each builder keeps its
diagnostics under `NEW_OUTPUT/builds/`; these are NOT distribution contents.
Only `NEW_OUTPUT/artifacts/` is the closed eight-file payload plus its manifest.
Build evidence flags and actual artifact hashes must agree before collection.
Partial build directories are retained, never adopted, overwritten or replayed.
`node runtime/portable/artifact-build.js verify ARTIFACT_DIRECTORY` checks the
closed inventory, bytes and publisher pin without executing any artifact.

`node runtime/portable/artifact-build.js compare LEFT RIGHT` compares two disjoint
artifact directories using that same closed inventory, bounded reads and fixed
publisher pin. All eight payload size/hash records and six component receipt
digests must match; both bundles and original directory identities are rechecked.
The path-free result includes a stable comparison digest, not an installation
receipt. Whitespace/key order in manifest JSON do not affect comparison evidence.
This is read-only: no builders/artifacts/services run and no output files are
created. Matching copied bundles do not prove independent build execution;
`independentBuildExecutionVerified` and `installable` remain false. This does not
reconstruct absent build receipts, authenticate publishers, prove different
toolchain reproducibility, retain ownership, or provide an atomic two-tree snapshot.

`node runtime/portable/artifact-build.js reproduce INPUTS_JSON NEW_OUTPUT` runs
all six builders twice in new `first/` and `second/` workspaces, then compares
their closed `artifacts/` bundles with the same production pins. The outer
directory must also be new; existing or failed attempts are preserved, never
adopted or retried. Original workspace/artifact directory identities and the
closed outer inventory are checked across the build and comparison boundaries.
The path-free result reports two completed fresh build invocations and a
same-host comparison digest. This is not an independent environment/toolchain
attestation, publisher signature, installation receipt or recovery authority;
`independentBuildExecutionVerified` and all installation/activation flags stay
false. No separate accepted marker is published after a failed comparison.

This automates preparation, not privileged installation or legal clearance.
Manifest hashes are corruption checks, not signatures or hostile same-UID
authentication. Repeatability still requires the same compiler/JDK/SDK/sysroot;
the existing protected staging copier independently enforces its fixed byte pins.
Do not bundle Android images, account data, TLS keys, or the build workspace into
source checkpoints. This component does not make the portable installer released.

## Container PID Startup

Guest-only pre-mount PID compatibility is required by runtime discovery. The
historical helper default remains unchanged; closed portable mode selects the
reviewed `/opt/droiddeck` hook. `pid-startup.js` first validates installed privileged
metadata/NSS and its expected generation. `pid-bundle.js build CONFIG NEW_OUTPUT`
creates a separate three-file component, not a replacement of runtime source.

`promote-images.py --pid-compat SOURCE` publishes the pinned component into a new
protected directory. `--register-pid-compat` independently publishes a new
container dropin directory: only PID preparation ExecStartPre and bound metadata
generation. No automatic session Wants, enable, startup or sysctl during copying.
Existing/raced/partial output refuses; uncertainty is preserved for inspection.

.215 registration completed once; do not replay. Actual cold boot proved guest
pid_max65535 while host4194304 stayed unchanged; installed runtime discovery passed.
Workers are stopped again. Normal startup reapplies the hook after future offline
LXC regeneration. Runtime75-file baseline remains unchanged; this is a separate
pinned component. GPU/allocator, streams/TLS and first reboot/recovery remain.
See docs/checkpoints/pid-startup-20261014bb.md. Not a released installer.

## Guest Payload Installation

`promote-images.py --guest-payloads` derives the data path from protected/NSS-
validated runtime metadata and admits only a live Waydroid container whose PID,
birth time and actual data/local/tmp mount identities match held host directories.
Preview runtime must stay inactive. Only seven pinned existing native/Java/launcher
files are independently copied into a new private guest directory, with an empty
lib64 for the already-static native binaries. No rebuild/download, application
state copy, helper startup or browser PNG fallback occurs during installation.

Source and destination generations/hashes/modes, exact inventory, actual mount
identity and no-replace publication are checked before/after the rename. Existing
or partial stages refuse; uncertain published output is retained for inspection.
No CLI PID/path/source/pin override. Input binaries and publisher Jar remain external
and never enter source-only checkpoint archives. Generated fixtures use private
injection only. Native command timeouts report incomplete/uncertain state safely.

.215 installation completed once; do not replay. Actual mounted hashes/modes and
independent source/guest inodes match; runtime artifact preflight passes. Three
bounded invalid-argument probes proved native loading without listeners/devices.
Android/session/compositor stopped cleanly afterward; GPU allocator/profile,
functional Jar/input/capture/audio/media and first reboot/recovery remain.
See docs/checkpoints/guest-payloads-20261014ba.md. Not a released installer.

## Recoverable First-Boot Profile

`promote-images.py --waydroid-baseline` preserves the pinned original config and
deterministic software/GPU candidates in a new root-private recovery directory,
then atomically exchanges the active config with the software codec4/RGBX/AAC
baseline. The displaced original is retained too. No arbitrary paths/pins, Android
data changes, service activation or implicit GPU switch. Existing backups/partial
files refuse replay. Hashes, held generations and ownership/modes gate exchange;
an exchange race or uncertain sync preserves displaced content for inspection.

`--restore-waydroid-baseline` accepts only the saved baseline, including its exact
known package-generated AIDL3 protocol augmentation, and restores original bytes.
All Android workers must be stopped; unknown config/backup changes refuse. This
restores configuration only, not Android data or generated properties. Explicit
offline `waydroid upgrade -o` regeneration is required before the next startup
after any profile transition; it is not an image download or initialization replay.
Do not run it blindly against active/unknown state. Observed package behavior and
exact pins are saved in docs/checkpoints/waydroid-first-boot-20261014az.md.

.215 profile installation is complete; do not replay it. Android completed a
private software-rendered baseline boot after offline property regeneration;
the real data mount is now known. Workers are stopped/disabled again. Host GL was
proved separately, but Android GPU/guest helpers/capture/media/recovery remain.
The GPU candidate is saved, not activated; no released installer claim.

## Fresh Codec Overlay

`promote-images.py --codecs` generates only the accepted AAC preference and early
software-decoding/RGBX init files. It verifies the pinned publisher vendor image
through a held descriptor, uses read-only debugfs inspection of its codec/init
metadata, and checks the original fresh Waydroid configuration without changing it.
Four Android/worker units must be inactive. A new vendor/etc tree is published
atomically/no-replace with root755 directories/root644 files and a closed receipt.
Existing overlay content, changed metadata, links or raced targets refuse; partial
or uncertain publication is preserved, not blindly deleted/retried. No arbitrary
source, target, policy or vendor override is available in CLI. Native generated
fixtures use private API overrides, not production paths.

The .215 installation succeeded once; do not replay --codecs. The actual installed
compositor separately proved host VirGL/Radeon GL and stopped cleanly, disabled.
Android itself never started, so decoder selection, guest rendering, playback,
capture and recovery are not yet proved. Codec4/GPU configuration, fresh session
allocator override and actual guest payload installation still precede activation.
See docs/checkpoints/codec-overlay-20261014ay.md. Not a released installer.

## Protected Service Registration

`service-bundle.js build CONFIG NEW_OUTPUT` reuses the pinned service plan to
generate exactly seven bounded, secret-free files. `verify OUTPUT` checks the
closed inventory and bytes. This is a noninstallable component, not activation.

`promote-images.py --services SOURCE` copies the pinned bundle into a new
`/etc/droiddeck/services`, bound to installed metadata and actual NSS identity.
`--register-services` publishes six independent root644 files into fixed `/etc`
locations without replacing existing files: four disabled workers, PAM and
tmpfiles. The Waydroid container hook stays staged, not registered, because it
would start the unfinished Android session on container restart. Held descriptors,
generations, hashes and no-replace publication protect every copy. A partial
publication is uncertain and retained for inspection, never blindly replayed.

Both operations completed on .215; do not replay. Explicit daemon-reload and
native unit verification passed; all four workers remain disabled/inactive/PID0.
No enable, start, tmpfiles activation or Android operation ran. These pins bind
the current disposable host/component, not a general released installer. Codec
overlays, private session, actual guest-data payload placement and boot/recovery
verification remain before activation. See service-registration-20261014ax.md.

## Protected Configuration Installation

`promote-images.py --configuration SOURCE` normalizes a secret-free identity/network
record with the existing schema and verifies actual NSS user/UID/GID/home. Only
independently hash-checked protected metadata modules execute, under a clean
environment; input is held bytes via stdin. New `/etc/droiddeck/runtime.json` is
published atomically/no-replace with root755 directory and root644 public metadata.
Existing/changed/linked/raced input/output refuses; uncertain published metadata
is retained for inspection. Private fixture validator is not a CLI override.
No workers/services, Android, DNS/TLS/proxy or accounts are provisioned by copying.
Hash-check root-private helper and copier sibling before use. The .215 metadata
installation is complete: don't replay it. Service/codec/session/guest payload
integration and boot/recovery still required; no released installer claim.

## Protected Production Dependencies

`dependency-bundle.js build ROLE SOURCE NEW_OUTPUT` reconstructs fixed gateway or
display production packages from an already verified unprivileged npm stage,
preserving license files and exact package bytes. No npm install, network or scripts.
Manifest/lock/inventory policy is fixed in CLI; `verify OUTPUT` checks exact bytes.
Temporary npm metadata, development packages, extra/private content and links are
excluded. Component receipts are not signatures or activation-ready installers.

`promote-images.py --gateway-dependencies SOURCE` and `--display-dependencies SOURCE`
publish new node_modules into the respective protected runtime package directory.
Exact source-code manifest and every package source file, separate LF Git-source
and CRLF npm-stage metadata pins, held descriptors/generations, input/output bytes,
root modes and atomic no-replace publication are checked. Existing targets refuse.
Marker `node_modules/bundle-manifest.json` is intentional metadata, not a package.
The stage-only npm receipt inspector is not an installed augmented-tree verifier.
Root helpers must be independently hash-checked in a protected directory first.
No worker/package code, Android operation or service activation runs during copy.
Each role commits separately; inspect partial/uncertain states without blindly
retrying or deleting a published role. Both .215 installations are complete: don't
replay them. Guest payloads/codecs/config/services and boot/recovery remain.

## Protected Runtime Source

`runtime-bundle.js build REPO NEW_OUTPUT` reads a closed75-file list from the
saved source revision, not mutable checkout files. No network/lazy fetch/Git
replacement objects; no CLI revision override. `verify OUTPUT` checks exact
inventory and hashes. Private state, tests, temporary tools, dependencies and
external binary/image artifacts are excluded. New output only. Manifest is a
noninstallable source component, not a signed release or license-clearance receipt.

`promote-images.py --runtime SOURCE` installs the independently pinned component
as a new `/opt/droiddeck/runtime` alongside existing protected artifacts and the
accepted frontend. Fixed CLI policy, held descriptor/generation/hash checks,
root755 dirs/644 files and atomic no-replace publication. Existing targets refuse;
uncertain published output is retained for inspection. Fixture overrides private
API only. Never execute a helper from mutable source with sudo; independently
hash-check it and the copier sibling in a root-private directory first.
No worker, Android, network, dependency install or service activation occurs.
The .215 source installation is complete; do not replay it. Dependencies,
guest payloads/codecs/config/services, GPU credential proof and first boot/recovery
are still required before this is a complete portable runtime.

## Protected Frontend Installation

`promote-images.py --frontend CANONICAL_SOURCE` copies the independently pinned
33-file public frontend component into the existing protected `/opt/droiddeck`
artifact skeleton. The manifest and skeleton receipt are fixed policy pins;
there is no CLI target/pin override. Exact bounded inventory, held descriptors,
byte hashes, generation checks and no-follow paths exclude extra/private content,
links, hardlinks and replacement. Explicit directory755/file644 modes make the
public component readable without weakening private guest payload permissions.
Publication is atomic/no-replace; existing or raced targets refuse. Exit2 means
published state needs inspection, not blind retry or deletion. Root Linux only,
after installing and independently hash-checking the helper and its copier sibling
in a protected directory. Native generated-content tests are
`promote_frontend_test.py`; no services or Android operations run during copying.

The .215 installation is already complete; do not replay it. It is not a released
installer or activation-ready runtime. Runtime code/dependencies, guest payloads,
codecs, configuration, service installation and first boot/recovery remain.

## Protected Runtime Artifact Staging

`promote-images.py --artifacts ROOT_STAGE_NAME` independently copies the fixed
eight-file stage, arranges private guest payloads and host Binder libraries, and
publishes a new `/opt/droiddeck` artifact skeleton with renameat2 no-replace.
Existing roots refuse, even a raced empty destination. Library aliases are fixed
relative links; no global loader path/cache is changed. Native guest files700,
JARs600, library files644/parent755, guest directory700. Receipt and all bytes are
verified and synced before publication; post-rename uncertainty is never deleted.
This is not a complete runtime: source, frontend, dependencies, guest payload
installation, codec overlays, configuration and services must still be installed
and verified before activation. Never execute incomplete services or replay this
on the working VM. The protected copier sibling must match its fixed source pin.

`stage-images.py --artifacts SOURCE NEW_STAGE_NAME` reuses the protected copier
for eight independently fixed byte/size pins: the reproducibly built native
helpers, GPS/capture DEX JARs, private Binder library pair and publisher scrcpy4
server. Input is a flat unprivileged collection; build receipts cannot override
pins. Output is a new root-private directory under
`/var/lib/droiddeck/artifact-staging`, files600, with a receipt published last.
No execution, system library installation, Android operation or activation.
The artifact receipt explicitly says installable=false/readyForActivation=false.
Artifacts stay external on the disposable VM, never in source mirrors. This
same-toolchain pin set is not a cross-toolchain release or license clearance.

## Protected Image Staging

`promote-images.py ROOT_STAGE_NAME` reuses the independently byte-pinned copier
from a hash-checked protected root-owned sibling. Fixed production source parent
and `/etc/waydroid-extra/images` target, Linux/root only. Copies a new private
sibling first, then publishes the entire directory using renameat2 no-replace.
Existing destination/config/stale initialized state refuse, including a raced
empty target. Late publication/sync state is uncertain and never auto-deleted.
No init or network inside the wrapper. See checkpointpreinstalled-images-20261013ao
for native19-test proof and actual fresh package initialization. Do not run this
on the working VM or reinitialize an already initialized disposable VM.

`stage-images.py SOURCE NEW_STAGE_NAME` independently rechecks the fixed extracted
Android16 publisher image pair and copies it into a new root-owned private stage
under `/var/lib/droiddeck/image-staging`. Linux root only; install/hash-check the
helper in a root-owned directory before execution, never sudo mutable source.
No caller receipt or CLI pin/parent override; exact sizes/hashes, held descriptors,
generation checks, private modes and receipt-last publication. Existing stages
are never overwritten. Exit2 means publication uncertainty or committed stage
whose receipt could not be printed; inspect before using, do not blindly retry.
This does not initialize Android, configure services or create a released installer.
See `docs/checkpoints/protected-image-stage-20261013an.md` for native proof and the
Waydroid arbitrary `init -i` OTA-overwrite warning. Images stay external.

Focused fixture tests: `python3 -I -B ROOT_PRIVATE/stage_images_test.py -v` after
independently verifying both root-owned source hashes. Root fixtures use generated
tiny images only. Windows runs policy tests, skipping Linux/root descriptor tests.

This is an offline reconstruction tool, not an installation or update package.
It does not configure Ubuntu, Waydroid, codecs, GPU, services, TLS or accounts.
Every manifest declares `installable=false` and `kind=frontend-component`.

With a local clone containing the revision in `config/source-lock.json`:

```sh
node runtime/portable/frontend-bundle.js build /absolute/upstream/frontend /absolute/new-output
node runtime/portable/frontend-bundle.js verify /absolute/new-output
```

The CLI always uses the locked revision, never HEAD or checkout files. No network
fetch is performed: Git lazy fetching is disabled and every transport denied;
missing objects and unsupported --no-lazy-fetch reject rather than download.
The module's explicit revision argument exists for bounded
test fixtures or deliberate source-lock migration; the production CLI has no
revision override. A valid manifest proves self-consistency, not publisher trust
or a cryptographic signature.

The consolidated text patch and two tracked branding assets reconstruct the
accepted browser frontend. Only UI assets and the upstream license are included;
Git metadata, home/runtime directories, credentials, certificates, Google/DRM
licenses and Android app data are excluded. Text changes are limited to project
patches and CRLF-to-LF normalization; binary bytes and UTF-8 BOMs are preserved.
Output is always a new directory. Failed builds remain inspectable and are never
installed. Verification rejects links, missing/extra/changed files, empty extra
directories, malformed metadata, Windows-unsafe paths and oversized artifacts.

Runtime portability, fresh-VM installation/recovery, external artifact provenance
and a signed update channel remain separate work. Do not expose this component
as an available update in the running UI.

## Runtime Configuration Foundation

`runtime-config.js` validates a separate secret-free identity/network record and
derives future service paths. It does not modify the current fixed runtime.

```sh
node runtime/portable/runtime-config.js inspect /absolute/runtime-config.json
```

Required JSON fields: `user`, numeric `uid`/`gid`, private IPv4 `lanAddress`.
Optional fields: `version` (1), `home` (/home/<user>), `port` (8443), `hostname`
(device.teslaandroid.com). No passwords, certificate contents or tokens accepted.
Account IDs below1000, noncanonical paths and wildcard/public endpoints fail.
IP last octets0/255 are conservatively excluded until subnet-aware preflight.
Public hostname metadata does not provision DNS or trusted TLS automatically.

`sudo node runtime/portable/tls-preflight.js inspect` reads protected runtime
metadata and checks its fixed local TLS paths without provisioning or starting
anything. Missing files return status=missing without reading secrets. Unsafe
ownership, links, bounds and races fail closed. A usable pair is only local
transport readiness: even verified host-store trust does not verify a proxy
route or Tesla browser trust. browserReady/proxyRouteVerified/installable stay
false. The existing trust verifier temporarily stages public certificates only,
never private keys, and removes its scratch directory. Do not use the inspect
command's exit status alone as installation acceptance. Supply your own trusted
certificate/proxy route separately; never copy a working VM's private key.

## Worker And Capture Foundations

`worker-config.js` verifies the actual worker UID and configured NSS account.
The root audio/video/compositor launchers accept configuration programmatically;
their normal CLI still uses the legacy runtime. Native capture cleanup, desktop
bus, private compositor identity and preference paths share the typed snapshot.
The compositor producer accepts an explicit expected UID only for a configured
launcher, while the legacy invocation still requires UID1000.

This is deliberately not activated through services or the protected opt-in yet.
Relay/helper producers and gateway paths now accept the same snapshot. The fixed
portable worker entry reads /etc/droiddeck/runtime.json with ownership/size/path
guards, validates UID/NSS, and dispatches only its closed registry. Configured
supervisor commands pin canonical metadata generation for their children; a
changed generation is rejected before lookup or launch. Legacy CLIs remain fixed.
Existing live deployments must not use these source files as replacements for
historically pinned artifacts.

## Startup Service Plan

```sh
node runtime/portable/service-plan.js inspect /absolute/runtime-config.json
```

This prints secret-free, immutable service/PAM/tmpfiles contents; it does not write
system files or enable/start anything, and declares installable=false. The plan
uses the accepted private PipeWire/GL compositor and unprivileged Android session,
with configured UID/GID/home/user bus. Headless preflight requires exact environment,
private sockets, an inactive/disabled desktop unit and vacant Waydroid session bus.
Root runtime and connection helper use fixed portable worker names. Metadata
generation is pinned in units; changing install metadata requires regeneration.

Artifact/dependency provenance, graphics/access preflight, stale socket recovery,
container/session restart integration and fresh-VM boot/recovery tests remain.
Do not install these generated files onto the working preview as an experiment.

## Dependency Receipts

After a separate, unprivileged production-only npm ci stage:

```sh
node runtime/portable/dependency-receipt.js inspect gateway /absolute/gateway-stage
node runtime/portable/dependency-receipt.js inspect display /absolute/display-stage
```

The inspector only reads fixed runtime package identities and records lock/SRI,
installed version, nonempty license-file presence and exact package byte inventory.
It rejects missing/extra/development packages, unsafe paths/links/hardlinks and
oversized artifacts. The current locked flat/unscoped dependency layout is
explicit; future layouts fail closed. npm handles actual dependency resolution
and tarball integrity, with install scripts disabled and development dependencies
omitted. Receipts are not signatures, legal compliance decisions or installers.

## External Image Inputs

```sh
node runtime/portable/image-inputs.js verify /absolute/original-archive-directory
```

Metadata-only config/portable-image-inputs.json pins the publisher's matched
community Android16QPR2 x86_64 GAPPS/MAINLINE pair. It forbids bundling/mirroring
the images. The verifier streams fixed files against published SHA256 values,
rejects links/hardlinks/oversize/changed inputs and reports only metadata. It
does not download, extract or initialize Waydroid, and never copies signed-in
app data, credentials or DRM/TLS material. This integrity receipt is not image
certification or a signature. Complete installation remains.

## Private Image Staging

```sh
python3 runtime/portable/extract-images.py /absolute/original-archive-directory "$HOME/new-image-stage"
```

The CLI is unprivileged Linux only. Output must not exist and must be below the
caller's home, without symlinked ancestors. Both publisher pins and single regular
ZIP members are checked before output creation. Bounded streams verify CRC,
length and extracted hashes; Linux directory descriptors anchor writes and
publication. Images are private0600 under0700 output. Source and output file
generations are rechecked before the metadata receipt is published last. Completed
files are rehashed through retained read/write handles; Linux publishes the verified
receipt inode via procfs rather than trusting a replaceable temporary pathname.

Before mkdir, Linux checks capacity through the held parent descriptor: each
image and the bounded64KiB receipt are rounded to filesystem blocks, with one
directory block, the configured byte reserve and sixteen spare inodes. Four
new inodes are budgeted (two images, receipt and output directory); pending and
final receipt names share an inode. Only unprivileged available blocks/inodes
count. Read-only, unknown-inode or inconsistent capacity observations refuse.
These checks do not reserve space or guarantee completion under concurrent use.
The non-POSIX development API retains a byte-only path check and claims no inode
observation; the production CLI remains unprivileged Linux only.

Failed stages are left for inspection without a success receipt. After receipt
publication, temporary receipt cleanup is best-effort and cannot turn committed
success into rejection. Receipts are metadata, not authenticated installation
authorization: a later installer must reverify images at its own trust boundary.
Private staging is not a defense against a malicious process running as the same
user before the first directory identity capture or after final verification.
This command does not download, initialize Waydroid or activate any services.

## Native Touch Build

```sh
node runtime/portable/touch-build.js build /absolute/public-libws-git "$HOME/new-touch-build"
```

This unprivileged Linux x86_64 preview recipe extracts fixed files from libws
commit8d2e2f2923d15baba6101d879fc9593ef4a0ef78 and tracked touch source, never a
live VM binary or checkout/HEAD. It keeps the upstream license and applies only
the two hash-pinned project patches; Git tolerates whitespace differences in
patch context because upstream uses tabs. It does not fetch from the network.
Missing Git objects reject with lazy-fetch and transport denial, never silently
populate a partial source cache. Unsupported --no-lazy-fetch fails closed.
Obtain the public source directly from https://github.com/tesla-android/android-external-libws.

Output must be a new private directory below caller home without linked ancestors.
Fixed bounded GCC/Git/readelf commands build a static x86_64 ELF executable and
run generated protocol/mapping/keepalive tests. The real uinput server is never
executed. Sources are rechecked and a metadata receipt is written last; failures
leave inspectable partial output without a success receipt. The retained warning
log contains upstream warnings; own source is separately checked with -Werror.

Two .215 builds with GCC15.2.0 and the same sysroot produced identical bytes.
This does not claim hermetic or cross-toolchain reproducibility. The receipt says
installable=false: not an installer, signature, license clearance or Android
Bionic production module, and not authentication against a hostile same-UID
writer. A future privileged installer must independently reverify artifacts.
No working VM binaries, app/account/DRM data or TLS keys belong in this component.

## Native API Build

```sh
node runtime/portable/api-build.js build /absolute/public-configuration-manager-git "$HOME/new-api-build"
```

This unprivileged Linux x86_64 component reproduces the existing required API
preview executable from tracked source, public httplib.h and the public license
at configuration-manager commit6e3707cbeafa4a9a29d1e77ab3ad0e793836ace2.
Obtain the source directly from
https://github.com/tesla-android/android-external-tesla-android-configuration-manager.
Checkout/HEAD and replacement refs are ignored. No network fetching is allowed;
missing promisor objects reject, and all Git transports are denied. Unsupported
Git --no-lazy-fetch also fails closed instead of weakening that policy.

Fixed bounded G++/readelf commands, private new-home output, strict syntax and
generated dimension/JSON/no-store tests, source rereads and last metadata receipt
follow the touch component's boundaries. No actual HTTP server or socket is
started: generated tests never call server main, and smoke checks use only invalid
arguments that exit before server construction. Two builds with the same .215
G++15.2.0/sysroot produce identical binaries, not a cross-toolchain guarantee.

The retained static glibc getaddrinfo linker warning is a runtime-compatibility
caveat, not suppressed or called warning-free. installable=false receipts are not
an installer, signature, license clearance or Android/Bionic/HTTP integration
acceptance. No live VM binaries or signed-in data were copied or replaced.

## GPS Java And DEX Build

```sh
node runtime/portable/gps-build.js build /absolute/publisher-r8-8.13.17.jar "$HOME/new-gps-build"
```

This unprivileged Linux x86_64 recipe compiles the existing reflection-only bridge
unchanged with fixed root-owned OpenJDK17, --release8, strict lint and no annotation
processors. It validates Google's published D8 8.13.17 SHA256 before executing a
private copy; no downloads, hash/tool overrides or ambient Java flags/classpaths.
Tool instructions and publisher location are documented in checkpointah; obtain
directly from Google Maven, retain license metadata and do not bundle the build tool.

New private home output only. Generated host assertions test numeric/null parsing
and invalid arguments, never Android services, UID stubs or real GPS operations.
Production DEX contains only the bridge, and the single-entry location-bridge.jar
uses a fixed input timestamp with standard JDK packaging. Javap and official D8
JAR reparse validate formats; two same-toolchain builds match class/DEX/JAR hashes.
Receipts publish last with installable=false after source/tool rereads.

GPS/API/touch source folders are checked for linked ancestors before capture and
again before receipt-time rereads, including generated tests and reviewed touch
patches. GPS D8 inputs must be canonical absolute paths; their ancestry is also
rechecked at the end. Same-byte inputs behind newly linked parents reject rather
than produce a success receipt. These checks are not a hostile same-UID security
boundary or authorization for a privileged installer to trust user build receipts.

D8's host-JDK standard-library input is for this Java8-only reflection source,
not proof of Android runtime/hidden-API compatibility. Fresh helper loading and
mock GPS delivery/cleanup remain gated on clean installation. No working preview
artifact, account/DRM/TLS state or location source was changed or copied.

## Logical Capture Java And DEX

```sh
node runtime/portable/logical-capture-build.js build /absolute/r8-8.13.17.jar /absolute/android.jar /absolute/new-output
```

The existing diagnostic capture/touch-calibration helper is rebuilt unchanged
with fixed JDK17, Android SDK Platform36 revision2 and accepted D8 8.13.17 pins.
Obtain SDK inputs directly from Google, retain the SDK license externally and
do not bundle them in source. Checkpointai records publisher metadata and hashes.
The builder performs no download and accepts no tool/hash/compile-flag override.

Strict Java8 Android bootclasspath compilation,15 invalid-matrix host tests and
three invalid main checks run without invoking Android APIs or valid operations.
Only the production class enters the single-entry DEX JAR. Javap/D8 reparse and
two-root artifact equality passed on the disposable VM. New private output only;
source/tool generation checks precede an installable=false receipt.

SDK compilation does not prove reflection-only hidden API compatibility, fresh
Android loading, capture bounds or valid touch calibration; clean installation
must verify those separately. Existing PNG output is diagnostic, never a browser
stream fallback. No helper deployment or working preview mutation occurs here.

## Private Binder Libraries

```sh
node runtime/portable/binder-build.js build /absolute/libgbinder-public-cache /absolute/libglibutil-public-cache /absolute/new-output
```

This unprivileged Linux recipe rebuilds libgbinder1.1.45 and libglibutil1.0.80
from fixed publisher commits. It selects regular C/header/build/license metadata
only, denies Git fetching, stages private sources and invokes upstream release
targets with fixed tools/flags. No make install/clean/test or package target runs;
the publisher caches are obtained separately, never mirrored with source backups.

The private pair has checked ELF64/SONAME/dependencies/no RPATH and relative
SONAME links. Generated dlopen/dlsym checks examine six symbols without calling
Binder functions. Sources, including the staged smoke test, are verified before
compile and receipt publication. Two-root builds match both library byte hashes.
Licenses remain with private source inventories; no legal clearance is implied.

Receipt installable=false. Matching artifacts do not verify Waydroid/Python Binder
loading, device/protocol compatibility or fresh installation/recovery; those are
separate fresh-VM gates. Neither library is deployed on the current working VM,
and no system-wide library path or loader configuration is changed.

## Diagnostic Native Display

```sh
node runtime/portable/display-build.js build /absolute/libws-public-cache /absolute/new-output
```

The diagnostic helper required by current runtime preflight has an offline static
build from tracked C and the accepted libws pin/two reviewed patches. This does
not add a PNG browser renderer or fallback. No real server, client callbacks or
Android capture is run; generated private producers test signature admission,
short/bad/failed output and the16MiB boundary, not full PNG validity.

An existing capacity bug was reproduced and fixed: at the maximum, the helper
now checks for EOF and rejects an extra byte/read error, instead of accepting a
truncated oversized capture. The source correction is not deployed to the live VM.
Two-root static artifacts match; strict own syntax, readelf and staged/source
generation checks precede an installable=false receipt. Public license and
upstream compiler warnings remain in private staging. No new download is needed.

Same compiler/sysroot only; guest loading and Android diagnostic compatibility
remain fresh-VM checks. The working browser keeps H264, without PNG fallback.

## External Scrcpy Server Input

```sh
node runtime/portable/scrcpy-input.js verify /absolute/publisher/scrcpy-server-v4.0
```

This read-only verifier pins the official4.0 server asset required by current
launchers/preflight: exact publisher URL, release asset ID,732226bytes and SHA256.
Download directly from Genymobile/scrcpy outside the verifier; keep release API
metadata and license externally. No network, policy/hash override, copy, extraction
or Android/server execution occurs inside verification.

Canonical absolute input, no linked ancestors, single-link bounded reads and
before/after file generation checks. Receipt excludes private source paths and
does not claim installation, redistribution, APK signature or full ZIP validation.
ZIP magic and pinned bytes are checked; fresh guest loading/H264/AAC integration
and protected root staging remain separate gates. Do not substitute a live VM Jar
or include this publisher binary in source-only checkpoint archives.

## Persistent GPU Permissions

```sh
node runtime/portable/gpu-access.js inspect /absolute/runtime-config.json
```

This Linux-only read-only inspector checks the configured NSS account and groups,
then fixed root-owned DRM character devices under /dev/dri. Mode eligibility is
reported separately for card/render nodes, gated by directory search permissions
with owner/group/other precedence, ignoring temporary desktop ACLs and
the inspector's root privileges. Missing groups appear as numeric device-group
requirements, not commands or permission to add arbitrary groups automatically.
No group, mode, session, driver or service settings are changed.

The receipt remains installable=false/gpuAccelerationVerified=false. Eligible
mode bits do not prove service supplemental groups, device sandbox access, adapter
pairing, GL acceleration, Android rendering or capture. The current bounded DRM
layout supports major226/card0..63/renderD128..191; other layouts fail closed.

The generated compositor and unprivileged Android-session system units now use
fixed SupplementaryGroups=render video, preserving configured primary UID/GID.
This avoids modifying the user's global group membership. The NSS-mode inspector
above intentionally does not count service-only overrides; installer checks must
validate named-group/device mappings and actual service credentials separately.
Root runtime and browser metadata helper do not gain extra GPU groups. Generated
units are still offline metadata, not activated services or a released installer.

```sh
node runtime/portable/gpu-service-access.js inspect /absolute/runtime-config.json
```

The separate read-only service preflight resolves the generated units' fixed
render/video groups through NSS and combines them with the account's ordinary
groups for predicted mode checks. It separately checks named device mappings
and inherited membership drift, even when added groups would hide that drift.
Missing/root/aliased mappings refuse; unavailable devices or permissions report
false. No group membership, mode, device, account, unit or service is changed.
Receipts omit group passwords/members. This snapshot is not activation authority:
actual service credentials, effective unit policy, adapter pairing, sandbox/ACL
access and GL/Android rendering still require separate fresh-install verification.

```sh
node runtime/portable/gpu-access.js inspect-self /absolute/runtime-config.json
```

The source startup guard checks this process's actual four UID/GID identities,
kernel supplemental groups and zero inheritable/permitted/effective/ambient
capabilities. Both DRM card/render mode eligibility and stable credentials are
required. Configured compositor/headless startup sources invoke it before socket
or bus operations. It never changes groups or opens GPU devices. This is not a
systemd MainPID, sandbox, adapter-pairing or rendering proof. The fixed release
bundle/promotion generation has not been advanced to these sources; do not deploy
individual files. Dedicated packaging and fresh-start verification remain required.
