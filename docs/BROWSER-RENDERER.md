# Browser H264 compatibility

In Settings â†’ Display â†’ Renderer, choose **H264 (WebCodecs)** or **H264 (Legacy)**.
The choice is saved only in that browser. Automatic keeps the compatible legacy default.
A browser without the native decoding API keeps legacy playback.

Both choices receive the same H264 video and AAC audio. Switching decoder does not
change Android apps, GPU rendering, resolution or bitrate. WebCodecs uses the browser's
native decoder; actual hardware selection and CPU savings depend on the device and
have not been measured for this pilot.

## Tesla compatibility fix

The WebCodecs input normalizes redundant H264 Access Unit Delimiter (AUD) markers.
It preserves all encoded picture slices, timestamps and complete frame boundaries.
The isolated Tesla trial rendered 38 frames with no fallback and the tester confirmed
a normal picture. This fixes the observed startup DataError; it is not a claim of
universal browser compatibility or a long-duration soak test.

The decoder checks support before configuring, uses bounded packet/byte queues,
and invalidates pending work when a new session starts. On decoder failure or no
startup frames, it navigates once to legacy. Failure of a saved WebCodecs choice
also saves legacy for future visits in that browser. A healthy stream has no expiry.
An explicit `?video=webcodecs-test` diagnostic leaves saved preferences unchanged.

## Updates and verification

The policies ship with the gateway update; protected frontend inventories remain
unchanged. Runtime health and the signed target read the same release version record.
The builder rejects a mismatch before signing. A rejected RC6 attempt automatically
rolled back; that attempt and its recovery history remain preserved. The exact signed RC7 package passed in-place installation, both Settings choices,
video/audio and native GPU checks. Reboot acceptance is recorded in release.json.

See the release's `release.json` for exact acceptance and pending gates. Historical
updater-engine reboot/crash/rollback evidence is listed separately, not reassigned
to a newly built package. Use [UPDATE.md](../UPDATE.md) for installation and rollback.
