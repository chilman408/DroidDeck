# SPDX-License-Identifier: GPL-3.0-only
"""Distinct allocator trial; unchanged apps, codec4/AAC, software video decoding."""
import configparser
import importlib.util
import io
import json
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('mesa_allocator_layers', Path(__file__).with_name('waydroid-gpu-offline-layers-trial.py'))
layers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layers)
engine = layers.engine
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-mesa-allocator-20261011o'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-mesa-allocator-20261011o.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-layers-mesa-allocator'
ALLOCATOR = 'minigbm_gbm_mesa'
SERVICE = 'init.svc.vendor.graphics.allocator-4-0-gbm_mesa'
engine.EXPECTED_NATIVE = {**engine.EXPECTED_NATIVE, 'ro.hardware.gralloc': ALLOCATOR, SERVICE: 'running'}
engine.NATIVE_PROPERTY_VALUES |= {ALLOCATOR, 'running'}
engine.RESTORED_PROPERTIES = {**engine.RESTORED_PROPERTIES, 'ro.hardware.gralloc': 'default'}
baseline_candidate = engine.candidate
baseline_verify = engine.verify_native


def candidate(data):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(baseline_candidate(data).decode())
    parser.set('properties', 'ro.hardware.gralloc', ALLOCATOR)
    output = io.StringIO()
    parser.write(output)
    return output.getvalue().encode()


def mapper_loaded(text):
    if not isinstance(text, str) or len(text) > 4 * 1024 * 1024:
        raise RuntimeError('Invalid mapper metadata')
    paths = [line.rsplit(None, 1)[-1] for line in text.splitlines() if line.split()]
    return '/vendor/lib64/libminigbm_gralloc_gbm_mesa.so' in paths and any(path in paths for path in (
        '/vendor/lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm_gbm_mesa.so',
        '/vendor/lib64/hw/gralloc.minigbm_gbm_mesa.so'))


def verify_native():
    baseline_verify()
    pid, _ = engine.guest_identity()
    guest = lambda args: engine.trial.run(['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', *args], 15)
    sf = guest(['/system/bin/pidof', 'surfaceflinger'])
    if not re.fullmatch(r'[1-9][0-9]{0,8}', sf) or int(sf) < 2:
        raise RuntimeError('Invalid allocator consumer identity')
    if not mapper_loaded(guest(['/system/bin/cat', '/proc/' + sf + '/maps'])):
        raise RuntimeError('Mesa-backed mapper is not loaded by SurfaceFlinger')
    if engine.guest_identity()[0] != pid:
        raise RuntimeError('Container changed during allocator verification')
    state = engine.BACKUP / 'native-state.json'
    engine.trial.protected(state)
    value = json.loads(state.read_text())
    value['mesaAllocatorConsumerVerified'] = True
    engine.trial.save(state, (json.dumps(value) + '\n').encode(), 0o600)


engine.candidate = candidate
engine.verify_native = verify_native

if __name__ == '__main__':
    try:
        engine.main(sys.argv[1:])
    except Exception:
        print('Mesa allocator GPU trial did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
