# SPDX-License-Identifier: GPL-3.0-only
"""Distinct h retest after unobstructed generated software control passed."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('offline_retest', Path(__file__).with_name('waydroid-gpu-offline-layers-trial.py'))
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
engine = wrapper.engine
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-layers-20261011h'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-layers-20261011h.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-layers-offline-property-retest'

if __name__ == '__main__':
    try:
        engine.main(sys.argv[1:])
    except Exception:
        print('Offline layer retest did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
