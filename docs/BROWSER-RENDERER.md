# Browser H264 compatibility

The amended RC5 pilot removes the **Renderer** setting entirely. Normal visits
use the proven legacy H264 decoder automatically.

Both decoder paths receive the same H264 stream. Switching decoder does not add
apps, change Android GPU rendering, or improve the stream's configured resolution,
bitrate or audio features. WebCodecs can use a browser's native decoder, but its
behavior depends on the browser/device. It displayed a picture in our Edge tests
while the Tesla browser showed black screens. The pilot keeps the compatible
default and includes an explicitly requested compatibility test.

Old saved WebCodecs preferences and bookmarked `?video=webcodecs` URLs select
legacy automatically. No browser-data reset, Android reinstall or repeated reload
is needed. Other browser preferences and Android data remain unchanged.

The compatibility policies are part of the gateway update. They are appended when
the renderer manager, Settings controller and WebCodecs worker are served; installed frontend files and their original
ownership records remain unchanged. Rolling the gateway back removes the policy.

## WebCodecs compatibility test

Only when parked, append `?video=webcodecs-test` to your existing HTTPS DroidDeck
address. This is a diagnostic link, not a Settings choice or new default.

The candidate requests `no-preference` rather than `prefer-hardware`, checks the
actual H264 configuration with `VideoDecoder.isConfigSupported`, and holds incoming
packets within a fixed bound while support is checked. New stream sessions discard
old pending checks. This lets the browser choose hardware decoding when supported;
it does not prove that hardware was selected or that CPU use decreased.

An unsupported configuration, decoder error or no rendered frames within the
startup interval navigates once to `?video=legacy`, preserving the rest of the URL.
There are no preference writes or automatic trial retries. A healthy stream has no
testing expiry. A black image reported as a rendered frame may still require Tesla
investigation; this is not claimed to fix every GPU presentation issue.

Verification includes Windows and Ubuntu gateway/TLS/authentication tests, native
package reproduction and signature verification, and real H264 streams with old
saved preferences and query parameters. The development-preview backport also
passed actual desktop/mobile H264 and AAC playback without restarting Android.

The amended RC5 package remains a draft candidate until the Tesla test and its
additional configured RC2 apply/browser check complete. Desktop tests confirm
visible no-preference WebCodecs on both live streams and actual one-navigation
recovery to legacy when an unsupported configuration is injected. The Renderer row
is absent in the actual preview UI while other display controls remain available.
The previous updater-engine crash/reboot and
rollback evidence is recorded separately; it is not a new full acceptance run of
the amended package. See the release's `release.json` for its current status.
