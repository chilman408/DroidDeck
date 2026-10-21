# Pilot Checklist

## Before Installing

- Use a clean disposable Ubuntu 26.04.1 x86_64 VM with working VirGL 3D support,
  128 GiB disk, 6 GiB RAM, an existing administrative user and a reserved LAN IP.
- The tested account is the first regular Ubuntu user, UID/GID 1000. Other
  layouts and hypervisors are not covered by this pilot's acceptance.
- Keep any configured VM and signed-in Android data separate. The installer
  refuses populated or partially installed targets; it does not upgrade them.
- Obtain all five prerelease assets through the trusted GitHub release channel,
  then verify `SHA256SUMS`. Checksums alone do not authenticate a download source.
- Read INSTALL.md and the known limitations. No community images are mirrored
  or bundled; input availability and publisher pin checks are mandatory.

## Install and Validate

Follow INSTALL.md, including root-owned source extraction and the single clean
installation invocation. Preserve original journals if any phase fails. Never
rerun the whole command, delete partial state, or replay a GPU activation token.

After explicitly verifying the public TLS certificate, check desktop and mobile
video, audio after a browser gesture, touch after a period of idle, display-role
handoff, and reconnect after a network interruption. Reboot the VM and verify
streaming before signing in to Ubuntu. Check that Aurora, media volume and your
chosen lockscreen setting remain correct. Test a vehicle browser only while parked.

## Report Results

Include Ubuntu and host versions, CPU/GPU configuration, browser/device version,
what worked, and minimal steps for failures. Share only selected, redacted errors.
Do not upload full system journals, network inventories, account screens or VM
backups. Report app compatibility separately from installer or streaming bugs.

## Recover Safely

Use only the original component's explicit recovery operation after inspecting
its journal. GPU tokens are single-use, and healthy services have no testing
expiry. If an initial trial is uncertain, keep that disk offline and use another
clean disposable OS disk. This is not permission to erase a configured VM.
