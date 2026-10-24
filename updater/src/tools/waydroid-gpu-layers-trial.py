# SPDX-License-Identifier: GPL-3.0-only
"""Historical held g3 candidate; CLI disabled because saved-key recovery is unsafe."""
import configparser
import importlib.util
import io
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('gpu_layers_engine', Path(__file__).with_name('waydroid-gpu-render-trial.py'))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-layers-20261010g3'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-layers-20261010g3.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-layers-persist-ready'
engine.INIT_CODE += b'    setprop debug.ffmpeg-codec2.pixel_format RGBX_8888\n'
# This historical rc action persists the key; hook removal alone cannot undo it.
engine.INIT_CODE += (b'\non property:ro.persistent_properties.ready=true\n'
                     b'    setprop persist.waydroid.use_subsurface true\n')
engine.EXPECTED_NATIVE = {**engine.EXPECTED_NATIVE, 'persist.waydroid.use_subsurface': 'true',
                         'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
engine.RESTORED_PROPERTIES = {'persist.waydroid.use_subsurface': 'false',
                            'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
baseline_candidate = engine.candidate


def candidate(data):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(baseline_candidate(data).decode())
    parser.set('properties', 'persist.waydroid.use_subsurface', 'true')
    stream = io.StringIO()
    parser.write(stream)
    return stream.getvalue().encode()


engine.candidate = candidate

if __name__ == '__main__':
    print('Held unsafe candidate: g3 was never applied; do not apply or roll back this profile', file=sys.stderr)
    sys.exit(1)
