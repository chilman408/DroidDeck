# SPDX-License-Identifier: GPL-3.0-only
"""Independent GPU liveness guard; never schedules a user testing deadline."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import time

spec = importlib.util.spec_from_file_location('gpu_health_policy', Path(__file__).with_name('waydroid-gpu-health-policy.py'))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def watch(read_stage, healthy, recover, sleep=time.sleep, clock=time.monotonic, report=lambda _value: None):
    started = clock()
    phase_started = started
    phase = None
    failures = 0
    observed_healthy = False
    while True:
        stage = read_stage()
        now = clock()
        if stage is None:
            if now - started >= 120:
                raise RuntimeError('GPU candidate not started; no mutation requested')
            sleep(5)
            continue
        changed = stage != phase
        if changed:
            phase = stage
            phase_started = now
            failures = 0
        sample = healthy() if stage == 'ready' else None
        if sample is True:
            observed_healthy = True
        # A saved ready status can outlive a host reboot; allow bounded runtime startup before counting failures.
        observation = 'starting' if stage == 'ready' and not observed_healthy else stage
        action, failures = policy.decision(observation, sample if observation == 'ready' else None, failures, now - phase_started)
        if changed or failures or action != 'continue':
            report({'stage': stage, 'action': action, 'failures': failures})
        if action == 'stop':
            return 'restored'
        if action == 'recover':
            recover()
            report({'action': 'recovered'})
            return 'recovered'
        sleep(30)


def load_trial():
    spec = importlib.util.spec_from_file_location('gpu_health_trial', Path(__file__).with_name('waydroid-gpu-health-trial.py'))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def read_owned_stage(engine):
    if not engine.BACKUP.exists() and not engine.BACKUP.is_symlink():
        return None
    engine.trial.protected(engine.BACKUP, directory=True)
    manifest_path = engine.BACKUP / 'manifest.json'
    # The engine publishes the manifest before status; do not infer ownership from a status string alone.
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return None
    engine.trial.protected(manifest_path)
    data = manifest_path.read_bytes()
    if len(data) > 65536:
        raise ValueError('GPU backup metadata exceeded limit')
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or manifest.get('profile') != engine.PROFILE:
        raise ValueError('Foreign GPU backup metadata')
    engine.trial.protected(engine.ORIGINAL)
    original = engine.ORIGINAL.read_bytes()
    if engine.trial.digest(original) != manifest.get('before') or \
            engine.trial.digest(engine.candidate(original)) != manifest.get('candidate'):
        raise ValueError('GPU backup identity changed')
    path = engine.BACKUP / 'status.json'
    if not path.exists() and not path.is_symlink():
        return 'prepared'
    engine.trial.protected(path)
    data = path.read_bytes()
    if len(data) > 65536:
        raise ValueError('GPU status exceeded limit')
    value = json.loads(data)
    if not isinstance(value, dict) or value.get('profile') != engine.PROFILE or \
            type(value.get('stage')) is not str or value['stage'] not in engine.STAGES:
        raise ValueError('Foreign GPU status')
    return value['stage']


def native_healthy(engine):
    try:
        units = ['tesla-waydroid-' + name + '-preview.service' for name in
                 ['gateway', 'video', 'video-relay', 'touch', 'display', 'display-helper', 'audio', 'audio-relay']]
        if engine.trial.run(['/usr/bin/systemctl', 'is-active', *units], 10).splitlines() != ['active'] * len(units):
            return False
        pid, _root = engine.guest_identity()
        guest = lambda args: engine.trial.run(['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', *args], 10)
        for key in ['sys.boot_completed', 'ro.hardware.gralloc', 'init.svc.vendor.graphics.allocator-4-0-gbm_mesa']:
            if guest(['/system/bin/getprop', key]) != engine.EXPECTED_NATIVE[key]:
                return False
        output = guest(['/system/bin/dumpsys', 'SurfaceFlinger'])
        if len(output) > 4 * 1024 * 1024:
            return False
        renderer = next((line for line in output.splitlines() if line.startswith('GLES:')), '').lower()
        return 'virgl' in renderer and not any(word in renderer for word in ['swiftshader', 'llvmpipe']) and engine.guest_identity()[0] == pid
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False


def main(args):
    fixed = Path('/opt/teslaandroid-waydroid/gpu-health-trial-20261011u/tools/waydroid-gpu-health-watch.py')
    if args != ['--watch'] or sys.platform != 'linux' or os.getuid() != 0 or Path(__file__).resolve() != fixed:
        raise RuntimeError('Fixed protected root GPU health watcher required')
    tool = load_trial()
    engine = tool.engine
    engine.trial.protected(fixed.parent, directory=True)
    for name in ['waydroid-gpu-health-watch.py', 'waydroid-gpu-health-policy.py', 'waydroid-gpu-health-trial.py']:
        engine.trial.protected(fixed.with_name(name))
    watch(lambda: read_owned_stage(engine), lambda: native_healthy(engine), lambda: tool.main(['--rollback']),
          report=lambda value: print(json.dumps(value), flush=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('GPU health guard requires inspection; no foreign backup is restored', file=sys.stderr)
        sys.exit(1)
