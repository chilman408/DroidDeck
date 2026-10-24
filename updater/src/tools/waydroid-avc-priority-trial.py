# SPDX-License-Identifier: GPL-3.0-only
"""Reversible AVC-only ranking trial preserving the accepted AAC overlay on .90."""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('aac_trial', Path(__file__).with_name('waydroid-aac-priority-trial.py'))
aac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aac)
trial = aac.trial
read_codecs = aac.read_codecs
OVERLAY = aac.OVERLAY
TEMP = OVERLAY.with_name('.tesla-avc-priority-20261010g.tmp')
BACKUP = trial.BACKUPS / 'avc-priority-20261010g'
BASELINE = aac.CODE
STOCK = ET.Element('MediaCodec', {'name': 'c2.android.avc.decoder', 'type': 'video/avc'})
ET.SubElement(STOCK, 'Alias', {'name': 'OMX.google.h264.decoder'})
for attributes in [{'name': 'size', 'min': '2x2', 'max': '4080x4080'},
    {'name': 'alignment', 'value': '2x2'}, {'name': 'block-size', 'value': '16x16'},
    {'name': 'block-count', 'range': '1-32768'}, {'name': 'blocks-per-second', 'range': '1-1966080'},
    {'name': 'bitrate', 'range': '1-48000000'}]:
    ET.SubElement(STOCK, 'Limit', attributes)
ET.SubElement(STOCK, 'Feature', {'name': 'adaptive-playback'})
root = ET.fromstring(BASELINE)
stock = ET.fromstring(ET.tostring(STOCK))
stock.set('rank', '1')
root.find('./Decoders').append(stock)
ET.SubElement(root.find('./Decoders'), 'MediaCodec',
              {'name': 'c2.ffmpeg.h264.decoder', 'type': 'video/avc', 'update': 'true', 'rank': '512'})
CODE = ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
STAGES = aac.STAGES | {'restoring-aac-overlay'}


class NoDtdTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, public_id, system_id):
        raise ValueError('Codec include DTD is not permitted')


def parse_include(data):
    if len(data) > 65536:
        raise ValueError('Codec include document too large')
    return ET.fromstring(data, parser=ET.XMLParser(target=NoDtdTreeBuilder()))


def validate_stock_source(data):
    document = parse_include(data)
    codecs = document.findall("./Decoders/MediaCodec[@name='c2.android.avc.decoder']")
    def structure(element):
        return (element.tag, element.attrib, [structure(e) for e in element])
    if document.tag != 'Included' or len(codecs) != 1 or structure(codecs[0]) != structure(STOCK):
        raise RuntimeError('Expected exact installed stock AVC declaration')


def validate_allocator(gralloc, force):
    if gralloc != 'minigbm' or force not in ['', '0', 'false']:
        raise RuntimeError('Image policy excludes stock video with this allocator; no changes made')


def validate_baseline(snapshot):
    selected = aac.selection(snapshot)
    if selected['audio/mp4a-latm'] != 'c2.android.aac.decoder':
        raise RuntimeError('Accepted AAC selection changed')
    if selected['video/avc'] != 'c2.ffmpeg.h264.decoder':
        raise RuntimeError('AVC baseline selection changed')
    codecs = [c for c in snapshot.get('decoders', []) if c.get('name') == 'c2.android.avc.decoder']
    if codecs or any(c.get('name') == 'OMX.google.h264.decoder' for c in snapshot.get('decoders', [])):
        raise RuntimeError('Stock AVC declaration already exposed; baseline changed')


def verify_selection(before, after):
    old, new = aac.selection(before), aac.selection(after)
    validate_baseline(before)
    if new['video/avc'] != 'c2.android.avc.decoder':
        raise RuntimeError('AVC selection did not change')
    if {k: v for k, v in old.items() if k != 'video/avc'} != {k: v for k, v in new.items() if k != 'video/avc'}:
        raise RuntimeError('Non-AVC selection changed')
    added_names = {'c2.android.avc.decoder', 'OMX.google.h264.decoder'}
    added = [c for c in after.get('decoders', []) if c.get('name') in added_names]
    if len(added) not in [1, 2] or len({c['name'] for c in added}) != len(added) or \
            not any(c['name'] == 'c2.android.avc.decoder' for c in added):
        raise RuntimeError('Stock AVC capabilities not exposed')
    for c in added:
        if c != {'name': c['name'], 'mime': 'video/avc', 'software': True, 'hardware': False,
                 'securePlaybackAdvertised': False, 'adaptivePlaybackAdvertised': True}:
            raise RuntimeError('Unexpected stock AVC capabilities')
    normalize = lambda items: sorted(json.dumps(c, sort_keys=True) for c in items)
    if normalize(before.get('decoders', [])) != normalize([c for c in after.get('decoders', []) if c.get('name') not in added_names]):
        raise RuntimeError('Other decoder capabilities changed')


def manifest(codecs, preferences):
    return {'profile': 'avc-priority', 'candidate': trial.digest(CODE), 'baseline': trial.digest(BASELINE),
            'config': trial.digest(trial.CFG.read_bytes()), 'codecsBefore': codecs, 'preferences': preferences}


def status(phase, stage=None):
    if phase not in {'prepared', 'installing', 'ready', 'recovering', 'rolled-back', 'recovery-required'} or \
            stage is not None and stage not in STAGES:
        raise ValueError('Invalid AVC trial phase')
    trial.save(BACKUP / 'status.json', (json.dumps({'profile': 'avc-priority', 'phase': phase,
        'stage': stage, 'aacPreserved': True, 'paidPlaybackAccepted': False,
        'drmChanged': False, 'preferencesRestored': False}) + '\n').encode(), 0o600)


def revert(saved):
    if saved.get('profile') != 'avc-priority' or saved.get('candidate') != trial.digest(CODE) or \
            saved.get('baseline') != trial.digest(BASELINE):
        raise RuntimeError('Invalid AVC backup')
    if trial.digest(trial.CFG.read_bytes()) != saved['config']:
        raise RuntimeError('Config changed; refusing stale AVC rollback')
    trial.protected(OVERLAY)
    if OVERLAY.read_bytes() not in [CODE, BASELINE]:
        raise RuntimeError('AVC overlay changed; refusing stale rollback')
    if TEMP.exists() or TEMP.is_symlink():
        trial.protected(TEMP)
        if TEMP.read_bytes() not in [CODE, BASELINE]:
            raise RuntimeError('AVC temporary overlay changed')
    trial.stop()
    trial.save(TEMP, BASELINE, 0o644)
    os.replace(TEMP, OVERLAY)
    trial.start()
    trial.ready()
    _, guest = aac.guest_path()
    mounted = guest / 'vendor/etc/media_codecs_c2.xml'
    trial.protected(mounted)
    if mounted.read_bytes() != BASELINE:
        raise RuntimeError('AAC baseline overlay not mounted')


def recover(saved, stage):
    reporting_failed = False
    def record(phase):
        nonlocal reporting_failed
        try: status(phase, stage)
        except (OSError, RuntimeError): reporting_failed = True
    record('recovering')
    try:
        revert(saved)
        if aac.selection(read_codecs()) != aac.selection(saved['codecsBefore']):
            raise RuntimeError('Accepted baseline codecs not restored')
        record('rolled-back')
    except Exception:
        record('recovery-required')
        raise RuntimeError('AVC recovery requires inspection') from None
    if reporting_failed:
        raise RuntimeError('AAC baseline restored; status unavailable') from None


def preflight():
    for unit in [trial.RUNTIME, trial.SESSION, trial.COMPOSITOR]:
        if trial.service('is-active', unit) != 'active': raise RuntimeError('Runtime not active')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(trial.CFG.read_text())
    if parser.get('properties', 'ro.waydroid.software_rendering', fallback='') != '1' or trial.GPU_OVERRIDE.exists():
        raise RuntimeError('AVC trial requires recovered software rendering')
    if BACKUP.exists() or BACKUP.is_symlink() or TEMP.exists() or TEMP.is_symlink() or \
            trial.DECODER_XML.exists() or trial.DECODER_XML.is_symlink():
        raise RuntimeError('Existing backup or foreign overlay must not be overwritten')
    trial.protected(OVERLAY)
    if OVERLAY.read_bytes() != BASELINE: raise RuntimeError('Expected exact accepted AAC overlay')
    pid, guest = aac.guest_path()
    def property_value(name):
        return trial.run(['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--',
                          '/system/bin/getprop', name], 10)
    validate_allocator(property_value('ro.hardware.gralloc'), property_value('debug.ffmpeg-codec2.hwaccel.force'))
    mounted = guest / 'vendor/etc/media_codecs_c2.xml'
    trial.protected(mounted)
    if mounted.read_bytes() != BASELINE: raise RuntimeError('Accepted AAC overlay not mounted')
    source = guest / 'vendor/etc/media_codecs_google_c2_video.xml'
    trial.protected(source)
    with source.open('rb') as stream:
        validate_stock_source(stream.read(65537))
    main_xml = guest / 'vendor/etc/media_codecs.xml'
    trial.protected(main_xml)
    with main_xml.open('rb') as source:
        document = parse_include(source.read(65537))
    includes = [e.get('href') for e in document.findall('./Include')]
    if document.tag != 'MediaCodecs' or includes.count('media_codecs_c2.xml') != 1 or \
            includes.count('media_codecs_ffmpeg_c2.xml') != 1 or \
            includes.index('media_codecs_ffmpeg_c2.xml') >= includes.index('media_codecs_c2.xml'):
        raise RuntimeError('Unexpected codec include order')
    snapshot = read_codecs()
    validate_baseline(snapshot)
    if aac.guest_path()[0] != pid: raise RuntimeError('Container changed during preparation')
    return snapshot


def main(args):
    if os.getuid() != 0 or len(args) != 1 or args[0] not in ['--apply', '--rollback', '--check']:
        raise RuntimeError('Fixed root AVC trial required')
    trial.protected(trial.CFG)
    trial.protected(trial.BACKUPS, directory=True)
    trial.protected(OVERLAY.parent, directory=True)
    if args[0] == '--rollback':
        trial.protected(BACKUP, directory=True)
        trial.protected(BACKUP / 'manifest.json')
        data = (BACKUP / 'manifest.json').read_bytes()
        if len(data) > 65536: raise RuntimeError('Backup too large')
        recover(json.loads(data), None)
        print(json.dumps({'phase': 'rolled-back', 'aacPreserved': True}))
        return
    snapshot = preflight()
    if args[0] == '--check':
        print(json.dumps({'preflight': True, 'aacPreserved': True, 'changesMade': False}))
        return
    saved = manifest(snapshot, trial.preferences())
    BACKUP.mkdir(mode=0o700)
    trial.save(BACKUP / 'manifest.json', (json.dumps(saved) + '\n').encode(), 0o600)
    trial.save(BACKUP / 'overlay-before.xml', BASELINE, 0o600)
    status('prepared')
    stage = 'stopping'
    def installing(value):
        nonlocal stage
        stage = value; status('installing', stage)
    try:
        installing('stopping'); trial.stop()
        installing('writing-overlay'); trial.save(TEMP, CODE, 0o644); os.replace(TEMP, OVERLAY)
        installing('starting'); trial.start()
        installing('waiting-h264'); trial.ready()
        installing('verifying-overlay')
        pid, guest = aac.guest_path()
        mounted = guest / 'vendor/etc/media_codecs_c2.xml'
        trial.protected(mounted)
        if mounted.read_bytes() != CODE: raise RuntimeError('AVC overlay not mounted')
        installing('verifying-codecs')
        after = read_codecs()
        trial.save(BACKUP / 'codecs-after.json', (json.dumps(after) + '\n').encode(), 0o600)
        verify_selection(snapshot, after)
        installing('verifying-state')
        if aac.guest_path()[0] != pid or trial.digest(trial.CFG.read_bytes()) != saved['config'] or \
                trial.preferences() != saved['preferences']:
            raise RuntimeError('Container/config/preferences changed')
        status('ready')
        print(json.dumps({'phase': 'ready', 'selectedAvc': 'c2.android.avc.decoder', 'aacPreserved': True,
                          'paidPlaybackAccepted': False}))
    except Exception:
        recover(saved, stage)
        raise RuntimeError('AVC trial failed; accepted AAC baseline restored') from None


if __name__ == '__main__':
    try: main(sys.argv[1:])
    except Exception:
        print('AVC trial did not complete; inspect protected status', file=sys.stderr)
        sys.exit(1)
