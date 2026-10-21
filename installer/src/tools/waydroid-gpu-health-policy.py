# SPDX-License-Identifier: GPL-3.0-only
"""Health-based GPU trial recovery; healthy ready state has no testing deadline."""
import math

STARTING = frozenset({'prepared', 'stopping', 'installing', 'starting', 'waiting-h264',
                      'verifying-native', 'recovering'})
STARTUP_LIMIT_SEC = 480
FAILURE_LIMIT = 3


def decision(stage, healthy, failures, phase_age):
    if stage not in STARTING | {'ready', 'restored', 'recovery-required'} or \
            type(failures) is not int or not 0 <= failures <= FAILURE_LIMIT or \
            type(phase_age) not in (float, int) or not math.isfinite(phase_age) or phase_age < 0 or \
            (stage == 'ready' and type(healthy) is not bool):
        raise ValueError('Invalid GPU health observation')
    if stage == 'restored':
        return 'stop', 0
    if stage == 'recovery-required' or (stage in STARTING and phase_age >= STARTUP_LIMIT_SEC):
        return 'recover', 0
    if stage != 'ready' or healthy:
        return 'continue', 0
    failures = min(FAILURE_LIMIT, failures + 1)
    return ('recover' if failures == FAILURE_LIMIT else 'continue'), failures
