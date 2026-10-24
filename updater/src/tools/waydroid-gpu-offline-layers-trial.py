# SPDX-License-Identifier: GPL-3.0-only
"""Distinct g4 candidate: set and restore the saved layer key while stopped."""
import configparser
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load('offline_layers_engine', 'waydroid-gpu-render-trial.py')
properties = load('offline_layer_property', 'waydroid-layer-property.py')
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-layers-20261010g4'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-layers-20261010g4.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-layers-offline-property'
engine.INIT_CODE += b'    setprop debug.ffmpeg-codec2.pixel_format RGBX_8888\n'
engine.EXPECTED_NATIVE = {**engine.EXPECTED_NATIVE, properties.KEY: 'true',
                         'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
engine.RESTORED_PROPERTIES = {properties.KEY: 'false', 'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
PROPERTY_FILE = Path('/home/hilman/.local/share/waydroid/data/property/persistent_properties')
EXTENSION = {'version': 1, 'key': properties.KEY, 'before': 'false', 'candidate': 'true'}
baseline_candidate = engine.candidate


def candidate(data):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(baseline_candidate(data).decode())
    parser.set('properties', properties.KEY, 'true')
    stream = io.StringIO()
    parser.write(stream)
    return stream.getvalue().encode()


def assert_stopped():
    state = engine.trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-sH'], 10)
    if state != 'STOPPED':
        raise RuntimeError('Offline layer edit requires a stopped container')


def protected_property(path, directory=False):
    info = engine.trial.protected(path, directory=directory)
    if stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise RuntimeError('Saved property store must be root-private')
    return info


def store():
    return properties.OfflineStore(PROPERTY_FILE, protected_property, assert_stopped, engine.trial.save)


def prepare_extension():
    pid, root = engine.guest_identity()
    guest_file = root / 'data/property/persistent_properties'
    engine.trial.protected(guest_file)
    if not os.path.samefile(PROPERTY_FILE, guest_file) or store().value() != 'false':
        raise RuntimeError('Expected mounted software layer baseline')
    if engine.guest_identity()[0] != pid:
        raise RuntimeError('Container changed during saved property preparation')
    return dict(EXTENSION)


def validate_extension(manifest):
    if manifest.get('extension') != EXTENSION or store().value() not in ('false', 'true'):
        raise RuntimeError('Invalid offline layer property backup')


def start_trial(manifest, restoring=False):
    validate_extension(manifest)
    engine.trial.protected(engine.trial.CFG)
    expected_config = manifest.get('before' if restoring else 'candidate')
    if engine.trial.digest(engine.trial.CFG.read_bytes()) != expected_config:
        raise RuntimeError('Config changed before offline layer edit')
    store().set_value(('false', 'true') if restoring else ('false',), 'false' if restoring else 'true')
    engine.trial.start()


engine.candidate = candidate
engine.prepare_extension = prepare_extension
engine.validate_extension = validate_extension
engine.start_trial = start_trial

if __name__ == '__main__':
    try:
        engine.main(sys.argv[1:])
    except Exception:
        print('Offline layer GPU trial did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
