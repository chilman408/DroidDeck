# Required Pilot Reboot Hotfix 1

Required source patch for **0.1.0-rc.1**, including already configured pilot VMs.
Fixes interrupted Android startup being mistaken for an expired GPU operation,
and extends the bounded session-startup wait for configured Android systems.

Download all three named assets, verify `SHA256SUMS`, and follow `INSTALL.md`.
The repair preserves Android apps/accounts and the VM's TLS identity. It never
reruns the clean installer or replays an old GPU activation token. Keep the
authenticated installation terminal connected until completion.

For new pilot installs, finish the original rc.1 installation and apply this
patch **before the first reboot**. This is not a rebuilt full-installer archive;
the original rc.1 files and release tag remain unchanged.

Verified on the configured pilot VM: native VirGL rendering; normal reboot;
reboot deliberately interrupted during Android startup; automatic recovery
before interactive guest login; desktop/mobile H264 video and AAC playback;
unchanged data-directory identity and public TLS certificate. No screenshots,
Android account data, credentials, private keys or DRM material are packaged.
Focused Linux boot-state, original-ownership and durable/no-replay tests passed.

This is still an early technical pilot, checksum-verified but not digitally
signed. Keep it on a trusted LAN; browser authentication defaults off.
Preserve private journals if repair stops. Never delete partial state or retry
uncertain admission/activation steps. Report selected redacted errors only.
