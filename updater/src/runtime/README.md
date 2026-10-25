# TeslaAndroid x86 runtime: first vertical slice

This tree starts the VM-native TeslaAndroid port without altering the working
vanilla ISO or the Raspberry Pi implementation. It is not an installable
TeslaAndroid image yet.

## Implemented here

- `patches/frontend-same-origin.patch` changes the pinned frontend's API and
  WebSocket endpoints to the origin serving the page. HTTP uses `ws:`; HTTPS
  uses `wss:`. The Tesla DNS name and Pi certificate are not required.
- `patches/frontend-fixed-display.patch` tells the frontend to retain the
  VM's real frame size instead of calling the Pi virtual-display resizer.
- `patches/frontend-browser-access.patch` adds Browser access to the browser
  settings panel, with a default-off login switch and user-chosen credentials.
- `api/` implements the read-only preview endpoints needed for bootstrap:
  `/api/health`, `/api/configuration`, `/api/deviceInfo`, and
  `/api/displayState`. Unsupported configuration changes return HTTP 501.
- `display/` sends actual PNG output from Android `screencap -p` as binary
  WebSocket frames at up to about two frames per second per viewer. It allows
  at most four concurrent viewers and releases each worker on disconnect.
  The pinned frontend's fallback image renderer can display these frames.
  This is a compatibility probe, not a smooth video or protected-content
  capture solution.
- `touch/` translates the pinned frontend's ten-slot text protocol to a
  Linux uinput multitouch device. It validates whole messages before sending
  events, releases contacts on disconnect, clamps edge coordinates, and
  accepts one controller at a time.
- `patches/libws-loopback.patch` confines the pinned WebSocket library to
  loopback. Apply it only to the isolated x86 build copy of libws. A future
  same-origin server must proxy `/sockets/touchscreen` to `127.0.0.1:9999`,
  `/sockets/display` to `127.0.0.1:9091`, and `/api` to
  `127.0.0.1:8081`.
- `patches/libws-case-insensitive-handshake.patch` accepts valid lowercase
  `sec-websocket-key` headers sent by standard WebSocket clients.
- `preview-gateway/` serves the pinned frontend and proxies its same-origin
  API, display, and touch routes to localhost ADB forwards in its default
  Windows test mode. An explicit HTTPS mode can run on Ubuntu
  in the Waydroid network namespace, reaching Android's loopback backends.
  Audio is opt-in for the Waydroid deployment described below; GPS remains
  unsupported. The default host-only preview does not enable audio.

### Waydroid Browser Audio

The disposable .90 VM has playback-only browser audio enabled with
`GATEWAY_AUDIO_STREAM=1` and protected `deploy-preview.js --enable-audio`.
Apply frontend patches in order: same-origin, fixed-display, browser-access,
dynamic-display, h264-stream, browser-audio, video-stability, browser-gps,
tesla-media, browser-renderer, video-settings, audio-gain, device-info,
audio-settings. The current migration also applies audio-delay, network-info,
legacy-backpressure-v2 and then `frontend-av-sync-signed.patch` after the
other current renderer patches. Signed A/V sync stays browser-local and defaults
to zero. Positive values hold audio; negative values hold encoded H264 video
(128 packets / 2.2 MiB maximum) so audio arrives earlier relative to the picture.
This adds intentional display/input feedback latency only in browsers selecting
a negative offset. It does not accelerate audio, alter Android or calibrate
physical lip sync automatically. Audio uses the
existing speaker gesture and Settings > Audio controls; no microphone is used.

`frontend-settings-cleanup.patch` follows signed A/V sync. It merges the
secondary browser role/settings into Display, using Primary Display / Secondary
Display labels while preserving `primary`/`rear` protocol IDs and role APIs.
VM Network/Device pages omit irrelevant Pi/peripheral placeholders; VM Network
does not fetch Pi Wi-Fi configuration. No shared preferences or playback code
are changed by this frontend-only cleanup.

`frontend-project-updates.patch` follows cleanup. This unreleased VM project
has no published update channel, so `fetchLatestRelease()` returns null and
the corner download badge stays hidden. It no longer checks the incompatible
upstream Raspberry Pi release feed. A versioned project-specific downloadable
release source must be implemented with portable release/update packaging;
this patch does not implement an updater or claim a new release exists.
New installation defaults are enabled and100% volume; existing login is preserved.

`audio-launcher.js` uses the SHA-pinned scrcpy4.0 jar and a distinct abstract
socket54410002, with video/control disabled. `audio-relay.js` runs unprivileged
in the Android network namespace on loopback9093 and requires host FFmpeg
(tested Ubuntu8.0.1-3ubuntu2). It shares one PCM-to-FLAC/fMP4 encoder for up to
four viewers. The supervised deployment keeps draining capture between viewers
to avoid Android audio-route churn; standalone tests retain on-demand behavior.
Fixed input probing limits are essential for prompt playback of silence.
Initialization is cached; old audio fragments are not replayed.

The gateway requires login when configured, same-origin audio WebSocket
upgrades and a live display session. Read-only viewers can listen; Sound
settings require the owner and per-session CSRF. Private atomic preferences
are `~/.config/teslaandroid-waydroid/browser-audio.json` (0600), with volume
0..150. Disabling audio, session expiry and login changes close streams.
The supervised gain path applies 0..150% to PCM before encoding and keeps the HTML
audio element at unity when the gateway advertises browserAudioGainSupported.
It adds no Web Audio graph. Paired flags and acceptance status are documented in
`docs/feasibility/audio-gain.md`; do not mix browser/relay versions.

Run `node --test audio-mux.integration.test.js` on the FFmpeg-equipped Linux
host for a bounded real-time silent-input startup check. Browser acceptance
uses `live-audio-smoke.js` through a fresh isolated localhost SSH gateway;
`--controls-only` and `--signal-only` separate tests when diagnosing. These
transient preview services do not survive a full VM reboot. See
`docs/RESUME.md` for deployment, rollback and user validation notes.

The pinned upstream revisions are in `config/source-lock.json`. Do not
inherit the Raspberry Pi product makefile: it assumes Pi kernel devices,
ARM prebuilts, Wi-Fi hotspot setup, and device-specific certificates.

## Checks

```sh
node tests/frontend-origin.test.js /path/to/pinned/frontend-checkout
cc -std=c11 -Wall -Wextra -Werror runtime/touch/touch_protocol.c \
  tests/native/test_touch_protocol.c -o touch-test
./touch-test
python3 tests/api-contract.test.py /path/to/tesla-android-x86-api
python3 tests/display-stream.test.py /path/to/tesla-android-x86-display
cd runtime/preview-gateway && npm ci && npm test
```

The four runner checks passed. The display test was rerun on 2026-10-02; it
uses a generated 1x1 PNG as a fake `screencap` result and checks WebSocket
framing with a lowercase `sec-websocket-key` header, a concurrent viewer, and
a reconnect. The API test checks the fixed display contract and 501 response.
The gateway's five routing tests
also passed, and its production dependencies had no reported npm audit
vulnerabilities.
The touch and display services also linked against the pinned, loopback-
patched libws source. Apply the case-insensitive handshake patch alongside
the loopback patch. That upstream library emitted two existing host compiler
warnings; the new services passed strict syntax checking. The three Android
35 x86_64 vendor modules and `libws.so` also built successfully in the
isolated `source-voyager` tree. The successful vanilla ISO in
`artifacts/first-iso` passed its SHA-256 check after the first module build.

On 2026-10-02, the disposable vanilla BlissOS VM passed a first live check
over paired ADB: the API returned HTTP 200 and reported the actual 1280x800
display; the display service delivered a valid 1280x800 PNG through a standard
WebSocket client; and Android InputReader classified the uinput device as a
direct multitouch touchscreen. A standard WebSocket client sent one controlled
tap, observed as down/up events on the new `/dev/input/event6`. The binaries
ran from `/data/local/tmp` with localhost-only ADB forwards, not from an
installed image. The host preview gateway then passed five routing tests and
a live same-origin check: the frontend loaded in Edge, rendered the VM's
Settings screen, and received a valid 1280x800 PNG through
`/sockets/display`. `/api/health` reached the guest, and
`/sockets/touchscreen` connected through the gateway. A headless Edge click
at `(1123,189)` in a 1440x900 viewport produced a guest touch down/up at
`(998,168)` on the 1280x800 uinput device, matching the expected scaling.
Smooth video playback has not been validated on this PNG preview path.
After a second isolated display-module build, two fresh Edge sessions also
received frames while the Codex preview tab remained connected. The guest
binaries still run only from `/data/local/tmp`; no new ISO was produced.

On 2026-10-03, the disposable Android 16 Waydroid trial passed an additional
private compatibility probe: the four API bootstrap endpoints returned JSON,
and the newer display binary delivered nonblack 1280x768 PNG frames to two
simultaneous viewers and after reconnect. A temporary mapping of the
prototype's synthetic evdev node let Android recognize a direct touchscreen;
its WebSocket handshake passed without sending any input events. This used
software rendering and container-local connections, not the frontend or a
LAN browser service. Test processes and device mappings were removed. See
`docs/feasibility/waydroid.md` for the library environment, hardware-rendering
failure, and remaining gates.

For a host-only preview after the guest binaries are running, forward their
three loopback ports with the paired ADB serial and start the gateway against
a pinned frontend checkout with both frontend patches applied:

```sh
adb -s GUEST_SERIAL forward tcp:18081 tcp:8081
adb -s GUEST_SERIAL forward tcp:19091 tcp:9091
adb -s GUEST_SERIAL forward tcp:19999 tcp:9999
cd runtime/preview-gateway
npm ci
node server.js /path/to/pinned/frontend-checkout 18080
```

Open `http://127.0.0.1:18080/` on the host. The gateway binds only to
`127.0.0.1`, checks browser Origin on WebSocket upgrades, accepts only local
backend targets, and serves only the frontend's app assets. Do not expose
this unauthenticated preview or its touch control to the LAN or vehicle.
On Windows with Edge installed, `npm run smoke:browser` checks that a live
frame and touch layer appear without sending a tap. Set `EDGE_PATH` when Edge
is installed elsewhere.

### Private Waydroid LAN mode

The disposable Ubuntu/Waydroid VM now serves the frontend at
`https://192.168.2.90:8443/`. Browser login is optional and defaults off.
Settings > Browser access > Require login enables it with a user-chosen
username and password. This is a temporary LAN-only test, not an installable or
reboot-persistent runtime. It uses a self-signed test certificate. Do not put
credentials or certificate keys in Git, use them in URLs, or expose this preview
to the internet. The existing Raspberry Pi/Synology route is unchanged.

The gateway CLI requires `GATEWAY_ORIGIN`, `GATEWAY_TLS_KEY_FILE`, and
`GATEWAY_TLS_CERT_FILE` for HTTPS. `GATEWAY_ACCESS_FILE` selects the private
persisted login state; by default it is
`~/.config/teslaandroid-waydroid/browser-access.json`. A missing file means
login is off; a corrupt or insecure existing file refuses startup rather than
silently disabling login. Passwords are salted scrypt hashes, not plaintext.
Usernames accept 1..64 ASCII letters, digits, `_`, `.`, `@`, or `-`; passwords
require at least 12 characters and at most 256 UTF-8 bytes. Disabling login
removes the saved credentials. `GATEWAY_BIND` sets the explicit
IPv4 listener; its port must match the origin. Set `GATEWAY_ANDROID_LOOPBACK=1`
only when running in Android's network namespace, to reach 8081/9091/9999.
The Ubuntu Node process runs unprivileged in that namespace and a user-owned
TCP relay forwards encrypted LAN traffic to it. Android's backend ports remain
loopback-only. When login is enabled, authentication is required for assets,
API, sockets, and access-setting changes. Saving access settings requires an
exact same-origin JSON request and a current CSRF token. Existing stream/control
sockets are revoked on changes. Foreign origins are rejected in either mode,
and gateway credentials are stripped upstream. With login off, anyone who can
reach the endpoint can view/control Android and change browser access settings.

Nine gateway tests pass on Windows and Ubuntu; two additional smoke-tool
regression checks originally brought the local Windows suite to 11 tests.
The optional-login update adds enable/change/disable/restart/revocation,
concurrent login, overload, corrupt-state, and smoke checks (16 tests passing
on both Windows and Ubuntu).
Hands-off Edge probes at desktop
and mobile sizes receive actual 1280x768 Android frames. The live API rejects
unauthenticated and foreign-origin requests. The touchscreen device/handshake
is verified, but no live taps were sent. Audio and GPS remain unsupported,
capture remains low-frame-rate software-rendered PNG, and this is not a
protected-content, video-playback, or Tesla-browser pass.

`live-browser-smoke.js` retains its default localhost HTTP check. For private
LAN HTTPS with login enabled, provide a test password file with
`GATEWAY_AUTH_FILE` and the chosen username with `GATEWAY_AUTH_USER`. With login
off, omit both. The probe restricts credentials to that HTTPS origin and explicitly ignores the test
certificate warning. `SMOKE_MOBILE=1` selects a
390x844 viewport and `SMOKE_SCREENSHOT_PATH` saves the test capture. No clicks
are sent. See `docs/feasibility/waydroid.md` for deployment state and teardown
constraints, especially stale guest processes and synthetic device numbering.

`node browser-access-ui-smoke.js /path/to/patched/frontend` tests the real
settings form against an isolated HTTPS gateway using test-only credentials.
It checks confirmation validation, enabling, disabling, and desktop/mobile
layout without sending clicks or Android input. This does not restart the
Waydroid container or validate the remaining resolution/audio/GPS controls.

`node live-browser-access-smoke.js https://PRIVATE_LAN_IP:PORT/` starts only
when login is off. It exercises the deployed settings form with temporary
random credentials, checks streaming and anonymous rejection, then restores
login off. The 2026-10-04 probe passed on the disposable VM, followed by a
gateway restart and another anonymous 1280x768 streaming check. This test
changes browser access briefly but sends no clicks or Android input.

To attempt an **isolated** Android build, place a clean copy of the pinned
`external/libws` repository in the Android source tree, apply the loopback
and case-insensitive handshake patches there (the pinned libws files use CRLF,
so `git apply --ignore-space-change` may be needed), and copy `touch/`,
`display/`, and `api/` to separate
`external/tesla-android-x86-*` paths. The API build uses the pinned
`httplib.h` from `external/tesla-android-configuration-manager`. Keep this
separate from the successful vanilla ISO and its resolved manifest. The runner
build used `BLISS_BUILD_VARIANT=vanilla`,
`lunch bliss_x86_64-ap4a-userdebug`, and
`m tesla-android-x86-touch tesla-android-x86-api tesla-android-x86-display`.
The binaries are not auto-started. The touch module requires a working
`/dev/uinput` node, permission and SELinux policy, and dimensions matching
the guest display. The API and touch commands are
`tesla-android-x86-api WIDTH HEIGHT` and
`tesla-android-x86-touch WIDTH HEIGHT`; its optional
`DEVICE_WIDTH DEVICE_HEIGHT LEFT TOP RIGHT BOTTOM` arguments pre-map logical
wire coordinates into a letterboxed physical viewport before uinput emission.
Waydroid uses these validated arguments and identity Android affine calibration:
Android rejects off-frame raw DOWN events before applying affine calibration.
The two-argument mode retains the existing identity mapping. The display service accepts an
optional `SCREENCAP_PATH` for testing.

## Remaining gates

### Dynamic Display Checkpoint

The `waydroid-display/` helper and `frontend-dynamic-display.patch` implement
logical Android resizing, verified synthetic touch calibration, bounded
rollback, private preferences and per-tab display authority. Apply frontend
patches in this order: same-origin, fixed-display, browser-access, dynamic-display,
H264-stream, browser-audio, video-stability, browser-gps, tesla-media,
browser-renderer, video-settings, audio-gain, device-info, audio-settings,
touch-lifecycle, navigation-floor, rear-display, network-info,
auto-h264-recovery, role-startup-recovery, legacy-default, supported-aspects,
legacy-backpressure-v2, audio-delay (the corresponding `frontend-*.patch` files).
Do not apply `frontend-legacy-backpressure.patch`: that earlier prototype is held.
The v2 patch installs a version-paired legacy worker, retains a bounded complete
GOP on the browser side, and allows only two outstanding encoded worker packets.
Per-ticket deadlines and fatal-worker handling permit one bounded recovery reload;
consumption acknowledgements do not themselves prove presentation or touch latency.
Serve both assets with `Cache-Control: no-store`, installing the worker first.

Audio delay is a per-browser preference, zero by default, adjustable from0 to750ms
in25ms steps. It keeps the direct HTML audio path and shared PCM gain unchanged.
Install playback before its guarded Settings control; no VM/runtime restart is
needed. The checksum-fenced `--audio-delay` profile preserves exact live settings
bytes, including any baseline differences from canonical patch replay.
The final canonical patch also guards repeated starts and respects media-session
pause through pipeline recovery. On the existing live install, the one-file
`--audio-delay-guard` profile uses its own `audio-delay-guard-20261011s6` backup.
To remove the feature, first roll back that guard with its protected s6 helper,
then roll back the base `--audio-delay` profile with its original protected helper.
No shared preference, volume, codec or Android app data is restored by either.
The live code-only deployment and exact rollback use the distinct protected
`legacy-backpressure-20261011p2` backup; no runtime restart or preferences restore.
On Windows, use a disposable checkout with `core.autocrlf=false` and normalize
patch-copy CRLF to LF before replay; do not change global Git configuration.
The experimental gateway opts in with `GATEWAY_DISPLAY_SOCKET` set to
`/run/teslaandroid-waydroid/display.sock`; without it the fixed preview remains
available. Helper state belongs under `/var/lib/teslaandroid-waydroid`, separate
from login credentials, with root-protected code under `/opt/teslaandroid-waydroid`.

Dynamic mode defaults on for fresh state, with measured density 180 on this
image. Dynamic aspect selection uses only 4:3, 1:1, 16:9, 9:16 and 3:4, choosing
the largest contained CSS picture in the available browser area before applying
the resolution preset. Exact ratios and even H264 dimensions are retained;
the pinned landscape navigation-height floor may increase internal resolution
without changing the selected ratio. Turning dynamic mode off retains the
committed shape. On 2026-10-06, real desktop/portrait/wide captures and overlay bounds,
two-tab dynamic off/on, view-only second tab and owner takeover all pass.
The 720p/480p/832p controls produce matching Android frames through the actual
frontend. This update is enabled at the existing private LAN HTTPS URL;
refresh the browser to load the new code. Saved login settings are unchanged.
The current deployment is reboot-persistent: private headless Weston and a
single Waydroid session recovered the public preview at55 seconds of VM uptime
without SSH or desktop login. See `docs/feasibility/headless-startup.md` for the
accepted boot check and reversible desktop-session migration. H264 and browser audio are enabled;
the user confirms smooth streaming and working audio. The video-stability patch
preserves idle frames and Settings-return rendering, bounds queued decode work,
and recovers only through H264, never automatic PNG fallback. Automatic browser
renderer selection now chooses the user-accepted legacy H264 path immediately.
Failure-triggered WebCodecs recovery was insufficient in the actual Tesla retest;
WebCodecs is now an explicit opt-in choice, never an automatic trial. Explicit
renderer choices remain unchanged; no preference writes are made at deployment.
See `docs/feasibility/browser-h264-recovery.md` for deployment and return checks.
The final review fixes add bounded touch cleanup after failed recovery,
correct active-contact releases and reconnection after cached browser-history
restoration. Regressions pass and the installed package passed live sizing,
dynamic switching and two-tab ownership acceptance again.
See `docs/RESUME.md` for the checkpoint, tests, backup and next milestone.
Do not blindly repeat the first-install `deploy-preview.js --prepare` script
over an active helper or run competing public/isolated coordinators.

### Browser GPS

The optional `GATEWAY_GPS_SOCKET=/run/teslaandroid-waydroid/gps.sock` bridge
uses the private root GPS helper and the existing display owner's session/CSRF.
`deploy-preview.js --enable-gps` retains dynamic sizing, H264 and audio and
adds this capability. GPS defaults off; the existing Settings > GPS switch
enables it. Only the controlling browser requests location, after the native
feed is ready. Disable, disconnect, revocation and expiry remove the owned
provider. Preferences store only the enabled boolean; coordinates are neither
saved nor logged. One latest pending fix bounds slow delivery.

Waydroid's current image has no GNSS HAL. This bridge honestly uses Android's
mock GPS provider; apps that reject mock locations may not accept it. It
does not supply satellite/NMEA data or hide the mock-location flag. Browser
location permissions, HTTPS trust and Android's Location master switch still
apply. The root helper holds an exclusive abstract-socket lock through cleanup.
Its private lease journal lets a restart remove only its own stale provider;
failed cleanup blocks another feed rather than removing a foreign provider.

`live-gps-browser-smoke.js` uses separate test-only credentials and synthetic
coordinates on the isolated SSH preview, never the user's login. During its
bounded readback window, `live-gps-browser-readback.js` independently asserts
Android received that fixture. Stop the public coordinator before running the
isolated preview, then stop the isolated one before restoring public access.

The VM still needs a guest-native same-origin server and reverse proxy, a
production capture/encoder integration, GNSS feed, and robust touch
mapping across other viewport sizes and orientations.
The Pi V4L2 display implementation cannot simply be reused on Virtio(3d).
The live `getevent`, `dumpsys input`, and single browser tap checks passed;
multi-touch, rotation, resize, and disconnect behavior still need live checks.
The API returns 501 for Pi-only settings, and the preview frontend must not
be represented as feature-complete. Do not route these unauthenticated
control endpoints to the public internet.
The separate private GApps prototype currently crashes and is not part of
this runtime slice.
