# SPDX-License-Identifier: GPL-3.0-only
"""Fresh GPU trial identity after the verified q capture and p2 browser fixes."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location(
    'mesa_cadence_boot', Path(__file__).with_name('waydroid-gpu-mesa-boot-trial.py'))
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
engine = boot.engine
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-cadence-20261011r'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-cadence-20261011r.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-mesa-boot-cadence'
boot.TEMP = '.tesla-mesa-allocator-20261011r.tmp'
ENCODER = Path('/opt/teslaandroid-waydroid/runtime/preview-gateway/compositor-h264.py')
ENCODER_SHA256 = '086ece12aaea13515c77719c6332ca42a955807f2a47f4c196636f8c8af87136'


def main(args):
    if args not in [['--apply'], ['--rollback']]:
        raise RuntimeError('Fixed cadence trial action required')
    if args == ['--apply']:
        engine.trial.protected(ENCODER)
        if engine.trial.digest(ENCODER.read_bytes()) != ENCODER_SHA256:
            raise RuntimeError('Verified q capture fix required before GPU trial')
    # Rollback must remain possible even if capture code changes independently.
    engine.main(args)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Cadence GPU trial did not complete; inspect its protected status metadata', file=sys.stderr)
        sys.exit(1)
