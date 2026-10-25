# SPDX-License-Identifier: GPL-3.0-only
"""o2 uses the initializer's supported override file; o remains immutable."""
import importlib.util
import ctypes
import os
from pathlib import Path
import stat
import sys

spec = importlib.util.spec_from_file_location('mesa_boot', Path(__file__).with_name('waydroid-gpu-mesa-allocator-trial.py'))
allocator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(allocator)
engine = allocator.engine
engine.BACKUP = engine.trial.BACKUPS / 'gpu-render-mesa-boot-20261011o2'
engine.ORIGINAL = engine.BACKUP / 'original.cfg'
engine.LOCK = engine.trial.BACKUPS / '.gpu-render-mesa-boot-20261011o2.lock'
engine.PROFILE = 'gpu-render-codec4-rgbx-layers-mesa-boot-override'
FILE = Path('/home/hilman/.local/share/waydroid/data/misc/waydroid_settings')
CODE = b'ro.hardware.gralloc=minigbm_gbm_mesa\n'
TEMP = '.tesla-mesa-allocator-20261011o2.tmp'
baseline_prepare = engine.prepare_extension
baseline_validate = engine.validate_extension
baseline_start = engine.start_trial


def parent_metadata():
    info = FILE.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 1000 or info.st_gid != 9998 or stat.S_IMODE(info.st_mode) != 0o1771:
        raise RuntimeError('Unexpected guest misc directory')
    return {'version': 1, 'beforeAbsent': True, 'candidate': engine.trial.digest(CODE),
            'parentDevice': info.st_dev, 'parentInode': info.st_ino}


def validate_metadata(value):
    keys = {'version', 'beforeAbsent', 'candidate', 'parentDevice', 'parentInode'}
    if not isinstance(value, dict) or set(value) != keys or value.get('version') != 1 or \
        value.get('beforeAbsent') is not True or value.get('candidate') != engine.trial.digest(CODE) or \
        any(type(value.get(key)) is not int or value[key] < 1 for key in ('parentDevice', 'parentInode')):
        raise RuntimeError('Invalid allocator override identity')


def open_parent(value):
    validate_metadata(value)
    if parent_metadata() != value:
        raise RuntimeError('Guest misc directory changed')
    fd = os.open(FILE.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if info.st_dev != value['parentDevice'] or info.st_ino != value['parentInode']:
        os.close(fd)
        raise RuntimeError('Guest misc directory raced')
    return fd


def owned_file(parent_fd, temporary=False):
    name = TEMP if temporary else FILE.name
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or \
            stat.S_IMODE(info.st_mode) != 0o600 or not 0 <= info.st_size <= len(CODE):
            raise RuntimeError('Foreign allocator override must not be changed')
        data = os.read(fd, len(CODE) + 1)
        if (not temporary and data != CODE) or (temporary and data != CODE[:info.st_size]):
            raise RuntimeError('Foreign allocator override must not be changed')
        return True
    finally:
        os.close(fd)


def prepare_extension():
    layer = baseline_prepare()
    pid, root = engine.guest_identity()
    if FILE.exists() or FILE.is_symlink() or (FILE.parent / TEMP).exists() or (FILE.parent / TEMP).is_symlink() or \
        not os.path.samefile(FILE.parent, root / 'data/misc'):
        raise RuntimeError('Expected absent mounted allocator override')
    meta = parent_metadata()
    if engine.guest_identity()[0] != pid:
        raise RuntimeError('Container changed during override preparation')
    return {'layerProperty': layer, 'allocatorOverride': meta}


def validate_extension(manifest):
    extension = manifest.get('extension')
    if not isinstance(extension, dict) or set(extension) != {'layerProperty', 'allocatorOverride'}:
        raise RuntimeError('Invalid allocator boot backup')
    baseline_validate({'extension': extension['layerProperty']})
    fd = open_parent(extension['allocatorOverride'])
    try:
        owned_file(fd)
        owned_file(fd, temporary=True)
    finally:
        os.close(fd)


def start_trial(manifest, restoring=False):
    allocator.layers.assert_stopped()
    validate_extension(manifest)
    engine.trial.protected(engine.trial.CFG)
    if engine.trial.digest(engine.trial.CFG.read_bytes()) != manifest.get('before' if restoring else 'candidate'):
        raise RuntimeError('Config changed before allocator boot override')
    fd = open_parent(manifest['extension']['allocatorOverride'])
    try:
        present = owned_file(fd)
        staged = owned_file(fd, temporary=True)
        if restoring:
            if present:
                os.unlink(FILE.name, dir_fd=fd)
                os.fsync(fd)
            if staged:
                os.unlink(TEMP, dir_fd=fd)
                os.fsync(fd)
        else:
            if present or staged:
                raise RuntimeError('Allocator override already installed')
            output = os.open(TEMP, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=fd)
            try:
                os.fchmod(output, 0o600)
                with os.fdopen(output, 'wb', closefd=False) as stream:
                    stream.write(CODE)
                    stream.flush()
                    os.fsync(output)
            finally:
                os.close(output)
            # Atomically publish without replacing any newly-arrived foreign file.
            rename = ctypes.CDLL(None, use_errno=True).renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(fd, TEMP.encode(), fd, FILE.name.encode(), 1) != 0:
                raise RuntimeError('Allocator override atomic publication refused')
            os.fsync(fd)
    finally:
        os.close(fd)
    # The inherited offline layer store must see its original extension shape.
    layered = {**manifest, 'extension': manifest['extension']['layerProperty']}
    # Its validation hook is dynamic; retain our full manifest for that callback.
    old_validate = engine.validate_extension
    engine.validate_extension = lambda _: validate_extension(manifest)
    try:
        baseline_start(layered, restoring=restoring)
    finally:
        engine.validate_extension = old_validate


engine.prepare_extension = prepare_extension
engine.validate_extension = validate_extension
engine.start_trial = start_trial

if __name__ == '__main__':
    try:
        engine.main(sys.argv[1:])
    except Exception:
        print('Mesa boot GPU trial did not complete; inspect protected status metadata', file=sys.stderr)
        sys.exit(1)
