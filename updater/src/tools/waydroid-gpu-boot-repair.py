# SPDX-License-Identifier: GPL-3.0-only
"""One new .90 repair transaction; old apply/rollback entry points never run."""
import configparser
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import time

ROOT = Path('/opt/teslaandroid-waydroid/gpu-boot-repair-20261017dv2')
OLD = Path('/opt/teslaandroid-waydroid/gpu-health-trial-20261011u/tools')
BACKUP = Path('/var/lib/teslaandroid-waydroid/backups/gpu-boot-repair-20261017dv2')
CFG = Path('/var/lib/waydroid/waydroid.cfg')
INIT = Path('/var/lib/waydroid/overlay/vendor/etc/init/waydroid-init.rc')
MISC = Path('/home/hilman/.local/share/waydroid/data/misc')
OVERRIDE = MISC / 'waydroid_settings'
PROPERTY = MISC.parent / 'property/persistent_properties'
DROP = Path('/etc/systemd/system/tesla-waydroid-compositor.service.d/65-gpu-boot-repair-20261017dv2.conf')
GUARD = 'tesla-gpu-boot-repair-20261017dv2.service'
UNIT = Path('/etc/systemd/system') / GUARD
BASE = 'd71721d0ff285d42ba8792814d4a52cc43cd34a08282a5ee630c589c6ed1af5e'
GPU = 'eb39cf4d68dd4796adbfbbf102007840a6a1cc6febc86337f8077ec4d85513f6'
AAC = Path('/var/lib/waydroid/overlay/vendor/etc/media_codecs_c2.xml')
AAC_SHA = '495bd954d845b80f615ed57f61654d2ba4be8c253d9a38a55b537a3c99824924'
CODE = b'ro.hardware.gralloc=minigbm_gbm_mesa\n'
INIT_CODE = (b'on post-fs-data\n    exec -- /vendor/bin/waydroid-init\n'
             b'    setprop media.sf.hwaccel 0\n'
             b'    setprop debug.ffmpeg-codec2.hwaccel.drm 0\n'
             b'    setprop debug.ffmpeg-codec2.hwaccel.force 0\n'
             b'    setprop debug.ffmpeg-codec2.pixel_format RGBX_8888\n')
DROP_CODE = (b'[Service]\nSupplementaryGroups=render video\nExecStart=\n'
             b'ExecStart=/usr/bin/weston --backend=pipewire --renderer=gl '
             b'--socket=wayland-teslaandroid --width=1280 --height=720 '
             b'--idle-time=0 --no-config --shell=kiosk-shell.so\n')
UNIT_CODE = (f'[Unit]\nDescription=DroidDeck preview GPU boot repair health guard\n'
             f'After=local-fs.target\n\n[Service]\nType=simple\nUser=root\n'
             f'ExecStart=/usr/bin/python3 -B {ROOT}/waydroid-gpu-boot-repair.py --watch\n'
             f'Restart=on-failure\nRestartSec=5s\nUMask=0077\n\n'
             f'[Install]\nWantedBy=multi-user.target\n').encode()
HELPERS = {
    'waydroid-render-trial.py': '014759a49a3ccdb862c6cc312a3d8dc37d1ad01d37a0565605ea6cc8bb85f264',
    'waydroid-layer-property.py': 'b552181635c210b11a3fa9a23c2bf8af21ad7f929171e9ac2649eb9f631616c3',
    'waydroid-gpu-health-watch.py': 'ef35467355fa3375f17bfb99445737cdb34992d02c026631595af30276678be5',
    'waydroid-gpu-health-policy.py': '7b089fae3af9eb49e946cf0518b0c5d6908cb58eba83330326a482578b3db3cf',
}
PINS = {
    Path('/etc/systemd/system/tesla-waydroid-compositor.service'): '3c1ec37194256040cd27b673722e83a3d41971725d3322e8889ccfc03c89dbef',
    DROP.parent / '50-compositor-capture.conf': '0648b1ddf8c5d842c69062e4e1ad49e63b35dca88e14961b5cd08f517b85d701',
    Path('/etc/systemd/system/tesla-waydroid-headless-session.service'): '85373baccbc718897cbada535e5a08efb84607b96ceeacfadd090ae3bce8ea33',
    Path('/opt/teslaandroid-waydroid/runtime/waydroid-display/pid-compat.js'): 'bc0e78e71baec68a21fe84ecc5a3a3d535a103435688bbea912486af07babb61',
    AAC: AAC_SHA,
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def protected(path, directory=False):
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Protected repair path required')
    return info


def trusted(path):
    for parent in reversed(path.parents):
        protected(parent, directory=True)
    protected(path)


def pinned(path, expected):
    trusted(path)
    if digest(path.read_bytes()) != expected:
        raise RuntimeError('Repair baseline source changed')


def load(name):
    path = OLD / name
    pinned(path, HELPERS[name])
    spec = importlib.util.spec_from_file_location('repair_' + name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate(raw):
    if digest(raw) != BASE:
        raise RuntimeError('Exact recovered software configuration required')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(raw.decode())
    for key, value in {'ro.waydroid.software_rendering': '0',
                       'ro.hardware.gralloc': 'minigbm_gbm_mesa',
                       'persist.waydroid.use_subsurface': 'true'}.items():
        parser.set('properties', key, value)
    output = io.StringIO()
    parser.write(output)
    result = output.getvalue().encode()
    if digest(result) != GPU:
        raise RuntimeError('Accepted graphics configuration recipe changed')
    return result


def absent(path):
    if path.exists() or path.is_symlink():
        raise RuntimeError('Repair never replaces a foreign or previous output')


def misc_fd():
    info = MISC.lstat()
    if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (1000, 9998, 0o1771):
        raise RuntimeError('Unexpected mounted guest misc directory')
    fd = os.open(MISC, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    opened = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
        os.close(fd)
        raise RuntimeError('Guest misc directory changed')
    return fd


def override(removing=False):
    fd = misc_fd()
    try:
        if removing:
            try:
                leaf = os.open(OVERRIDE.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                return
            try:
                info = os.fstat(leaf)
                if not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)) != (0, 0, 1, 0o600) or os.read(leaf, 128) != CODE:
                    raise RuntimeError('Foreign allocator override retained')
                current = os.stat(OVERRIDE.name, dir_fd=fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise RuntimeError('Allocator override changed')
                os.unlink(OVERRIDE.name, dir_fd=fd)
            finally:
                os.close(leaf)
        else:
            leaf = os.open(OVERRIDE.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
            try:
                os.fchmod(leaf, 0o600)
                if os.write(leaf, CODE) != len(CODE):
                    raise RuntimeError('Incomplete allocator override retained for inspection')
                os.fsync(leaf)
            finally:
                os.close(leaf)
        os.fsync(fd)
        current = MISC.lstat()
        if (current.st_dev, current.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
            raise RuntimeError('Guest misc directory changed')
    finally:
        os.close(fd)


def report(value):
    print(json.dumps(value), flush=True)


def state(trial, stage):
    trial.save(BACKUP / 'status.json', (json.dumps({'profile': 'gpu-boot-repair-20261017dv2', 'stage': stage}) + '\n').encode(), 0o600)


def read_stage():
    path = BACKUP / 'status.json'
    trusted(path)
    value = json.loads(path.read_bytes())
    if set(value) != {'profile', 'stage'} or value['profile'] != 'gpu-boot-repair-20261017dv2':
        raise RuntimeError('Foreign repair status')
    return value['stage']


def store(trial, layers):
    def stopped():
        if trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-sH'], 10) != 'STOPPED':
            raise RuntimeError('Stopped container required for layer property')
    def private(path, directory=False):
        info = protected(path, directory)
        if stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
            raise RuntimeError('Private layer property required')
        return info
    return layers.OfflineStore(PROPERTY, private, stopped, trial.save)


def baseline(trial, layers):
    for path, sha in PINS.items():
        pinned(path, sha)
    for path in (BACKUP, DROP, INIT, OVERRIDE, UNIT):
        absent(path)
    if trial.service('show', 'tesla-gpu-health-watch-20261011u.service', '-p', 'ActiveState', '--value') != 'inactive':
        raise RuntimeError('Original guard must already have completed recovery')
    old_status = BACKUP.parent / 'gpu-render-health-20261011u/status.json'
    trusted(old_status)
    previous = json.loads(old_status.read_bytes())
    if previous.get('stage') != 'restored' or previous.get('profile') != 'gpu-render-codec4-rgbx-mesa-boot-health':
        raise RuntimeError('Original guard recovery must remain complete')
    pinned(CFG, BASE)
    candidate(CFG.read_bytes())
    if store(trial, layers).value() != 'false':
        raise RuntimeError('Recovered layer property required')
    fd = misc_fd()
    os.close(fd)
    pid = trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
    if not pid.isdecimal() or int(pid) < 2 or not os.path.samefile(MISC, Path('/proc', pid, 'root/data/misc')):
        raise RuntimeError('Mounted guest misc identity required')
    for unit in (trial.RUNTIME, trial.SESSION, trial.COMPOSITOR):
        if trial.service('is-active', unit) != 'active':
            raise RuntimeError('Working baseline services required')


def healthy(trial):
    try:
        units = ['tesla-waydroid-' + name + '-preview.service' for name in ('gateway', 'video', 'video-relay', 'touch', 'display', 'display-helper', 'audio', 'audio-relay')]
        if trial.service('is-active', *units).splitlines() != ['active'] * len(units):
            raise RuntimeError('preview-peers')
        pid = trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
        if not pid.isdecimal() or int(pid) < 2:
            raise RuntimeError('container-identity')
        guest = lambda *args: trial.run(['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', *args], 10)
        expected = {'sys.boot_completed': '1', 'ro.hardware.gralloc': 'minigbm_gbm_mesa',
                    'init.svc.vendor.graphics.allocator-4-0-gbm_mesa': 'running',
                    'ro.waydroid.software_rendering': '0', 'debug.stagefright.ccodec': '4',
                    'persist.waydroid.use_subsurface': 'true', 'media.sf.hwaccel': '0',
                    'debug.ffmpeg-codec2.hwaccel.drm': '0', 'debug.ffmpeg-codec2.hwaccel.force': '0',
                    'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
        for key, value in expected.items():
            if guest('/system/bin/getprop', key) != value:
                raise RuntimeError('native-property:' + key)
        renderer = next((line for line in guest('/system/bin/dumpsys', 'SurfaceFlinger').splitlines() if line.startswith('GLES:')), '').lower()
        if 'virgl' not in renderer or any(word in renderer for word in ('llvmpipe', 'swiftshader')):
            raise RuntimeError('native-renderer')
        compositor = trial.service('show', trial.COMPOSITOR, '-p', 'MainPID', '--value')
        groups = next(line for line in Path('/proc', compositor, 'status').read_text().splitlines() if line.startswith('Groups:')).split()[1:]
        if not {'44', '990'} <= set(groups):
            raise RuntimeError('compositor-device-groups')
        sf = guest('/system/bin/pidof', 'surfaceflinger')
        if not sf.isdecimal():
            raise RuntimeError('allocator-consumer')
        maps = guest('/system/bin/cat', '/proc/' + sf + '/maps')
        if '/vendor/lib64/libminigbm_gralloc_gbm_mesa.so' not in maps or not any(name in maps for name in ('gralloc.minigbm_gbm_mesa.so', 'android.hardware.graphics.mapper@4.0-impl.minigbm_gbm_mesa.so')):
            raise RuntimeError('allocator-consumer')
        pinned(AAC, AAC_SHA)
        if trial.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10) != pid:
            raise RuntimeError('container-generation')
        return True
    except Exception as error:
        # Never export raw guest output, application names or property values.
        reason = str(error) if str(error).startswith(('native-property:', 'native-renderer', 'preview-peers', 'compositor-device-groups', 'allocator-consumer', 'container-')) else type(error).__name__
        report({'healthy': False, 'reason': reason})
        return False


def restore(trial, layers):
    protected(BACKUP, directory=True)
    pinned(BACKUP / 'software.cfg', BASE)
    current = digest(CFG.read_bytes())
    if current not in (BASE, GPU):
        raise RuntimeError('Foreign live configuration retained')
    for path, code in ((DROP, DROP_CODE), (INIT, INIT_CODE)):
        if path.exists() or path.is_symlink():
            trusted(path)
            if path.read_bytes() != code:
                raise RuntimeError('Foreign repair output retained')
    state(trial, 'recovering')
    trial.stop()
    store(trial, layers).set_value(('false', 'true'), 'false')
    override(removing=True)
    trial.save(CFG, (BACKUP / 'software.cfg').read_bytes(), 0o644)
    for path in (DROP, INIT):
        if path.exists():
            path.unlink()
    trial.start()
    trial.ready()
    state(trial, 'restored')
    report({'softwareRestored': True, 'h264Ready': True})


def wait_healthy(trial, *, clock=time.monotonic, sleep=time.sleep):
    # H264 may become ready before the supervisor has started audio/gateway peers.
    deadline = clock() + 120
    while clock() < deadline:
        if healthy(trial):
            return True
        sleep(2)
    return False


def apply(trial, layers):
    baseline(trial, layers)
    BACKUP.mkdir(mode=0o700)
    trial.save(BACKUP / 'software.cfg', CFG.read_bytes(), 0o600)
    trial.save(BACKUP / 'source.sha256', (digest(Path(__file__).read_bytes()) + '\n').encode(), 0o600)
    state(trial, 'prepared')
    trial.save(UNIT, UNIT_CODE, 0o644)
    trial.service('daemon-reload')
    trial.service('enable', '--now', GUARD)
    if trial.service('is-active', GUARD) != 'active':
        raise RuntimeError('Independent recovery guard required before mutation')
    try:
        trial.stop()
        state(trial, 'installing')
        store(trial, layers).set_value(('false',), 'true')
        override()
        trial.save(INIT, INIT_CODE, 0o644)
        trial.save(DROP, DROP_CODE, 0o644)
        trial.save(CFG, candidate((BACKUP / 'software.cfg').read_bytes()), 0o644)
        state(trial, 'starting')
        trial.start()
        trial.ready()
        if not wait_healthy(trial):
            raise RuntimeError('GPU repair native verification failed')
        pinned(CFG, GPU)
        state(trial, 'ready')
        report({'gpuRestored': True, 'serviceGroupsPersistent': True, 'h264Ready': True, 'paidPlaybackAccepted': False})
    except Exception:
        restore(trial, layers)
        raise


def main(args):
    if args not in (['--prepare'], ['--apply'], ['--watch'], ['--restore']) or sys.platform != 'linux' or os.getuid() != 0:
        raise RuntimeError('Fixed root preview repair required')
    if Path(__file__).resolve() != ROOT / 'waydroid-gpu-boot-repair.py' or Path('/etc/hostname').read_text().strip() != 'Waydroid-TeslaAndroid-Test':
        raise RuntimeError('Exact preview repair deployment required')
    trusted(Path(__file__))
    for name, sha in HELPERS.items():
        pinned(OLD / name, sha)
    trial, layers = load('waydroid-render-trial.py'), load('waydroid-layer-property.py')
    if args == ['--prepare']:
        baseline(trial, layers)
        report({'readOnlyRepairPreflight': True})
        return
    if args == ['--watch']:
        watcher = load('waydroid-gpu-health-watch.py')
        def recover():
            with lock():
                restore(trial, layers)
        watcher.watch(read_stage, lambda: healthy(trial), recover, report=report)
        return
    with lock():
        if args == ['--apply']:
            apply(trial, layers)
        else:
            restore(trial, layers)


class lock:
    def __enter__(self):
        import fcntl
        path = BACKUP.parent / '.gpu-boot-repair-20261017dv2.lock'
        protected(path.parent, directory=True)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        info = os.fstat(self.fd)
        if not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)) != (0, 0, 1, 0o600):
            os.close(self.fd)
            raise RuntimeError('Unsafe repair lock')
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args):
        os.close(self.fd)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('GPU boot repair requires inspection; no old deployment helper replayed', file=sys.stderr)
        sys.exit(1)
