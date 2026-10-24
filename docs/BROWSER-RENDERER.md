# Browser H264 compatibility

The amended RC5 pilot removes **h264 (WebCodecs)** from the renderer menu.
**Automatic** and **h264 (legacy)** use the proven legacy H264 decoder.

Both decoder paths receive the same H264 stream. Switching decoder does not add
apps, change Android GPU rendering, or improve the stream's configured resolution,
bitrate or audio features. WebCodecs can use a browser's native decoder, but its
behavior depends on the browser/device. It displayed a picture in our Edge tests
while users reported black screens elsewhere. The pilot keeps the compatible path.

Old saved WebCodecs preferences and bookmarked `?video=webcodecs` URLs select
legacy automatically. No browser-data reset, Android reinstall or repeated reload
is needed. Other browser preferences and Android data remain unchanged.

The compatibility policy is part of the gateway update. It is appended when the
existing renderer manager is served; installed frontend files and their original
ownership records remain unchanged. Rolling the gateway back removes the policy.

Verification includes Windows and Ubuntu gateway/TLS/authentication tests, native
package reproduction and signature verification, and real H264 streams with old
saved preferences and query parameters. The development-preview backport also
passed actual desktop/mobile H264 and AAC playback without restarting Android.

The amended RC5 package remains a draft candidate until its additional configured
RC2 apply/browser check completes. The previous updater-engine crash/reboot and
rollback evidence is recorded separately; it is not a new full acceptance run of
the amended package. See the release's `release.json` for its current status.
