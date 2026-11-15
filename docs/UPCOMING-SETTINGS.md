# Settings and automatic quality: development source

The source in `updater/src/` now includes the reviewed five-section Settings overlay,
responsive video/AAC quality policy and persistent CPU Only / Automatic encoder
preference. Opening Settings keeps the existing player running. Automatic video
quality preserves resolution and selected ceilings; 320/450 kbps and 10 fps are
emergency automatic targets, while 6/10 Mbps are available in the manual menu.

This is development source. The RC17 download, installer, tag and checksums are
unchanged. These source changes are not yet an installable update and do not
update an existing VM by themselves. Private successor qualification is in progress. One portable handoff and a
saved CPU-only reboot have passed; the startup-order correction, repeatable
recovery and the final all-VM rollout are still being verified. Follow the existing RC17 installation guide for the published build.

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
