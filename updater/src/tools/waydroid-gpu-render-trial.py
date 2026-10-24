# SPDX-License-Identifier: GPL-3.0-only
"""Fixed .90 GPU rendering trial; accepted codec4/AAC and app data stay intact."""
import configparser
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys

spec = importlib.util.spec_from_file_location('render_trial', Path(__file__).with_name('waydroid-render-trial.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
BACKUP = trial.BACKUPS / 'gpu-render-codec4-20261010b'
ORIGINAL = BACKUP / 'original.cfg'
INIT = Path('/var/lib/waydroid/overlay/vendor/etc/init/waydroid-init.rc')
BASE_INIT = b'on post-fs-data\n    exec -- /vendor/bin/waydroid-init\n'
INIT_CODE = BASE_INIT + (b'    setprop media.sf.hwaccel 0\n'
                         b'    setprop debug.ffmpeg-codec2.hwaccel.drm 0\n'
                         b'    setprop debug.ffmpeg-codec2.hwaccel.force 0\n')
AAC = Path('/var/lib/waydroid/overlay/vendor/etc/media_codecs_c2.xml')
AAC_SHA = '495bd954d845b80f615ed57f61654d2ba4be8c253d9a38a55b537a3c99824924'
PROBE = Path('/var/lib/teslaandroid-waydroid/aac-priority-diagnostics-20261010c/codec-probe.js')
PROFILE = 'gpu-render-codec4-early-softdecode'
LOCK = trial.BACKUPS / '.gpu-render-codec4-20261010b.lock'
EXPECTED_NATIVE = {'ro.hardware.gralloc': 'minigbm', 'debug.stagefright.ccodec': '4',
                   'ro.waydroid.software_rendering': '0', 'persist.waydroid.use_subsurface': 'false',
                   'media.sf.hwaccel': '0', 'debug.ffmpeg-codec2.hwaccel.drm': '0',
                   'debug.ffmpeg-codec2.hwaccel.force': '0', 'sys.boot_completed': '1'}
RESTORED_PROPERTIES = {}
NATIVE_PROPERTY_VALUES = frozenset({'minigbm', 'default', '0', '1', '3', '4', 'false', 'true', '',
                                    'RGBX_8888', 'YUV_420', 'RGB_565'})
STAGES = {'prepared', 'stopping', 'installing', 'starting', 'waiting-h264',
          'verifying-native', 'ready', 'recovering', 'restored', 'recovery-required'}


def candidate(data):
    if len(data) > 65536:
        raise ValueError('Invalid graphics config')
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode())
    except (configparser.Error, UnicodeError):
        raise ValueError('Invalid graphics config') from None
    expected = {'ro.waydroid.software_rendering': '1', 'debug.stagefright.ccodec': '4',
                'persist.waydroid.use_subsurface': 'false'}
    if not parser.has_section('waydroid') or any(
            parser.get('properties', key, fallback='') != value for key, value in expected.items()):
        raise ValueError('Accepted software/codec4/subsurface baseline required')
    if any(parser.has_option('properties', key) for key in ['ro.hardware.egl', 'ro.hardware.gralloc']):
        raise ValueError('Explicit graphics overrides must not be replaced')
    parser.set('properties', 'ro.waydroid.software_rendering', '0')
    stream = io.StringIO()
    parser.write(stream)
    return stream.getvalue().encode()


def guest_identity():
    pid = trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
    if not pid.isdecimal() or int(pid) < 2:
        raise RuntimeError('Invalid container identity')
    return pid, Path('/proc') / pid / 'root'


def aac_unchanged(root=None):
    paths = [AAC] + ([root / 'vendor/etc/media_codecs_c2.xml'] if root else [])
    for path in paths:
        trial.protected(path)
        if trial.digest(path.read_bytes()) != AAC_SHA:
            raise RuntimeError('Accepted AAC overlay changed')


def codec_selection():
    trial.protected(PROBE.parent, directory=True)
    for name in ['codec-probe.js', 'host-adapter.js', 'frame-check.js']:
        trial.protected(PROBE.with_name(name))
    modules = trial.ROOT + '/runtime/waydroid-display/node_modules:' + trial.ROOT + '/runtime/preview-gateway/node_modules'
    data = trial.run(['/usr/bin/env', 'NODE_PATH=' + modules, '/usr/bin/node', str(PROBE)], 30)
    if len(data) > 65536:
        raise RuntimeError('Codec metadata too large')
    value = json.loads(data)
    selected = value.get('selected')
    expected = {'video/avc': 'c2.ffmpeg.h264.decoder', 'video/hevc': 'c2.ffmpeg.hevc.decoder',
                'video/av01': 'c2.ffmpeg.av1.decoder', 'video/x-vnd.on2.vp9': 'c2.ffmpeg.vp9.decoder',
                'audio/mp4a-latm': 'c2.android.aac.decoder', 'audio/ac3': 'c2.ffmpeg.ac3.decoder',
                'audio/eac3': None}
    if value.get('version') != 1 or not isinstance(selected, list) or len(selected) != 7 \
            or {item.get('mime'): item.get('decoder') for item in selected} != expected:
        raise RuntimeError('Accepted decoder selection changed')
    return expected


def status(stage, **fields):
    if stage not in STAGES:
        raise ValueError('Invalid trial stage')
    value = {'profile': PROFILE, 'stage': stage, 'paidPlaybackAccepted': False, **fields}
    trial.save(BACKUP / 'status.json', (json.dumps(value) + '\n').encode(), 0o600)


def prepare_extension():
    return {}


def validate_extension(manifest):
    pass


def start_trial(manifest, restoring=False):
    trial.start()


def verify_native():
    pid, root = guest_identity()
    aac_unchanged(root)
    mounted = root / 'vendor/etc/init/waydroid-init.rc'
    trial.protected(mounted)
    if mounted.read_bytes() != INIT_CODE:
        raise RuntimeError('Early decode guard not mounted')
    values = {}
    expected = EXPECTED_NATIVE
    guest = lambda args: trial.run(['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', *args], 15)
    for key in expected:
        value = guest(['/system/bin/getprop', key])
        values[key] = value if value in NATIVE_PROPERTY_VALUES else 'other'
    lines = guest(['/system/bin/dumpsys', 'SurfaceFlinger']).splitlines()
    renderer = next((line for line in lines if line.startswith('GLES:')), '')
    gpu = 'virgl' in renderer.lower() and not any(word in renderer.lower() for word in ['swiftshader', 'llvmpipe'])
    report = {'properties': values, 'surfaceFlingerVirgl': gpu, 'softwareRenderer': not gpu}
    trial.save(BACKUP / 'native-state.json', (json.dumps(report) + '\n').encode(), 0o600)
    if values != expected or not gpu:
        raise RuntimeError('Android GPU/early soft-decode verification failed')
    codec_selection()
    if guest_identity()[0] != pid:
        raise RuntimeError('Container changed during verification')


def revert(manifest):
    if manifest.get('profile') != PROFILE or manifest.get('init') != trial.digest(INIT_CODE) \
            or manifest.get('gpu') != trial.digest(trial.GPU_CODE) or manifest.get('aac') != AAC_SHA:
        raise RuntimeError('Invalid GPU rendering backup')
    trial.protected(ORIGINAL)
    original = ORIGINAL.read_bytes()
    if trial.digest(original) != manifest.get('before') or trial.digest(candidate(original)) != manifest.get('candidate'):
        raise RuntimeError('GPU rendering backup changed')
    trial.protected(trial.CFG)
    current = trial.digest(trial.CFG.read_bytes())
    if current not in [manifest['before'], manifest['candidate']]:
        raise RuntimeError('Config changed; refusing stale rollback')
    aac_unchanged()
    if trial.DECODER_XML.exists() or trial.DECODER_XML.is_symlink():
        raise RuntimeError('Foreign decoder overlay; refusing stale rollback')
    for path, code in [(INIT, INIT_CODE), (trial.GPU_OVERRIDE, trial.GPU_CODE)]:
        if path.exists() or path.is_symlink():
            trial.protected(path)
            if path.read_bytes() != code:
                raise RuntimeError('GPU rendering trial file changed; refusing stale rollback')
    validate_extension(manifest)
    trial.stop()
    if current != manifest['before']:
        trial.save(trial.CFG, original, 0o644)
    for path in [INIT, trial.GPU_OVERRIDE]:
        if path.exists():
            path.unlink()
    start_trial(manifest, restoring=True)
    trial.ready()
    pid, root = guest_identity()
    aac_unchanged(root)
    guest = lambda args: trial.run(['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', *args], 15)
    if guest(['/system/bin/getprop', 'ro.waydroid.software_rendering']) != '1' \
            or guest(['/system/bin/getprop', 'debug.stagefright.ccodec']) != '4':
        raise RuntimeError('Software/codec4 baseline not recovered')
    renderer = next((line for line in guest(['/system/bin/dumpsys', 'SurfaceFlinger']).splitlines()
                     if line.startswith('GLES:')), '')
    if 'swiftshader' not in renderer.lower():
        raise RuntimeError('Software renderer not recovered')
    for key, value in RESTORED_PROPERTIES.items():
        if guest(['/system/bin/getprop', key]) != value:
            raise RuntimeError('Baseline graphics properties not recovered')
    codec_selection()
    if guest_identity()[0] != pid:
        raise RuntimeError('Container changed during recovery')


def recover(manifest, failed_stage=None):
    # Status I/O failure must not prevent baseline restoration.
    fields = {'failedStage': failed_stage} if failed_stage in STAGES else {}
    for stage in ['recovering', 'restored']:
        try:
            status(stage, **fields)
        except (OSError, RuntimeError):
            pass
        if stage == 'recovering':
            try:
                revert(manifest)
            except Exception:
                try:
                    status('recovery-required', **fields)
                except (OSError, RuntimeError):
                    pass
                raise RuntimeError('GPU rendering recovery requires inspection') from None


def main(args):
    import fcntl
    if os.getuid() != 0 or len(args) != 1 or args[0] not in ['--apply', '--rollback']:
        raise RuntimeError('Fixed root GPU rendering trial required')
    trial.protected(trial.CFG.parent, directory=True)
    trial.protected(trial.BACKUPS, directory=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
            raise RuntimeError('Unsafe GPU rendering lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        perform(args[0])
    finally:
        os.close(fd)


def perform(action):
    trial.protected(trial.CFG)
    if action == '--rollback':
        trial.protected(BACKUP, directory=True)
        trial.protected(BACKUP / 'manifest.json')
        manifest = json.loads((BACKUP / 'manifest.json').read_text())
        recover(manifest)
        print(json.dumps({'restored': True, 'h264Ready': True, 'preferencesRestored': False}))
        return
    for unit in [trial.RUNTIME, trial.SESSION, trial.COMPOSITOR]:
        if trial.service('is-active', unit) != 'active':
            raise RuntimeError('Owned runtime must be active')
    for directory in [INIT.parent, trial.GPU_OVERRIDE.parent]:
        trial.protected(directory, directory=True)
    for path in [INIT, trial.GPU_OVERRIDE, trial.DECODER_XML]:
        if path.exists() or path.is_symlink():
            raise RuntimeError('Previous or foreign trial must not be overwritten')
    aac_unchanged()
    pid, root = guest_identity()
    source = root / 'vendor/etc/init/waydroid-init.rc'
    trial.protected(source)
    if source.read_bytes() != BASE_INIT:
        raise RuntimeError('Unexpected Waydroid initializer action')
    codec_selection()
    if guest_identity()[0] != pid:
        raise RuntimeError('Container changed during preparation')
    original = trial.CFG.read_bytes()
    changed = candidate(original)
    before = trial.preferences()
    manifest = {'profile': PROFILE, 'before': trial.digest(original), 'candidate': trial.digest(changed),
                'init': trial.digest(INIT_CODE), 'gpu': trial.digest(trial.GPU_CODE), 'aac': AAC_SHA,
                'preferences': before, 'extension': prepare_extension()}
    BACKUP.mkdir(mode=0o700)
    trial.save(ORIGINAL, original, 0o600)
    trial.save(BACKUP / 'manifest.json', (json.dumps(manifest) + '\n').encode(), 0o600)
    phase = 'prepared'
    def progress(stage, **fields):
        nonlocal phase
        phase = stage
        status(stage, **fields)
    try:
        progress('prepared')
        progress('stopping')
        trial.stop()
        progress('installing')
        trial.save(trial.CFG, changed, 0o644)
        trial.save(INIT, INIT_CODE, 0o644)
        trial.save(trial.GPU_OVERRIDE, trial.GPU_CODE, 0o644)
        progress('starting')
        start_trial(manifest)
        progress('waiting-h264')
        trial.ready()
        progress('verifying-native')
        verify_native()
        progress('ready', preferencesUnchanged=before == trial.preferences())
        print(json.dumps({'androidGpuRendering': True, 'hardwareVideoDecode': False,
                          'codec4AndAacPreserved': True, 'paidPlaybackAccepted': False}))
    except Exception:
        recover(manifest, failed_stage=phase)
        raise RuntimeError('GPU rendering trial failed; accepted baseline restored') from None


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('GPU rendering trial did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
