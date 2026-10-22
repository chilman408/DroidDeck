# Security

This pilot is intended for a trusted LAN. Optional browser authentication starts
off; direct Internet exposure is not supported by this pilot configuration.
Anyone who can access an unauthenticated endpoint may control the Android session.

Never publish VM or Unraid passwords, Android account data, access tokens, private
TLS/SSH keys, full VM disks or DRM material. Keep VM backups private. Changing a
browser password does not revoke sessions in third-party Android applications.

Do not post exploit details or secrets in public issues. Use GitHub's private
vulnerability reporting when the repository offers it. If private reporting is
unavailable, open a minimal issue asking for a private reporting channel without
including sensitive details. No response-time or support guarantee is provided.

If a credential is accidentally exposed, revoke or rotate it first. Removing a
file from the latest commit does not remove it from Git history, forks or caches.
