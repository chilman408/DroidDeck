# Settings and automatic quality: development source

The source in `updater/src/` now includes the reviewed five-section Settings overlay,
responsive video/AAC quality policy and persistent CPU Only / Automatic encoder
preference. Opening Settings keeps the existing player running. Automatic video
quality preserves resolution and selected ceilings; 320/450 kbps and 10 fps are
emergency automatic targets, while 6/10 Mbps are available in the manual menu.

This is development source. The RC17 download, installer, tag and checksums are
unchanged. These source changes are not yet an installable update and do not
update an existing VM by themselves. The signed private candidate has now been
installed and checked on three qualification VMs, including the original legacy
layout. Both actual video renderers and all eight Settings follow-up checks pass;
the disposable VM also recovered GPU encoding after reboot. Runtime source hashes
match across all three. Follow the existing RC17 installation guide for the
published build.

The generated-API browser test exercises the real shipped frontend controllers at
1280x720, 480x480, 640x480 and 390x844. Focused backend tests cover ownership,
CSRF/origin/revision checks, display handoff and quality traces. Native Linux
qualification also checks no-follow permissions and stale encoder telemetry.
These checks do not substitute for a Tesla road/reception test.


Startup presentation uses font-independent buttons and a centered DroidMate logo.
The logo fades out only after a successfully rendered Android frame, with reduced
motion support. Settings hooks wait for the real deferred Home/Shell scripts.

New effective video targets use versioned preference files. Existing targets and
selected ceilings are inherited without rewriting the predecessor files, so the
immutable CPU recovery runtime never sees unsupported 320 kbps or 10 fps values.
The private successor compiler and control sources are included for review. They
are development tools, not a released updater or a supported installation command.

Validation follow-up: This Display appears first; video and audio summaries appear
at the bottom. Connection/device values use consistent typography and wrap long
addresses. About/donation copy sits beside the illustrations. Corner icon ink is
slightly more transparent. Closed, non-sensitive encoding telemetry remains
readable under a restrictive service umask; unfinished writes stay private.
The qualified private candidate remains unpublished; the public RC17 installer is unchanged.


The next private candidate uses 28px corner icons in 39px buttons, with white ink
and dark background independently 49% transparent. Controls fade after eleven
seconds without interaction and return on input. Startup, open Settings and
keyboard focus keep them available. Reveal touches preserve the original Android
gesture and do not accidentally unmute; reduced motion is respected. This follow-up
is in private qualification and does not replace the published installer.
