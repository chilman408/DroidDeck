# SPDX-License-Identifier: GPL-3.0-only
"""Diagnostic only: compare generated raw compositor motion with H264 output."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('source_trial', Path(__file__).with_name('waydroid-gpu-offline-layers-trial.py'))
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
engine = wrapper.engine
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-source-20261011k'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-source-20261011k.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-generated-source-diagnostic'

if __name__ == '__main__':
    try:
        engine.main(sys.argv[1:])
    except Exception:
        print('GPU source diagnostic did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
