# SPDX-License-Identifier: GPL-3.0-only
"""Distinct i trial after compression-resistant software motion control passed."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('motion_trial', Path(__file__).with_name('waydroid-gpu-offline-layers-trial.py'))
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
engine = wrapper.engine
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-motion-20261011i'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-motion-20261011i.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-layers-motion-verified-control'

if __name__ == '__main__':
    try:
        engine.main(sys.argv[1:])
    except Exception:
        print('GPU motion trial did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
