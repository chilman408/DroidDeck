# SPDX-License-Identifier: GPL-3.0-only
"""Reversible stock AAC preference trial on disposable .90; no video or DRM changes."""
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
TEMP = OVERLAY.with_name('.tesla-aac-priority-20261010c.tmp')
BACKUP = trial.BACKUPS / 'aac-priority-20261010c'
PROBE = Path('/var/lib/teslaandroid-waydroid/aac-priority-diagnostics-20261010c/codec-probe.js')
STAGES = {'stopping', 'writing-overlay', 'starting', 'waiting-h264', 'verifying-overlay',
          'verifying-codecs', 'verifying-state', 'removing-overlay'}
CODE = (b'<?xml version="1.0" encoding="utf-8"?>\n<Included>\n  <Decoders>\n'
        b'    <MediaCodec name="c2.android.aac.decoder" type="audio/mp4a-latm" update="true" rank="1" />\n'
        b'    <MediaCodec name="c2.ffmpeg.aac.decoder" type="audio/mp4a-latm" update="true" rank="512" />\n'
        b'  </Decoders>\n</Included>\n')


def selection(snapshot):
    items = snapshot.get('selected', [])
    if snapshot.get('version') != 1 or len(items) != 7:
        raise RuntimeError('Invalid codec selection metadata')
    result = {item['mime']: item['decoder'] for item in items}
    if len(result) != 7 or 'audio/mp4a-latm' not in result:
        raise RuntimeError('Ambiguous codec selection metadata')
    return result


def validate_baseline(snapshot):
    if selection(snapshot)['audio/mp4a-latm'] != 'c2.ffmpeg.aac.decoder':
        raise RuntimeError('AAC baseline selection changed')
    codecs = [item for item in snapshot.get('decoders', []) if item.get('name') == 'c2.android.aac.decoder']
    if len(codecs) != 1 or codecs[0].get('mime') != 'audio/mp4a-latm' or codecs[0].get('software') is not True or \
            codecs[0].get('securePlaybackAdvertised') is not False:
        raise RuntimeError('Existing software stock AAC decoder not verified')


def verify_selection(before, after):
    old, new = selection(before), selection(after)
    if old['audio/mp4a-latm'] != 'c2.ffmpeg.aac.decoder' or new['audio/mp4a-latm'] != 'c2.android.aac.decoder':
        raise RuntimeError('AAC selection did not change as expected')
    if {k: v for k, v in old.items() if k != 'audio/mp4a-latm'} != {k: v for k, v in new.items() if k != 'audio/mp4a-latm'}:
        raise RuntimeError('Non-AAC decoder selection changed')
    # Ranking may reorder the list, but no codec or advertised capability may change.
    normalize = lambda value: sorted(json.dumps(item, sort_keys=True) for item in value.get('decoders', []))
    if normalize(before) != normalize(after):
        raise RuntimeError('Decoder capabilities changed')


def guest_path():
    pid = trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
    if not pid.isdecimal() or int(pid) < 2:
        raise RuntimeError('Invalid container identity')
    return pid, Path('/proc') / pid / 'root'


def read_codecs():
    trial.protected(PROBE.parent, directory=True)
    for name in ['codec-probe.js', 'host-adapter.js', 'frame-check.js']:
        trial.protected(PROBE.with_name(name))
    modules = trial.ROOT + '/runtime/waydroid-display/node_modules:' + trial.ROOT + '/runtime/preview-gateway/node_modules'
    data = trial.run(['/usr/bin/env', 'NODE_PATH=' + modules,
                      '/usr/bin/node', str(PROBE)], 30)
    if len(data) > 65536:
        raise RuntimeError('Codec metadata too large')
    snapshot = json.loads(data)
    selection(snapshot)
    return snapshot


def status(phase, stage=None, failed_stage=None, recovery_stage=None):
    if phase not in {'prepared', 'installing', 'ready', 'recovering', 'rolled-back', 'recovery-required'} or \
            any(value is not None and value not in STAGES for value in [stage, recovery_stage]) or \
            (failed_stage is not None and failed_stage not in STAGES | {'unavailable'}):
        raise ValueError('Invalid trial status metadata')
    data = {'profile': 'aac-priority', 'phase': phase, 'paidPlaybackAccepted': False,
            'preferencesRestored': False, 'videoOrDrmChanged': False}
    for key, value in [('stage', stage), ('failedStage', failed_stage), ('recoveryStage', recovery_stage)]:
        if value is not None:
            data[key] = value
    trial.save(BACKUP / 'status.json', (json.dumps(data) + '\n').encode(), 0o600)


def revert(manifest, progress=lambda stage: None):
    if manifest.get('profile') != 'aac-priority' or manifest.get('candidate') != trial.digest(CODE):
        raise RuntimeError('Invalid AAC backup')
    if trial.digest(trial.CFG.read_bytes()) != manifest['config']:
        raise RuntimeError('Config changed; refusing stale AAC rollback')
    for path in [OVERLAY, TEMP]:
        if path.exists() or path.is_symlink():
            trial.protected(path)
            if path.read_bytes() != CODE:
                raise RuntimeError('AAC overlay changed; refusing stale rollback')
    progress('stopping')
    trial.stop()
    progress('removing-overlay')
    for path in [OVERLAY, TEMP]:
        if path.exists():
            path.unlink()
    progress('starting')
    trial.start()
    progress('waiting-h264')
    trial.ready()


def recover(manifest, failed_stage=None):
    recovery_stage, status_failed = None, False
    def record(phase, **fields):
        nonlocal status_failed
        try:
            status(phase, **fields)
        except (OSError, RuntimeError):
            # Reporting must never prevent removing the overlay or restarting services.
            status_failed = True
    def progress(value):
        nonlocal recovery_stage
        recovery_stage = value
        record('recovering', failed_stage=failed_stage, recovery_stage=value)
    record('recovering', failed_stage=failed_stage)
    try:
        revert(manifest, progress)
        progress('verifying-codecs')
        if selection(read_codecs()) != selection(manifest['codecsBefore']):
            raise RuntimeError('Original decoder selection not recovered')
        record('rolled-back', failed_stage=failed_stage)
    except Exception:
        record('recovery-required', failed_stage=failed_stage, recovery_stage=recovery_stage)
        raise RuntimeError('AAC trial recovery requires inspection') from None
    if status_failed:
        raise RuntimeError('AAC baseline restored; recovery status could not be saved') from None


def main(args):
    if os.getuid() != 0 or len(args) != 1 or args[0] not in ['--apply', '--rollback']:
        raise RuntimeError('Fixed root AAC trial required')
    trial.protected(trial.CFG)
    trial.protected(trial.BACKUPS, directory=True)
    trial.protected(OVERLAY.parent, directory=True)
    if args[0] == '--rollback':
        trial.protected(BACKUP, directory=True)
        trial.protected(BACKUP / 'manifest.json')
        manifest = json.loads((BACKUP / 'manifest.json').read_text())
        failed_stage = None
        status_path = BACKUP / 'status.json'
        if status_path.exists() or status_path.is_symlink():
            failed_stage = 'unavailable'
            try:
                trial.protected(status_path)
                with status_path.open('rb') as source:
                    data = source.read(4097)
                previous = json.loads(data) if len(data) <= 4096 else None
                if isinstance(previous, dict) and previous.get('profile') == 'aac-priority':
                    value = previous.get('failedStage')
                    if value is None or (isinstance(value, str) and value in STAGES | {'unavailable'}):
                        failed_stage = value
            except (OSError, RuntimeError, ValueError):
                pass
        recover(manifest, failed_stage=failed_stage)
        print(json.dumps({'rollback': 'aac-priority', 'h264Ready': True, 'preferencesRestored': False}))
        return
    for unit in [trial.RUNTIME, trial.SESSION, trial.COMPOSITOR]:
        if trial.service('is-active', unit) != 'active':
            raise RuntimeError('Owned runtime must be active before trial')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(trial.CFG.read_text())
    if parser.get('properties', 'ro.waydroid.software_rendering', fallback='') != '1' or trial.GPU_OVERRIDE.exists():
        raise RuntimeError('AAC trial requires recovered software rendering')
    for path in [OVERLAY, TEMP, trial.DECODER_XML]:
        if path.exists() or path.is_symlink():
            raise RuntimeError('Previous or foreign codec overlay must not be overwritten')
    pid, root = guest_path()
    existing_include = root / 'vendor/etc/media_codecs_c2.xml'
    if existing_include.exists() or existing_include.is_symlink():
        raise RuntimeError('Existing codec include must not be replaced')
    main_xml = root / 'vendor/etc/media_codecs.xml'
    trial.protected(main_xml)
    main_data = main_xml.read_bytes()
    if len(main_data) > 65536:
        raise RuntimeError('Invalid main codec document')
    main_root = ET.fromstring(main_data)
    includes = [item.get('href') for item in main_root.findall('./Include')]
    if main_root.tag != 'MediaCodecs' or includes.count('media_codecs_c2.xml') != 1 or \
            includes.count('media_codecs_ffmpeg_c2.xml') != 1 or \
            includes.index('media_codecs_ffmpeg_c2.xml') >= includes.index('media_codecs_c2.xml'):
        raise RuntimeError('Expected codec include reference')
    baseline = read_codecs()
    validate_baseline(baseline)
    if guest_path()[0] != pid:
        raise RuntimeError('Container changed during preparation')
    before = trial.preferences()
    manifest = {'profile': 'aac-priority', 'config': trial.digest(trial.CFG.read_bytes()),
                'candidate': trial.digest(CODE),
                'preferences': before, 'codecsBefore': baseline}
    BACKUP.mkdir(mode=0o700)
    trial.save(BACKUP / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)
    status('prepared')
    stage = 'stopping'
    def installing(value):
        nonlocal stage
        stage = value
        status('installing', stage=stage)
    try:
        installing('stopping')
        trial.stop()
        installing('writing-overlay')
        trial.save(TEMP, CODE, 0o644)
        os.replace(TEMP, OVERLAY)
        installing('starting')
        trial.start()
        installing('waiting-h264')
        trial.ready()
        installing('verifying-overlay')
        mounted_pid, mounted_root = guest_path()
        mounted = mounted_root / 'vendor/etc/media_codecs_c2.xml'
        trial.protected(mounted)
        if mounted.read_bytes() != CODE:
            raise RuntimeError('AAC overlay not mounted')
        installing('verifying-codecs')
        observed = read_codecs()
        trial.save(BACKUP / 'codecs-after.json', (json.dumps(observed, indent=2) + '\n').encode(), 0o600)
        verify_selection(baseline, observed)
        installing('verifying-state')
        if guest_path()[0] != mounted_pid or trial.digest(trial.CFG.read_bytes()) != manifest['config']:
            raise RuntimeError('Container/config changed during verification')
        if before != trial.preferences():
            raise RuntimeError('Preferences changed during trial')
        status('ready')
        print(json.dumps({'trial': 'aac-priority', 'h264Ready': True, 'selectedAac': 'c2.android.aac.decoder',
                          'preferencesUnchanged': True, 'paidPlaybackAccepted': False}))
    except Exception:
        recover(manifest, failed_stage=stage)
        raise RuntimeError('AAC trial failed; original decoder selection and H264 restored') from None


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
