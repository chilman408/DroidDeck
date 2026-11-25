# DroidMate 0.1.3

- In-place upgrades from 0.1.2 can restore native GPU graphics when the
  previous boot recovered to the CPU profile. The installer verifies the
  original registration and signed core update before starting GPU recovery.
- The new native GPU watcher waits for the core boot check. If it restores the
  original CPU profile, it starts CPU playback again.

The signed Android core remains `0.1.1-rc.20`. Follow the installation guide
for a fresh Ubuntu install or an in-place update.
