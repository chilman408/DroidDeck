# GPU recovery and idle streaming

GPU recovery must check Android's renderer, allocator and graphics properties.
An idle or restarting touch stream is not evidence of a GPU failure.

An older development-preview guard incorrectly required every streaming service
to remain active. When its touch process stopped, three failed service checks
caused a working GPU profile to be replaced by software rendering. That legacy
preview was repaired with a separate guard based on native graphics health.

The released RC2 installation uses a newer native GPU guard, which the RC5 updater
preserves. Its health probe does not query touch, audio or gateway service states.
RC5 therefore already has the required behavior; the signed installer and source
assets retain their previously verified bytes and signatures. RC5 is an updater
for completed RC2 installations, not an installer for legacy development previews.

New regression tests exercise the exact RC5 source identities with unavailable
touch peers, six successive healthy observations, long uptime and three actual
native-health failures. They prove that idle peers do not trigger fallback while
native failures still request recovery. The tests use generated observations and
do not start services, connect to Android, or read account data:

```sh
node --test updater/tests/gpu-idle-regression.test.js
python3 -B updater/tests/gpu_idle_watch_test.py -v
```

Separately, the repaired development preview passed actual Mesa/VirGL and allocator
checks, software codec4/RGBX/AAC preservation, and pinned-TLS desktop and legacy
mobile H264/AAC playback. This was a service restart, not a new reboot acceptance
test. RC5's earlier disposable-VM healthy and rollback reboot evidence remains
unchanged. No new GPU acceleration or network-adaptive streaming feature is claimed.
