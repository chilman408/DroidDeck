# Settings and automatic quality: development source

The source in `updater/src/` now includes the reviewed five-section Settings overlay,
responsive video/AAC quality policy and persistent CPU Only / Automatic encoder
preference. Opening Settings keeps the existing player running. Automatic video
quality preserves resolution and selected ceilings; 320/450 kbps and 10 fps are
emergency automatic targets, while 6/10 Mbps are available in the manual menu.

This is development source. The RC17 download, installer, tag and checksums are
unchanged. These source changes are not yet an installable update and do not
update an existing VM by themselves. A newly signed successor package, recovery
handoff, hardware execution, reboot and Undo verification are still required
before rollout. Follow the existing RC17 installation guide for the published build.

The generated-API browser test exercises the real shipped frontend controllers at
1280x720, 480x480, 640x480 and 390x844. Focused backend tests cover ownership,
CSRF/origin/revision checks, display handoff and quality traces. Native Linux
qualification also checks no-follow permissions and stale encoder telemetry.
These checks do not substitute for a Tesla road/reception test.
