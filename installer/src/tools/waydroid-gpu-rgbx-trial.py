# SPDX-License-Identifier: GPL-3.0-only
"""Fixed GPU/RGBX trial, separate from the rejected default-buffer profile."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('gpu_rgbx_engine', Path(__file__).with_name('waydroid-gpu-render-trial.py'))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-rgbx-20261010e'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-rgbx-20261010e.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-early-softdecode'
engine.INIT_CODE += b'    setprop debug.ffmpeg-codec2.pixel_format RGBX_8888\n'
engine.EXPECTED_NATIVE = {**engine.EXPECTED_NATIVE, 'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
engine.RESTORED_PROPERTIES = {'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}

if __name__ == '__main__':
    try:
        engine.main(sys.argv[1:])
    except Exception:
        print('RGBX GPU trial did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
