# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import configparser
import io
import json
import os
from pathlib import Path
import sys
import subprocess

spec = importlib.util.spec_from_file_location('render_trial', Path(__file__).with_name('waydroid-render-trial.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
BACKUP = trial.BACKUPS / 'gpu-softdecode-bootcomplete-20261010'
ORIGINAL = BACKUP / 'original'
INIT = Path('/var/lib/waydroid/overlay/vendor/etc/init/zz-tesla-softdecode-bootcomplete-20261010.rc')
INIT_CODE = (b'on property:sys.boot_completed=1\n'
             b'    setprop media.sf.hwaccel 0\n'
             b'    setprop debug.ffmpeg-codec2.hwaccel.drm 0\n'
             b'    setprop debug.ffmpeg-codec2.hwaccel.force 0\n')


def candidate(data):
    if len(data) > 65536:
        raise ValueError('Invalid graphics config')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(data.decode())
    if not parser.has_section('waydroid') or parser.get('properties', 'ro.waydroid.software_rendering', fallback='') != '1' \
            or parser.get('properties', 'debug.stagefright.ccodec', fallback='') != '4':
        raise ValueError('Recovered software/codec4 baseline required')
    parser.set('properties', 'ro.waydroid.software_rendering', '0')
    parser.set('properties', 'debug.stagefright.ccodec', '3')
    stream = io.StringIO()
    parser.write(stream)
    return stream.getvalue().encode()


def revert(manifest):
    if manifest.get('profile') != 'gpu-softdecode':
        raise RuntimeError('Invalid graphics backup')
    trial.protected(ORIGINAL)
    original = ORIGINAL.read_bytes()
    if trial.digest(original) != manifest['before']:
        raise RuntimeError('Graphics backup changed')
    if trial.digest(trial.CFG.read_bytes()) not in [manifest['before'], manifest['candidate']]:
        raise RuntimeError('Graphics config changed; refusing stale rollback')
    for path, code in [(trial.GPU_OVERRIDE, trial.GPU_CODE), (INIT, INIT_CODE)]:
        if path.exists() or path.is_symlink():
            trial.protected(path)
            if path.read_bytes() != code:
                raise RuntimeError('Graphics trial file changed; refusing stale rollback')
    trial.stop()
    trial.save(trial.CFG, original, 0o644)
    for path in [trial.GPU_OVERRIDE, INIT]:
        if path.exists():
            path.unlink()
    trial.start()
    trial.ready()


def verify_native():
    pid = trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
    if not pid.isdecimal() or int(pid) < 2:
        raise RuntimeError('Invalid container identity')
    expected_values = {'ro.hardware.gralloc': 'minigbm', 'debug.stagefright.ccodec': '3',
                       'media.sf.hwaccel': '0', 'debug.ffmpeg-codec2.hwaccel.drm': '0',
                       'debug.ffmpeg-codec2.hwaccel.force': '0'}
    values = {}
    for prop in [*expected_values, 'waydroid.init.done']:
        value = trial.run(['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', '/system/bin/getprop', prop], 10)
        allowed = {'default', 'minigbm', 'minigbm_gbm_mesa'} if prop == 'ro.hardware.gralloc' else {'', '0', '1', '3', '4'}
        values[prop] = value if value in allowed else 'other'
    trial.save(BACKUP / 'native-state.json', (json.dumps(values) + '\n').encode(), 0o600)
    for prop, expected in expected_values.items():
        if values[prop] != expected:
            raise RuntimeError('Graphics/decoder trial property did not load: ' + prop)
    mounted = Path('/proc') / pid / 'root/vendor/etc/init' / INIT.name
    trial.protected(mounted)
    if mounted.read_bytes() != INIT_CODE:
        raise RuntimeError('Software decode hook not mounted')
    if trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10) != pid:
        raise RuntimeError('Container changed during graphics verification')


def failure_reason(error):
    message = str(error)
    prefix = 'Graphics/decoder trial property did not load: '
    properties = {'ro.hardware.gralloc', 'debug.stagefright.ccodec', 'media.sf.hwaccel',
                  'debug.ffmpeg-codec2.hwaccel.drm', 'debug.ffmpeg-codec2.hwaccel.force'}
    if message.startswith(prefix) and message[len(prefix):] in properties:
        return 'property-mismatch:' + message[len(prefix):]
    if message == 'H264 recovery deadline exceeded':
        return 'h264-readiness-timeout'
    if isinstance(error, subprocess.TimeoutExpired):
        return 'command-timeout'
    return 'unexpected-error'


def status(phase, outcome, reason=None, rollback=None):
    report = {'phase': phase, 'outcome': outcome}
    if reason is not None:
        report.update(reason=reason, rollback=rollback)
    trial.save(BACKUP / 'status.json', (json.dumps(report) + '\n').encode(), 0o600)


def main(args):
    if os.getuid() != 0 or len(args) != 1 or args[0] not in ['--apply', '--rollback', '--diagnose-retry']:
        raise RuntimeError('Fixed root graphics trial required')
    trial.protected(trial.CFG)
    trial.protected(trial.CFG.parent, directory=True)
    trial.protected(trial.BACKUPS, directory=True)
    trial.protected(trial.GPU_OVERRIDE.parent, directory=True)
    if args[0] == '--rollback':
        trial.protected(BACKUP, directory=True)
        trial.protected(BACKUP / 'manifest.json')
        manifest = json.loads((BACKUP / 'manifest.json').read_text())
        status('explicit-restoration', 'running')
        try:
            revert(manifest)
        except Exception as error:
            reason = failure_reason(error)
            status('explicit-restoration', 'failed', reason, 'failed')
            raise RuntimeError('Graphics rollback failed (' + reason + ')') from None
        status('explicit-restoration', 'restored')
        print(json.dumps({'rollback': 'gpu-softdecode', 'h264Ready': True, 'preferencesRestored': False}))
        return
    for unit in [trial.RUNTIME, trial.SESSION, trial.COMPOSITOR]:
        if trial.service('is-active', unit) != 'active':
            raise RuntimeError('Owned runtime must be active before trial')
    for path in [trial.GPU_OVERRIDE, INIT, trial.DECODER_XML, INIT.parent.parent / 'media_codecs_c2.xml']:
        if path.exists() or path.is_symlink():
            raise RuntimeError('Previous or foreign trial files must not be overwritten')
    trial.protected(INIT.parent.parent, directory=True)
    if not INIT.parent.exists():
        INIT.parent.mkdir(mode=0o755)
    trial.protected(INIT.parent, directory=True)
    original = trial.CFG.read_bytes()
    changed = candidate(original)
    before = trial.preferences()
    manifest = {'profile': 'gpu-softdecode', 'before': trial.digest(original),
                'candidate': trial.digest(changed), 'preferences': before}
    diagnostic = args[0] == '--diagnose-retry'
    if diagnostic:
        trial.protected(BACKUP, directory=True)
        trial.protected(ORIGINAL)
        trial.protected(BACKUP / 'manifest.json')
        previous = json.loads((BACKUP / 'manifest.json').read_text())
        if any(previous.get(key) != manifest[key] for key in ['profile', 'before', 'candidate']) \
                or trial.digest(ORIGINAL.read_bytes()) != manifest['before']:
            raise RuntimeError('Diagnostic backup does not match recovered baseline')
    else:
        BACKUP.mkdir(mode=0o700)
        trial.save(ORIGINAL, original, 0o600)
        trial.save(BACKUP / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)
    phase = 'stopping-baseline'
    try:
        status(phase, 'running')
        trial.stop()
        phase = 'installing-candidate'
        status(phase, 'running')
        trial.save(trial.CFG, changed, 0o644)
        trial.save(trial.GPU_OVERRIDE, trial.GPU_CODE, 0o644)
        trial.save(INIT, INIT_CODE, 0o644)
        phase = 'starting-candidate'
        status(phase, 'running')
        trial.start()
        phase = 'h264-readiness'
        status(phase, 'running')
        trial.ready()
        phase = 'native-verification'
        status(phase, 'running')
        verify_native()
        if diagnostic:
            phase = 'diagnostic-restoration'
            status(phase, 'running')
            revert(manifest)
            status('verified', 'diagnosed-restored')
            print(json.dumps({'diagnostic': 'gpu-softdecode', 'h264Ready': True,
                              'preferencesUnchanged': before == trial.preferences(), 'paidPlaybackAccepted': False}))
            return
        status('verified', 'ready')
        print(json.dumps({'trial': 'gpu-softdecode', 'h264Ready': True,
                          'preferencesUnchanged': before == trial.preferences(), 'paidPlaybackAccepted': False}))
    except Exception as error:
        reason = failure_reason(error)
        status(phase, 'failed', reason, 'pending')
        try:
            revert(manifest)
        except Exception:
            status(phase, 'failed', reason, 'failed')
            raise RuntimeError('Graphics trial failed at ' + phase + ' (' + reason + '); rollback failed') from None
        status(phase, 'failed', reason, 'restored')
        raise RuntimeError('Graphics trial failed at ' + phase + ' (' + reason +
                           '); previous renderer and H264 restored') from None


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
