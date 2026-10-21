# SPDX-License-Identifier: GPL-3.0-only
"""Restore a missing stock video XML include on the disposable .90 VM only."""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('render_trial', Path(__file__).with_name('waydroid-render-trial.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
OVERLAY = Path('/var/lib/waydroid/overlay/vendor/etc/media_codecs_c2.xml')
TEMP = OVERLAY.with_name('.tesla-stock-codecs-20261010.tmp')
SOURCE = '/vendor/etc/media_codecs_google_c2_video.xml'
BACKUP = trial.BACKUPS / 'stock-video-codecs-20261010'
CODE = b'<?xml version="1.0" encoding="utf-8"?>\n<Included>\n    <Include href="media_codecs_google_c2_video.xml" />\n</Included>\n'


def validate_source(data):
    if len(data) > 65536 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Invalid stock decoder XML')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ValueError('Invalid stock decoder XML') from None
    if root.tag != 'Included':
        raise ValueError('Unexpected stock decoder document')
    for name, mime in [('c2.android.avc.decoder', 'video/avc'), ('c2.android.hevc.decoder', 'video/hevc')]:
        codecs = root.findall("./Decoders/MediaCodec[@name='" + name + "']")
        if len(codecs) != 1 or codecs[0].get('type') != mime:
            raise ValueError('Expected existing stock video decoder declarations')


def guest_path():
    pid = trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
    if not pid.isdecimal() or int(pid) < 2:
        raise RuntimeError('Invalid container identity')
    return pid, Path('/proc') / pid / 'root'


def revert(manifest):
    if manifest.get('profile') != 'stock-video-codecs' or manifest.get('candidate') != trial.digest(CODE):
        raise RuntimeError('Invalid stock decoder backup')
    if trial.digest(trial.CFG.read_bytes()) != manifest['config']:
        raise RuntimeError('Config changed; refusing stale stock decoder rollback')
    for path in [OVERLAY, TEMP]:
        if path.exists() or path.is_symlink():
            trial.protected(path)
            if path.read_bytes() != CODE:
                raise RuntimeError('Stock decoder overlay changed; refusing stale rollback')
    trial.stop()
    for path in [OVERLAY, TEMP]:
        if path.exists():
            path.unlink()
    trial.start()
    trial.ready()


def main(args):
    if os.getuid() != 0 or len(args) != 1 or args[0] not in ['--apply', '--rollback']:
        raise RuntimeError('Fixed root stock decoder trial required')
    trial.protected(trial.CFG)
    trial.protected(trial.BACKUPS, directory=True)
    trial.protected(OVERLAY.parent, directory=True)
    if args[0] == '--rollback':
        trial.protected(BACKUP, directory=True)
        trial.protected(BACKUP / 'manifest.json')
        revert(json.loads((BACKUP / 'manifest.json').read_text()))
        print(json.dumps({'rollback': 'stock-video-codecs', 'h264Ready': True, 'preferencesRestored': False}))
        return
    for unit in [trial.RUNTIME, trial.SESSION, trial.COMPOSITOR]:
        if trial.service('is-active', unit) != 'active':
            raise RuntimeError('Owned runtime must be active before trial')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(trial.CFG.read_text())
    if parser.get('properties', 'ro.waydroid.software_rendering', fallback='') != '1' or trial.GPU_OVERRIDE.exists():
        raise RuntimeError('Stock decoder trial requires recovered software rendering')
    for path in [OVERLAY, TEMP, trial.DECODER_XML]:
        if path.exists() or path.is_symlink():
            raise RuntimeError('Previous or foreign codec overlay must not be overwritten')
    pid, root = guest_path()
    source = root / SOURCE.lstrip('/')
    trial.protected(source)
    data = source.read_bytes()
    validate_source(data)
    missing = root / 'vendor/etc/media_codecs_c2.xml'
    if missing.exists() or missing.is_symlink():
        raise RuntimeError('Stock codec include is not missing')
    # Confirm the image actually references the missing file before repairing it.
    main_xml = root / 'vendor/etc/media_codecs.xml'
    trial.protected(main_xml)
    main_data = main_xml.read_bytes()
    if len(main_data) > 65536:
        raise RuntimeError('Invalid main codec document')
    main_root = ET.fromstring(main_data)
    if main_root.tag != 'MediaCodecs' or len(main_root.findall("./Include[@href='media_codecs_c2.xml']")) != 1:
        raise RuntimeError('Expected missing stock codec include reference')
    if guest_path()[0] != pid:
        raise RuntimeError('Container changed during preparation')
    before = trial.preferences()
    manifest = {'profile': 'stock-video-codecs', 'config': trial.digest(trial.CFG.read_bytes()),
                'source': trial.digest(data), 'candidate': trial.digest(CODE), 'preferences': before}
    BACKUP.mkdir(mode=0o700)
    trial.save(BACKUP / 'source.xml', data, 0o600)
    trial.save(BACKUP / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)
    trial.stop()
    try:
        trial.save(TEMP, CODE, 0o644)
        os.replace(TEMP, OVERLAY)
        trial.start()
        trial.ready()
        _, mounted_root = guest_path()
        mounted = mounted_root / 'vendor/etc/media_codecs_c2.xml'
        trial.protected(mounted)
        if mounted.read_bytes() != CODE:
            raise RuntimeError('Stock codec include overlay not mounted')
        print(json.dumps({'trial': 'stock-video-codecs', 'h264Ready': True,
                          'preferencesUnchanged': before == trial.preferences(), 'paidPlaybackAccepted': False}))
    except Exception:
        revert(manifest)
        raise RuntimeError('Stock decoder trial failed; previous overlay and H264 restored')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
