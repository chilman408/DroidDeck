# SPDX-License-Identifier: GPL-3.0-only
"""Distinct Mesa GPU candidate after verified capture, input, audio and Network fixes."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('mesa_health_boot', Path(__file__).with_name('waydroid-gpu-mesa-boot-trial.py'))
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
engine = boot.engine
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-health-20261011u'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-health-20261011u.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-mesa-boot-health'
boot.TEMP = '.tesla-mesa-allocator-20261011u.tmp'
WATCHDOG = 'tesla-gpu-health-watch-20261011u.service'
ENCODER = Path('/opt/teslaandroid-waydroid/runtime/preview-gateway/compositor-h264.py')
FRONTEND = Path('/home/hilman/teslaandroid-waydroid/frontend')
REQUIRED = {
    ENCODER: '63f8e631cc31822545ffca5160a46fb72e87d11f908fc4eb4d23e0e64db0d08e',
    FRONTEND / 'js/display/renderers/h264-broadway/h264BrodwayWorker.js': 'f34045ad9d0c10aed19bb9c5d2e621dd96779d56c74e5040e338a09409acb280',
    FRONTEND / 'js/display/renderers/h264-broadway/h264Brodway.js': 'a42d5e5a1b15383c3d96a40fc14d63d03dd4f789769ef58eb628c6ad7f58b1d6',
    FRONTEND / 'js/audio/audioplayback.js': '4c18cf01f9d7a1d85b99cb9d2aa326a9ec649e3af57d057db7ec00f548c4e7a7',
    FRONTEND / 'js/settings/settings-controller.js': 'e36e138f1db164a501fda08e05beb0482123b8cfc93829648a0019fe51cfae33',
}


def main(args):
    if args not in [['--apply'], ['--rollback']]:
        raise RuntimeError('Fixed health GPU trial action required')
    if args == ['--apply']:
        if engine.trial.service('is-active', WATCHDOG) != 'active':
            raise RuntimeError('Independent health guard must be armed before apply')
        for path, expected in REQUIRED.items():
            engine.trial.protected(path)
            if engine.trial.digest(path.read_bytes()) != expected:
                raise RuntimeError('Verified streaming baseline required before GPU trial')
    # Recovery must stay usable after independent frontend/capture maintenance.
    engine.main(args)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Health GPU trial did not complete; inspect its protected status metadata', file=sys.stderr)
        sys.exit(1)
