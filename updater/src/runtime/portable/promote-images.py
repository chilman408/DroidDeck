# SPDX-License-Identifier: GPL-3.0-only
"""Publish fixed images, artifacts and source components; never activate services."""
from contextlib import ExitStack
import configparser
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types
import xml.etree.ElementTree as ET

SOURCE_PARENT = Path('/var/lib/droiddeck/image-staging')
PARENT = Path('/etc/waydroid-extra')
WORK = Path('/var/lib/waydroid')
COPIER_SHA256 = '077de6f524d575ee67750d7f54df62d3c08115ec0490937bff14f4e99cedc59a'
STALE = ('waydroid.cfg', 'images', 'rootfs', 'overlay', 'overlay_rw', 'data', 'lxc')
_FAILED_STREAMS = []
ARTIFACT_PARENT = Path('/opt')
ARTIFACT_TARGET = 'droiddeck'
FRONTEND_MANIFEST_SHA256 = 'b7b943a36015737ceed2d03e084ebf3933784fe9ba75fd62c1877e942817a9ef'
ARTIFACT_RECEIPT_SHA256 = '804d5fa13cb1e128f82a2cb056d5fa87f7a7f4c516b0c912af7c6b839b3360c9'
FRONTEND_REVISION = '34763a62819a1dedad9d3eb62f1e175f2659091f'
FRONTEND_PATCH = 'ca11610dbd330ef03e0111ea1e5379d17dd3b8a8757c6121fa2d61dd0a651373'
RUNTIME_REVISION = '76c89fbb8de9a672a9ec843808f9eca6fd8c9528'
RUNTIME_MANIFEST_SHA256 = 'c9cdeb4d5b2d74216253404336ff6671d3ae390e524e0a173714e5d7b0c8bd4c'
CONFIGURATION_SHA256 = '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3'
SERVICE_BUNDLE_SHA256 = 'ed6aefdbdfdaf2e22bc372757c9588dc2d6a3ebc0874480e3191b4976e859cfb'
SERVICE_PLAN_SHA256 = '0bc59d1db855eed1112e3ea12ae034899dcd4117a00d1f0824187fb039c9e621'
REPAIRED_METADATA_MANIFEST_SHA256 = '9de6dc880c7608aa3f959b94f3b72537eeae9597dc02002038015195ad224f71'
SERVICE_FILES = ('pam.d/droiddeck', 'systemd/system/droiddeck-browser-connection.service',
                 'systemd/system/tesla-waydroid-compositor.service', 'systemd/system/tesla-waydroid-headless-session.service',
                 'systemd/system/tesla-waydroid-runtime.service',
                 'systemd/system/waydroid-container.service.d/50-droiddeck-session.conf', 'tmpfiles.d/droiddeck.conf')
REGISTERED_SERVICE_FILES = tuple(p for p in SERVICE_FILES if not p.endswith('/50-droiddeck-session.conf'))
WAYDROID_CONFIGURATION_SHA256 = '2d38c5fd4407e119a02fc16435c747cc659e231361b27bb4f50aee191dd51d73'
GUEST_TARGET = 'tesla-waydroid-test-20261003'
MACHINE_GUEST_TARGET = 'droiddeck-guest'
GUEST_LAUNCHER_PIN = (617, 'fe31295243f7e8f8440d4e59c4daddb06c0da26c4a310b284b7769eaf7bd6a9a')
PID_MANIFEST_SHA256 = '939582f1ddaaba2fb034fe399f842fa82026087ee4c6a982e55dabf1cf4a248c'
PID_CONFIGURATION_DIGEST = '2841520938496e5dadc0cc5c4f0a867c3c3ffaf8906e66b3a08992b69b8c76e9'
PID_FILES = ('portable/pid-startup.js', 'systemd/40-droiddeck-pid-compat.conf', 'waydroid-display/pid-compat.js')
PID_SCRIPT_PINS = {
    'portable/pid-startup.js': (1165, '81576cbbaebed73c39d608cf7cba25a10b93bfc0020868d18c1ecf03acfc9d27'),
    'waydroid-display/pid-compat.js': (6162, '410d4b10294932f31cea99dcd8364e0a84bbecc5c84d1b4b952727f3f4e6d4ef'),
}
HEADLESS_SCRIPT_PINS = {
    'headless-runtime.py': (12277, '2c0c34bf167411556e5a478669a86ec791a73e257cec117c596a0570af368d7b'),
    'headless-session.py': (4701, '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba'),
    'host-platform-compat.py': (4976, '6a1191680ed1380a1bcecbd9c6704cfa5ff9837935b24e33af9aae1fc51471b2'),
}
OWNED_HEADLESS_SCRIPT_PINS = {
    **HEADLESS_SCRIPT_PINS,
    'headless-owned-runtime.py': (8501, 'e99b053dd76f1a8054bec920007887a1729f57810c96e18500d3399d807342d8'),
}
LAYER_SOURCE_SHA256 = '568e12583f931d6ad464bff79692b11c3dbc4ff0de88725c7f0e214a2861698f'
GUEST_RECEIPT_SHA256 = 'fabc0bd5c2df4507fea2f1fd6aa87ea106096132f2e4fcd2de58cf5c35491285'
ALLOCATOR_CODE = b'ro.hardware.gralloc=minigbm_gbm_mesa\n'
ALLOCATOR_READY = 'pending-droiddeck-allocator-ready'
ALLOCATOR_RESTORED = 'pending-droiddeck-allocator-restored'
CODEC_FILES = {
    'init/waydroid-init.rc': (b'on post-fs-data\n    exec -- /vendor/bin/waydroid-init\n'
                             b'    setprop media.sf.hwaccel 0\n'
                             b'    setprop debug.ffmpeg-codec2.hwaccel.drm 0\n'
                             b'    setprop debug.ffmpeg-codec2.hwaccel.force 0\n'
                             b'    setprop debug.ffmpeg-codec2.pixel_format RGBX_8888\n'),
    'media_codecs_c2.xml': (b'<?xml version="1.0" encoding="utf-8"?>\n<Included>\n  <Decoders>\n'
                          b'    <MediaCodec name="c2.android.aac.decoder" type="audio/mp4a-latm" update="true" rank="1" />\n'
                          b'    <MediaCodec name="c2.ffmpeg.aac.decoder" type="audio/mp4a-latm" update="true" rank="512" />\n'
                          b'  </Decoders>\n</Included>\n'),
}
DEPENDENCIES = {
    'gateway': {'directory': 'preview-gateway', 'bundle': '70c5281fd6333cee014fd66de0f604ed758bfb90df46e23af729697f4b52d600',
                'runtimeManifestSha256': 'b81858425ecda17351ae3a16173e9383b3dcd9eff61dd0b8000a729b4507bd3b',
                'runtimeLockSha256': '6dcd7347aa12def63cd943a6336cb0648c657e72fea911e7cc7f8716b94c794f',
                'manifestSha256': '8fcaef9f5a9e9a0391b04a831aae75269f495a9eac662268d591891081a1af3f',
                'lockSha256': 'f901bd3bbde7880f468e1e02df179551d84f041dba0fe0789c196e63ec26560f',
                'filesSha256': '36a31e5d8046a012b1ff78716102347144af36523f4463db2669e8122b554d98'},
    'display': {'directory': 'waydroid-display', 'bundle': '32bc31152453ff27c8bd65f103b450fa859c2f197e21640e58905d578cc91bad',
                'runtimeManifestSha256': 'dadda34469dc81a6c38eaba5a633a336e266ca6fca38d6a87cdefdd05dce992a',
                'runtimeLockSha256': 'e85d82dc2276d9367edfb2273d7d2fc58c656d651c823d858b93f80c07f6d41f',
                'manifestSha256': '411ec4535eb1eda92b0218d735b02aaca8210adb15936775bfdd5b0334fc3337',
                'lockSha256': 'b694e109e850916294c12fd0850f69f0a0733e9d40b287e8b7380d0c3506e679',
                'filesSha256': 'b4a6884fea6fd759584daeefa1316d25e9ea539b0a0f6be0bd04610fa6a3be88'},
}
ARTIFACT_LAYOUT = {
    'tesla-android-x86-touch': ('guest/bin/tesla-android-x86-touch', 0o700),
    'tesla-android-x86-api': ('guest/bin/tesla-android-x86-api', 0o700),
    'tesla-android-x86-display-latest': ('guest/bin/tesla-android-x86-display-latest', 0o700),
    'location-bridge.jar': ('guest/location-bridge.jar', 0o600),
    'logical-capture.jar': ('guest/logical-capture.jar', 0o600),
    'scrcpy-server-v4.0': ('guest/scrcpy-server-v4.0.jar', 0o600),
    'libglibutil.so.1.0.80': ('libgbinder/libglibutil.so.1.0.80', 0o644),
    'libgbinder.so.1.1.45': ('libgbinder/libgbinder.so.1.1.45', 0o644),
}
ARTIFACT_LINKS = {'libglibutil.so': 'libglibutil.so.1.0.80',
                  'libglibutil.so.1': 'libglibutil.so.1.0.80',
                  'libgbinder.so': 'libgbinder.so.1.1.45',
                  'libgbinder.so.1': 'libgbinder.so.1.1.45'}
ARTIFACT_DIRECTORIES = {'guest': 0o700, 'guest/bin': 0o700, 'libgbinder': 0o755}


class PromotionUncertain(RuntimeError):
    """The final directory is published, but its durable outcome needs inspection."""


def arguments(args):
    if len(args) != 1 or not isinstance(args[0], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', args[0]):
        raise ValueError('One safe root-stage name required')
    return args[0]


def _load_copier(filename):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux helper load required')
    filename = Path(filename)
    if not filename.is_absolute() or '..' in filename.parts:
        raise ValueError('Absolute helper path required')
    with ExitStack() as stack:
        ancestors = []
        parent_fd = None
        for index in range(len(filename.parent.parts)):
            path = Path(*filename.parent.parts[:index + 1])
            fd = os.open(path.anchor if parent_fd is None else path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            stack.callback(os.close, fd)
            opened = os.fstat(fd)
            current = path.lstat()
            if ((current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                    or not stat.S_ISDIR(current.st_mode) or opened.st_uid != 0
                    or opened.st_gid != 0 or opened.st_mode & 0o022):
                raise ValueError('Unsafe helper ancestor')
            ancestors.append((path, fd, opened.st_dev, opened.st_ino))
            parent_fd = fd
        fd = os.open(filename.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != 0 or before.st_gid != 0 or before.st_mode & 0o022
                or not 0 < before.st_size <= 1024 ** 2):
            raise ValueError('Unsafe helper file')
        data = bytearray()
        while block := os.read(fd, min(65536, before.st_size - len(data) + 1)):
            data.extend(block)
            if len(data) > before.st_size:
                raise ValueError('Helper changed')
        fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                               info.st_ctime_ns, info.st_nlink, info.st_uid, info.st_gid, info.st_mode)
        if (len(data) != before.st_size or any(fields(i) != fields(before) for i in
                (os.fstat(fd), os.stat(filename.name, dir_fd=parent_fd, follow_symlinks=False)))
                or hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest() != COPIER_SHA256):
            raise ValueError('Helper pin or generation mismatch')
        for path, descriptor, device, inode in ancestors:
            for info in (path.lstat(), os.fstat(descriptor)):
                if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (device, inode)
                        or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022):
                    raise ValueError('Helper ancestor changed')
        module = types.ModuleType('protected_image_copier')
        module.__file__ = str(filename)
        # Execute the verified held bytes, never reopen a mutable module pathname.
        exec(compile(bytes(data), str(filename), 'exec'), module.__dict__)
        return module


def load_copier():
    return _load_copier(Path(__file__).with_name('stage-images.py'))


def rename_exclusive(parent_fd, pending, target='images'):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent_fd, pending.encode('ascii'), parent_fd, target.encode('ascii'), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _promote(copier, source_parent, parent, work, name, pins):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux promotion required')
    name = arguments([name])
    source = Path(copier.canonical(str(source_parent))) / name
    parent, work = Path(copier.canonical(str(parent))), Path(copier.canonical(str(work)))
    pending = 'pending-' + hashlib.sha256(name.encode('ascii')).hexdigest()[:24]
    with ExitStack() as stack:
        source_chain = copier.open_chain(stack, source, root_owned=True)
        parent_chain = copier.open_chain(stack, parent, root_owned=True)
        work_chain = copier.open_chain(stack, work, root_owned=True)
        parent_fd, work_fd = parent_chain[-1][1], work_chain[-1][1]

        def absent(fd, entry):
            try:
                os.stat(entry, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('Existing destination or initialized Waydroid state')

        def fresh():
            copier.check_chain(work_chain, root_owned=True)
            for entry in STALE:
                absent(work_fd, entry)

        def layout(chain):
            copier.check_chain(chain, root_owned=True)
            fd = chain[-1][1]
            copier.protected(os.fstat(fd), 0o700)
            if sorted(os.listdir(fd)) != ['ready.json', 'system.img', 'vendor.img']:
                raise ValueError('Unexpected protected image layout')
            for entry in ('system.img', 'vendor.img', 'ready.json'):
                info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
                size = pins[entry][0] if entry in pins else info.st_size
                copier.regular(info, size)
                if entry == 'ready.json' and not 0 < size <= 1024 ** 2:
                    raise ValueError('Unsafe staging receipt')
                copier.protected(info, 0o600)

        fresh()
        absent(parent_fd, 'images')
        layout(source_chain)
        receipt = copier._stage_images(str(source), parent, pending, pins)
        pending_chain = copier.open_chain(stack, parent / pending, root_owned=True)
        layout(pending_chain)
        layout(source_chain)
        copier.check_chain(parent_chain, root_owned=True)
        fresh()
        absent(parent_fd, 'images')
        rename_exclusive(parent_fd, pending)
        try:
            os.fsync(parent_fd)
            fresh()
            info = os.stat('images', dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(pending_chain[-1][1])
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError('Published directory changed')
            copier.protected(info, 0o700)
            copier.check_chain(parent_chain, root_owned=True)
        except (OSError, ValueError) as error:
            raise PromotionUncertain('Image promotion uncertain; inspect preinstalled layout before initialization') from error
        return {'kind': 'preinstalled-image-promotion', 'promoted': True,
                'publisherPinsMatched': receipt['publisherPinsMatched'], 'initialized': False,
                'installable': False, 'redistribute': False}


def artifact_receipt(copier, pins):
    return {'kind': 'protected-runtime-artifact-layout', 'promoted': True,
            'buildPinsMatched': pins == copier.ARTIFACT_PINS,
            'installable': False, 'readyForActivation': False,
            'androidOperationsExecuted': False, 'redistribute': False,
            'artifacts': [{'name': n, 'path': relative, 'mode': mode,
                           'size': pins[n][0], 'sha256': pins[n][1]}
                          for n, (relative, mode) in ARTIFACT_LAYOUT.items()]}


def verify_artifact_layout(copier, directory, pins):
    with ExitStack() as stack:
        chain = copier.open_chain(stack, directory, root_owned=True)
        root_fd = chain[-1][1]
        copier.protected(os.fstat(root_fd), 0o755)
        directories = {'': root_fd}
        for relative, mode in ARTIFACT_DIRECTORIES.items():
            parent, _, leaf = relative.rpartition('/')
            fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directories[parent])
            stack.callback(os.close, fd)
            copier.protected(os.fstat(fd), mode)
            directories[relative] = fd
        expected = {'': {'guest', 'libgbinder', 'artifact-layout.json'},
                    'guest': {'bin', 'location-bridge.jar', 'logical-capture.jar', 'scrcpy-server-v4.0.jar'},
                    'guest/bin': {n for n in ARTIFACT_LAYOUT if n.startswith('tesla-')},
                    'libgbinder': {*ARTIFACT_LINKS, *ARTIFACT_LINKS.values()}}
        for relative, fd in directories.items():
            if set(os.listdir(fd)) != expected[relative]:
                raise ValueError('Unexpected runtime artifact layout')
        for name, (relative, mode) in ARTIFACT_LAYOUT.items():
            parent, _, leaf = relative.rpartition('/')
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directories[parent])
            stack.callback(os.close, fd)
            before = os.fstat(fd)
            copier.regular(before, pins[name][0])
            copier.protected(before, mode)
            if copier.digest_fd(fd, pins[name][0]) != pins[name][1]:
                raise ValueError('Runtime artifact hash mismatch')
            copier.check_image(directories[parent], leaf, fd, before)
            os.fsync(fd)
        for name, target in ARTIFACT_LINKS.items():
            info = os.stat(name, dir_fd=directories['libgbinder'], follow_symlinks=False)
            if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or os.readlink(name, dir_fd=directories['libgbinder']) != target:
                raise ValueError('Unsafe private library alias')
        fd = os.open('artifact-layout.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        copier.regular(before, before.st_size)
        copier.protected(before, 0o600)
        expected_receipt = (json.dumps(artifact_receipt(copier, pins), sort_keys=True, indent=2) + '\n').encode()
        if before.st_size != len(expected_receipt) or copier.digest_fd(fd, len(expected_receipt)) != hashlib.sha256(expected_receipt).hexdigest():
            raise ValueError('Runtime artifact receipt mismatch')
        copier.check_image(root_fd, 'artifact-layout.json', fd, before)
        os.fsync(fd)
        for fd in reversed(list(directories.values())):
            os.fsync(fd)
        copier.check_chain(chain, root_owned=True)


def _promote_artifacts(copier, source_parent, parent, name, pins):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux artifact promotion required')
    name = arguments([name])
    source = Path(copier.canonical(str(source_parent))) / name
    parent = Path(copier.canonical(str(parent)))
    pending = 'pending-artifacts-' + hashlib.sha256(name.encode('ascii')).hexdigest()[:24]
    with ExitStack() as stack:
        source_chain = copier.open_chain(stack, source, root_owned=True)
        parent_chain = copier.open_chain(stack, parent, root_owned=True)
        source_fd, parent_fd = source_chain[-1][1], parent_chain[-1][1]
        if ARTIFACT_TARGET in os.listdir(parent_fd):
            raise ValueError('Existing runtime installation')
        copier.protected(os.fstat(source_fd), 0o700)
        if set(os.listdir(source_fd)) != {*pins, 'ready.json'}:
            raise ValueError('Unexpected artifact stage')
        for entry in [*pins, 'ready.json']:
            info = os.stat(entry, dir_fd=source_fd, follow_symlinks=False)
            copier.regular(info, pins[entry][0] if entry in pins else info.st_size)
            copier.protected(info, 0o600)
            if entry == 'ready.json' and not 0 < info.st_size <= 1024 ** 2:
                raise ValueError('Unsafe source receipt')
        copier._stage_images(str(source), parent, pending, pins, artifacts=True)
        pending_chain = copier.open_chain(stack, parent / pending, root_owned=True)
        pending_fd = pending_chain[-1][1]
        copier.protected(os.fstat(pending_fd), 0o700)
        for relative, mode in ARTIFACT_DIRECTORIES.items():
            os.mkdir(relative, mode, dir_fd=pending_fd)
            os.chmod(relative, mode, dir_fd=pending_fd, follow_symlinks=False)
        for name, (relative, mode) in ARTIFACT_LAYOUT.items():
            os.rename(name, relative, src_dir_fd=pending_fd, dst_dir_fd=pending_fd)
            os.chmod(relative, mode, dir_fd=pending_fd, follow_symlinks=False)
        for alias, target in ARTIFACT_LINKS.items():
            os.symlink(target, 'libgbinder/' + alias, dir_fd=pending_fd)
        receipt = artifact_receipt(copier, pins)
        fd = copier.create_private(stack, pending_fd, 'artifact-layout.json')
        copier.write_all(fd, (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode())
        os.fsync(fd)
        os.unlink('ready.json', dir_fd=pending_fd)
        os.fchmod(pending_fd, 0o755)
        verify_artifact_layout(copier, parent / pending, pins)
        copier.check_chain(parent_chain, root_owned=True)
        copier.check_chain(pending_chain, root_owned=True)
        os.fsync(parent_fd)
        rename_exclusive(parent_fd, pending, ARTIFACT_TARGET)
        try:
            os.fsync(parent_fd)
            published = os.stat(ARTIFACT_TARGET, dir_fd=parent_fd, follow_symlinks=False)
            held = os.fstat(pending_fd)
            if (published.st_dev, published.st_ino) != (held.st_dev, held.st_ino):
                raise ValueError('Published artifact root changed')
            copier.check_chain(parent_chain, root_owned=True)
        except (OSError, ValueError) as error:
            raise PromotionUncertain('Runtime artifact publication uncertain; inspect before use') from error
        return receipt


def component_records(encoded, *, runtime=False, dependency_role=None, dependency_policy=None, services=False, configuration_digest=None, pid_compat=False, headless_policy=None, owned_headless_policy=None):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate frontend manifest field')
            result[key] = value
        return result

    try:
        manifest = json.loads(encoded, object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid frontend manifest') from error
    expected = {'version': 1, 'product': 'DroidDeck', 'productVersion': '0.1.0-preview',
                'kind': 'frontend-component', 'installable': False,
                'sourceRevision': FRONTEND_REVISION, 'patchSha256': FRONTEND_PATCH}
    required = {'LICENSE', 'index.html'}
    if runtime:
        expected = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-source-component',
                    'installable': False, 'sourceRevision': RUNTIME_REVISION}
        required = {'portable/worker-entry.js', 'preview-gateway/server.js',
                    'waydroid-display/runtime-main.js', 'browser-connection/helper.js'}
    if dependency_role is not None:
        policy = DEPENDENCIES[dependency_role] if dependency_policy is None else dependency_policy
        expected = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-dependency-component',
                    'installable': False, 'role': dependency_role,
                    **{k: policy[k] for k in ('manifestSha256', 'lockSha256', 'filesSha256')}}
        required = set()
    if services:
        expected = {'version': 1, 'product': 'DroidDeck', 'kind': 'service-component', 'installable': False,
                    'sourcePlanSha256': SERVICE_PLAN_SHA256, 'configurationDigest': configuration_digest}
        required = set(SERVICE_FILES)
    if pid_compat:
        generation = PID_CONFIGURATION_DIGEST if configuration_digest is None else configuration_digest
        if not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{64}', generation):
            raise ValueError('Invalid PID configuration generation')
        expected = {'version': 1, 'product': 'DroidDeck', 'kind': 'pid-startup-component', 'installable': False,
                    'configurationDigest': generation}
        required = set(PID_FILES)
    if headless_policy is not None:
        generation, profile = headless_policy['configurationDigest'], headless_policy['waydroidConfigurationSha256']
        _headless_recipe_policy(generation, profile)
        expected = {'version': 1, 'product': 'DroidDeck', 'kind': 'headless-session-component',
                    'configurationDigest': generation, 'waydroidConfigurationSha256': profile,
                    'installable': False, 'readyForActivation': False, 'servicesActivated': False,
                    'androidOperationsExecuted': False, 'accountValidated': False}
        required = {*HEADLESS_SCRIPT_PINS, 'policy.json', 'systemd/60-droiddeck-headless.conf'}
    if owned_headless_policy is not None:
        if runtime or dependency_role is not None or services or pid_compat or headless_policy is not None:
            raise ValueError('Owned headless component cannot mix roles')
        recipe = _owned_headless_recipe(owned_headless_policy['configurationDigest'],
            owned_headless_policy['originalConfigurationSha256'], owned_headless_policy['softwareConfigurationSha256'],
            owned_headless_policy['gpuConfigurationSha256'])
        expected = {k: v for k, v in recipe.items() if k != 'files'}
        required = {*OWNED_HEADLESS_SCRIPT_PINS, 'policy.json', 'systemd/60-droiddeck-headless.conf'}
    if (not isinstance(manifest, dict) or set(manifest) != {*expected, 'files'}
            or any(type(manifest[k]) is not type(v) or manifest[k] != v for k, v in expected.items())):
        raise ValueError('Unexpected frontend component metadata')
    files = manifest['files']
    if not isinstance(files, list) or not max(1, len(required)) <= len(files) <= (256 if dependency_role else 128 if runtime else 64):
        raise ValueError('Invalid frontend file count')
    pins = {}
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}:
            raise ValueError('Invalid frontend file record')
        name, size, digest = entry['path'], entry['size'], entry['sha256']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,19}', name)
                or any(p in ('.', '..') or len(p) > 255 for p in name.split('/'))
                or name == 'bundle-manifest.json' or name.casefold() in {p.casefold() for p in pins}
                or type(size) is not int or not (0 if dependency_role else 1) <= size <= 8 * 1024 ** 2
                or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Unsafe frontend file record')
        pins[name] = (size, digest)
    if (list(pins) != sorted(pins) or not required <= set(pins)
            or sum(p[0] for p in pins.values()) > 64 * 1024 ** 2):
        raise ValueError('Unexpected frontend file inventory')
    if (services or pid_compat or headless_policy is not None or owned_headless_policy is not None) and set(pins) != required:
        raise ValueError('Unexpected service plan files')
    if owned_headless_policy is not None and files != recipe['files']:
        raise ValueError('Owned headless source recipe changed')
    if dependency_role and hashlib.sha256(json.dumps(files, separators=(',', ':')).encode()).hexdigest() != policy['filesSha256']:
        raise ValueError('Dependency byte inventory mismatch')
    return pins


def frontend_records(encoded):
    return component_records(encoded)


def _install_frontend(copier, source, root, *, manifest_pin=FRONTEND_MANIFEST_SHA256,
                      base_pin=ARTIFACT_RECEIPT_SHA256):
    return _install_component(copier, source, root, manifest_pin=manifest_pin, base_pin=base_pin)


def _install_runtime(copier, source, root, *, manifest_pin=RUNTIME_MANIFEST_SHA256,
                     base_pin=ARTIFACT_RECEIPT_SHA256, frontend_pin=FRONTEND_MANIFEST_SHA256):
    return _install_component(copier, source, root, manifest_pin=manifest_pin, base_pin=base_pin,
                              runtime=True, frontend_pin=frontend_pin)


def _install_dependencies(copier, source, root, role, *, manifest_pin=None,
                          base_pin=RUNTIME_MANIFEST_SHA256, policy=None):
    if role not in DEPENDENCIES:
        raise ValueError('Unknown dependency role')
    return _install_component(copier, source, root, manifest_pin=DEPENDENCIES[role]['bundle'] if manifest_pin is None else manifest_pin,
                              base_pin=base_pin, dependency_role=role, dependency_policy=policy)


def _install_services(copier, source, root, *, manifest_pin=SERVICE_BUNDLE_SHA256,
                      base_pin=CONFIGURATION_SHA256, validate=None):
    return _install_component(copier, source, root, manifest_pin=manifest_pin, base_pin=base_pin,
                              services=True, validate_services=validate)


def _canonical_service_plan(encoded):
    if not isinstance(encoded, bytes) or not 0 < len(encoded) <= 16384:
        raise ValueError('Invalid protected service generator size')
    canonical = encoded.replace(b'\r\n', b'\n')
    if hashlib.sha256(canonical).hexdigest() != SERVICE_PLAN_SHA256:
        raise ValueError('Protected service generator pin mismatch')
    return canonical


def _service_policy_fields(value):
    for key in ('configurationDigest', 'serviceManifestSha256'):
        if not isinstance(value, dict) or not isinstance(value.get(key), str) or not re.fullmatch('[a-f0-9]{64}', value[key]):
            raise ValueError('Invalid regenerated service policy')
    return value


def _machine_service_policy(copier, root=Path('/etc/droiddeck'), *, validate=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux machine policy inspection required')
    root = Path(copier.canonical(str(root)))
    with ExitStack() as stack:
        chain = copier.open_chain(stack, root, root_owned=True)
        directory = chain[-1][1]
        before = os.stat('runtime.json', dir_fd=directory, follow_symlinks=False)
        copier.regular(before, before.st_size)
        copier.protected(before, 0o644)
        if not 0 < before.st_size <= 16384:
            raise ValueError('Invalid machine metadata size')
        fd = os.open('runtime.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        copier.check_image(directory, 'runtime.json', fd, before)
        encoded = os.read(fd, before.st_size + 1)
        if len(encoded) != before.st_size:
            raise ValueError('Machine metadata changed')
        value = _service_policy_fields(_validate_configuration(copier, encoded, service_policy=True)
                                       if validate is None else validate(encoded))
        copier.check_chain(chain, root_owned=True)
        copier.check_image(directory, 'runtime.json', fd, before)
        return {'kind': 'protected-machine-service-policy', 'configurationDigest': value['configurationDigest'],
                'serviceManifestSha256': value['serviceManifestSha256'], 'metadataSha256': hashlib.sha256(encoded).hexdigest(),
                'accountValidated': validate is None, 'servicesActivated': False,
                'installable': False, 'readyForActivation': False, 'androidOperationsExecuted': False}


def _install_machine_services(copier, source, root, *, validate=None):
    # The private validator injection is fixture-only, never a CLI policy override.
    policy = _machine_service_policy(copier, root, validate=validate)

    def consistent(encoded):
        current = _service_policy_fields(_validate_configuration(copier, encoded, service_policy=True)
                                         if validate is None else validate(encoded))
        if any(current[key] != policy[key] for key in ('configurationDigest', 'serviceManifestSha256')):
            raise ValueError('Machine service policy changed')
        return current

    receipt = _install_services(copier, source, root, manifest_pin=policy['serviceManifestSha256'],
                                base_pin=policy['metadataSha256'], validate=consistent)
    receipt.pop('pinnedBundleMatched', None)
    receipt.pop('protectedBaseMatched', None)
    return {**receipt, 'servicePolicyRegenerated': True, 'protectedMetadataMatched': True,
            'accountValidated': policy['accountValidated'], 'sourcePlanSha256': SERVICE_PLAN_SHA256}


def _install_component(copier, source, root, *, manifest_pin, base_pin,
                       runtime=False, frontend_pin=FRONTEND_MANIFEST_SHA256,
                       dependency_role=None, dependency_policy=None, services=False, validate_services=None, pid_compat=False,
                       pid_configuration_digest=None, guard=None, headless_policy=None, owned_headless_policy=None):
    # Pin overrides belong only to generated fixtures; the CLI has a fixed policy.
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux frontend installation required')
    if any(not isinstance(p, str) or not re.fullmatch('[a-f0-9]{64}', p) for p in (manifest_pin, base_pin)):
        raise ValueError('Invalid frontend policy pin')
    source, root = Path(copier.canonical(source)), Path(copier.canonical(str(root)))
    if owned_headless_policy is not None and guard is None:
        raise ValueError('Owned headless installation requires held machine policy')
    headless = headless_policy is not None or owned_headless_policy is not None
    target = 'headless-owned-runtime' if owned_headless_policy is not None else 'headless-runtime' if headless else 'pid-compat' if pid_compat else 'services' if services else 'node_modules' if dependency_role else 'runtime' if runtime else 'frontend'
    pending = 'pending-' + target + '-' + manifest_pin[:24]
    with ExitStack() as stack:
        source_chain = copier.open_chain(stack, source)
        root_chain = copier.open_chain(stack, root, root_owned=True)
        source_fd, root_fd = source_chain[-1][1], root_chain[-1][1]
        copier.protected(os.fstat(root_fd), 0o755)
        base_names = {'runtime.json'} if services else {'guest', 'libgbinder', 'artifact-layout.json'}
        if runtime:
            base_names.add('frontend')
        if pid_compat:
            base_names.update(('frontend', 'runtime'))
        if headless:
            base_names.update(('frontend', 'runtime', 'pid-compat'))
        if not dependency_role and set(os.listdir(root_fd)) != base_names:
            raise ValueError('Existing frontend or unexpected runtime skeleton')
        base_directories = {}
        for name in (() if dependency_role or services else ('guest', 'libgbinder', *(['frontend'] if runtime or pid_compat or headless else []),
                     *(['runtime'] if pid_compat or headless else []), *(['pid-compat'] if headless else []))):
            info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Unsafe runtime skeleton directory')
            copier.protected(info)
            base_directories[name] = (info.st_dev, info.st_ino, info.st_mode)

        def open_file(directory, name, *, maximum, protected=False, allow_empty=False):
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            copier.regular(before, before.st_size)
            if not (0 if allow_empty else 1) <= before.st_size <= maximum:
                raise ValueError('Oversized or empty component file')
            if protected:
                copier.protected(before, 0o600)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            stack.callback(os.close, fd)
            copier.check_image(directory, name, fd, before)
            return fd, before

        base_directory = root_chain[-2][1] if dependency_role else root_fd
        base_name = 'runtime.json' if services else 'bundle-manifest.json' if dependency_role else 'artifact-layout.json'
        base_fd, base_before = open_file(base_directory, base_name, maximum=65536, protected=not (dependency_role or services))
        if dependency_role or services:
            copier.protected(base_before, 0o644)
        if copier.digest_fd(base_fd, base_before.st_size) != base_pin:
            raise ValueError('Protected runtime skeleton receipt mismatch')
        configuration_digest = pid_configuration_digest if pid_compat else None
        if services:
            os.lseek(base_fd, 0, os.SEEK_SET)
            encoded_config = os.read(base_fd, base_before.st_size + 1)
            value = _validate_configuration(copier, encoded_config) if validate_services is None else validate_services(encoded_config)
            configuration_digest = value['configurationDigest']
            if not re.fullmatch('[a-f0-9]{64}', configuration_digest):
                raise ValueError('Invalid service configuration generation')
        base_files = {}
        if dependency_role:
            policy = DEPENDENCIES[dependency_role] if dependency_policy is None else dependency_policy
            os.lseek(base_fd, 0, os.SEEK_SET)
            baseline = component_records(os.read(base_fd, base_before.st_size + 1), runtime=True)
            prefix = DEPENDENCIES[dependency_role]['directory'] + '/'
            package_pins = {name[len(prefix):]: pin for name, pin in baseline.items() if name.startswith(prefix)}
            if (not {'package.json', 'package-lock.json'} <= set(package_pins)
                    or any('/' in name for name in package_pins)
                    or package_pins['package.json'][1] != policy.get('runtimeManifestSha256', policy['manifestSha256'])
                    or package_pins['package-lock.json'][1] != policy.get('runtimeLockSha256', policy['lockSha256'])):
                raise ValueError('Installed source package policy mismatch')
            base_names = set(package_pins)
            if set(os.listdir(root_fd)) != base_names:
                raise ValueError('Existing dependencies or unexpected package entries')
            for name, pin in package_pins.items():
                fd, before = open_file(root_fd, name, maximum=8 * 1024 ** 2)
                copier.protected(before, 0o644)
                if before.st_size != pin[0] or copier.digest_fd(fd, pin[0]) != pin[1]:
                    raise ValueError('Installed source package changed')
                base_files[name] = (fd, before, pin)
        if runtime:
            frontend_fd = os.open('frontend', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            stack.callback(os.close, frontend_fd)
            copier.protected(os.fstat(frontend_fd), 0o755)
            frontend_manifest_fd, frontend_before = open_file(frontend_fd, 'bundle-manifest.json', maximum=1024 ** 2)
            copier.protected(frontend_before, 0o644)
            if copier.digest_fd(frontend_manifest_fd, frontend_before.st_size) != frontend_pin:
                raise ValueError('Installed frontend manifest mismatch')
        manifest_fd, manifest_before = open_file(source_fd, 'bundle-manifest.json', maximum=1024 ** 2)
        if copier.digest_fd(manifest_fd, manifest_before.st_size) != manifest_pin:
            raise ValueError('Frontend manifest pin mismatch')
        os.lseek(manifest_fd, 0, os.SEEK_SET)
        encoded = os.read(manifest_fd, manifest_before.st_size + 1)
        if len(encoded) != manifest_before.st_size or hashlib.sha256(encoded).hexdigest() != manifest_pin:
            raise ValueError('Frontend manifest changed')
        pins = component_records(encoded, runtime=runtime, dependency_role=dependency_role, dependency_policy=dependency_policy,
                                 services=services, configuration_digest=configuration_digest, pid_compat=pid_compat,
                                 headless_policy=headless_policy, owned_headless_policy=owned_headless_policy)
        file_count = len(pins)
        pins['bundle-manifest.json'] = (len(encoded), manifest_pin)
        expected = {'': set()}
        for name in pins:
            parts = name.split('/')
            for index, part in enumerate(parts):
                directory = '/'.join(parts[:index])
                expected.setdefault(directory, set()).add(part)
        if set(expected) & set(pins):
            raise ValueError('Frontend file/directory collision')
        source_dirs = {'': source_fd}
        for relative in sorted(set(expected) - {''}, key=lambda p: (p.count('/'), p)):
            parent, _, leaf = relative.rpartition('/')
            info = os.stat(leaf, dir_fd=source_dirs[parent], follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Unsafe frontend source directory')
            fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_dirs[parent])
            stack.callback(os.close, fd)
            if (info.st_dev, info.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                raise ValueError('Frontend source directory changed')
            source_dirs[relative] = fd
        sources = {}
        for name, pin in pins.items():
            parent, _, leaf = name.rpartition('/')
            fd, before = open_file(source_dirs[parent], leaf, maximum=8 * 1024 ** 2, allow_empty=bool(dependency_role))
            copier.regular(before, pin[0])
            if copier.digest_fd(fd, pin[0]) != pin[1]:
                raise ValueError('Frontend source hash mismatch')
            copier.check_image(source_dirs[parent], leaf, fd, before)
            sources[name] = (fd, before)

        def check_tree(directories, files, *, output=False):
            for relative, fd in directories.items():
                held = os.fstat(fd)
                if relative:
                    parent, _, leaf = relative.rpartition('/')
                    current = os.stat(leaf, dir_fd=directories[parent], follow_symlinks=False)
                    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                        raise ValueError('Frontend directory changed')
                if output:
                    copier.protected(held, 0o755)
                if set(os.listdir(fd)) != expected[relative]:
                    raise ValueError('Unexpected frontend entries')
            for name, (fd, before) in files.items():
                parent, _, leaf = name.rpartition('/')
                copier.check_image(directories[parent], leaf, fd, before)
                if output:
                    copier.protected(os.fstat(fd), 0o644)
                    if copier.digest_fd(fd, pins[name][0]) != pins[name][1]:
                        raise ValueError('Frontend output hash mismatch')
                    copier.check_image(directories[parent], leaf, fd, before)

        def check_base(extra=()):
            if guard is not None:
                guard()
            copier.check_chain(root_chain, root_owned=True)
            copier.protected(os.fstat(root_fd), 0o755)
            if set(os.listdir(root_fd)) != base_names | set(extra):
                raise ValueError('Runtime skeleton changed')
            for name, identity in base_directories.items():
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                copier.protected(info)
                if (info.st_dev, info.st_ino, info.st_mode) != identity:
                    raise ValueError('Runtime skeleton directory changed')
            copier.check_image(base_directory, base_name, base_fd, base_before)
            if copier.digest_fd(base_fd, base_before.st_size) != base_pin:
                raise ValueError('Runtime skeleton receipt changed')
            for name, (fd, before, pin) in base_files.items():
                copier.check_image(root_fd, name, fd, before)
                if copier.digest_fd(fd, pin[0]) != pin[1]:
                    raise ValueError('Installed package code changed')
            if runtime:
                copier.check_image(frontend_fd, 'bundle-manifest.json', frontend_manifest_fd, frontend_before)
                if copier.digest_fd(frontend_manifest_fd, frontend_before.st_size) != frontend_pin:
                    raise ValueError('Installed frontend manifest changed')

        def check_source():
            copier.check_chain(source_chain)
            check_tree(source_dirs, sources)

        check_base()
        check_source()
        space = os.fstatvfs(root_fd)
        if space.f_bavail * space.f_frsize < sum(p[0] for p in pins.values()) + copier.RESERVE:
            raise ValueError('Insufficient frontend installation space')
        os.mkdir(pending, 0o700, dir_fd=root_fd)
        output_fd = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        stack.callback(os.close, output_fd)
        os.fchmod(output_fd, 0o700)
        output_dirs = {'': output_fd}
        for relative in sorted(set(expected) - {''}, key=lambda p: (p.count('/'), p)):
            parent, _, leaf = relative.rpartition('/')
            os.mkdir(leaf, 0o755, dir_fd=output_dirs[parent])
            fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=output_dirs[parent])
            stack.callback(os.close, fd)
            os.fchmod(fd, 0o755)
            output_dirs[relative] = fd
        outputs = {}
        for name, pin in pins.items():
            check_source()
            parent, _, leaf = name.rpartition('/')
            fd, _ = copier.copy_image(stack, sources[name][0], output_dirs[parent], leaf, pin)
            os.fchmod(fd, 0o644)
            os.fsync(fd)
            outputs[name] = (fd, os.fstat(fd))
        os.fchmod(output_fd, 0o755)
        check_tree(output_dirs, outputs, output=True)
        for fd in reversed(list(output_dirs.values())):
            os.fsync(fd)
        os.fsync(root_fd)
        check_source()
        check_base((pending,))
        check_tree(output_dirs, outputs, output=True)
        held = os.fstat(output_fd)
        current = os.stat(pending, dir_fd=root_fd, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('Frontend pending root changed')
        rename_exclusive(root_fd, pending, target)
        try:
            os.fsync(root_fd)
            current = os.stat(target, dir_fd=root_fd, follow_symlinks=False)
            if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError('Published frontend changed')
            check_base((target,))
            check_tree(output_dirs, outputs, output=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Frontend publication uncertain; inspect before use') from error
        return {'kind': 'protected-' + target + '-install', 'promoted': True, 'files': file_count,
                target + 'ManifestSha256': manifest_pin,
                'sourceRevision': None if pid_compat or headless else RUNTIME_REVISION if runtime or dependency_role or services else FRONTEND_REVISION,
                'pinnedBundleMatched': manifest_pin == (PID_MANIFEST_SHA256 if pid_compat else SERVICE_BUNDLE_SHA256 if services else DEPENDENCIES[dependency_role]['bundle'] if dependency_role else RUNTIME_MANIFEST_SHA256 if runtime else FRONTEND_MANIFEST_SHA256),
                'protectedBaseMatched': base_pin == (CONFIGURATION_SHA256 if services else RUNTIME_MANIFEST_SHA256 if dependency_role else ARTIFACT_RECEIPT_SHA256),
                **({'role': dependency_role} if dependency_role else {}),
                **({'configurationDigest': configuration_digest, 'servicesActivated': False} if services else {}),
                'installable': False, 'readyForActivation': False, 'androidOperationsExecuted': False}


def _validate_configuration(copier, encoded, *, service_policy=False):
    # Only the hash-verified, protected metadata modules execute, never workers.
    root = Path('/opt/droiddeck/runtime')
    selected = ('portable/runtime-config.js', 'portable/runtime-account.js',
                'portable/configuration-generation.js', 'portable/read-bounded.js', 'browser-connection/policy.js')
    with ExitStack() as stack:
        chain = copier.open_chain(stack, root, root_owned=True)
        root_fd = chain[-1][1]
        marker_fd = os.open('bundle-manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        stack.callback(os.close, marker_fd)
        marker = os.fstat(marker_fd)
        copier.regular(marker, marker.st_size)
        copier.protected(marker, 0o644)
        allowed = {RUNTIME_MANIFEST_SHA256}
        if service_policy:
            allowed.add(REPAIRED_METADATA_MANIFEST_SHA256)
        if not 0 < marker.st_size <= 65536 or copier.digest_fd(marker_fd, marker.st_size) not in allowed:
            raise ValueError('Installed metadata module policy mismatch')
        os.lseek(marker_fd, 0, os.SEEK_SET)
        records = component_records(os.read(marker_fd, marker.st_size + 1), runtime=True)
        files = []
        for name in selected:
            parent, _, leaf = name.rpartition('/')
            child_chain = copier.open_chain(stack, root / parent, root_owned=True)
            directory = child_chain[-1][1]
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            stack.callback(os.close, fd)
            before = os.fstat(fd)
            copier.regular(before, records[name][0])
            copier.protected(before, 0o644)
            if copier.digest_fd(fd, before.st_size) != records[name][1]:
                raise ValueError('Installed metadata validator changed')
            copier.check_image(directory, leaf, fd, before)
            files.append((child_chain, directory, leaf, fd, before))
        script = '''const fs=require('node:fs'), r='/opt/droiddeck/runtime/portable/';
const {normalizeRuntimeConfig}=require(r+'runtime-config');
const {validateRuntimeAccount}=require(r+'runtime-account');
const {configurationDigest}=require(r+'configuration-generation');
(async()=>{const text=new TextDecoder('utf-8',{fatal:true}).decode(fs.readFileSync(0));
const config=normalizeRuntimeConfig(JSON.parse(text)); await validateRuntimeAccount(config);
process.stdout.write(JSON.stringify({config,configurationDigest:configurationDigest(config)}));
})().catch(()=>{process.stderr.write('Metadata/account validation rejected');process.exitCode=1;});'''
        payload = encoded
        if service_policy:
            plan_chain = copier.open_chain(stack, Path(__file__).parent, root_owned=True)
            plan_directory = plan_chain[-1][1]
            plan_before = os.stat('service-plan.js', dir_fd=plan_directory, follow_symlinks=False)
            copier.regular(plan_before, plan_before.st_size)
            copier.protected(plan_before, 0o644)
            if not 0 < plan_before.st_size <= 16384:
                raise ValueError('Invalid protected service generator size')
            plan_fd = os.open('service-plan.js', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=plan_directory)
            stack.callback(os.close, plan_fd)
            copier.check_image(plan_directory, 'service-plan.js', plan_fd, plan_before)
            plan_text = os.read(plan_fd, plan_before.st_size + 1)
            if len(plan_text) != plan_before.st_size:
                raise ValueError('Protected service generator changed')
            plan_text = _canonical_service_plan(plan_text)
            files.append((plan_chain, plan_directory, 'service-plan.js', plan_fd, plan_before))
            payload = json.dumps({'configurationText': encoded.decode('utf-8'), 'servicePlan': plan_text.decode('utf-8')}).encode()
            script = '''const fs=require('node:fs'), Module=require('node:module'), {createHash}=require('node:crypto');
const r='/opt/droiddeck/runtime/portable/';
const {normalizeRuntimeConfig}=require(r+'runtime-config');
const {validateRuntimeAccount}=require(r+'runtime-account');
const {configurationDigest}=require(r+'configuration-generation');
const sha=b=>createHash('sha256').update(b).digest('hex');
(async()=>{const packet=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(fs.readFileSync(0)));
const config=normalizeRuntimeConfig(JSON.parse(packet.configurationText)); await validateRuntimeAccount(config);
const generator=new Module(r+'service-plan.js');generator.filename=r+'service-plan.js';generator.paths=[];
generator._compile(packet.servicePlan,generator.filename);
const plan=generator.exports.createServicePlan(config);
const files=Object.keys(plan.files).sort().map(path=>{const bytes=Buffer.from(plan.files[path]);return {path,size:bytes.length,sha256:sha(bytes)};});
const manifest={version:1,product:'DroidDeck',kind:'service-component',installable:false,
sourcePlanSha256:sha(Buffer.from(packet.servicePlan)),configurationDigest:configurationDigest(config),files};
process.stdout.write(JSON.stringify({config,configurationDigest:manifest.configurationDigest,
serviceManifestSha256:sha(Buffer.from(JSON.stringify(manifest,null,2)+'\\n'))}));
})().catch(()=>{process.stderr.write('Machine service policy rejected');process.exitCode=1;});'''
        try:
            result = subprocess.run(['/usr/bin/node', '-e', script], input=payload, capture_output=True, timeout=12,
                                    env={'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'}, check=False)
        except subprocess.TimeoutExpired as error:
            raise ValueError('Metadata/account validation timed out') from error
        if result.returncode != 0 or not 0 < len(result.stdout) <= 16384 or len(result.stderr) > 4096:
            raise ValueError('Runtime configuration/account validation rejected')
        copier.check_chain(chain, root_owned=True)
        copier.check_image(root_fd, 'bundle-manifest.json', marker_fd, marker)
        for child_chain, directory, leaf, fd, before in files:
            copier.check_chain(child_chain, root_owned=True)
            copier.check_image(directory, leaf, fd, before)
        value = json.loads(result.stdout)
        expected_fields = {'config', 'configurationDigest'} | ({'serviceManifestSha256'} if service_policy else set())
        if (not isinstance(value, dict) or set(value) != expected_fields
                or not isinstance(value['config'], dict)
                or not re.fullmatch('[a-f0-9]{64}', value.get('configurationDigest', ''))):
            raise ValueError('Invalid metadata validator result')
        if service_policy:
            _service_policy_fields(value)
        return value


def _install_configuration(copier, source, parent, *, validate=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux metadata installation required')
    source, parent = Path(copier.canonical(source)), Path(copier.canonical(str(parent)))
    with ExitStack() as stack:
        source_chain = copier.open_chain(stack, source.parent)
        parent_chain = copier.open_chain(stack, parent, root_owned=True)
        source_parent, parent_fd = source_chain[-1][1], parent_chain[-1][1]
        before = os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)
        copier.regular(before, before.st_size)
        if not 0 < before.st_size <= 16384:
            raise ValueError('Metadata input size rejected')
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent)
        stack.callback(os.close, fd)
        copier.check_image(source_parent, source.name, fd, before)
        encoded = os.read(fd, before.st_size + 1)
        copier.check_image(source_parent, source.name, fd, before)
        if len(encoded) != before.st_size:
            raise ValueError('Metadata input changed')
        validated = _validate_configuration(copier, encoded) if validate is None else validate(encoded)
        normalized = (json.dumps(validated['config'], sort_keys=True, indent=2) + '\n').encode()
        if not 0 < len(normalized) <= 16384:
            raise ValueError('Invalid normalized configuration size')
        digest = hashlib.sha256(normalized).hexdigest()
        pending = 'pending-droiddeck-config-' + digest[:24]

        def check_base(extra=()):
            copier.check_chain(parent_chain, root_owned=True)
            copier.check_chain(source_chain)
            copier.check_image(source_parent, source.name, fd, before)
            for name in ('droiddeck', pending):
                if name in extra:
                    continue
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError('Existing configuration or partial candidate')

        check_base()
        os.mkdir(pending, 0o700, dir_fd=parent_fd)
        output_fd = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        stack.callback(os.close, output_fd)
        output_info = os.fstat(output_fd)
        file_fd = copier.create_private(stack, output_fd, 'runtime.json')
        copier.write_all(file_fd, normalized)
        os.fchmod(file_fd, 0o644)
        os.fsync(file_fd)
        saved = os.fstat(file_fd)
        os.fchmod(output_fd, 0o755)

        def check_output(name):
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (output_info.st_dev, output_info.st_ino):
                raise ValueError('Configuration directory changed')
            copier.protected(info, 0o755)
            if os.listdir(output_fd) != ['runtime.json'] or copier.digest_fd(file_fd, len(normalized)) != digest:
                raise ValueError('Configuration output changed')
            copier.check_image(output_fd, 'runtime.json', file_fd, saved)
            copier.protected(os.fstat(file_fd), 0o644)

        check_output(pending)
        os.fsync(output_fd)
        os.fsync(parent_fd)
        check_base((pending,))
        check_output(pending)
        rename_exclusive(parent_fd, pending, 'droiddeck')
        try:
            os.fsync(parent_fd)
            copier.check_chain(parent_chain, root_owned=True)
            check_output('droiddeck')
        except (OSError, ValueError) as error:
            raise PromotionUncertain('Configuration published; inspect durable state before use') from error
        return {'kind': 'protected-runtime-configuration', 'promoted': True, 'sha256': digest,
                'configurationDigest': validated['configurationDigest'], 'accountValidated': validate is None,
                'installable': False, 'readyForActivation': False, 'servicesActivated': False,
                'androidOperationsExecuted': False}


def _assert_service_vacancy():
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'}
    for name in [Path(p).name for p in SERVICE_FILES if p.endswith('.service')]:
        result = subprocess.run(['/usr/bin/systemctl', 'show', name, '--property=LoadState', '--value'],
                                capture_output=True, timeout=5, env=env)
        if result.returncode not in (0, 4) or result.stdout.strip() != b'not-found':
            raise ValueError('Existing systemd worker service')
    result = subprocess.run(['/usr/bin/systemctl', 'show', 'waydroid-container.service', '--property=ActiveState', '--value'],
                            capture_output=True, timeout=5, env=env)
    if result.returncode != 0 or result.stdout.strip() != b'inactive':
        raise ValueError('Waydroid must be inactive for service registration')


def _register_services(copier, source, etc, *, manifest_pin=SERVICE_BUNDLE_SHA256,
                       configuration_digest=None, inactive=None, metadata_pin=CONFIGURATION_SHA256,
                       validate=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux service registration required')
    source, etc = Path(copier.canonical(str(source))), Path(copier.canonical(str(etc)))
    with ExitStack() as stack:
        source_chain = copier.open_chain(stack, source, root_owned=True)
        etc_chain = copier.open_chain(stack, etc, root_owned=True)
        config_snapshot = None
        source_fd = source_chain[-1][1]
        marker_fd = os.open('bundle-manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_fd)
        stack.callback(os.close, marker_fd)
        before = os.fstat(marker_fd)
        copier.regular(before, before.st_size)
        copier.protected(before, 0o644)
        if not 0 < before.st_size <= 16384 or copier.digest_fd(marker_fd, before.st_size) != manifest_pin:
            raise ValueError('Protected service manifest mismatch')
        if configuration_digest is None:
            config_chain = copier.open_chain(stack, etc / 'droiddeck', root_owned=True)
            config_fd = os.open('runtime.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=config_chain[-1][1])
            stack.callback(os.close, config_fd)
            config_before = os.fstat(config_fd)
            copier.protected(config_before, 0o644)
            copier.regular(config_before, config_before.st_size)
            if not 0 < config_before.st_size <= 16384 or copier.digest_fd(config_fd, config_before.st_size) != metadata_pin:
                raise ValueError('Registered service configuration changed')
            os.lseek(config_fd, 0, os.SEEK_SET)
            encoded = os.read(config_fd, config_before.st_size + 1)
            configuration_digest = (_validate_configuration(copier, encoded) if validate is None else validate(encoded))['configurationDigest']
            config_snapshot = (config_chain, config_fd, config_before)
        os.lseek(marker_fd, 0, os.SEEK_SET)
        pins = component_records(os.read(marker_fd, before.st_size + 1), services=True, configuration_digest=configuration_digest)
        sources, directories = {}, {}
        expected_dirs = {'': {'bundle-manifest.json'}}
        for name in SERVICE_FILES:
            parts = name.split('/')
            for i, leaf in enumerate(parts):
                expected_dirs.setdefault('/'.join(parts[:i]), set()).add(leaf)
        for relative, entries in expected_dirs.items():
            chain = copier.open_chain(stack, source / relative, root_owned=True)
            fd = chain[-1][1]
            if set(os.listdir(fd)) != entries:
                raise ValueError('Unexpected service source entries')
            directories[relative] = chain
        for name, pin in pins.items():
            parent, _, leaf = name.rpartition('/')
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directories[parent][-1][1])
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            copier.regular(info, pin[0])
            copier.protected(info, 0o644)
            if copier.digest_fd(fd, pin[0]) != pin[1]:
                raise ValueError('Registered service source bytes changed')
            sources[name] = (fd, info)

        def check_source():
            copier.check_chain(source_chain, root_owned=True)
            copier.check_chain(etc_chain, root_owned=True)
            copier.check_image(source_fd, 'bundle-manifest.json', marker_fd, before)
            if config_snapshot is not None:
                chain, fd, info = config_snapshot
                copier.check_chain(chain, root_owned=True)
                copier.check_image(chain[-1][1], 'runtime.json', fd, info)
            for relative, chain in directories.items():
                copier.check_chain(chain, root_owned=True)
                if set(os.listdir(chain[-1][1])) != expected_dirs[relative]:
                    raise ValueError('Service source entries changed')
            for name, (fd, info) in sources.items():
                parent, _, leaf = name.rpartition('/')
                copier.check_image(directories[parent][-1][1], leaf, fd, info)

        check_source()
        (_assert_service_vacancy if inactive is None else inactive)()
        destinations = {}
        for name in REGISTERED_SERVICE_FILES:
            parent, _, leaf = name.rpartition('/')
            target_parent = etc / parent
            chain = copier.open_chain(stack, target_parent, root_owned=True)
            fd = chain[-1][1]
            try:
                os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                destinations[name] = chain
                continue
            raise ValueError('Existing system configuration target')
        published = []
        try:
            for name, pin in pins.items():
                if name not in REGISTERED_SERVICE_FILES:
                    continue
                check_source()
                chain = destinations[name]
                copier.check_chain(chain, root_owned=True)
                directory = chain[-1][1]
                leaf = name.rsplit('/', 1)[1]
                pending = 'pending-droiddeck-' + pin[1][:24]
                fd, _ = copier.copy_image(stack, sources[name][0], directory, pending, pin)
                os.fchmod(fd, 0o644)
                os.fsync(fd)
                info = os.fstat(fd)
                copier.check_image(directory, pending, fd, info)
                check_source()
                copier.check_chain(chain, root_owned=True)
                rename_exclusive(directory, pending, leaf)
                published.append(name)
                os.fsync(directory)
                renamed = os.fstat(fd)
                # renameat2 changes file ctime; every other identity field and byte stays pinned.
                old_identity, new_identity = copier.identity(info), copier.identity(renamed)
                if (old_identity[:4] + old_identity[5:] != new_identity[:4] + new_identity[5:]
                        or copier.digest_fd(fd, pin[0]) != pin[1]):
                    raise ValueError('Published service file changed')
                copier.check_image(directory, leaf, fd, renamed)
            check_source()
        except (OSError, ValueError) as error:
            if published:
                raise PromotionUncertain('Service registration partial or uncertain; inspect files before enabling') from error
            raise
        return {'kind': 'protected-service-registration', 'files': len(published), 'registered': True,
                'configurationDigest': configuration_digest, 'installable': False, 'readyForActivation': False,
                'servicesActivated': False, 'servicesEnabled': False, 'daemonReloaded': False,
                'containerHookRegistered': False,
                'androidOperationsExecuted': False}


def _register_machine_services(copier, etc, *, validate=None, inactive=None):
    root = Path(etc) / 'droiddeck'
    policy = _machine_service_policy(copier, root, validate=validate)

    def consistent(encoded):
        current = _service_policy_fields(_validate_configuration(copier, encoded, service_policy=True)
                                         if validate is None else validate(encoded))
        if any(current[key] != policy[key] for key in ('configurationDigest', 'serviceManifestSha256')):
            raise ValueError('Machine registration policy changed')
        return current

    receipt = _register_services(copier, root / 'services', etc,
                                 manifest_pin=policy['serviceManifestSha256'], metadata_pin=policy['metadataSha256'],
                                 validate=consistent, inactive=inactive)
    return {**receipt, 'servicePolicyRegenerated': True, 'protectedMetadataMatched': True,
            'accountValidated': policy['accountValidated'], 'sourcePlanSha256': SERVICE_PLAN_SHA256}


def validate_codec_vendor(main_xml, ffmpeg_xml, init_rc):
    if any(not isinstance(b, bytes) or not 0 < len(b) <= 65536 for b in (main_xml, ffmpeg_xml, init_rc)):
        raise ValueError('Invalid vendor codec metadata bounds')
    if init_rc != b'on post-fs-data\n    exec -- /vendor/bin/waydroid-init\n':
        raise ValueError('Publisher init source changed')
    try:
        main, ffmpeg = ET.fromstring(main_xml), ET.fromstring(ffmpeg_xml)
    except ET.ParseError as error:
        raise ValueError('Invalid publisher codec XML') from error
    includes = [e.get('href') for e in main.findall('./Include')]
    if main.tag != 'MediaCodecs' or includes != ['media_codecs_ffmpeg_c2.xml', 'media_codecs_c2.xml']:
        raise ValueError('Publisher codec include order changed')
    expected = {'c2.ffmpeg.aac.decoder': 'audio/mp4a-latm', 'c2.ffmpeg.h264.decoder': 'video/avc',
                'c2.ffmpeg.hevc.decoder': 'video/hevc', 'c2.ffmpeg.av1.decoder': 'video/av01',
                'c2.ffmpeg.vp9.decoder': 'video/x-vnd.on2.vp9'}
    if ffmpeg.tag != 'MediaCodecs':
        raise ValueError('Unexpected publisher FFmpeg metadata')
    for name, mime in expected.items():
        found = ffmpeg.findall("./Decoders/MediaCodec[@name='" + name + "']")
        if len(found) != 1 or found[0].get('type') != mime:
            raise ValueError('Expected publisher software decoder missing or ambiguous')
    if ffmpeg.findall("./Decoders/MediaCodec[@type='audio/eac3']"):
        raise ValueError('Unaccepted EAC3 codec declaration')


def _assert_codec_stopped():
    for name in ('waydroid-container', 'tesla-waydroid-runtime', 'tesla-waydroid-headless-session', 'tesla-waydroid-compositor'):
        r = subprocess.run(['/usr/bin/systemctl', 'show', name + '.service', '--property=ActiveState', '--value'],
                           capture_output=True, timeout=5, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        if r.returncode != 0 or r.stdout.strip() != b'inactive':
            raise ValueError('Codec installation requires inactive Android and workers')


def _verify_codec_vendor(copier):
    with ExitStack() as stack:
        chain = copier.open_chain(stack, Path('/etc/waydroid-extra/images'), root_owned=True)
        parent = chain[-1][1]
        fd = os.open('vendor.img', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        size, digest = copier.PINS['vendor.img']
        copier.regular(info, size)
        copier.protected(info, 0o600)
        if copier.digest_fd(fd, size) != digest:
            raise ValueError('Publisher vendor image pin changed')
        def inspect(command):
            copier.check_chain(chain, root_owned=True)
            copier.check_image(parent, 'vendor.img', fd, info)
            result = subprocess.run(['/usr/sbin/debugfs', '-R', command, '/proc/self/fd/' + str(fd)],
                                    pass_fds=(fd,), capture_output=True, timeout=15,
                                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
            if result.returncode != 0 or len(result.stdout) > 65536 or len(result.stderr) > 4096:
                raise ValueError('Bounded read-only vendor inspection failed')
            copier.check_image(parent, 'vendor.img', fd, info)
            return result
        metadata = [inspect('cat ' + p).stdout for p in ('/etc/media_codecs.xml', '/etc/media_codecs_ffmpeg_c2.xml', '/etc/init/waydroid-init.rc')]
        absent = inspect('stat /etc/media_codecs_c2.xml')
        if absent.stdout or b'File not found by ext2_lookup' not in absent.stderr:
            raise ValueError('Publisher AAC include must be absent')
        validate_codec_vendor(*metadata)


def _install_codecs(copier, parent, configuration, *, config_pin=WAYDROID_CONFIGURATION_SHA256,
                    stopped=None, verify_vendor=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux codec installation required')
    parent, configuration = Path(copier.canonical(str(parent))), Path(copier.canonical(str(configuration)))
    guard = _assert_codec_stopped if stopped is None else stopped
    guard()
    (_verify_codec_vendor if verify_vendor is None else verify_vendor)(copier)
    with ExitStack() as stack:
        chain = copier.open_chain(stack, parent, root_owned=True)
        cfg_chain = copier.open_chain(stack, configuration.parent, root_owned=True)
        root = chain[-1][1]
        copier.protected(os.fstat(root), 0o755)
        if os.listdir(root):
            raise ValueError('Existing vendor overlay must not be replaced')
        cfg_fd = os.open(configuration.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=cfg_chain[-1][1])
        stack.callback(os.close, cfg_fd)
        cfg_info = os.fstat(cfg_fd)
        copier.regular(cfg_info, cfg_info.st_size)
        copier.protected(cfg_info)
        if not 0 < cfg_info.st_size <= 65536 or copier.digest_fd(cfg_fd, cfg_info.st_size) != config_pin:
            raise ValueError('Fresh Waydroid configuration changed')
        records = [{'path': n, 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()} for n, b in sorted(CODEC_FILES.items())]
        receipt = {'kind': 'protected-codec-overlay', 'version': 1, 'installable': False,
                   'readyForActivation': False, 'androidOperationsExecuted': False,
                   'configurationChanged': False, 'configurationSha256': config_pin, 'files': records}
        contents = {**CODEC_FILES, 'droiddeck-codec-receipt.json': (json.dumps(receipt, indent=2) + '\n').encode()}
        pending = 'pending-droiddeck-codecs'
        os.mkdir(pending, 0o755, dir_fd=root)
        out = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        stack.callback(os.close, out)
        os.fchmod(out, 0o755)
        identity = os.fstat(out)
        os.mkdir('init', 0o755, dir_fd=out)
        init = os.open('init', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=out)
        stack.callback(os.close, init)
        os.fchmod(init, 0o755)
        init_identity = os.fstat(init)
        outputs = {}
        for name, data in contents.items():
            directory = init if name.startswith('init/') else out
            fd = copier.create_private(stack, directory, name.rsplit('/', 1)[-1])
            copier.write_all(fd, data)
            os.fchmod(fd, 0o644)
            os.fsync(fd)
            outputs[name] = (fd, os.fstat(fd))
        def check_output():
            for fd, relative, before, entries in ((out, None, identity, {'init', 'media_codecs_c2.xml', 'droiddeck-codec-receipt.json'}),
                                                  (init, 'init', init_identity, {'waydroid-init.rc'})):
                now = os.fstat(fd) if relative is None else os.stat(relative, dir_fd=out, follow_symlinks=False)
                if not stat.S_ISDIR(now.st_mode) or (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino) or set(os.listdir(fd)) != entries:
                    raise ValueError('Generated codec directory changed')
                copier.protected(now, 0o755)
            for name, (fd, info) in outputs.items():
                copier.check_image(init if name.startswith('init/') else out, name.rsplit('/', 1)[-1], fd, info)
                copier.protected(os.fstat(fd), 0o644)
                if copier.digest_fd(fd, len(contents[name])) != hashlib.sha256(contents[name]).hexdigest():
                    raise ValueError('Generated codec output changed')
        def check_base(leaf):
            copier.check_chain(chain, root_owned=True)
            copier.check_chain(cfg_chain, root_owned=True)
            copier.check_image(cfg_chain[-1][1], configuration.name, cfg_fd, cfg_info)
            if copier.digest_fd(cfg_fd, cfg_info.st_size) != config_pin or set(os.listdir(root)) != {leaf}:
                raise ValueError('Codec installation base changed')
            before = os.stat(leaf, dir_fd=root, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or (before.st_dev, before.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError('Codec output directory changed')
            copier.protected(before, 0o755)
        check_base(pending)
        check_output()
        guard()
        os.fsync(init)
        os.fsync(out)
        check_base(pending)
        check_output()
        rename_exclusive(root, pending, 'etc')
        try:
            os.fsync(root)
            check_base('etc')
            check_output()
        except (OSError, ValueError) as error:
            raise PromotionUncertain('Codec overlay published; inspect before activation or retry') from error
        return receipt


def profile_candidate(encoded, *, gpu=False):
    if not isinstance(encoded, bytes) or not 0 < len(encoded) <= 65536:
        raise ValueError('Invalid fresh Waydroid configuration bounds')
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(encoded.decode('utf-8'))
    except (UnicodeError, configparser.Error) as error:
        raise ValueError('Invalid fresh Waydroid configuration') from error
    if (parser.defaults() or set(parser.sections()) != {'waydroid', 'properties'} or parser.items('properties')
            or parser.get('waydroid', 'arch', fallback='') != 'x86_64'
            or parser.get('waydroid', 'images_path', fallback='') != '/etc/waydroid-extra/images'):
        raise ValueError('Fresh secret-free x86 Waydroid configuration required')
    properties = {'debug.stagefright.ccodec': '4', 'ro.waydroid.software_rendering': '0' if gpu else '1',
                  'persist.waydroid.use_subsurface': 'true' if gpu else 'false',
                  'media.sf.hwaccel': '0', 'debug.ffmpeg-codec2.hwaccel.drm': '0',
                  'debug.ffmpeg-codec2.hwaccel.force': '0', 'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
    for name, value in properties.items():
        parser.set('properties', name, value)
    stream = io.StringIO()
    parser.write(stream)
    return stream.getvalue().encode()


def _verify_profile_codecs(copier):
    with ExitStack() as stack:
        root = Path('/var/lib/waydroid/overlay/vendor/etc')
        chain = copier.open_chain(stack, root, root_owned=True)
        if set(os.listdir(chain[-1][1])) != {'init', 'media_codecs_c2.xml', 'droiddeck-codec-receipt.json'}:
            raise ValueError('Installed codec inventory changed')
        for name, encoded in CODEC_FILES.items():
            parent, _, leaf = name.rpartition('/')
            child = copier.open_chain(stack, root / parent, root_owned=True)
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=child[-1][1])
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            copier.regular(info, len(encoded))
            copier.protected(info, 0o644)
            if copier.digest_fd(fd, len(encoded)) != hashlib.sha256(encoded).hexdigest():
                raise ValueError('Installed codec profile changed')
            copier.check_image(child[-1][1], leaf, fd, info)


def _read_profile_file(copier, stack, directory, name, *, maximum=65536, mode=0o600):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    copier.regular(info, info.st_size)
    copier.protected(info, mode)
    if not 0 < info.st_size <= maximum:
        raise ValueError('Profile metadata bounds')
    data = os.read(fd, info.st_size + 1)
    copier.check_image(directory, name, fd, info)
    if len(data) != info.st_size:
        raise ValueError('Profile metadata changed')
    return fd, info, data


def _exchange_profile(copier, configuration, candidate, before_pin, pending, *, guard, check_source):
    with ExitStack() as stack:
        chain = copier.open_chain(stack, configuration.parent, root_owned=True)
        parent = chain[-1][1]
        old_fd, old_info, original = _read_profile_file(copier, stack, parent, configuration.name, mode=0o644)
        actual_pin = hashlib.sha256(original).hexdigest()
        accepted = before_pin if isinstance(before_pin, tuple) else (before_pin,)
        if actual_pin not in accepted:
            raise ValueError('Configuration is not the expected profile')
        output = copier.create_private(stack, parent, pending)
        copier.write_all(output, candidate)
        os.fchmod(output, 0o644)
        os.fsync(output)
        new_info = os.fstat(output)
        check_source()
        guard()
        copier.check_chain(chain, root_owned=True)
        copier.check_image(parent, configuration.name, old_fd, old_info)
        copier.check_image(parent, pending, output, new_info)
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        # Exchange retains the displaced file even if a root actor races the last check.
        if rename(parent, pending.encode(), parent, configuration.name.encode(), 2) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        try:
            os.fsync(parent)
            guard()
            check_source()
            copier.check_chain(chain, root_owned=True)
            for name, fd, old, data in ((pending, old_fd, old_info, original), (configuration.name, output, new_info, candidate)):
                now = os.fstat(fd)
                a, b = copier.identity(old), copier.identity(now)
                if a[:4] + a[5:] != b[:4] + b[5:] or copier.digest_fd(fd, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('Exchanged configuration identity or bytes changed')
                copier.check_image(parent, name, fd, now)
                copier.protected(now, 0o644)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Profile exchanged; preserve displaced configuration and inspect before retry') from error
        return {'kind': 'protected-waydroid-profile', 'configurationSha256': hashlib.sha256(candidate).hexdigest(),
                'displacedSha256': actual_pin, 'servicesActivated': False, 'androidOperationsExecuted': False,
                'propertiesRegenerated': False,
                'installable': False, 'readyForActivation': False}


def profile_with_detected_protocols(encoded):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(encoded.decode())
    for key in ('binder_protocol', 'service_manager_protocol'):
        if parser.has_option('waydroid', key):
            raise ValueError('Protocol normalization expects the stored pre-boot profile')
        parser.set('waydroid', key, 'aidl3')
    stream = io.StringIO()
    parser.write(stream)
    return stream.getvalue().encode()


def _configure_waydroid(copier, configuration, backup_parent, *, original_pin=WAYDROID_CONFIGURATION_SHA256,
                        guard=None, verify_codecs=None, restore=False):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux profile configuration required')
    configuration, backup_parent = Path(copier.canonical(str(configuration))), Path(copier.canonical(str(backup_parent)))
    guard = _assert_codec_stopped if guard is None else guard
    guard()
    (_verify_profile_codecs if verify_codecs is None else verify_codecs)(copier)
    with ExitStack() as stack:
        parent_chain = copier.open_chain(stack, backup_parent, root_owned=True)
        parent = parent_chain[-1][1]
        if not restore:
            cfg_chain = copier.open_chain(stack, configuration.parent, root_owned=True)
            cfg_fd, cfg_info, original = _read_profile_file(copier, stack, cfg_chain[-1][1], configuration.name, mode=0o644)
            if hashlib.sha256(original).hexdigest() != original_pin:
                raise ValueError('Original fresh configuration pin changed')
            # Publish a complete immutable recovery bundle before changing the active file.
            for name in ('waydroid-profile', 'pending-waydroid-profile'):
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError('Existing or partial profile backup; inspect before replay')
            os.mkdir('pending-waydroid-profile', 0o700, dir_fd=parent)
            directory = os.open('pending-waydroid-profile', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, directory)
            os.fchmod(directory, 0o700)
            files = {'original.cfg': original, 'baseline.cfg': profile_candidate(original), 'gpu.cfg': profile_candidate(original, gpu=True)}
            for name, data in files.items():
                fd = copier.create_private(stack, directory, name)
                copier.write_all(fd, data)
                os.fsync(fd)
                if copier.digest_fd(fd, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('Profile backup copy changed')
            os.fsync(directory)
            copier.check_chain(parent_chain, root_owned=True)
            copier.check_chain(cfg_chain, root_owned=True)
            copier.check_image(cfg_chain[-1][1], configuration.name, cfg_fd, cfg_info)
            rename_exclusive(parent, 'pending-waydroid-profile', 'waydroid-profile')
            os.fsync(parent)
        chain = copier.open_chain(stack, backup_parent / 'waydroid-profile', root_owned=True)
        directory = chain[-1][1]
        copier.protected(os.fstat(directory), 0o700)
        if set(os.listdir(directory)) != {'original.cfg', 'baseline.cfg', 'gpu.cfg'}:
            raise ValueError('Unexpected profile backup inventory')
        snapshots = {name: _read_profile_file(copier, stack, directory, name) for name in ('original.cfg', 'baseline.cfg', 'gpu.cfg')}
        original = snapshots['original.cfg'][2]
        if (hashlib.sha256(original).hexdigest() != original_pin or snapshots['baseline.cfg'][2] != profile_candidate(original)
                or snapshots['gpu.cfg'][2] != profile_candidate(original, gpu=True)):
            raise ValueError('Profile backup bytes changed')
        def check_source():
            copier.check_chain(parent_chain, root_owned=True)
            copier.check_chain(chain, root_owned=True)
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != set(snapshots):
                raise ValueError('Profile backup inventory changed')
            for name, (fd, before, data) in snapshots.items():
                copier.check_image(directory, name, fd, before, private=True)
                if copier.digest_fd(fd, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('Profile backup generation changed')
        baseline = snapshots['baseline.cfg'][2]
        candidate, before_pin = (original, (hashlib.sha256(baseline).hexdigest(),
                                           hashlib.sha256(profile_with_detected_protocols(baseline)).hexdigest())) if restore else (baseline, original_pin)
        return _exchange_profile(copier, configuration, candidate, before_pin,
                                 'pending-droiddeck-' + ('restore' if restore else 'baseline') + '.cfg',
                                 guard=guard, check_source=check_source)


def _prepare_profile_protocols(copier, configuration, backup, *, original_pin=WAYDROID_CONFIGURATION_SHA256,
                               guard=None, verify_codecs=None):
    """Prepare the exact SDK36 software profile before first session admission."""
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux fresh profile preparation required')
    configuration, backup = Path(copier.canonical(str(configuration))), Path(copier.canonical(str(backup)))
    guard = _assert_codec_stopped if guard is None else guard
    verify_codecs = _verify_profile_codecs if verify_codecs is None else verify_codecs
    guard()
    verify_codecs(copier)
    with ExitStack() as stack:
        chain = copier.open_chain(stack, backup, root_owned=True)
        directory = chain[-1][1]
        copier.protected(os.fstat(directory), 0o700)
        names = {'original.cfg', 'baseline.cfg', 'gpu.cfg'}
        if set(os.listdir(directory)) != names:
            raise ValueError('Closed original profile preparation sources required')
        snapshots = {name: _read_profile_file(copier, stack, directory, name) for name in sorted(names)}
        original = snapshots['original.cfg'][2]
        baseline = profile_candidate(original)
        if (hashlib.sha256(original).hexdigest() != original_pin
                or snapshots['baseline.cfg'][2] != baseline
                or snapshots['gpu.cfg'][2] != profile_candidate(original, gpu=True)):
            raise ValueError('Independent fresh profile recipe required')

        def check_source():
            copier.check_chain(chain, root_owned=True)
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != names:
                raise ValueError('Original profile preparation inventory changed')
            for name, (fd, before, data) in snapshots.items():
                copier.check_image(directory, name, fd, before, private=True)
                if copier.digest_fd(fd, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('Original profile preparation bytes changed')
            verify_codecs(copier)

        check_source()
        result = _exchange_profile(copier, configuration, profile_with_detected_protocols(baseline),
            hashlib.sha256(baseline).hexdigest(), 'pending-droiddeck-protocols.cfg',
            guard=guard, check_source=check_source)
        return {**result, 'protocolBaselinePrepared': True, 'graphicsProfile': 'software',
                'independentRecoveryArmed': False}


def _switch_gpu_profile(copier, configuration, backup, *, original_pin=WAYDROID_CONFIGURATION_SHA256,
                        guard=None, verify_codecs=None, restore=False):
    """Exchange only the config; activation needs a separate allocator/property health transaction."""
    if sys.platform != 'linux' or os.geteuid() != 0 or type(restore) is not bool:
        raise ValueError('Root Linux closed GPU profile transition required')
    configuration, backup = Path(copier.canonical(str(configuration))), Path(copier.canonical(str(backup)))
    guard = (lambda: (_assert_codec_stopped(), _assert_pid_metadata(copier))) if guard is None else guard
    verify_codecs = _verify_profile_codecs if verify_codecs is None else verify_codecs
    guard()
    verify_codecs(copier)
    with ExitStack() as stack:
        chain = copier.open_chain(stack, backup, root_owned=True)
        directory = chain[-1][1]
        copier.protected(os.fstat(directory), 0o700)
        names = {'original.cfg', 'baseline.cfg', 'gpu.cfg'}
        if set(os.listdir(directory)) != names:
            raise ValueError('Unexpected GPU recovery source inventory')
        snapshots = {name: _read_profile_file(copier, stack, directory, name) for name in sorted(names)}
        original = snapshots['original.cfg'][2]
        if (hashlib.sha256(original).hexdigest() != original_pin
                or snapshots['baseline.cfg'][2] != profile_candidate(original)
                or snapshots['gpu.cfg'][2] != profile_candidate(original, gpu=True)):
            raise ValueError('GPU recovery source bytes changed')
        baseline = profile_with_detected_protocols(snapshots['baseline.cfg'][2])
        gpu = profile_with_detected_protocols(snapshots['gpu.cfg'][2])
        displaced = None
        cfg_chain = copier.open_chain(stack, configuration.parent, root_owned=True)
        if restore:
            displaced = _read_profile_file(copier, stack, cfg_chain[-1][1], 'pending-droiddeck-gpu.cfg', mode=0o644)
            if displaced[2] != baseline:
                raise ValueError('GPU transition does not own the displaced software baseline')

        def check_source():
            copier.check_chain(chain, root_owned=True)
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != names:
                raise ValueError('GPU recovery source inventory changed')
            for name, (fd, before, data) in snapshots.items():
                copier.check_image(directory, name, fd, before, private=True)
                if copier.digest_fd(fd, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('GPU recovery source generation changed')
            copier.check_chain(cfg_chain, root_owned=True)
            if displaced:
                fd, before, data = displaced
                copier.check_image(cfg_chain[-1][1], 'pending-droiddeck-gpu.cfg', fd, before)
                if copier.digest_fd(fd, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('Displaced software baseline changed')
            verify_codecs(copier)

        check_source()
        result = _exchange_profile(copier, configuration, baseline if restore else gpu,
                                   hashlib.sha256(gpu if restore else baseline).hexdigest(),
                                   'pending-droiddeck-gpu-restore.cfg' if restore else 'pending-droiddeck-gpu.cfg',
                                   guard=guard, check_source=check_source)
        return {**result, 'graphicsProfile': 'software' if restore else 'gpu',
                'allocatorOverrideChanged': False, 'savedPropertiesChanged': False,
                'independentRecoveryArmed': False}


def _machine_gpu_profiles(original):
    software = profile_candidate(original)
    gpu = profile_candidate(original, gpu=True)
    return {'originalConfigurationSha256': hashlib.sha256(original).hexdigest(),
            'softwareConfigurationSha256': hashlib.sha256(profile_with_detected_protocols(software)).hexdigest(),
            'gpuConfigurationSha256': hashlib.sha256(profile_with_detected_protocols(gpu)).hexdigest()}


def _hold_machine_gpu_policy(copier, stack, metadata_root, backup, *, validate=None, inactive=None):
    policy, check_metadata = _hold_machine_pid_policy(copier, stack, metadata_root, validate=validate, inactive=inactive)
    backup = Path(copier.canonical(str(backup)))
    chain = copier.open_chain(stack, backup, root_owned=True)
    directory = chain[-1][1]
    copier.protected(os.fstat(directory), 0o700)
    names = {'original.cfg', 'baseline.cfg', 'gpu.cfg'}
    if set(os.listdir(directory)) != names:
        raise ValueError('Unexpected machine GPU source inventory')
    held = {name: _read_profile_file(copier, stack, directory, name) for name in sorted(names)}
    original = held['original.cfg'][2]
    if held['baseline.cfg'][2] != profile_candidate(original) or held['gpu.cfg'][2] != profile_candidate(original, gpu=True):
        raise ValueError('Machine GPU sources do not match accepted recipe')
    policy = {**policy, 'kind': 'protected-machine-gpu-profile-policy', **_machine_gpu_profiles(original),
              'gpuProfilePolicyRegenerated': True}

    def guard():
        check_metadata()
        copier.check_chain(chain, root_owned=True)
        copier.protected(os.fstat(directory), 0o700)
        if set(os.listdir(directory)) != names:
            raise ValueError('Machine GPU source inventory changed')
        for name, (fd, before, data) in held.items():
            copier.check_image(directory, name, fd, before, private=True)
            if copier.digest_fd(fd, len(data)) != hashlib.sha256(data).hexdigest():
                raise ValueError('Machine GPU source generation changed')
    guard()
    return policy, guard


def _machine_gpu_policy(copier, metadata_root=Path('/etc/droiddeck'), backup=Path('/var/lib/droiddeck/waydroid-profile'),
                        *, validate=None, verify_codecs=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_gpu_policy(copier, stack, metadata_root, backup, validate=validate, inactive=lambda: None)
        (_verify_profile_codecs if verify_codecs is None else verify_codecs)(copier)
        guard()
        return {**policy, 'codecsVerified': verify_codecs is None, 'independentRecoveryArmed': False}


def _switch_machine_gpu_profile(copier, configuration, backup, metadata_root, *, restore=False, validate=None,
                                inactive=None, verify_codecs=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_gpu_policy(copier, stack, metadata_root, backup, validate=validate, inactive=inactive)
        receipt = _switch_gpu_profile(copier, configuration, backup, original_pin=policy['originalConfigurationSha256'],
                                      guard=guard, verify_codecs=verify_codecs, restore=restore)
        return {**receipt, **{key: policy[key] for key in ('configurationDigest', 'metadataSha256', 'accountValidated',
                'originalConfigurationSha256', 'softwareConfigurationSha256', 'gpuConfigurationSha256')},
                'gpuProfilePolicyRegenerated': True, 'protectedMetadataMatched': True, 'codecsVerified': verify_codecs is None}


def _load_layer_parser(copier):
    source = Path(__file__).parents[2] / 'tools/waydroid-layer-property.py'
    with ExitStack() as stack:
        chain = copier.open_chain(stack, source.parent, root_owned=True)
        fd, before, encoded = _read_profile_file(copier, stack, chain[-1][1], source.name)
        if hashlib.sha256(encoded.replace(b'\r\n', b'\n')).hexdigest() != LAYER_SOURCE_SHA256:
            raise ValueError('Fixed-key parser source pin changed')
        copier.check_chain(chain, root_owned=True)
        copier.check_image(chain[-1][1], source.name, fd, before, private=True)
        module = types.ModuleType('protected_layer_parser')
        module.__file__ = str(source)
        exec(compile(encoded, str(source), 'exec'), module.__dict__)
        return module


def _offline_layer_context(stack, copier):
    _assert_codec_stopped()
    cfg_chain = copier.open_chain(stack, Path('/etc/droiddeck'), root_owned=True)
    fd, before, encoded = _read_profile_file(copier, stack, cfg_chain[-1][1], 'runtime.json', maximum=16384, mode=0o644)
    if hashlib.sha256(encoded).hexdigest() != CONFIGURATION_SHA256:
        raise ValueError('Guest layer runtime metadata changed')
    config = _validate_configuration(copier, encoded)['config']
    data = Path(config['home']) / '.local/share/waydroid/data'
    chain = copier.open_chain(stack, data)
    property_chain = copier.open_chain(stack, data / 'property')
    receipt_chain = copier.open_chain(stack, data / 'local/tmp' / GUEST_TARGET)
    receipt_fd, receipt_info, raw = _read_profile_file(copier, stack, receipt_chain[-1][1], 'droiddeck-guest-receipt.json')
    if hashlib.sha256(raw).hexdigest() != GUEST_RECEIPT_SHA256:
        raise ValueError('Admitted actual-data receipt changed')
    receipt = json.loads(raw)
    admitted = (receipt.get('dataDevice'), receipt.get('dataInode'))
    if any(type(value) is not int or value < 1 for value in admitted):
        raise ValueError('Invalid admitted guest data identity')
    def check():
        _assert_codec_stopped()
        copier.check_chain(cfg_chain, root_owned=True)
        copier.check_image(cfg_chain[-1][1], 'runtime.json', fd, before)
        _validate_configuration(copier, encoded)
        for current_chain in (chain, property_chain, receipt_chain):
            copier.check_chain(current_chain)
        info = os.fstat(chain[-1][1])
        if (info.st_dev, info.st_ino) != admitted or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (config['uid'], config['gid'], 0o771):
            raise ValueError('Actual guest data identity changed')
        for path, descriptor, _device, _inode in property_chain[:-1]:
            info = os.fstat(descriptor)
            if info.st_uid not in (0, config['uid']) or (path != data and info.st_mode & 0o022):
                raise ValueError('Unsafe guest property ancestor')
        copier.protected(os.fstat(property_chain[-1][1]), 0o700)
        copier.protected(os.fstat(receipt_chain[-1][1]), 0o700)
        copier.check_image(receipt_chain[-1][1], 'droiddeck-guest-receipt.json', receipt_fd, receipt_info, private=True)
        if copier.digest_fd(receipt_fd, len(raw)) != GUEST_RECEIPT_SHA256:
            raise ValueError('Actual-data receipt generation changed')
    check()
    return {'property': data / 'property', 'check': check, 'dataDevice': admitted[0], 'dataInode': admitted[1]}


def _edit_saved_layer(copier, layer, property_directory, *, expected, target, guard, check_context,
                      pending=None, recovery_source='pending-droiddeck-layer-on'):
    if sys.platform != 'linux' or os.geteuid() != 0 or (expected, target) not in (
            ('false', 'true'), ('absent', 'true'), ('true', 'false'), ('true', 'absent')):
        raise ValueError('Root Linux fixed boolean layer transition required')
    pending = pending or ('pending-droiddeck-layer-on' if target == 'true' else 'pending-droiddeck-layer-off')
    if not isinstance(pending, str) or not re.fullmatch(r'pending-droiddeck-layer-[a-z0-9-]{1,40}', pending):
        raise ValueError('Closed guest layer staging name required')
    if not isinstance(recovery_source, str) or not re.fullmatch(r'pending-droiddeck-layer-[a-z0-9-]{1,40}', recovery_source):
        raise ValueError('Closed guest layer recovery source required')
    guard()
    check_context()
    property_directory = Path(copier.canonical(str(property_directory)))
    with ExitStack() as stack:
        chain = copier.open_chain(stack, property_directory)
        parent = chain[-1][1]
        copier.protected(os.fstat(parent), 0o700)
        recovery = None
        if expected == 'true':
            recovery = _read_profile_file(copier, stack, parent, recovery_source, maximum=layer.MAX_BYTES)
            if layer.saved_value(recovery[2]) != target:
                raise ValueError('Saved layer transition does not own the original boolean or absence')
        def check_recovery():
            if recovery:
                descriptor, before, data = recovery
                copier.check_image(parent, recovery_source, descriptor, before, private=True)
                if copier.digest_fd(descriptor, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('Saved layer recovery source changed')
        original_fd, original_info, original = _read_profile_file(copier, stack, parent, 'persistent_properties', maximum=layer.MAX_BYTES)
        candidate = layer.rewrite_optional(original, (expected,), target)
        if candidate == original or len(candidate) > layer.MAX_BYTES:
            raise ValueError('Saved layer transition is absent or too large')
        output = copier.create_private(stack, parent, pending)
        copier.write_all(output, candidate)
        os.fsync(output)
        output_info = os.fstat(output)
        guard()
        check_context()
        check_recovery()
        copier.check_chain(chain)
        copier.protected(os.fstat(parent), 0o700)
        copier.check_image(parent, 'persistent_properties', original_fd, original_info, private=True)
        copier.check_image(parent, pending, output, output_info, private=True)
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(parent, pending.encode(), parent, b'persistent_properties', 2) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        try:
            os.fsync(parent)
            guard()
            check_context()
            check_recovery()
            copier.check_chain(chain)
            copier.protected(os.fstat(parent), 0o700)
            for name, descriptor, old, data in ((pending, original_fd, original_info, original),
                    ('persistent_properties', output, output_info, candidate)):
                now = os.fstat(descriptor)
                a, b = copier.identity(old), copier.identity(now)
                if a[:4] + a[5:] != b[:4] + b[5:] or copier.digest_fd(descriptor, len(data)) != hashlib.sha256(data).hexdigest():
                    raise ValueError('Saved layer exchange generation changed')
                copier.check_image(parent, name, descriptor, now, private=True)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Saved layer exchanged; inspect private in-data pending file before retry') from error
        # Displaced opaque bytes stay inside root-only Android property storage, never in source or artifacts.
        return {'kind': 'protected-saved-layer', 'selectedKey': layer.KEY, 'before': expected, 'after': target,
                'servicesActivated': False, 'propertiesRegenerated': False, 'opaqueDocumentExported': False,
                'independentRecoveryArmed': False, 'installable': False, 'readyForActivation': False}


def _configure_saved_layer(copier, *, restore=False):
    if type(restore) is not bool:
        raise ValueError('Closed layer restore mode required')
    layer = _load_layer_parser(copier)
    with ExitStack() as stack:
        context = _offline_layer_context(stack, copier)
        chain = copier.open_chain(stack, context['property'])
        name = 'pending-droiddeck-layer-on' if restore else 'persistent_properties'
        fd, before, data = _read_profile_file(copier, stack, chain[-1][1], name, maximum=layer.MAX_BYTES)
        original = layer.saved_value(data)
        if original not in ('false', 'absent'):
            raise ValueError('Saved layer original must be software boolean or absence')
        def check():
            context['check']()
            copier.check_chain(chain)
            copier.check_image(chain[-1][1], name, fd, before, private=True)
        # The initial active file is deliberately exchanged; only its pre-write generation is checked here.
        if restore:
            check_context = check
        else:
            check()
            check_context = context['check']
        result = _edit_saved_layer(copier, layer, context['property'],
            expected='true' if restore else original, target=original if restore else 'true',
            guard=_assert_codec_stopped, check_context=check_context)
        return {**result, 'dataDevice': context['dataDevice'], 'dataInode': context['dataInode']}


def _allocator_metadata(value, data_identity, misc_info, scope):
    keys = {'kind', 'version', 'beforeAbsent', 'candidateSha256', 'dataDevice', 'dataInode',
            'miscDevice', 'miscInode', 'fileDevice', 'fileInode', 'scope'}
    if (not isinstance(value, dict) or set(value) != keys or value.get('kind') != 'protected-mesa-allocator'
            or type(value.get('version')) is not int or value['version'] != 1 or value.get('beforeAbsent') is not True
            or value.get('candidateSha256') != hashlib.sha256(ALLOCATOR_CODE).hexdigest()
            or value.get('scope') != scope
            or any(type(value.get(key)) is not int or value[key] < 1 for key in keys if key.endswith(('Device', 'Inode')))
            or (value['dataDevice'], value['dataInode']) != (data_identity['dataDevice'], data_identity['dataInode'])
            or (value['miscDevice'], value['miscInode']) != (misc_info.st_dev, misc_info.st_ino)):
        raise ValueError('Foreign allocator ownership receipt')
    return value


def _configure_mesa_allocator(copier, misc, receipt_parent, data_identity, *, restore=False,
                              guard=None, check_context=None, scope='proof'):
    if sys.platform != 'linux' or os.geteuid() != 0 or type(restore) is not bool:
        raise ValueError('Root Linux closed allocator operation required')
    if type(scope) is not str or (scope not in ('proof', 'activation', 'machine') and not re.fullmatch('fresh-[a-f0-9]{12}', scope)):
        raise ValueError('Closed allocator transaction scope required')
    suffix = '' if scope == 'proof' else '-' + scope
    namespace = 'mesa-allocator' + suffix
    ready = 'pending-droiddeck-allocator' + suffix + '-ready'
    restored = 'pending-droiddeck-allocator' + suffix + '-restored'
    if (not isinstance(data_identity, dict) or set(data_identity) != {'dataDevice', 'dataInode'}
            or any(type(value) is not int or value < 1 for value in data_identity.values())):
        raise ValueError('Admitted data identity required')
    guard = _assert_codec_stopped if guard is None else guard
    if check_context is None:
        raise ValueError('Held actual-data context required')
    misc, receipt_parent = Path(copier.canonical(str(misc))), Path(copier.canonical(str(receipt_parent)))
    guard()
    check_context()
    with ExitStack() as stack:
        chain = copier.open_chain(stack, misc)
        parent_chain = copier.open_chain(stack, receipt_parent, root_owned=True)
        parent, metadata_parent = chain[-1][1], parent_chain[-1][1]
        misc_info = os.fstat(parent)
        def check():
            guard()
            check_context()
            copier.check_chain(chain)
            copier.check_chain(parent_chain, root_owned=True)
            now = os.fstat(parent)
            if (now.st_uid, now.st_gid, stat.S_IMODE(now.st_mode)) != (1000, 9998, 0o1771):
                raise ValueError('Unexpected Android misc ownership or mode')
        def absent(name):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('Existing allocator namespace must be preserved')
        check()
        if not restore:
            for name in ('waydroid_settings', ready, restored):
                absent(name)
            for name in (namespace, 'pending-' + namespace):
                try:
                    os.stat(name, dir_fd=metadata_parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError('Existing allocator receipt must be inspected, not replayed')
            output = copier.create_private(stack, parent, ready)
            copier.write_all(output, ALLOCATOR_CODE)
            os.fsync(output)
            file_info = os.fstat(output)
            metadata = {'kind': 'protected-mesa-allocator', 'version': 1, 'beforeAbsent': True, 'scope': scope,
                        'candidateSha256': hashlib.sha256(ALLOCATOR_CODE).hexdigest(), **data_identity,
                        'miscDevice': misc_info.st_dev, 'miscInode': misc_info.st_ino,
                        'fileDevice': file_info.st_dev, 'fileInode': file_info.st_ino}
            os.mkdir('pending-' + namespace, 0o700, dir_fd=metadata_parent)
            directory = os.open('pending-' + namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata_parent)
            stack.callback(os.close, directory)
            os.fchmod(directory, 0o700)
            directory_info = os.fstat(directory)
            marker = copier.create_private(stack, directory, 'receipt.json')
            encoded = (json.dumps(metadata, indent=2) + '\n').encode()
            copier.write_all(marker, encoded)
            os.fsync(marker)
            marker_info = os.fstat(marker)
            check()
            copier.check_image(parent, ready, output, file_info, private=True)
            if copier.digest_fd(output, len(ALLOCATOR_CODE)) != metadata['candidateSha256']:
                raise ValueError('Allocator candidate changed')
            copier.check_image(directory, 'receipt.json', marker, marker_info, private=True)
            if copier.digest_fd(marker, len(encoded)) != hashlib.sha256(encoded).hexdigest():
                raise ValueError('Allocator receipt changed')
            def check_new_receipt(entry):
                current = os.stat(entry, dir_fd=metadata_parent, follow_symlinks=False)
                if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (directory_info.st_dev, directory_info.st_ino):
                    raise ValueError('Allocator receipt directory changed')
                copier.protected(current, 0o700)
                if set(os.listdir(directory)) != {'receipt.json'}:
                    raise ValueError('Allocator receipt staging inventory changed')
                copier.check_image(directory, 'receipt.json', marker, marker_info, private=True)
                if copier.digest_fd(marker, len(encoded)) != hashlib.sha256(encoded).hexdigest():
                    raise ValueError('Allocator receipt staging generation changed')
            os.fsync(directory)
            check_new_receipt('pending-' + namespace)
            rename_exclusive(metadata_parent, 'pending-' + namespace, namespace)
            try:
                os.fsync(metadata_parent)
                check_new_receipt(namespace)
            except (OSError, ValueError) as error:
                raise PromotionUncertain('Allocator receipt published; inspect owned candidate before retry') from error
        # Immutable ownership metadata is durable before the Android-visible name is changed.
        receipt_chain = copier.open_chain(stack, receipt_parent / namespace, root_owned=True)
        directory = receipt_chain[-1][1]
        copier.protected(os.fstat(directory), 0o700)
        if set(os.listdir(directory)) != {'receipt.json'}:
            raise ValueError('Allocator receipt inventory changed')
        marker, marker_info, encoded = _read_profile_file(copier, stack, directory, 'receipt.json', maximum=16384)
        metadata = _allocator_metadata(json.loads(encoded), data_identity, misc_info, scope)
        if scope == 'machine' and encoded != (json.dumps(metadata, indent=2) + '\n').encode():
            raise ValueError('Closed machine allocator receipt encoding required')
        def check_receipt():
            check()
            copier.check_chain(receipt_chain, root_owned=True)
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != {'receipt.json'}:
                raise ValueError('Allocator receipt inventory changed')
            copier.check_image(directory, 'receipt.json', marker, marker_info, private=True)
            if copier.digest_fd(marker, len(encoded)) != hashlib.sha256(encoded).hexdigest():
                raise ValueError('Allocator receipt generation changed')
        source = ready
        if restore:
            absent(restored)
            present = []
            for name in ('waydroid_settings', ready):
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                present.append(name)
            if len(present) != 1:
                raise ValueError('Allocator source missing or ambiguous; no foreign state removed')
            source = present[0]
        descriptor, before, data = _read_profile_file(copier, stack, parent, source, maximum=len(ALLOCATOR_CODE))
        if data != ALLOCATOR_CODE or (before.st_dev, before.st_ino) != (metadata['fileDevice'], metadata['fileInode']):
            raise ValueError('Allocator receipt does not own the current file')
        check_receipt()
        copier.check_image(parent, source, descriptor, before, private=True)
        destination = restored if restore else 'waydroid_settings'
        absent(destination)
        rename_exclusive(parent, source, destination)
        try:
            os.fsync(parent)
            check_receipt()
            now = os.fstat(descriptor)
            a, b = copier.identity(before), copier.identity(now)
            if a[:4] + a[5:] != b[:4] + b[5:] or copier.digest_fd(descriptor, len(data)) != metadata['candidateSha256']:
                raise ValueError('Allocator publication generation changed')
            copier.check_image(parent, destination, descriptor, now, private=True)
            absent(source)
            if restore:
                absent('waydroid_settings')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Allocator name changed; preserve receipt and retained file before retry') from error
        return {**metadata, 'allocatorPresent': not restore, 'servicesActivated': False,
                'propertiesRegenerated': False, 'independentRecoveryArmed': False,
                'installable': False, 'readyForActivation': False}


def _mesa_allocator(copier, *, restore=False):
    with ExitStack() as stack:
        context = _offline_layer_context(stack, copier)
        identity = {key: context[key] for key in ('dataDevice', 'dataInode')}
        return _configure_mesa_allocator(copier, context['property'].parent / 'misc', Path('/var/lib/droiddeck'),
                                         identity, restore=restore, check_context=context['check'])


def _guest_process_identity():
    result = subprocess.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'],
                            capture_output=True, timeout=5, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw.isdigit() or not 1 < int(raw) < 2 ** 31:
        raise ValueError('Live Waydroid container identity required')
    pid = int(raw)
    if Path('/proc/' + str(pid)).stat().st_uid != 0:
        raise ValueError('Container process owner changed')
    with open('/proc/' + str(pid) + '/stat', 'rb') as stream:
        encoded = stream.read(4097)
    fields = encoded.rpartition(b') ')[2].split()
    if len(encoded) > 4096 or not encoded.startswith(raw + b' (') or len(fields) < 20 or not fields[19].isdigit():
        raise ValueError('Invalid process birth metadata')
    return pid, int(fields[19])


def _assert_guest_runtime_inactive():
    result = subprocess.run(['/usr/bin/systemctl', 'show', 'tesla-waydroid-runtime.service', '--property=ActiveState', '--value'],
                            capture_output=True, timeout=5, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    if result.returncode != 0 or result.stdout.strip() != b'inactive':
        raise ValueError('Preview runtime must remain inactive during guest admission')


def _machine_guest_context(stack, copier, metadata_root=Path('/etc/droiddeck'), *, validate=None, process=None,
                           mounted=None, runtime_idle=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux machine guest admission required')
    root = Path(copier.canonical(str(metadata_root)))
    cfg_chain = copier.open_chain(stack, root, root_owned=True)
    fd, before, encoded = _read_profile_file(copier, stack, cfg_chain[-1][1], 'runtime.json', maximum=16384, mode=0o644)
    def validation():
        return _validate_configuration(copier, encoded, service_policy=True) if validate is None else validate(encoded)
    value = validation()
    config, generation = dict(value['config']), value['configurationDigest']
    if not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{64}', generation):
        raise ValueError('Machine guest generation required')
    metadata_sha = hashlib.sha256(encoded).hexdigest()
    data = Path(copier.canonical(config['home'])) / '.local/share/waydroid/data'
    account_validated, mount_verified = validate is None, process is None and mounted is None
    process = _guest_process_identity if process is None else process
    mounted = (lambda pid, relative: os.stat('/proc/' + str(pid) + '/root/' + relative)) if mounted is None else mounted
    runtime_idle = _assert_guest_runtime_inactive if runtime_idle is None else runtime_idle

    def identity():
        current = process()
        if (type(current) is not tuple or len(current) != 2 or any(type(v) is not int for v in current)
                or not 1 < current[0] < 2**31 or not 0 < current[1] < 2**63):
            raise ValueError('Bounded machine guest process identity required')
        return current
    initial = identity()
    data_chain = copier.open_chain(stack, data)
    tmp_chain = copier.open_chain(stack, data / 'local/tmp')
    data_info, tmp_info = os.fstat(data_chain[-1][1]), os.fstat(tmp_chain[-1][1])

    def check():
        runtime_idle()
        copier.check_chain(cfg_chain, root_owned=True)
        copier.check_image(cfg_chain[-1][1], 'runtime.json', fd, before)
        if copier.digest_fd(fd, before.st_size) != metadata_sha:
            raise ValueError('Held machine guest metadata changed')
        current = validation()
        if current['config'] != config or current['configurationDigest'] != generation or identity() != initial:
            raise ValueError('Machine guest account or container generation changed')
        for chain, relative, original, owner in ((data_chain, 'data', data_info, (config['uid'], config['gid'])),
                (tmp_chain, 'data/local/tmp', tmp_info, (2000, 2000))):
            copier.check_chain(chain)
            now, actual = os.fstat(chain[-1][1]), mounted(initial[0], relative)
            if ((now.st_uid, now.st_gid, stat.S_IMODE(now.st_mode)) != (*owner, 0o771)
                    or (now.st_dev, now.st_ino) != (original.st_dev, original.st_ino)
                    or (actual.st_dev, actual.st_ino) != (original.st_dev, original.st_ino)):
                raise ValueError('Actual machine guest data mount changed')
        if identity() != initial:
            raise ValueError('Machine guest process changed during mount check')
    check()
    return {'tmp_chain': tmp_chain, 'check': check, 'pid': initial[0], 'birth': initial[1],
            'dataDevice': data_info.st_dev, 'dataInode': data_info.st_ino, 'configurationDigest': generation,
            'metadataSha256': metadata_sha, 'accountValidated': account_validated, 'guestMountMatched': mount_verified}


def _machine_guest_data_policy(copier):
    with ExitStack() as stack:
        context = _machine_guest_context(stack, copier)
        context['check']()
        return {'kind': 'protected-machine-guest-data-policy', **{k: context[k] for k in
                ('configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode', 'pid', 'birth', 'accountValidated', 'guestMountMatched')},
                'servicesActivated': False, 'androidOperationsExecuted': False, 'installable': False, 'readyForActivation': False}


def _install_machine_guest_payloads(copier, *, context=None, sources=None):
    return _install_guest_payloads(copier, context=_machine_guest_context if context is None else context,
                                   sources=sources, machine=True)


def _assert_machine_guest_stopped():
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
    for unit in ('waydroid-container.service', 'tesla-waydroid-runtime.service',
                 'tesla-waydroid-headless-session.service', 'tesla-waydroid-compositor.service'):
        result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID'],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=5, env=environment)
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError('Machine guest service status unavailable')
        lines = result.stdout.decode('ascii').splitlines()
        if len(lines) != 3 or set(lines) != {'ActiveState=inactive', 'MainPID=0', 'ControlPID=0'}:
            raise ValueError('Machine guest services must be stopped without workers')
    result = subprocess.run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-sH'],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=5, env=environment)
    if result.returncode or len(result.stdout) > 4096 or result.stdout.strip() != b'STOPPED':
        raise ValueError('Machine guest container must be stopped')


def _machine_guest_receipt(encoded, generation, metadata_sha, expected_records):
    def closed_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate machine guest receipt key')
            result[key] = value
        return result
    if not isinstance(encoded, bytes) or not 0 < len(encoded) <= 65536 or any(
            not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in (generation, metadata_sha)):
        raise ValueError('Bounded machine guest receipt required')
    value = json.loads(encoded.decode('utf-8'), object_pairs_hook=closed_object)
    false_flags = ('installable', 'readyForActivation', 'helperProcessesStarted', 'accountsOrDrmChanged',
                   'browserPngFallback', 'servicesActivated', 'androidOperationsExecuted')
    true_flags = ('accountValidated', 'guestMountMatched')
    fields = {'kind', 'version', 'pid', 'birth', 'dataDevice', 'dataInode', 'files',
              'configurationDigest', 'metadataSha256', *false_flags, *true_flags}
    if (type(value) is not dict or set(value) != fields or value['kind'] != 'protected-guest-payloads'
            or type(value['version']) is not int or value['version'] != 2
            or value['configurationDigest'] != generation or value['metadataSha256'] != metadata_sha
            or any(value[k] is not False for k in false_flags) or any(value[k] is not True for k in true_flags)):
        raise ValueError('Closed machine-bound guest receipt required')
    for name, minimum, limit in (('pid', 2, 2**31), ('birth', 1, 2**63), ('dataDevice', 1, 2**64), ('dataInode', 1, 2**64)):
        if type(value[name]) is not int or not minimum <= value[name] < limit:
            raise ValueError('Bounded machine guest receipt identity required')
    if type(value['files']) is not list or len(value['files']) != 7:
        raise ValueError('Closed machine guest receipt inventory required')
    for record in value['files']:
        if (type(record) is not dict or set(record) != {'path', 'size', 'sha256', 'mode'}
                or type(record['path']) is not str or type(record['size']) is not int or not 0 < record['size'] <= 8 * 1024 * 1024
                or type(record['mode']) is not int or record['mode'] not in (0o600, 0o700)
                or not isinstance(record['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise ValueError('Invalid machine guest file record')
    if value['files'] != expected_records:
        raise ValueError('Machine guest receipt does not match independent source recipe')
    return value


def _machine_offline_context(stack, copier, metadata_root=Path('/etc/droiddeck'), *, validate=None, inactive=None, sources=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux machine offline admission required')
    stopped = _assert_machine_guest_stopped if inactive is None else inactive
    stopped()
    root = Path(copier.canonical(str(metadata_root)))
    cfg_chain = copier.open_chain(stack, root, root_owned=True)
    metadata_fd, metadata_info, encoded = _read_profile_file(copier, stack, cfg_chain[-1][1], 'runtime.json', maximum=16384, mode=0o644)
    def validation():
        return _validate_configuration(copier, encoded, service_policy=True) if validate is None else validate(encoded)
    value = validation()
    config, generation = dict(value['config']), value['configurationDigest']
    metadata_sha = hashlib.sha256(encoded).hexdigest()
    selected = (_guest_sources if sources is None else sources)(stack, copier)
    expected_names = {'bin/tesla-android-x86-touch', 'bin/tesla-android-x86-api', 'bin/tesla-android-x86-display-latest',
                      'logical-capture.jar', 'location-bridge.jar', 'scrcpy-server-v4.0.jar', 'logical-screencap'}
    if set(selected) != expected_names:
        raise ValueError('Closed offline guest source recipe required')
    records = [{'path': n, 'size': v[4][0], 'sha256': v[4][1], 'mode': v[5]} for n, v in sorted(selected.items())]
    data = Path(copier.canonical(config['home'])) / '.local/share/waydroid/data'
    data_chain = copier.open_chain(stack, data)
    property_chain = copier.open_chain(stack, data / 'property')
    guest = data / 'local/tmp' / MACHINE_GUEST_TARGET
    chains = {n: copier.open_chain(stack, guest / n) for n in ('', 'bin', 'lib64')}
    directories = {n: chain[-1][1] for n, chain in chains.items()}
    receipt_name = 'droiddeck-guest-receipt.json'
    receipt_fd, receipt_info, raw = _read_profile_file(copier, stack, directories[''], receipt_name)
    receipt = _machine_guest_receipt(raw, generation, metadata_sha, records)
    receipt_sha = hashlib.sha256(raw).hexdigest()
    admitted = (receipt['dataDevice'], receipt['dataInode'])
    held = {}
    for record in records:
        relative, _, leaf = record['path'].rpartition('/')
        held[record['path']] = _read_profile_file(copier, stack, directories[relative], leaf,
                                                maximum=8 * 1024 * 1024, mode=record['mode'])[:2]
    expected_entries = {'': {'bin', 'lib64', receipt_name} | {n for n in expected_names if '/' not in n},
                        'bin': {n.split('/')[1] for n in expected_names if n.startswith('bin/')}, 'lib64': set()}

    def check():
        stopped()
        copier.check_chain(cfg_chain, root_owned=True)
        copier.check_image(cfg_chain[-1][1], 'runtime.json', metadata_fd, metadata_info)
        if copier.digest_fd(metadata_fd, metadata_info.st_size) != metadata_sha:
            raise ValueError('Held offline metadata changed')
        current = validation()
        if current['config'] != config or current['configurationDigest'] != generation:
            raise ValueError('Offline machine account or generation changed')
        for chain in (data_chain, property_chain, *chains.values()):
            copier.check_chain(chain)
        info = os.fstat(data_chain[-1][1])
        if (info.st_dev, info.st_ino) != admitted or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (config['uid'], config['gid'], 0o771):
            raise ValueError('Offline machine data identity changed')
        for path, descriptor, _device, _inode in property_chain[:-1]:
            info = os.fstat(descriptor)
            if info.st_uid not in (0, config['uid']) or (path != data and info.st_mode & 0o022):
                raise ValueError('Unsafe offline property ancestor')
        copier.protected(os.fstat(property_chain[-1][1]), 0o700)
        for name, directory in directories.items():
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != expected_entries[name]:
                raise ValueError('Unexpected offline guest entries')
        copier.check_image(directories[''], receipt_name, receipt_fd, receipt_info, private=True)
        if copier.digest_fd(receipt_fd, receipt_info.st_size) != receipt_sha:
            raise ValueError('Held offline receipt changed')
        for record in records:
            name, pin, mode = record['path'], (record['size'], record['sha256']), record['mode']
            relative, _, leaf = name.rpartition('/')
            descriptor, before = held[name]
            copier.check_image(directories[relative], leaf, descriptor, before)
            copier.protected(os.fstat(descriptor), mode)
            chain, source_leaf, source_fd, source_info, source_pin, _ = selected[name]
            copier.check_chain(chain, root_owned=True)
            copier.check_image(chain[-1][1], source_leaf, source_fd, source_info)
            copier.protected(os.fstat(source_fd))
            if before.st_size != pin[0] or copier.digest_fd(descriptor, pin[0]) != pin[1] or copier.digest_fd(source_fd, source_pin[0]) != source_pin[1]:
                raise ValueError('Independent offline guest recipe changed')
        stopped()
    check()
    return {'property': data / 'property', 'check': check, 'dataDevice': admitted[0], 'dataInode': admitted[1],
            'configurationDigest': generation, 'metadataSha256': metadata_sha, 'guestReceiptSha256': receipt_sha,
            'accountValidated': validate is None, 'guestPayloadsVerified': sources is None, 'servicesStoppedVerified': inactive is None}


def _machine_offline_data_policy(copier):
    with ExitStack() as stack:
        context = _machine_offline_context(stack, copier)
        context['check']()
        return {'kind': 'protected-machine-offline-data-policy', **{k: context[k] for k in
                ('configurationDigest', 'metadataSha256', 'guestReceiptSha256', 'dataDevice', 'dataInode',
                 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified')},
                'servicesActivated': False, 'androidOperationsExecuted': False, 'installable': False, 'readyForActivation': False}


def _hold_machine_gpu_baseline(copier, stack, configuration, metadata_root, backup, *, offline=None, profiles=None,
                               verify_codecs=None, property_name='persistent_properties'):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux machine GPU baseline required')
    if offline is None:
        _assert_machine_guest_stopped()
    if property_name not in ('persistent_properties', 'pending-droiddeck-layer-machine-on'):
        raise ValueError('Closed machine property baseline name required')
    context = _machine_offline_context(stack, copier, metadata_root) if offline is None else offline(stack, copier)
    if profiles is None:
        policy, profile_guard = _hold_machine_gpu_policy(copier, stack, metadata_root, backup, inactive=_assert_machine_guest_stopped)
    else:
        policy, profile_guard = profiles(copier, stack, metadata_root, backup)
    profile_keys = ('originalConfigurationSha256', 'softwareConfigurationSha256', 'gpuConfigurationSha256')
    if (any(policy[k] != context[k] for k in ('configurationDigest', 'metadataSha256')) or any(
            not isinstance(policy[k], str) or not re.fullmatch('[a-f0-9]{64}', policy[k]) for k in profile_keys)):
        raise ValueError('Machine GPU profile and guest generation differ')
    check_codecs = _verify_profile_codecs if verify_codecs is None else verify_codecs
    check_codecs(copier)
    layer = _load_layer_parser(copier)
    configuration = Path(copier.canonical(str(configuration)))
    cfg_chain = copier.open_chain(stack, configuration.parent, root_owned=True)
    property_chain = copier.open_chain(stack, context['property'])
    misc_chain = copier.open_chain(stack, context['property'].parent / 'misc')
    cfg_directory, property_directory, misc = cfg_chain[-1][1], property_chain[-1][1], misc_chain[-1][1]
    cfg_fd, cfg_info, cfg_raw = _read_profile_file(copier, stack, cfg_directory, configuration.name, mode=0o644)
    if hashlib.sha256(cfg_raw).hexdigest() != policy['softwareConfigurationSha256']:
        raise ValueError('Fresh stopped machine software baseline required')
    property_fd, property_info, document = _read_profile_file(copier, stack, property_directory, property_name, maximum=layer.MAX_BYTES)
    before_layer = layer.saved_value(document)
    if before_layer not in ('false', 'absent'):
        raise ValueError('Machine GPU baseline must have software layer or absence')
    property_sha = hashlib.sha256(document).hexdigest()
    misc_info, property_directory_info = os.fstat(misc), os.fstat(property_directory)

    def check(*, configuration_current=True, property_current=True):
        context['check']()
        profile_guard()
        check_codecs(copier)
        copier.check_chain(cfg_chain, root_owned=True)
        for chain in (property_chain, misc_chain):
            copier.check_chain(chain)
        copier.protected(os.fstat(property_directory), 0o700)
        now = os.fstat(misc)
        if (now.st_uid, now.st_gid, stat.S_IMODE(now.st_mode)) != (1000, 9998, 0o1771):
            raise ValueError('Unexpected machine Android misc ownership')
        if configuration_current:
            copier.check_image(cfg_directory, configuration.name, cfg_fd, cfg_info)
        if property_current:
            copier.check_image(property_directory, property_name, property_fd, property_info, private=True)
        if ((configuration_current and copier.digest_fd(cfg_fd, cfg_info.st_size) != policy['softwareConfigurationSha256'])
                or (property_current and copier.digest_fd(property_fd, property_info.st_size) != property_sha)):
            raise ValueError('Machine GPU baseline generation changed')
    check()
    # Recompute the same canonical record at prepare and reopen; never trust a stored file allowlist.
    manifest = {'kind': 'protected-machine-gpu-transaction', 'version': 1, 'stage': 'prepared',
                'beforeLayer': before_layer, 'savedDocumentSha256': property_sha,
                **{key: context[key] for key in ('configurationDigest', 'metadataSha256', 'guestReceiptSha256', 'dataDevice', 'dataInode')},
                **{key: policy[key] for key in profile_keys}}
    for prefix, info in (('configuration', cfg_info), ('property', property_info),
                         ('propertyDirectory', property_directory_info), ('misc', misc_info)):
        manifest.update({prefix + 'Device': info.st_dev, prefix + 'Inode': info.st_ino})
    return {'manifest': manifest, 'context': context, 'check': check, 'configurationDirectory': cfg_directory,
            'propertyDirectory': property_directory, 'miscDirectory': misc, 'misc': context['property'].parent / 'misc',
            'configurationFd': cfg_fd, 'configurationInfo': cfg_info, 'propertyDocument': document, 'layerParser': layer}


def _prepare_machine_gpu_transaction(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, offline=None, profiles=None, verify_codecs=None):
    with ExitStack() as stack:
        baseline = _hold_machine_gpu_baseline(copier, stack, configuration, metadata_root, backup,
                                             offline=offline, profiles=profiles, verify_codecs=verify_codecs)
        context, manifest = baseline['context'], baseline['manifest']
        state_parent = Path(copier.canonical(str(state_parent)))
        parent_chain = copier.open_chain(stack, state_parent, root_owned=True)
        parent = parent_chain[-1][1]
        cfg_directory, property_directory, misc = (baseline[k] for k in ('configurationDirectory', 'propertyDirectory', 'miscDirectory'))
        final, pending = 'machine-gpu-transaction', 'pending-machine-gpu-transaction'
        reserved = ((cfg_directory, ('pending-droiddeck-machine-gpu.cfg', 'pending-droiddeck-machine-gpu-restore.cfg',
                        'pending-droiddeck-gpu.cfg', 'pending-droiddeck-gpu-restore.cfg',
                        'pending-droiddeck-gpu-activation.cfg', 'pending-droiddeck-gpu-activation-restore.cfg')),
                    (property_directory, tuple('pending-droiddeck-layer-' + suffix for suffix in
                        ('machine-on', 'machine-off', 'on', 'off', 'activation-on', 'activation-off'))),
                    (misc, ('waydroid_settings',) + tuple('pending-droiddeck-allocator-' + prefix + suffix
                        for prefix in ('', 'activation-', 'machine-') for suffix in ('ready', 'restored'))),
                    (parent, ('gpu-activation',) + tuple(prefix + name for prefix in ('', 'pending-')
                        for name in ('mesa-allocator', 'mesa-allocator-activation', 'mesa-allocator-machine'))))

        def absent(directory, name):
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('Existing machine GPU namespace must be preserved')

        def check():
            baseline['check']()
            copier.check_chain(parent_chain, root_owned=True)
            for directory, names in reserved:
                for name in names:
                    absent(directory, name)
        check()
        for name in (final, pending):
            absent(parent, name)
        encoded = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
        manifest_sha = hashlib.sha256(encoded).hexdigest()
        os.mkdir(pending, 0o700, dir_fd=parent)
        directory = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, directory)
        os.fchown(directory, 0, 0)
        os.fchmod(directory, 0o700)
        directory_info = os.fstat(directory)
        output = copier.create_private(stack, directory, 'manifest.json')
        copier.write_all(output, encoded)
        os.fsync(output)
        output_info = os.fstat(output)

        def check_output(name):
            now = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(now.st_mode) or (now.st_dev, now.st_ino) != (directory_info.st_dev, directory_info.st_ino):
                raise ValueError('Machine GPU transaction directory changed')
            copier.protected(now, 0o700)
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != {'manifest.json'}:
                raise ValueError('Unexpected machine GPU transaction inventory')
            copier.check_image(directory, 'manifest.json', output, output_info, private=True)
            if copier.digest_fd(output, len(encoded)) != manifest_sha:
                raise ValueError('Machine GPU transaction manifest changed')
        os.fsync(directory)
        check()
        check_output(pending)
        rename_exclusive(parent, pending, final)
        try:
            os.fsync(parent)
            check()
            check_output(final)
            absent(parent, pending)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Machine GPU transaction published; inspect protected checkpoint before retry') from error
        return {'kind': 'protected-machine-gpu-preparation', 'stage': 'prepared', 'transactionManifestSha256': manifest_sha,
                **{k: context[k] for k in ('configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode')},
                'accountValidated': offline is None and profiles is None, 'guestPayloadsVerified': offline is None,
                'servicesStoppedVerified': offline is None, 'codecsVerified': verify_codecs is None,
                'servicesActivated': False, 'propertiesRegenerated': False, 'configurationChanged': False,
                'savedPropertiesChanged': False, 'allocatorOverrideChanged': False, 'independentRecoveryArmed': False,
                'androidOperationsExecuted': False, 'installable': False, 'readyForActivation': False}


def _hold_machine_gpu_transaction(copier, stack, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, offline=None, profiles=None, verify_codecs=None,
        allocator=False, configuration_receipt=False, layer_receipt=False, property_name='persistent_properties'):
    if any(type(v) is not bool for v in (allocator, configuration_receipt, layer_receipt)) or sum((allocator, configuration_receipt, layer_receipt)) > 1:
        raise ValueError('Closed machine transaction admission mode required')
    baseline = _hold_machine_gpu_baseline(copier, stack, configuration, metadata_root, backup,
                                         offline=offline, profiles=profiles, verify_codecs=verify_codecs, property_name=property_name)
    import fcntl
    state = Path(copier.canonical(str(state_parent))) / 'machine-gpu-transaction'
    chain = copier.open_chain(stack, state, root_owned=True)
    directory = chain[-1][1]
    copier.protected(os.fstat(directory), 0o700)
    # A directory flock serializes cooperating mutators without creating/replacing a lock file.
    fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fd, info, encoded = _read_profile_file(copier, stack, directory, 'manifest.json')
    expected = (json.dumps(baseline['manifest'], sort_keys=True, indent=2) + '\n').encode()
    if encoded != expected:
        raise ValueError('Machine transaction does not match independently held baseline')
    digest = hashlib.sha256(encoded).hexdigest()

    def check(*, configuration_current=True, property_current=True):
        baseline['check'](configuration_current=configuration_current, property_current=property_current)
        copier.check_chain(chain, root_owned=True)
        copier.protected(os.fstat(directory), 0o700)
        copier.check_image(directory, 'manifest.json', fd, info, private=True)
        if copier.digest_fd(fd, info.st_size) != digest:
            raise ValueError('Held machine transaction changed')
        entries = set(os.listdir(directory))
        allowed = ({'manifest.json'}, {'manifest.json', 'mesa-allocator-machine'}, {'manifest.json', 'pending-mesa-allocator-machine'}) if allocator else ({'manifest.json'},)
        if configuration_receipt:
            allowed = ({'manifest.json'}, {'manifest.json', 'configuration.json'}, {'manifest.json', 'pending-configuration.json'})
        if layer_receipt:
            allowed = ({'manifest.json'}, {'manifest.json', 'saved-layer.json'}, {'manifest.json', 'pending-saved-layer.json'})
        if entries not in allowed:
            raise ValueError('Unexpected machine transaction inventory')
        try:
            os.stat('pending-machine-gpu-transaction', dir_fd=chain[-2][1], follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('Partial machine transaction must be inspected')
    check()
    return {**baseline, 'state': state, 'stateDirectory': directory, 'check': check, 'transactionManifestSha256': digest}


def _machine_gpu_allocator(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, restore=False, supervised=None,
        offline=None, profiles=None, verify_codecs=None):
    # Internal only until the independent machine watcher/worker is wired; there is deliberately no CLI.
    if sys.platform != 'linux' or os.geteuid() != 0 or type(restore) is not bool or not callable(supervised):
        raise ValueError('Supervised root Linux machine allocator operation required')
    supervised()
    with ExitStack() as stack:
        admitted = _hold_machine_gpu_transaction(copier, stack, state_parent, configuration, metadata_root, backup,
            offline=offline, profiles=profiles, verify_codecs=verify_codecs, allocator=True)
        expected = {'manifest.json', 'mesa-allocator-machine'} if restore else {'manifest.json'}
        if set(os.listdir(admitted['stateDirectory'])) != expected:
            raise ValueError('Machine allocator replay or partial receipt must be inspected')
        context = admitted['context']
        result = _configure_mesa_allocator(copier, admitted['misc'], admitted['state'],
            {k: context[k] for k in ('dataDevice', 'dataInode')}, restore=restore, scope='machine',
            guard=supervised, check_context=admitted['check'])
        return {**result, 'transactionManifestSha256': admitted['transactionManifestSha256'],
                **{k: context[k] for k in ('configurationDigest', 'metadataSha256')},
                'accountValidated': offline is None and profiles is None, 'guestPayloadsVerified': offline is None,
                'servicesStoppedVerified': offline is None, 'codecsVerified': verify_codecs is None,
                'configurationChanged': False, 'savedPropertiesChanged': False, 'androidOperationsExecuted': False}


def _machine_gpu_finish_allocator_receipt(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, expected_manifest_sha256=None,
        supervised=None, offline=None, profiles=None, verify_codecs=None):
    # Metadata only: never select the allocator while finishing its interrupted receipt.
    if (sys.platform != 'linux' or os.geteuid() != 0 or not callable(supervised)
            or type(expected_manifest_sha256) is not str or not re.fullmatch('[a-f0-9]{64}', expected_manifest_sha256)):
        raise ValueError('Supervised root Linux explicit transaction digest required')
    supervised()
    with ExitStack() as stack:
        admitted = _hold_machine_gpu_transaction(copier, stack, state_parent, configuration, metadata_root, backup,
            offline=offline, profiles=profiles, verify_codecs=verify_codecs, allocator=True)
        if admitted['transactionManifestSha256'] != expected_manifest_sha256:
            raise ValueError('Stale allocator receipt transaction')
        state_directory, misc = admitted['stateDirectory'], admitted['miscDirectory']
        pending, final = 'pending-mesa-allocator-machine', 'mesa-allocator-machine'
        ready = 'pending-droiddeck-allocator-machine-ready'
        if set(os.listdir(state_directory)) != {'manifest.json', pending}:
            raise ValueError('Only an exact pending machine allocator receipt may be completed')
        directory = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=state_directory)
        stack.callback(os.close, directory)
        directory_info = os.fstat(directory)
        copier.protected(directory_info, 0o700)
        candidate_fd, candidate_info, candidate_raw = _read_profile_file(copier, stack, misc, ready)
        if candidate_raw != ALLOCATOR_CODE:
            raise ValueError('Pending allocator does not match independent fixed recipe')
        receipt_fd, receipt_info, encoded = _read_profile_file(copier, stack, directory, 'receipt.json', maximum=16384)
        context, misc_info = admitted['context'], os.fstat(misc)
        candidate_sha = hashlib.sha256(ALLOCATOR_CODE).hexdigest()
        value = {'kind': 'protected-mesa-allocator', 'version': 1, 'beforeAbsent': True, 'scope': 'machine',
                 'candidateSha256': candidate_sha, **{k: context[k] for k in ('dataDevice', 'dataInode')},
                 'miscDevice': misc_info.st_dev, 'miscInode': misc_info.st_ino,
                 'fileDevice': candidate_info.st_dev, 'fileInode': candidate_info.st_ino}
        if encoded != (json.dumps(value, indent=2) + '\n').encode():
            raise ValueError('Pending allocator receipt must exactly bind the independent candidate and data')
        digest = hashlib.sha256(encoded).hexdigest()
        absent_names = [(misc, 'waydroid_settings'), (misc, 'pending-droiddeck-allocator-machine-restored'),
            (admitted['configurationDirectory'], 'pending-droiddeck-machine-gpu.cfg'),
            (admitted['configurationDirectory'], 'pending-droiddeck-machine-gpu-restore.cfg'),
            (admitted['propertyDirectory'], 'pending-droiddeck-layer-machine-on'),
            (admitted['propertyDirectory'], 'pending-droiddeck-layer-machine-off')]

        def guard(published=False):
            supervised()
            admitted['check']()
            name = final if published else pending
            if set(os.listdir(state_directory)) != {'manifest.json', name}:
                raise ValueError('Allocator completion transaction inventory changed')
            current = os.stat(name, dir_fd=state_directory, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (directory_info.st_dev, directory_info.st_ino):
                raise ValueError('Held allocator receipt directory changed')
            copier.protected(current, 0o700)
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != {'receipt.json'}:
                raise ValueError('Allocator receipt completion inventory changed')
            for parent, entry in absent_names:
                try:
                    os.stat(entry, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError('Selected, retained or ambiguous candidate must be preserved')
            copier.check_image(misc, ready, candidate_fd, candidate_info, private=True)
            if copier.digest_fd(candidate_fd, candidate_info.st_size) != candidate_sha:
                raise ValueError('Held allocator candidate changed')
            copier.check_image(directory, 'receipt.json', receipt_fd, receipt_info, private=True)
            if copier.digest_fd(receipt_fd, receipt_info.st_size) != digest:
                raise ValueError('Held pending allocator receipt changed')
        guard()
        for fd in (candidate_fd, misc, receipt_fd, directory, state_directory):
            os.fsync(fd)
        guard()
        rename_exclusive(state_directory, pending, final)
        try:
            os.fsync(state_directory)
            guard(True)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Allocator receipt completion published; inspect compound ownership before retry') from error
        return {'kind': 'protected-machine-gpu-allocator-receipt-completion',
                'transactionManifestSha256': admitted['transactionManifestSha256'],
                'componentReceiptPublished': True, 'componentSelected': False,
                **{k: context[k] for k in ('configurationDigest', 'metadataSha256')},
                'accountValidated': offline is None and profiles is None, 'guestPayloadsVerified': offline is None,
                'servicesStoppedVerified': offline is None, 'codecsVerified': verify_codecs is None,
                'recoveryJournalChanged': False, 'configurationChanged': False, 'savedPropertiesChanged': False,
                'allocatorOverrideChanged': False, 'servicesActivated': False, 'propertiesRegenerated': False,
                'androidOperationsExecuted': False, 'independentRecoveryArmed': False, 'installable': False, 'readyForActivation': False}


def _exchange_machine_gpu_names(directory, current, pending):
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(directory, current.encode('ascii'), directory, pending.encode('ascii'), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _machine_gpu_configuration(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, restore=False, supervised=None,
        offline=None, profiles=None, verify_codecs=None):
    return _machine_gpu_owned_exchange(copier, state_parent, configuration, metadata_root, backup, role='configuration',
        restore=restore, supervised=supervised, offline=offline, profiles=profiles, verify_codecs=verify_codecs)


def _machine_gpu_saved_layer(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, restore=False, supervised=None,
        offline=None, profiles=None, verify_codecs=None):
    return _machine_gpu_owned_exchange(copier, state_parent, configuration, metadata_root, backup, role='saved-layer',
        restore=restore, supervised=supervised, offline=offline, profiles=profiles, verify_codecs=verify_codecs)


def _machine_gpu_owned_exchange(copier, state_parent, configuration, metadata_root, backup, *, role,
        restore=False, supervised=None, offline=None, profiles=None, verify_codecs=None):
    # Internal only: an independent machine watcher must be wired before exposing activation.
    if (sys.platform != 'linux' or os.geteuid() != 0 or type(restore) is not bool or not callable(supervised)
            or role not in ('configuration', 'saved-layer')):
        raise ValueError('Supervised root Linux closed machine exchange required')
    supervised()
    if offline is None:
        _assert_machine_guest_stopped()
    configuration = Path(copier.canonical(str(configuration)))
    state = Path(copier.canonical(str(state_parent))) / 'machine-gpu-transaction'
    saved_layer = role == 'saved-layer'
    on, off = (('pending-droiddeck-layer-machine-on', 'pending-droiddeck-layer-machine-off') if saved_layer
               else ('pending-droiddeck-machine-gpu.cfg', 'pending-droiddeck-machine-gpu-restore.cfg'))
    receipt_name = 'saved-layer.json' if saved_layer else 'configuration.json'
    receipt_kind = 'protected-machine-gpu-' + role
    file_mode = 0o600 if saved_layer else 0o644
    with ExitStack() as stack:
        if saved_layer:
            initial_context = _machine_offline_context(stack, copier, metadata_root) if offline is None else offline(stack, copier)
            selected_path = initial_context['property'] / 'persistent_properties'
        else:
            selected_path = configuration
        selected_chain = copier.open_chain(stack, selected_path.parent, root_owned=not saved_layer)
        directory = selected_chain[-1][1]
        initial_receipt = None
        baseline_path = configuration
        property_name = 'persistent_properties'
        if restore:
            receipt_chain = copier.open_chain(stack, state, root_owned=True)
            initial_receipt = _read_profile_file(copier, stack, receipt_chain[-1][1], receipt_name)
            receipt_value = json.loads(initial_receipt[2])
            if (type(receipt_value) is not dict or set(receipt_value) != {'kind', 'version', 'transactionManifestSha256',
                    'candidateDevice', 'candidateInode', 'candidateSha256'} or receipt_value['kind'] != receipt_kind
                    or type(receipt_value['version']) is not int or receipt_value['version'] != 1
                    or any(type(receipt_value[k]) is not int or not 0 < receipt_value[k] < 2**64 for k in ('candidateDevice', 'candidateInode'))
                    or initial_receipt[2] != (json.dumps(receipt_value, sort_keys=True, indent=2) + '\n').encode()):
                raise ValueError('Closed machine configuration ownership receipt required')
            current = os.stat(selected_path.name, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (receipt_value['candidateDevice'], receipt_value['candidateInode']):
                if saved_layer:
                    property_name = on
                else:
                    baseline_path = configuration.parent / on
        admitted = _hold_machine_gpu_transaction(copier, stack, state_parent, baseline_path, metadata_root, backup,
            offline=offline, profiles=profiles, verify_codecs=verify_codecs,
            configuration_receipt=not saved_layer, layer_receipt=saved_layer, property_name=property_name)
        manifest = admitted['manifest']
        state_directory = admitted['stateDirectory']
        if saved_layer:
            initial_context['check']()
            original_document = admitted['propertyDocument']
            layer = admitted['layerParser']
            candidate = layer.rewrite_optional(original_document, (manifest['beforeLayer'],), 'true')
            if not 0 < len(candidate) <= layer.MAX_BYTES:
                raise ValueError('Bounded fixed-key machine layer candidate required')
            candidate_sha = hashlib.sha256(candidate).hexdigest()
        else:
            candidate_sha = manifest['gpuConfigurationSha256']

        def absent(name):
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('Existing machine configuration namespace must be preserved')

        def environment():
            supervised()
            admitted['check'](configuration_current=saved_layer, property_current=not saved_layer)
            copier.check_chain(selected_chain, root_owned=not saved_layer)
            if saved_layer:
                initial_context['check']()

        absent(off)
        if not restore:
            if set(os.listdir(state_directory)) != {'manifest.json'}:
                raise ValueError('Existing configuration ownership receipt must be inspected')
            absent(on)
            if not saved_layer:
                source_chain = copier.open_chain(stack, Path(copier.canonical(str(backup))), root_owned=True)
                source_fd, source_info, source = _read_profile_file(copier, stack, source_chain[-1][1], 'gpu.cfg')
                candidate = profile_with_detected_protocols(source)
                if hashlib.sha256(candidate).hexdigest() != candidate_sha:
                    raise ValueError('Independent machine GPU candidate changed')
            admitted['check']()
            output = copier.create_private(stack, directory, on)
            copier.write_all(output, candidate)
            os.fchmod(output, file_mode)
            os.fsync(output)
            output_info = os.fstat(output)
            admitted['check']()
            if not saved_layer:
                copier.check_chain(source_chain, root_owned=True)
                copier.check_image(source_chain[-1][1], 'gpu.cfg', source_fd, source_info, private=True)
            copier.check_image(directory, on, output, output_info, private=saved_layer)
            if copier.digest_fd(output, len(candidate)) != candidate_sha:
                raise ValueError('Machine GPU staged candidate changed')
            receipt_value = {'kind': receipt_kind, 'version': 1,
                'transactionManifestSha256': admitted['transactionManifestSha256'], 'candidateDevice': output_info.st_dev,
                'candidateInode': output_info.st_ino, 'candidateSha256': candidate_sha}
            encoded = (json.dumps(receipt_value, sort_keys=True, indent=2) + '\n').encode()
            pending_receipt = 'pending-' + receipt_name
            receipt_fd = copier.create_private(stack, state_directory, pending_receipt)
            copier.write_all(receipt_fd, encoded)
            os.fsync(receipt_fd)
            receipt_info = os.fstat(receipt_fd)
            os.fsync(directory)
            os.fsync(state_directory)
            supervised()
            admitted['check']()
            copier.check_image(directory, on, output, output_info, private=saved_layer)
            copier.check_image(state_directory, pending_receipt, receipt_fd, receipt_info, private=True)
            receipt_sha = hashlib.sha256(encoded).hexdigest()
            if (copier.digest_fd(output, len(candidate)) != candidate_sha
                    or copier.digest_fd(receipt_fd, len(encoded)) != receipt_sha
                    or set(os.listdir(state_directory)) != {'manifest.json', pending_receipt}):
                raise ValueError('Staged machine ownership receipt or candidate changed')
            rename_exclusive(state_directory, pending_receipt, receipt_name)
            try:
                os.fsync(state_directory)
                supervised()
                admitted['check']()
                now = os.fstat(receipt_fd)
                a, b = copier.identity(receipt_info), copier.identity(now)
                if a[:4] + a[5:] != b[:4] + b[5:]:
                    raise ValueError('Published component receipt generation changed')
                copier.check_image(state_directory, receipt_name, receipt_fd, now, private=True)
                copier.check_image(directory, on, output, output_info, private=saved_layer)
                if (copier.digest_fd(receipt_fd, len(encoded)) != receipt_sha
                        or copier.digest_fd(output, len(candidate)) != candidate_sha):
                    raise ValueError('Published machine ownership receipt or candidate changed')
                receipt_info = now
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                raise PromotionUncertain('Machine component receipt published; inspect owned candidate before retry') from error
        else:
            receipt_fd, receipt_info, encoded = initial_receipt
            if (receipt_value['transactionManifestSha256'] != admitted['transactionManifestSha256']
                    or receipt_value['candidateSha256'] != candidate_sha):
                raise ValueError('Machine configuration receipt binding changed')
        receipt_sha = hashlib.sha256(encoded).hexdigest()
        original_identity = ((manifest['propertyDevice'], manifest['propertyInode']) if saved_layer
                             else (manifest['configurationDevice'], manifest['configurationInode']))
        original_sha = manifest['savedDocumentSha256'] if saved_layer else manifest['softwareConfigurationSha256']
        candidate_identity = (receipt_value['candidateDevice'], receipt_value['candidateInode'])

        def owned(name, expected, digest):
            descriptor, info, raw = _read_profile_file(copier, stack, directory, name, mode=file_mode)
            if (info.st_dev, info.st_ino) != expected or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Machine transaction does not own configuration inode or bytes')
            return descriptor, info

        def check_pair(gpu_selected, *, retained=False):
            environment()
            copier.check_image(state_directory, receipt_name, receipt_fd, receipt_info, private=True)
            if copier.digest_fd(receipt_fd, receipt_info.st_size) != receipt_sha:
                raise ValueError('Held configuration receipt changed')
            if set(os.listdir(state_directory)) != {'manifest.json', receipt_name}:
                raise ValueError('Foreign machine configuration state inventory')
            owned(selected_path.name if gpu_selected else (off if retained else on), candidate_identity, candidate_sha)
            owned(on if gpu_selected else selected_path.name, original_identity, original_sha)
            if retained:
                absent(on)
            else:
                absent(off)
        gpu_selected = restore and (property_name == on if saved_layer else baseline_path.name == on)
        check_pair(gpu_selected)
        if not restore or gpu_selected:
            _exchange_machine_gpu_names(directory, selected_path.name, on)
            try:
                os.fsync(directory)
                check_pair(not gpu_selected)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                raise PromotionUncertain('Machine ' + role + ' exchanged; preserve private receipt and displaced files before retry') from error
        if restore:
            check_pair(False)
            rename_exclusive(directory, on, off)
            try:
                os.fsync(directory)
                check_pair(False, retained=True)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                raise PromotionUncertain('Machine ' + role + ' restored; inspect retained private candidate before retry') from error
        context = admitted['context']
        return {'kind': receipt_kind, ('gpuLayerSelected' if saved_layer else 'gpuConfigurationSelected'): not restore,
                'transactionManifestSha256': admitted['transactionManifestSha256'], 'configurationChanged': not saved_layer,
                **{k: context[k] for k in ('configurationDigest', 'metadataSha256')},
                'accountValidated': offline is None and profiles is None, 'guestPayloadsVerified': offline is None,
                'servicesStoppedVerified': offline is None, 'codecsVerified': verify_codecs is None,
                'servicesActivated': False, 'propertiesRegenerated': False, 'savedPropertiesChanged': saved_layer,
                'allocatorOverrideChanged': False, 'independentRecoveryArmed': False, 'androidOperationsExecuted': False,
                'installable': False, 'readyForActivation': False}


def _machine_gpu_finish_component_receipt(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, role=None, expected_manifest_sha256=None,
        supervised=None, offline=None, profiles=None, verify_codecs=None):
    # Finish only a complete receipt for an unselected, independently verified candidate.
    if (sys.platform != 'linux' or os.geteuid() != 0 or not callable(supervised)
            or type(role) is not str or role not in ('configuration', 'saved-layer')
            or type(expected_manifest_sha256) is not str or not re.fullmatch('[a-f0-9]{64}', expected_manifest_sha256)):
        raise ValueError('Supervised root Linux closed role and explicit transaction digest required')
    supervised()
    with ExitStack() as stack:
        saved_layer = role == 'saved-layer'
        admitted = _hold_machine_gpu_transaction(copier, stack, state_parent, configuration, metadata_root, backup,
            offline=offline, profiles=profiles, verify_codecs=verify_codecs,
            configuration_receipt=not saved_layer, layer_receipt=saved_layer)
        if admitted['transactionManifestSha256'] != expected_manifest_sha256:
            raise ValueError('Stale component receipt transaction')
        directory = admitted['propertyDirectory'] if saved_layer else admitted['configurationDirectory']
        state_directory = admitted['stateDirectory']
        on, off = (('pending-droiddeck-layer-machine-on', 'pending-droiddeck-layer-machine-off') if saved_layer
                   else ('pending-droiddeck-machine-gpu.cfg', 'pending-droiddeck-machine-gpu-restore.cfg'))
        receipt_name = 'saved-layer.json' if saved_layer else 'configuration.json'
        pending_name = 'pending-' + receipt_name
        if set(os.listdir(state_directory)) != {'manifest.json', pending_name}:
            raise ValueError('Only an exact pending single-component receipt may be completed')
        candidate_sha = (hashlib.sha256(admitted['layerParser'].rewrite_optional(admitted['propertyDocument'],
            (admitted['manifest']['beforeLayer'],), 'true')).hexdigest() if saved_layer else admitted['manifest']['gpuConfigurationSha256'])
        candidate_fd, candidate_info, candidate_raw = _read_profile_file(copier, stack, directory, on, mode=0o600 if saved_layer else 0o644)
        if hashlib.sha256(candidate_raw).hexdigest() != candidate_sha:
            raise ValueError('Pending receipt candidate does not match independent recipe')
        receipt_fd, receipt_info, encoded = _read_profile_file(copier, stack, state_directory, pending_name)
        value = {'kind': 'protected-machine-gpu-' + role, 'version': 1,
                 'transactionManifestSha256': admitted['transactionManifestSha256'],
                 'candidateDevice': candidate_info.st_dev, 'candidateInode': candidate_info.st_ino, 'candidateSha256': candidate_sha}
        if encoded != (json.dumps(value, sort_keys=True, indent=2) + '\n').encode():
            raise ValueError('Pending receipt must exactly bind the independent candidate inode and recipe')
        digest = hashlib.sha256(encoded).hexdigest()

        def guard(published=False):
            supervised()
            admitted['check']()
            if set(os.listdir(state_directory)) != {'manifest.json', receipt_name if published else pending_name}:
                raise ValueError('Component receipt completion inventory changed')
            try:
                os.stat(off, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('Conflicting retained component candidate must be preserved')
            copier.check_image(directory, on, candidate_fd, candidate_info, private=saved_layer)
            if copier.digest_fd(candidate_fd, candidate_info.st_size) != candidate_sha:
                raise ValueError('Held receipt candidate changed')
        guard()
        os.fsync(candidate_fd)
        os.fsync(directory)
        os.fsync(receipt_fd)
        os.fsync(state_directory)
        guard()
        copier.check_image(state_directory, pending_name, receipt_fd, receipt_info, private=True)
        if copier.digest_fd(receipt_fd, receipt_info.st_size) != digest:
            raise ValueError('Held pending component receipt changed')
        rename_exclusive(state_directory, pending_name, receipt_name)
        try:
            os.fsync(state_directory)
            guard(True)
            now = os.fstat(receipt_fd)
            a, b = copier.identity(receipt_info), copier.identity(now)
            if a[:4] + a[5:] != b[:4] + b[5:]:
                raise ValueError('Completed component receipt generation changed')
            copier.check_image(state_directory, receipt_name, receipt_fd, now, private=True)
            if copier.digest_fd(receipt_fd, receipt_info.st_size) != digest:
                raise ValueError('Completed component receipt bytes changed')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Component receipt completion published; inspect compound ownership before retry') from error
        return {'kind': 'protected-machine-gpu-component-receipt-completion', 'role': role,
                'transactionManifestSha256': admitted['transactionManifestSha256'], 'componentReceiptPublished': True,
                'componentSelected': False, **{k: admitted['context'][k] for k in ('configurationDigest', 'metadataSha256')},
                'accountValidated': offline is None and profiles is None, 'guestPayloadsVerified': offline is None,
                'servicesStoppedVerified': offline is None, 'codecsVerified': verify_codecs is None,
                'recoveryJournalChanged': False, 'configurationChanged': False, 'savedPropertiesChanged': False,
                'allocatorOverrideChanged': False, 'servicesActivated': False, 'propertiesRegenerated': False,
                'androidOperationsExecuted': False, 'independentRecoveryArmed': False, 'installable': False, 'readyForActivation': False}


def _hold_machine_gpu_compound(copier, stack, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, offline=None, profiles=None, verify_codecs=None,
        _transaction_fd=None):
    """Read-only ownership admission across stopped, potentially interrupted component states."""
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux compound ownership admission required')
    if offline is None:
        _assert_machine_guest_stopped()
    context = _machine_offline_context(stack, copier, metadata_root) if offline is None else offline(stack, copier)
    import fcntl
    configuration = Path(copier.canonical(str(configuration)))
    state = Path(copier.canonical(str(state_parent))) / 'machine-gpu-transaction'
    chain = copier.open_chain(stack, state, root_owned=True)
    state_directory = chain[-1][1]
    copier.protected(os.fstat(state_directory), 0o700)
    lock_fd = state_directory if _transaction_fd is None else _transaction_fd
    if type(lock_fd) is not int:
        raise ValueError('Held compound directory descriptor required')
    lock_info, state_info = os.fstat(lock_fd), os.fstat(state_directory)
    copier.protected(lock_info, 0o700)
    if not stat.S_ISDIR(lock_info.st_mode) or (lock_info.st_dev, lock_info.st_ino) != (state_info.st_dev, state_info.st_ino):
        raise ValueError('Borrowed compound lock is not the held transaction directory')
    # Refresh after an owned rename using the SAME open-file-description lock, without an unlock gap.
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    entries = set(os.listdir(state_directory))
    if 'manifest.json' not in entries or not entries <= {'manifest.json', 'configuration.json', 'saved-layer.json', 'mesa-allocator-machine'}:
        raise ValueError('Unknown or partial compound transaction inventory')
    inventories = [(state_directory, entries)]
    absent_names, held = [], []

    def absent(directory, name):
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError('Unexpected compound namespace or ambiguous ownership')

    def require_absent(directory, name):
        absent(directory, name)
        absent_names.append((directory, name))

    def read(directory, name, *, mode=0o600):
        fd, info, encoded = _read_profile_file(copier, stack, directory, name, mode=mode)
        digest = hashlib.sha256(encoded).hexdigest()
        held.append((directory, name, fd, info, digest, mode))
        return info, encoded

    require_absent(chain[-2][1], 'pending-machine-gpu-transaction')
    _, manifest_raw = read(state_directory, 'manifest.json')
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    cfg_chain = copier.open_chain(stack, configuration.parent, root_owned=True)
    property_chain = copier.open_chain(stack, context['property'])
    layout = {
        'configuration': (cfg_chain[-1][1], configuration.name, 'pending-droiddeck-machine-gpu.cfg',
                          'pending-droiddeck-machine-gpu-restore.cfg', 'configuration.json', 'configuration', 0o644),
        'savedLayer': (property_chain[-1][1], 'persistent_properties', 'pending-droiddeck-layer-machine-on',
                       'pending-droiddeck-layer-machine-off', 'saved-layer.json', 'saved-layer', 0o600)}
    receipts, baseline_names = {}, {}
    for role, (directory, current, on, off, receipt_name, kind, mode) in layout.items():
        if receipt_name not in entries:
            require_absent(directory, on)
            require_absent(directory, off)
            baseline_names[role] = current
            continue
        _, raw = read(state_directory, receipt_name)
        value = json.loads(raw)
        if (type(value) is not dict or set(value) != {'kind', 'version', 'transactionManifestSha256',
                'candidateDevice', 'candidateInode', 'candidateSha256'} or value['kind'] != 'protected-machine-gpu-' + kind
                or type(value['version']) is not int or value['version'] != 1
                or value['transactionManifestSha256'] != manifest_sha
                or any(type(value[k]) is not int or not 0 < value[k] < 2**64 for k in ('candidateDevice', 'candidateInode'))
                or not isinstance(value['candidateSha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['candidateSha256'])
                or raw != (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()):
            raise ValueError('Closed independently bound compound ownership receipt required')
        receipts[role] = value
        info = os.stat(current, dir_fd=directory, follow_symlinks=False)
        baseline_names[role] = on if (info.st_dev, info.st_ino) == (value['candidateDevice'], value['candidateInode']) else current

    baseline = _hold_machine_gpu_baseline(copier, stack, configuration.parent / baseline_names['configuration'],
        metadata_root, backup, offline=offline, profiles=profiles, verify_codecs=verify_codecs,
        property_name=baseline_names['savedLayer'])
    manifest = baseline['manifest']
    if manifest_raw != (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode():
        raise ValueError('Compound original state does not match independently reconstructed manifest')
    candidate_hashes = {'configuration': manifest['gpuConfigurationSha256'], 'savedLayer': hashlib.sha256(
        baseline['layerParser'].rewrite_optional(baseline['propertyDocument'], (manifest['beforeLayer'],), 'true')).hexdigest()}
    states = {}
    for role, (directory, current, on, off, receipt_name, kind, mode) in layout.items():
        if role not in receipts:
            states[role] = 'baseline'
            continue
        value = receipts[role]
        if value['candidateSha256'] != candidate_hashes[role]:
            raise ValueError('Compound candidate recipe changed')
        if baseline_names[role] == on:
            candidate_name, states[role] = current, 'selected'
            require_absent(directory, off)
        else:
            present = []
            for name in (on, off):
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    require_absent(directory, name)
                else:
                    present.append(name)
            if len(present) != 1:
                raise ValueError('Missing or ambiguous compound candidate')
            candidate_name = present[0]
            states[role] = 'staged' if candidate_name == on else 'restored'
        info, raw = read(directory, candidate_name, mode=mode)
        if ((info.st_dev, info.st_ino) != (value['candidateDevice'], value['candidateInode'])
                or hashlib.sha256(raw).hexdigest() != candidate_hashes[role]):
            raise ValueError('Foreign compound candidate inode or bytes')

    misc = baseline['miscDirectory']
    allocator_names = ('waydroid_settings', 'pending-droiddeck-allocator-machine-ready', 'pending-droiddeck-allocator-machine-restored')
    if 'mesa-allocator-machine' not in entries:
        for name in allocator_names:
            require_absent(misc, name)
        states['allocator'] = 'baseline'
        allocator_chain = None
    else:
        allocator_chain = copier.open_chain(stack, state / 'mesa-allocator-machine', root_owned=True)
        allocator_directory = allocator_chain[-1][1]
        copier.protected(os.fstat(allocator_directory), 0o700)
        if set(os.listdir(allocator_directory)) != {'receipt.json'}:
            raise ValueError('Unknown compound allocator receipt inventory')
        inventories.append((allocator_directory, {'receipt.json'}))
        _, raw = read(allocator_directory, 'receipt.json')
        value = _allocator_metadata(json.loads(raw), {k: context[k] for k in ('dataDevice', 'dataInode')}, os.fstat(misc), 'machine')
        if raw != (json.dumps(value, indent=2) + '\n').encode():
            raise ValueError('Closed compound allocator receipt encoding required')
        present = []
        for name in allocator_names:
            try:
                os.stat(name, dir_fd=misc, follow_symlinks=False)
            except FileNotFoundError:
                require_absent(misc, name)
            else:
                present.append(name)
        if len(present) != 1:
            raise ValueError('Missing or ambiguous owned compound allocator')
        info, raw = read(misc, present[0])
        if raw != ALLOCATOR_CODE or (info.st_dev, info.st_ino) != (value['fileDevice'], value['fileInode']):
            raise ValueError('Foreign compound allocator inode or bytes')
        states['allocator'] = dict(zip(allocator_names, ('selected', 'staged', 'restored')))[present[0]]

    def check_metadata():
        context['check']()
        baseline['check'](configuration_current=False, property_current=False)
        for current_chain, root_owned in ((chain, True), (cfg_chain, True), (property_chain, False)):
            copier.check_chain(current_chain, root_owned=root_owned)
        copier.protected(os.fstat(state_directory), 0o700)
        if allocator_chain:
            copier.check_chain(allocator_chain, root_owned=True)
            copier.protected(os.fstat(allocator_chain[-1][1]), 0o700)
        for directory, names in inventories:
            if set(os.listdir(directory)) != names:
                raise ValueError('Held compound inventory changed')
        metadata_directories = {directory for directory, _ in inventories}
        for directory, name, fd, info, digest, mode in held:
            if directory in metadata_directories:
                copier.check_image(directory, name, fd, info)
                copier.protected(os.fstat(fd), mode)
                if copier.digest_fd(fd, info.st_size) != digest:
                    raise ValueError('Held compound metadata changed')

    def check():
        check_metadata()
        baseline['check']()
        for directory, name in absent_names:
            absent(directory, name)
        for directory, name, fd, info, digest, mode in held:
            copier.check_image(directory, name, fd, info)
            copier.protected(os.fstat(fd), mode)
            if copier.digest_fd(fd, info.st_size) != digest:
                raise ValueError('Held compound receipt or candidate changed')
    check()
    return {'check': check, 'checkMetadata': check_metadata, 'states': states, 'transactionManifestSha256': manifest_sha,
            'transactionFd': lock_fd, 'recoveryTargets': {role: (directory, current, on, off)
                for role, (directory, current, on, off, _, _, _) in layout.items()} | {
                'allocator': (misc, *allocator_names)},
            **{k: context[k] for k in ('configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode')},
            'accountValidated': offline is None and profiles is None, 'guestPayloadsVerified': offline is None,
            'servicesStoppedVerified': offline is None, 'codecsVerified': verify_codecs is None}


def _machine_gpu_compound_policy(copier):
    with ExitStack() as stack:
        context = _hold_machine_gpu_compound(copier, stack)
        context['check']()
        return {'kind': 'protected-machine-gpu-compound-policy', **{k: context[k] for k in
            ('states', 'transactionManifestSha256', 'configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode',
             'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified')},
            'configurationChanged': False, 'savedPropertiesChanged': False, 'allocatorOverrideChanged': False,
            'servicesActivated': False, 'propertiesRegenerated': False, 'androidOperationsExecuted': False,
            'independentRecoveryArmed': False, 'readyForActivation': False, 'installable': False}


def _machine_recovery_plan(states):
    order = ('allocator', 'savedLayer', 'configuration')
    if (type(states) is not dict or set(states) != set(order) or any(
            type(value) is not str or value not in ('baseline', 'staged', 'selected', 'restored') for value in states.values())):
        raise ValueError('Closed compound recovery states required')
    for component in order:
        if states[component] not in ('baseline', 'restored'):
            operation = 'restore-original' if component != 'allocator' and states[component] == 'selected' else 'retain-candidate'
            return {'component': component, 'operation': operation}
    return None


def _machine_recovery_step(previous, current):
    plan = _machine_recovery_plan(previous)
    _machine_recovery_plan(current)
    if previous == current:
        return False
    changed = {key for key in previous if previous[key] != current[key]}
    if plan is None or changed != {plan['component']}:
        raise ValueError('Observed recovery changes are out of order')
    role = plan['component']
    targets = ('restored',) if role == 'allocator' or previous[role] == 'staged' else ('staged', 'restored')
    if current[role] not in targets:
        raise ValueError('Observed state is not a forward recovery step')
    return True


def _machine_recovery_record(encoded, context):
    if not isinstance(encoded, bytes) or not 0 < len(encoded) <= 16384:
        raise ValueError('Bounded machine recovery record required')
    value = json.loads(encoded)
    bindings = ('transactionManifestSha256', 'configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode')
    if (type(value) is not dict or set(value) != {'kind', 'version', 'sequence', 'stage', 'states', 'nextAction', *bindings}
            or value['kind'] != 'protected-machine-gpu-recovery' or type(value['version']) is not int or value['version'] != 1
            or type(value['sequence']) is not int or not 0 <= value['sequence'] < 2**63
            or any(value[k] != context[k] or type(value[k]) is not type(context[k]) for k in bindings)):
        raise ValueError('Closed machine-bound recovery record required')
    plan = _machine_recovery_plan(value['states'])
    if (value['nextAction'] != plan or value['stage'] != ('recovering' if plan else 'settled')
            or encoded != (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()):
        raise ValueError('Recovery record encoding or derived plan changed')
    return value


def _hold_machine_gpu_recovery(copier, stack, context, state_parent, *, _pending_update=False, _initial=False):
    if any(type(value) is not bool for value in (_pending_update, _initial)) or (_pending_update and _initial):
        raise ValueError('Closed recovery journal inspection mode required')
    path = Path(copier.canonical(str(state_parent)))
    entry = 'pending-machine-gpu-recovery' if _initial else 'machine-gpu-recovery'
    chain = copier.open_chain(stack, path / entry, root_owned=True)
    directory = chain[-1][1]

    def namespace():
        copier.check_chain(chain, root_owned=True)
        copier.protected(os.fstat(directory), 0o700)
        try:
            os.stat('machine-gpu-recovery' if _initial else 'pending-machine-gpu-recovery',
                    dir_fd=chain[-2][1], follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('Conflicting recovery initialization namespace requires inspection')
        expected = {'recovery.json', 'pending-recovery.json'} if _pending_update else {'recovery.json'}
        if set(os.listdir(directory)) != expected:
            raise ValueError('Unexpected recovery journal inventory')

    namespace()
    fd, info, raw = _read_profile_file(copier, stack, directory, 'recovery.json', maximum=16384)
    value = _machine_recovery_record(raw, context)
    if _initial and (value['sequence'] != 0 or value['states'] != context['states']):
        raise ValueError('Pending initialization must exactly match independent current state at sequence zero')
    held = [('recovery.json', fd, info, hashlib.sha256(raw).hexdigest())]
    pending = None
    if _pending_update:
        candidate_fd, candidate_info, candidate_raw = _read_profile_file(copier, stack, directory, 'pending-recovery.json', maximum=16384)
        candidate = _machine_recovery_record(candidate_raw, context)
        if not _machine_recovery_step(value['states'], context['states']):
            raise ValueError('Pending update requires independently observed recovery progress')
        plan = _machine_recovery_plan(context['states'])
        expected = {**value, 'sequence': value['sequence'] + 1, 'states': dict(context['states']),
                    'stage': 'recovering' if plan else 'settled', 'nextAction': plan}
        if candidate != expected:
            raise ValueError('Pending update does not exactly match independently observed next progress')
        held.append(('pending-recovery.json', candidate_fd, candidate_info, hashlib.sha256(candidate_raw).hexdigest()))
        pending = {'fd': candidate_fd, 'info': candidate_info, 'value': candidate}

    def check():
        context['check']()
        namespace()
        for name, descriptor, before, digest in held:
            copier.check_image(directory, name, descriptor, before, private=True)
            if copier.digest_fd(descriptor, before.st_size) != digest:
                raise ValueError('Held recovery record bytes changed')
    check()
    return {'value': value, 'check': check, 'chain': chain, 'directory': directory, 'fd': fd, 'info': info, 'pending': pending}


def _machine_gpu_recovery_status(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, offline=None, profiles=None, verify_codecs=None,
        _transaction_fd=None):
    # A completed file action may precede its journal save; never return the stale action as actionable.
    with ExitStack() as stack:
        context = _hold_machine_gpu_compound(copier, stack, state_parent, configuration, metadata_root, backup,
            offline=offline, profiles=profiles, verify_codecs=verify_codecs, _transaction_fd=_transaction_fd)
        journal = _hold_machine_gpu_recovery(copier, stack, context, state_parent)
        value = journal['value']
        changed = _machine_recovery_step(value['states'], context['states'])
        journal['check']()
        return {'kind': 'protected-machine-gpu-recovery-status', **{k: value[k] for k in
                ('sequence', 'stage', 'states', 'transactionManifestSha256', 'configurationDigest',
                 'metadataSha256', 'dataDevice', 'dataInode')},
                'observedStates': dict(context['states']), 'checkpointRequired': changed,
                'recordedNextAction': value['nextAction'], 'nextAction': None if changed else value['nextAction'],
                **{k: context[k] for k in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified')},
                'recoveryJournalChanged': False, 'configurationChanged': False, 'savedPropertiesChanged': False,
                'allocatorOverrideChanged': False, 'servicesActivated': False, 'propertiesRegenerated': False,
                'androidOperationsExecuted': False, 'independentRecoveryArmed': False, 'installable': False, 'readyForActivation': False}


def _machine_gpu_recovery_checkpoint(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, initialize=False, expected_sequence=None,
        supervised=None, offline=None, profiles=None, verify_codecs=None, _transaction_fd=None):
    # This records observed progress only; it does not execute any planned Android recovery action.
    if (sys.platform != 'linux' or os.geteuid() != 0 or type(initialize) is not bool or not callable(supervised)
            or (initialize and expected_sequence is not None)
            or (not initialize and (type(expected_sequence) is not int or not 0 <= expected_sequence < 2**63))):
        raise ValueError('Supervised root Linux closed recovery checkpoint required')
    supervised()
    with ExitStack() as stack:
        context = _hold_machine_gpu_compound(copier, stack, state_parent, configuration, metadata_root, backup,
            offline=offline, profiles=profiles, verify_codecs=verify_codecs, _transaction_fd=_transaction_fd)
        chain = copier.open_chain(stack, Path(copier.canonical(str(state_parent))), root_owned=True)
        parent = chain[-1][1]
        final, pending = 'machine-gpu-recovery', 'pending-machine-gpu-recovery'

        def absent(name, directory=parent):
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('Existing or partial recovery metadata must be preserved')

        def guard():
            supervised()
            context['check']()
            copier.check_chain(chain, root_owned=True)
            absent(pending)

        def result(value, changed):
            return {'kind': 'protected-machine-gpu-recovery-checkpoint', **{k: value[k] for k in
                    ('sequence', 'stage', 'states', 'nextAction', 'transactionManifestSha256', 'configurationDigest',
                     'metadataSha256', 'dataDevice', 'dataInode')},
                    **{k: context[k] for k in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified')},
                    'recoveryJournalChanged': changed, 'configurationChanged': False, 'savedPropertiesChanged': False,
                    'allocatorOverrideChanged': False, 'servicesActivated': False, 'propertiesRegenerated': False,
                    'androidOperationsExecuted': False, 'independentRecoveryArmed': False, 'installable': False, 'readyForActivation': False}

        old = None
        if initialize:
            absent(final)
            absent(pending)
            sequence = 0
        else:
            guard()
            journal = _hold_machine_gpu_recovery(copier, stack, context, state_parent)
            journal_chain, directory = journal['chain'], journal['directory']
            old_fd, old_info, old = journal['fd'], journal['info'], journal['value']
            if old['sequence'] != expected_sequence:
                raise ValueError('Stale recovery journal writer')
            if not _machine_recovery_step(old['states'], context['states']):
                guard()
                journal['check']()
                return result(old, False)
            sequence = old['sequence'] + 1
        plan = _machine_recovery_plan(context['states'])
        value = {'kind': 'protected-machine-gpu-recovery', 'version': 1, 'sequence': sequence,
                 'stage': 'recovering' if plan else 'settled', 'states': dict(context['states']), 'nextAction': plan,
                 **{k: context[k] for k in ('transactionManifestSha256', 'configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode')}}
        encoded = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
        _machine_recovery_record(encoded, context)
        if initialize:
            supervised()
            context['check']()
            copier.check_chain(chain, root_owned=True)
            absent(final)
            absent(pending)
            os.mkdir(pending, 0o700, dir_fd=parent)
            directory = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, directory)
            os.fchown(directory, 0, 0)
            os.fchmod(directory, 0o700)
        directory_info = os.fstat(directory)
        output_name = 'recovery.json' if initialize else 'pending-recovery.json'
        output = copier.create_private(stack, directory, output_name)
        copier.write_all(output, encoded)
        os.fsync(output)
        output_info = os.fstat(output)
        output_sha = hashlib.sha256(encoded).hexdigest()

        def check_output(entry, leaf, *, renamed=False):
            current = os.stat(entry, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (directory_info.st_dev, directory_info.st_ino):
                raise ValueError('Recovery journal directory changed')
            copier.protected(current, 0o700)
            copier.protected(os.fstat(directory), 0o700)
            now = os.fstat(output)
            a, b = copier.identity(output_info), copier.identity(now)
            if (a[:4] + a[5:] != b[:4] + b[5:] if renamed else a != b):
                raise ValueError('Recovery record generation changed')
            copier.check_image(directory, leaf, output, now, private=True)
            if copier.digest_fd(output, len(encoded)) != output_sha:
                raise ValueError('Recovery record bytes changed')

        supervised()
        context['check']()
        copier.check_chain(chain, root_owned=True)
        if initialize:
            absent(final)
            check_output(pending, output_name)
            if set(os.listdir(directory)) != {'recovery.json'}:
                raise ValueError('Recovery journal staging inventory changed')
            os.fsync(directory)
            rename_exclusive(parent, pending, final)
        else:
            guard()
            copier.check_chain(journal_chain, root_owned=True)
            copier.check_image(directory, 'recovery.json', old_fd, old_info, private=True)
            if set(os.listdir(directory)) != {'recovery.json', output_name}:
                raise ValueError('Recovery update staging inventory changed')
            check_output(final, output_name)
            os.replace(output_name, 'recovery.json', src_dir_fd=directory, dst_dir_fd=directory)
        try:
            os.fsync(directory)
            os.fsync(parent)
            guard()
            check_output(final, 'recovery.json', renamed=not initialize)
            if set(os.listdir(directory)) != {'recovery.json'}:
                raise ValueError('Published recovery inventory changed')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Recovery progress published; inspect durable journal before retry') from error
        return result(value, True)


def _machine_gpu_recovery_finish_initialization(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, expected_sequence=None, supervised=None,
        offline=None, profiles=None, verify_codecs=None):
    # Publish only an existing complete initial record into an absent fixed namespace.
    if (sys.platform != 'linux' or os.geteuid() != 0 or not callable(supervised)
            or type(expected_sequence) is not int or expected_sequence != 0):
        raise ValueError('Supervised root Linux explicit initial sequence required')
    supervised()
    with ExitStack() as stack:
        context = _hold_machine_gpu_compound(copier, stack, state_parent, configuration, metadata_root, backup,
            offline=offline, profiles=profiles, verify_codecs=verify_codecs)
        journal = _hold_machine_gpu_recovery(copier, stack, context, state_parent, _initial=True)
        directory, parent = journal['directory'], journal['chain'][-2][1]
        directory_info = os.fstat(directory)
        os.fsync(journal['fd'])
        os.fsync(directory)
        supervised()
        journal['check']()
        rename_exclusive(parent, 'pending-machine-gpu-recovery', 'machine-gpu-recovery')
        try:
            os.fsync(directory)
            os.fsync(parent)
            supervised()
            context['check']()
            copier.check_chain(journal['chain'][:-1], root_owned=True)
            published = os.stat('machine-gpu-recovery', dir_fd=parent, follow_symlinks=False)
            if (published.st_dev, published.st_ino) != (directory_info.st_dev, directory_info.st_ino):
                raise ValueError('Published initial recovery directory changed')
            copier.protected(published, 0o700)
            copier.protected(os.fstat(directory), 0o700)
            copier.check_image(directory, 'recovery.json', journal['fd'], journal['info'], private=True)
            final = _hold_machine_gpu_recovery(copier, stack, context, state_parent)
            if final['value'] != journal['value']:
                raise ValueError('Published initial recovery record changed')
            final['check']()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Initial recovery record published; inspect durable status before retry') from error
        return {'kind': 'protected-machine-gpu-recovery-initialization-completion', **{k: final['value'][k] for k in
                ('sequence', 'stage', 'states', 'nextAction', 'transactionManifestSha256', 'configurationDigest',
                 'metadataSha256', 'dataDevice', 'dataInode')},
                **{k: context[k] for k in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified')},
                'initialRecordPublished': True, 'recoveryJournalChanged': True, 'configurationChanged': False,
                'savedPropertiesChanged': False, 'allocatorOverrideChanged': False, 'servicesActivated': False,
                'propertiesRegenerated': False, 'androidOperationsExecuted': False, 'independentRecoveryArmed': False,
                'installable': False, 'readyForActivation': False}


def _machine_gpu_recovery_finish_checkpoint(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, expected_sequence=None, supervised=None,
        offline=None, profiles=None, verify_codecs=None):
    # Complete only an exact staged metadata save; never repair or advance Android files here.
    if (sys.platform != 'linux' or os.geteuid() != 0 or not callable(supervised)
            or type(expected_sequence) is not int or not 0 <= expected_sequence < 2**63):
        raise ValueError('Supervised root Linux sequenced checkpoint completion required')
    supervised()
    with ExitStack() as stack:
        args = (copier, state_parent, configuration, metadata_root, backup)
        kwargs = {'offline': offline, 'profiles': profiles, 'verify_codecs': verify_codecs}
        context = _hold_machine_gpu_compound(copier, stack, *args[1:], **kwargs)
        journal = _hold_machine_gpu_recovery(copier, stack, context, state_parent, _pending_update=True)
        if journal['value']['sequence'] != expected_sequence:
            raise ValueError('Stale checkpoint completion sequence')
        directory, pending = journal['directory'], journal['pending']
        os.fsync(pending['fd'])
        os.fsync(directory)
        supervised()
        journal['check']()
        os.replace('pending-recovery.json', 'recovery.json', src_dir_fd=directory, dst_dir_fd=directory)
        try:
            os.fsync(directory)
            os.fsync(journal['chain'][-2][1])
            supervised()
            context['check']()
            copier.check_chain(journal['chain'], root_owned=True)
            copier.protected(os.fstat(directory), 0o700)
            now = os.fstat(pending['fd'])
            a, b = copier.identity(pending['info']), copier.identity(now)
            if a[:4] + a[5:] != b[:4] + b[5:]:
                raise ValueError('Completed checkpoint inode generation changed')
            copier.check_image(directory, 'recovery.json', pending['fd'], now, private=True)
            final = _hold_machine_gpu_recovery(copier, stack, context, state_parent)
            if final['value'] != pending['value']:
                raise ValueError('Completed checkpoint does not match verified pending record')
            final['check']()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Checkpoint completion published; inspect durable status before retry') from error
        return {'kind': 'protected-machine-gpu-recovery-checkpoint-completion', **{k: final['value'][k] for k in
                ('sequence', 'stage', 'states', 'nextAction', 'transactionManifestSha256', 'configurationDigest',
                 'metadataSha256', 'dataDevice', 'dataInode')},
                **{k: context[k] for k in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified')},
                'pendingRecordFinalized': True, 'recoveryJournalChanged': True, 'configurationChanged': False,
                'savedPropertiesChanged': False, 'allocatorOverrideChanged': False, 'servicesActivated': False,
                'propertiesRegenerated': False, 'androidOperationsExecuted': False, 'independentRecoveryArmed': False,
                'installable': False, 'readyForActivation': False}


def _machine_gpu_recovery_step(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, expected_sequence=None, supervised=None,
        offline=None, profiles=None, verify_codecs=None, _transaction_fd=None):
    # One owned file action only. The caller must durably checkpoint before another action.
    if (sys.platform != 'linux' or os.geteuid() != 0 or not callable(supervised)
            or type(expected_sequence) is not int or not 0 <= expected_sequence < 2**63):
        raise ValueError('Supervised root Linux sequenced recovery step required')
    supervised()
    with ExitStack() as stack:
        kwargs = {'offline': offline, 'profiles': profiles, 'verify_codecs': verify_codecs}
        context = _hold_machine_gpu_compound(copier, stack, state_parent, configuration, metadata_root, backup,
            _transaction_fd=_transaction_fd, **kwargs)
        journal = _hold_machine_gpu_recovery(copier, stack, context, state_parent)
        value = journal['value']
        if value['sequence'] != expected_sequence:
            raise ValueError('Stale recovery step sequence')
        if _machine_recovery_step(value['states'], context['states']):
            raise ValueError('Observed recovery progress must be checkpointed before another step')
        plan = _machine_recovery_plan(context['states'])
        before = dict(context['states'])

        def result(observed):
            role = plan['component'] if plan else None
            return {'kind': 'protected-machine-gpu-recovery-step', 'sequence': value['sequence'],
                'states': before, 'observedStates': dict(observed), 'actionPerformed': plan,
                'checkpointRequired': plan is not None, 'nextAction': None,
                **{k: context[k] for k in ('transactionManifestSha256', 'configurationDigest', 'metadataSha256',
                    'dataDevice', 'dataInode', 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified')},
                'recoveryJournalChanged': False, 'configurationChanged': role == 'configuration',
                'savedPropertiesChanged': role == 'savedLayer', 'allocatorOverrideChanged': role == 'allocator',
                'servicesActivated': False, 'propertiesRegenerated': False, 'androidOperationsExecuted': False,
                'independentRecoveryArmed': False, 'installable': False, 'readyForActivation': False}

        supervised()
        journal['check']()
        if plan is None:
            return result(before)
        role = plan['component']
        directory, current, on, off = context['recoveryTargets'][role]
        target = 'staged' if plan['operation'] == 'restore-original' else 'restored'
        if plan['operation'] == 'restore-original':
            _exchange_machine_gpu_names(directory, current, on)
        else:
            source = current if role == 'allocator' and before[role] == 'selected' else on
            rename_exclusive(directory, source, off)
        try:
            os.fsync(directory)
            supervised()
            refreshed = _hold_machine_gpu_compound(copier, stack, state_parent, configuration, metadata_root, backup,
                _transaction_fd=context['transactionFd'], **kwargs)
            if (refreshed['states'] != {**before, role: target} or any(refreshed[k] != context[k] for k in
                    ('transactionManifestSha256', 'configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode'))):
                raise ValueError('Recovery step changed more than its exact owned transition')
            context['checkMetadata']()
            latest = _hold_machine_gpu_recovery(copier, stack, refreshed, state_parent)
            if latest['value'] != value:
                raise ValueError('Recovery journal changed during file action')
            copier.check_image(journal['directory'], 'recovery.json', journal['fd'], journal['info'], private=True)
            supervised()
            context['checkMetadata']()
            latest['check']()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Owned recovery action completed; inspect status and checkpoint before another step') from error
        return result(refreshed['states'])


def _machine_gpu_recovery_run(copier, state_parent=Path('/var/lib/droiddeck'),
        configuration=Path('/var/lib/waydroid/waydroid.cfg'), metadata_root=Path('/etc/droiddeck'),
        backup=Path('/var/lib/droiddeck/waydroid-profile'), *, expected_sequence=None, supervised=None,
        offline=None, profiles=None, verify_codecs=None):
    # Bounded by the five possible owned file actions, not a healthy-runtime deadline.
    if (sys.platform != 'linux' or os.geteuid() != 0 or not callable(supervised)
            or type(expected_sequence) is not int or not 0 <= expected_sequence < 2**63):
        raise ValueError('Supervised root Linux sequenced recovery pass required')
    supervised()
    with ExitStack() as stack:
        args = (copier, state_parent, configuration, metadata_root, backup)
        overrides = {'offline': offline, 'profiles': profiles, 'verify_codecs': verify_codecs}
        context = _hold_machine_gpu_compound(copier, stack, *args[1:], **overrides)
        journal = _hold_machine_gpu_recovery(copier, stack, context, state_parent)
        value = journal['value']
        if value['sequence'] != expected_sequence:
            raise ValueError('Stale recovery pass sequence')
        pending = _machine_recovery_step(value['states'], context['states'])
        needed = int(pending) + sum(0 if state in ('baseline', 'restored') else
            2 if role != 'allocator' and state == 'selected' else 1 for role, state in context['states'].items())
        if expected_sequence + needed >= 2**63:
            raise ValueError('Insufficient durable sequence capacity for recovery')
        kwargs = {**overrides, '_transaction_fd': context['transactionFd']}
        bindings = ('transactionManifestSha256', 'configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode')
        sequence, recorded = expected_sequence, dict(value['states'])
        actions, checkpoints, changed_roles = 0, 0, set()
        may_have_changed = False

        def guard():
            supervised()
            context['checkMetadata']()
            copier.check_chain(journal['chain'], root_owned=True)
            copier.protected(os.fstat(journal['directory']), 0o700)

        try:
            for _ in range(6):
                guard()
                status = _machine_gpu_recovery_status(*args, **kwargs)
                if (status['sequence'] != sequence or status['states'] != recorded
                        or any(status[k] != context[k] for k in bindings)):
                    raise ValueError('Recovery pass journal generation changed')
                if status['checkpointRequired']:
                    may_have_changed = True
                    saved = _machine_gpu_recovery_checkpoint(*args, expected_sequence=sequence, supervised=guard, **kwargs)
                    if (saved['sequence'] != sequence + 1 or saved['states'] != status['observedStates']
                            or not saved['recoveryJournalChanged']):
                        raise ValueError('Recovery checkpoint did not record the observed single step')
                    sequence, recorded = saved['sequence'], dict(saved['states'])
                    checkpoints += 1
                    status = _machine_gpu_recovery_status(*args, **kwargs)
                    if (status['checkpointRequired'] or status['sequence'] != sequence or status['states'] != recorded
                            or any(status[k] != context[k] for k in bindings)):
                        raise ValueError('Recovery saved progress changed before the next action')
                if status['nextAction'] is None:
                    if status['stage'] != 'settled':
                        raise ValueError('Recovery pass did not settle owned data')
                    guard()
                    final = _machine_gpu_recovery_status(*args, **kwargs)
                    if (final['checkpointRequired'] or final['sequence'] != sequence or final['states'] != recorded
                            or final['stage'] != 'settled' or final['nextAction'] is not None
                            or any(final[k] != context[k] for k in bindings)):
                        raise ValueError('Recovery terminal observation changed')
                    return {'kind': 'protected-machine-gpu-recovery-pass', 'sequence': sequence,
                        'stage': 'settled', 'states': dict(status['states']), 'nextAction': None,
                        'actionsPerformed': actions, 'progressCheckpoints': checkpoints, 'ownedDataRestored': True,
                        **{k: context[k] for k in (*bindings, 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified')},
                        'recoveryJournalChanged': checkpoints > 0, 'configurationChanged': 'configuration' in changed_roles,
                        'savedPropertiesChanged': 'savedLayer' in changed_roles, 'allocatorOverrideChanged': 'allocator' in changed_roles,
                        'servicesActivated': False, 'propertiesRegenerated': False, 'androidOperationsExecuted': False,
                        'independentRecoveryArmed': False, 'installable': False, 'readyForActivation': False}
                if actions >= 5:
                    raise ValueError('Recovery pass exceeded the closed action bound')
                may_have_changed = True
                result = _machine_gpu_recovery_step(*args, expected_sequence=sequence, supervised=guard, **kwargs)
                if not result['checkpointRequired'] or result['actionPerformed'] != status['nextAction']:
                    raise ValueError('Recovery executor did not perform the exact planned step')
                changed_roles.add(result['actionPerformed']['component'])
                actions += 1
            raise ValueError('Recovery pass failed to converge within the closed action bound')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            if may_have_changed:
                raise PromotionUncertain('Recovery pass may have advanced; inspect durable status before resuming') from error
            raise


def _guest_context(stack, copier):
    cfg_chain = copier.open_chain(stack, Path('/etc/droiddeck'), root_owned=True)
    fd, before, data = _read_profile_file(copier, stack, cfg_chain[-1][1], 'runtime.json', maximum=16384, mode=0o644)
    if hashlib.sha256(data).hexdigest() != CONFIGURATION_SHA256:
        raise ValueError('Installed guest metadata changed')
    config = _validate_configuration(copier, data)['config']
    path = Path(config['home']) / '.local/share/waydroid/data'
    identity = _guest_process_identity()
    data_chain = copier.open_chain(stack, path)
    tmp_chain = copier.open_chain(stack, path / 'local/tmp')
    data_info, tmp_info = os.fstat(data_chain[-1][1]), os.fstat(tmp_chain[-1][1])
    if ((data_info.st_uid, data_info.st_gid, stat.S_IMODE(data_info.st_mode)) != (config['uid'], config['gid'], 0o771)
            or (tmp_info.st_uid, tmp_info.st_gid, stat.S_IMODE(tmp_info.st_mode)) != (2000, 2000, 0o771)):
        raise ValueError('Unexpected fresh guest-data ownership or mode')
    def check():
        copier.check_chain(cfg_chain, root_owned=True)
        copier.check_image(cfg_chain[-1][1], 'runtime.json', fd, before)
        if _guest_process_identity() != identity:
            raise ValueError('Container changed during guest installation')
        for chain, relative, info in ((data_chain, 'data', data_info), (tmp_chain, 'data/local/tmp', tmp_info)):
            copier.check_chain(chain)
            current = os.fstat(chain[-1][1])
            mounted = os.stat('/proc/' + str(identity[0]) + '/root/' + relative)
            if (current.st_dev, current.st_ino, current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode)) != (
                    info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) or (
                    mounted.st_dev, mounted.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('Actual Android data mount changed')
        r = subprocess.run(['/usr/bin/systemctl', 'show', 'tesla-waydroid-runtime.service', '--property=ActiveState', '--value'],
                           capture_output=True, timeout=5, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        if r.returncode != 0 or r.stdout.strip() != b'inactive':
            raise ValueError('Preview runtime must remain inactive during guest installation')
    check()
    return {'tmp_chain': tmp_chain, 'check': check, 'pid': identity[0], 'birth': identity[1],
            'dataDevice': data_info.st_dev, 'dataInode': data_info.st_ino}


def _guest_sources(stack, copier):
    records = {}
    for name, (relative, mode) in ARTIFACT_LAYOUT.items():
        if not relative.startswith('guest/'):
            continue
        filename = Path('/opt/droiddeck') / relative
        records[relative.removeprefix('guest/')] = (filename, copier.ARTIFACT_PINS[name], mode)
    records['logical-screencap'] = (Path('/opt/droiddeck/runtime/waydroid-display/logical-screencap.sh'), GUEST_LAUNCHER_PIN, 0o700)
    sources = {}
    for name, (filename, pin, mode) in records.items():
        chain = copier.open_chain(stack, filename.parent, root_owned=True)
        fd = os.open(filename.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=chain[-1][1])
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        copier.regular(info, pin[0])
        copier.protected(info, 0o644 if name == 'logical-screencap' else mode)
        if copier.digest_fd(fd, pin[0]) != pin[1]:
            raise ValueError('Protected guest artifact pin changed')
        copier.check_image(chain[-1][1], filename.name, fd, info)
        sources[name] = (chain, filename.name, fd, info, pin, mode)
    return sources


def _install_guest_payloads(copier, *, context=None, sources=None, machine=False):
    if sys.platform != 'linux' or os.geteuid() != 0 or type(machine) is not bool:
        raise ValueError('Root Linux guest installation required')
    with ExitStack() as stack:
        admitted = (_guest_context if context is None else context)(stack, copier)
        if machine and (any(not isinstance(admitted.get(k), str) or not re.fullmatch('[a-f0-9]{64}', admitted[k])
                            for k in ('configurationDigest', 'metadataSha256'))
                        or any(type(admitted.get(k)) is not bool for k in ('accountValidated', 'guestMountMatched'))):
            raise ValueError('Held machine guest policy required')
        selected = (_guest_sources if sources is None else sources)(stack, copier)
        expected_files = {'bin/tesla-android-x86-touch', 'bin/tesla-android-x86-api',
                          'bin/tesla-android-x86-display-latest', 'logical-capture.jar',
                          'location-bridge.jar', 'scrcpy-server-v4.0.jar', 'logical-screencap'}
        if set(selected) != expected_files:
            raise ValueError('Unexpected guest payload inventory')
        tmp_chain = admitted['tmp_chain']
        parent = tmp_chain[-1][1]
        target = MACHINE_GUEST_TARGET if machine else GUEST_TARGET
        pending = 'pending-droiddeck-machine-guest' if machine else 'pending-droiddeck-guest'
        def check_base():
            admitted['check']()
            copier.check_chain(tmp_chain)
            for name, (chain, leaf, fd, info, pin, mode) in selected.items():
                copier.check_chain(chain, root_owned=True)
                copier.check_image(chain[-1][1], leaf, fd, info)
                copier.protected(info)
                if copier.digest_fd(fd, pin[0]) != pin[1]:
                    raise ValueError('Guest source generation changed')
        check_base()
        for name in (target, pending):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ValueError('Existing guest payload or partial stage; inspect before replay')
        os.mkdir(pending, 0o700, dir_fd=parent)
        output = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, output)
        os.fchown(output, 0, 0)
        os.fchmod(output, 0o700)
        output_info = os.fstat(output)
        directories = {'': output}
        for name in ('bin', 'lib64'):
            os.mkdir(name, 0o700, dir_fd=output)
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=output)
            stack.callback(os.close, fd)
            os.fchown(fd, 0, 0)
            os.fchmod(fd, 0o700)
            directories[name] = fd
        records = [{'path': n, 'size': value[4][0], 'sha256': value[4][1], 'mode': value[5]} for n, value in sorted(selected.items())]
        receipt = {'kind': 'protected-guest-payloads', 'version': 1, 'installable': False, 'readyForActivation': False,
                   'pid': admitted['pid'], 'birth': admitted['birth'], 'dataDevice': admitted['dataDevice'],
                   'dataInode': admitted['dataInode'], 'files': records, 'helperProcessesStarted': False,
                   'accountsOrDrmChanged': False, 'browserPngFallback': False}
        if machine:
            receipt.update(version=2, **{k: admitted[k] for k in ('configurationDigest', 'metadataSha256',
                           'accountValidated', 'guestMountMatched')}, servicesActivated=False, androidOperationsExecuted=False)
        outputs = {}
        for name, (_, _, source_fd, _, pin, mode) in selected.items():
            relative, _, leaf = name.rpartition('/')
            fd, _ = copier.copy_image(stack, source_fd, directories[relative], leaf, pin)
            os.fchmod(fd, mode)
            os.fsync(fd)
            outputs[name] = (fd, os.fstat(fd), pin, mode)
        encoded = (json.dumps(receipt, indent=2) + '\n').encode()
        fd = copier.create_private(stack, output, 'droiddeck-guest-receipt.json')
        copier.write_all(fd, encoded)
        os.fsync(fd)
        outputs['droiddeck-guest-receipt.json'] = (fd, os.fstat(fd), (len(encoded), hashlib.sha256(encoded).hexdigest()), 0o600)
        expected_entries = {'': {'bin', 'lib64', 'droiddeck-guest-receipt.json'} | {n for n in expected_files if '/' not in n},
                            'bin': {n.split('/')[1] for n in expected_files if n.startswith('bin/')}, 'lib64': set()}
        def check_output(leaf):
            current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (output_info.st_dev, output_info.st_ino):
                raise ValueError('Guest output directory changed')
            copier.protected(current, 0o700)
            for name, directory in directories.items():
                info = os.fstat(directory)
                copier.protected(info, 0o700)
                if name:
                    linked = os.stat(name, dir_fd=output, follow_symlinks=False)
                    if not stat.S_ISDIR(linked.st_mode) or (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError('Guest child directory changed')
                if set(os.listdir(directory)) != expected_entries[name]:
                    raise ValueError('Unexpected guest output entries')
            for name, (fd, info, pin, mode) in outputs.items():
                relative, _, part = name.rpartition('/')
                copier.check_image(directories[relative], part, fd, info)
                copier.protected(os.fstat(fd), mode)
                if copier.digest_fd(fd, pin[0]) != pin[1]:
                    raise ValueError('Guest output bytes changed')
        check_base()
        check_output(pending)
        for directory in reversed(list(directories.values())):
            os.fsync(directory)
        check_base()
        check_output(pending)
        rename_exclusive(parent, pending, target)
        try:
            os.fsync(parent)
            check_base()
            check_output(target)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('Guest payload published; inspect mounted state before retry or activation') from error
        return receipt


def _pid_recipe_policy(generation):
    if not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{64}', generation):
        raise ValueError('Invalid machine PID generation')
    # Rebuild only the generation-bearing dropin; accepted executable bytes stay pinned.
    unit = ('[Service]\nEnvironment=DROIDDECK_CONFIGURATION_SHA256=' + generation + '\n'
            'ExecStartPre=/usr/bin/node /opt/droiddeck/pid-compat/portable/pid-startup.js\n').encode()
    pins = {**PID_SCRIPT_PINS, 'systemd/40-droiddeck-pid-compat.conf': (len(unit), hashlib.sha256(unit).hexdigest())}
    manifest = {'version': 1, 'product': 'DroidDeck', 'kind': 'pid-startup-component', 'installable': False,
                'configurationDigest': generation,
                'files': [{'path': name, 'size': pins[name][0], 'sha256': pins[name][1]} for name in sorted(pins)]}
    return hashlib.sha256((json.dumps(manifest, indent=2) + '\n').encode()).hexdigest()


def _headless_recipe_policy(generation, profile):
    if any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value) for value in (generation, profile)):
        raise ValueError('Invalid headless configuration generation or profile')
    policy = (json.dumps({'version': 1, 'configurationDigest': generation,
                         'waydroidConfigurationSha256': profile}, indent=2) + '\n').encode()
    unit = (b'[Service]\nType=notify\nNotifyAccess=main\nExecStart=\n'
            b'ExecStart=/usr/bin/python3 -I -B /opt/droiddeck/headless-runtime/headless-runtime.py\nTimeoutStartSec=60s\n')
    pins = {**HEADLESS_SCRIPT_PINS, 'policy.json': (len(policy), hashlib.sha256(policy).hexdigest()),
            'systemd/60-droiddeck-headless.conf': (len(unit), hashlib.sha256(unit).hexdigest())}
    manifest = {'version': 1, 'product': 'DroidDeck', 'kind': 'headless-session-component',
                'configurationDigest': generation, 'waydroidConfigurationSha256': profile,
                'installable': False, 'readyForActivation': False, 'servicesActivated': False,
                'androidOperationsExecuted': False, 'accountValidated': False,
                'files': [{'path': name, 'size': pins[name][0], 'sha256': pins[name][1]} for name in sorted(pins)]}
    return hashlib.sha256((json.dumps(manifest, indent=2) + '\n').encode()).hexdigest()


def _owned_headless_recipe(generation, original, software, gpu):
    if (any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
            for value in (generation, original, software, gpu)) or len({original, software, gpu}) != 3):
        raise ValueError('Distinct owned headless profiles and generation required')
    profiles = {'software': software, 'gpu': gpu}
    policy = (json.dumps({'version': 2, 'configurationDigest': generation,
                         'originalConfigurationSha256': original, 'profiles': profiles}, indent=2) + '\n').encode()
    unit = (b'[Service]\nType=notify\nNotifyAccess=main\nExecStart=\n'
            b'ExecStart=/usr/bin/python3 -I -B /opt/droiddeck/headless-owned-runtime/headless-owned-runtime.py\nTimeoutStartSec=60s\n')
    pins = {**OWNED_HEADLESS_SCRIPT_PINS, 'policy.json': (len(policy), hashlib.sha256(policy).hexdigest()),
            'systemd/60-droiddeck-headless.conf': (len(unit), hashlib.sha256(unit).hexdigest())}
    return {'version': 1, 'product': 'DroidDeck', 'kind': 'headless-owned-session-component',
            'configurationDigest': generation, 'originalConfigurationSha256': original, 'profiles': profiles,
            'installable': False, 'readyForActivation': False, 'servicesActivated': False,
            'androidOperationsExecuted': False, 'accountValidated': False,
            'files': [{'path': name, 'size': pins[name][0], 'sha256': pins[name][1]} for name in sorted(pins)]}


def _owned_headless_recipe_policy(generation, original, software, gpu):
    return hashlib.sha256((json.dumps(_owned_headless_recipe(generation, original, software, gpu), indent=2) + '\n').encode()).hexdigest()


def _hold_machine_owned_headless_policy(copier, stack, metadata_root, backup, *, validate=None, inactive=None, verify_codecs=None):
    policy, held_guard = _hold_machine_gpu_policy(copier, stack, metadata_root, backup, validate=validate, inactive=inactive)
    policy = {**policy, 'kind': 'protected-machine-owned-headless-policy',
              'ownedHeadlessManifestSha256': _owned_headless_recipe_policy(policy['configurationDigest'],
                  policy['originalConfigurationSha256'], policy['softwareConfigurationSha256'], policy['gpuConfigurationSha256']),
              'ownedHeadlessPolicyRegenerated': True, 'codecsVerified': verify_codecs is None, 'independentRecoveryArmed': False}

    def guard():
        held_guard()
        (_verify_profile_codecs if verify_codecs is None else verify_codecs)(copier)
        held_guard()
    guard()
    return policy, guard


def _machine_owned_headless_policy(copier, metadata_root=Path('/etc/droiddeck'),
                                   backup=Path('/var/lib/droiddeck/waydroid-profile'), *, validate=None, verify_codecs=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_owned_headless_policy(copier, stack, metadata_root, backup,
            validate=validate, inactive=lambda: None, verify_codecs=verify_codecs)
        guard()
        return policy


def _machine_owned_headless_receipt(receipt, policy):
    return {**receipt, **{key: policy[key] for key in ('configurationDigest', 'originalConfigurationSha256',
                'softwareConfigurationSha256', 'gpuConfigurationSha256', 'ownedHeadlessManifestSha256',
                'accountValidated', 'codecsVerified')},
            'ownedHeadlessPolicyRegenerated': True, 'protectedMetadataMatched': True, 'independentRecoveryArmed': False,
            'servicesActivated': False, 'readyForActivation': False, 'installable': False, 'androidOperationsExecuted': False}


def _install_machine_owned_headless(copier, source, root, metadata_root, backup, *, validate=None, inactive=None, verify_codecs=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_owned_headless_policy(copier, stack, metadata_root, backup,
            validate=validate, inactive=inactive, verify_codecs=verify_codecs)
        receipt = _install_component(copier, source, root, manifest_pin=policy['ownedHeadlessManifestSha256'],
            base_pin=ARTIFACT_RECEIPT_SHA256, owned_headless_policy=policy, guard=guard)
        receipt.pop('pinnedBundleMatched', None)
        return _machine_owned_headless_receipt(receipt, policy)


def _register_machine_owned_headless(copier, source, parent, metadata_root, backup, *, validate=None, inactive=None, verify_codecs=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_owned_headless_policy(copier, stack, metadata_root, backup,
            validate=validate, inactive=inactive, verify_codecs=verify_codecs)
        receipt = _register_pid_compat(copier, source, parent, manifest_pin=policy['ownedHeadlessManifestSha256'],
            configuration_digest=policy['configurationDigest'], owned_headless_policy=policy, guard=guard)
        return _machine_owned_headless_receipt(receipt, policy)


def _hold_machine_headless_policy(copier, stack, metadata_root, profile_root, *, validate=None, inactive=None):
    policy, check_metadata = _hold_machine_pid_policy(copier, stack, metadata_root, validate=validate, inactive=inactive)
    root = Path(copier.canonical(str(profile_root)))
    chain = copier.open_chain(stack, root, root_owned=True)
    directory = chain[-1][1]
    fd, info, encoded = _read_profile_file(copier, stack, directory, 'waydroid.cfg', mode=0o644)
    profile = hashlib.sha256(encoded).hexdigest()
    policy = {**policy, 'kind': 'protected-machine-headless-policy', 'waydroidConfigurationSha256': profile,
              'headlessManifestSha256': _headless_recipe_policy(policy['configurationDigest'], profile)}

    def guard():
        check_metadata()
        copier.check_chain(chain, root_owned=True)
        copier.check_image(directory, 'waydroid.cfg', fd, info)
        if copier.digest_fd(fd, info.st_size) != profile:
            raise ValueError('Headless Waydroid profile changed')
    guard()
    return policy, guard


def _machine_headless_policy(copier, metadata_root=Path('/etc/droiddeck'), profile_root=Path('/var/lib/waydroid')):
    with ExitStack() as stack:
        # Read-only admission is useful while the existing graph runs; publication is not.
        policy, guard = _hold_machine_headless_policy(copier, stack, metadata_root, profile_root, inactive=lambda: None)
        guard()
        return policy


def _machine_headless_receipt(receipt, policy):
    return {**receipt, 'headlessPolicyRegenerated': True, 'protectedMetadataMatched': True,
            'headlessManifestSha256': policy['headlessManifestSha256'], 'configurationDigest': policy['configurationDigest'],
            'waydroidConfigurationSha256': policy['waydroidConfigurationSha256'], 'accountValidated': policy['accountValidated'],
            'servicesActivated': False, 'readyForActivation': False, 'installable': False, 'androidOperationsExecuted': False}


def _install_machine_headless(copier, source, root, metadata_root, profile_root, *, validate=None, inactive=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_headless_policy(copier, stack, metadata_root, profile_root, validate=validate, inactive=inactive)
        receipt = _install_component(copier, source, root, manifest_pin=policy['headlessManifestSha256'],
                                     base_pin=ARTIFACT_RECEIPT_SHA256, headless_policy=policy, guard=guard)
        receipt.pop('pinnedBundleMatched', None)
        return _machine_headless_receipt(receipt, policy)


def _register_machine_headless(copier, source, parent, metadata_root, profile_root, *, validate=None, inactive=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_headless_policy(copier, stack, metadata_root, profile_root, validate=validate, inactive=inactive)
        receipt = _register_pid_compat(copier, source, parent, manifest_pin=policy['headlessManifestSha256'],
                                       configuration_digest=policy['configurationDigest'], headless_policy=policy, guard=guard)
        return _machine_headless_receipt(receipt, policy)


def _machine_pid_policy(copier, root=Path('/etc/droiddeck'), *, validate=None):
    policy = _machine_service_policy(copier, root, validate=validate)
    return {**policy, 'kind': 'protected-machine-pid-policy',
            'pidManifestSha256': _pid_recipe_policy(policy['configurationDigest'])}


def _hold_machine_pid_policy(copier, stack, root, *, validate=None, inactive=None):
    root = Path(copier.canonical(str(root)))
    chain = copier.open_chain(stack, root, root_owned=True)
    directory = chain[-1][1]
    fd, info, encoded = _read_profile_file(copier, stack, directory, 'runtime.json', maximum=16384, mode=0o644)
    policy = _machine_pid_policy(copier, root, validate=validate)
    if hashlib.sha256(encoded).hexdigest() != policy['metadataSha256']:
        raise ValueError('Machine PID metadata changed during admission')

    def guard():
        (_assert_codec_stopped if inactive is None else inactive)()
        copier.check_chain(chain, root_owned=True)
        copier.check_image(directory, 'runtime.json', fd, info)
        if copier.digest_fd(fd, info.st_size) != policy['metadataSha256']:
            raise ValueError('Machine PID metadata changed')
    guard()
    return policy, guard


def _machine_pid_receipt(receipt, policy):
    return {**receipt, 'pidPolicyRegenerated': True, 'protectedMetadataMatched': True,
            'pidManifestSha256': policy['pidManifestSha256'], 'configurationDigest': policy['configurationDigest'],
            'accountValidated': policy['accountValidated'], 'servicesActivated': False,
            'readyForActivation': False, 'installable': False, 'androidOperationsExecuted': False}


def _install_machine_pid(copier, source, root, metadata_root, *, validate=None, inactive=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_pid_policy(copier, stack, metadata_root, validate=validate, inactive=inactive)
        receipt = _install_component(copier, source, root, manifest_pin=policy['pidManifestSha256'],
                                     base_pin=ARTIFACT_RECEIPT_SHA256, pid_compat=True,
                                     pid_configuration_digest=policy['configurationDigest'], guard=guard)
        receipt.pop('pinnedBundleMatched', None)
        return _machine_pid_receipt(receipt, policy)


def _register_machine_pid(copier, source, parent, metadata_root, *, validate=None, inactive=None):
    with ExitStack() as stack:
        policy, guard = _hold_machine_pid_policy(copier, stack, metadata_root, validate=validate, inactive=inactive)
        receipt = _register_pid_compat(copier, source, parent, manifest_pin=policy['pidManifestSha256'],
                                       configuration_digest=policy['configurationDigest'], guard=guard)
        return _machine_pid_receipt(receipt, policy)


def _assert_pid_metadata(copier):
    with ExitStack() as stack:
        chain = copier.open_chain(stack, Path('/etc/droiddeck'), root_owned=True)
        fd, before, data = _read_profile_file(copier, stack, chain[-1][1], 'runtime.json', maximum=16384, mode=0o644)
        if hashlib.sha256(data).hexdigest() != CONFIGURATION_SHA256 or _validate_configuration(copier, data)['configurationDigest'] != PID_CONFIGURATION_DIGEST:
            raise ValueError('PID startup metadata changed')
        copier.check_image(chain[-1][1], 'runtime.json', fd, before)


def _register_pid_compat(copier, source, parent, *, manifest_pin=PID_MANIFEST_SHA256, guard=None,
                         configuration_digest=PID_CONFIGURATION_DIGEST, headless_policy=None, owned_headless_policy=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux PID registration required')
    source, parent = Path(copier.canonical(str(source))), Path(copier.canonical(str(parent)))
    headless = headless_policy is not None or owned_headless_policy is not None
    if headless and guard is None:
        raise ValueError('Headless registration requires held machine policy')
    guard = (lambda: (_assert_codec_stopped(), _assert_pid_metadata(copier))) if guard is None else guard
    guard()
    with ExitStack() as stack:
        chain = copier.open_chain(stack, source, root_owned=True)
        parent_chain = copier.open_chain(stack, parent, root_owned=True)
        src, destination = chain[-1][1], parent_chain[-1][1]
        marker_fd, marker_info, encoded = _read_profile_file(copier, stack, src, 'bundle-manifest.json', mode=0o644)
        if hashlib.sha256(encoded).hexdigest() != manifest_pin:
            raise ValueError('PID component manifest changed')
        records = component_records(encoded, pid_compat=not headless, configuration_digest=configuration_digest,
                                    headless_policy=headless_policy, owned_headless_policy=owned_headless_policy)
        held = {}
        for name, pin in records.items():
            p, _, leaf = name.rpartition('/')
            child = copier.open_chain(stack, source / p, root_owned=True)
            fd, info, data = _read_profile_file(copier, stack, child[-1][1], leaf, mode=0o644)
            if info.st_size != pin[0] or hashlib.sha256(data).hexdigest() != pin[1]:
                raise ValueError('PID startup component bytes changed')
            held[name] = (child, leaf, fd, info)
        target = 'tesla-waydroid-headless-session.service.d' if headless else 'waydroid-container.service.d'
        pending = 'pending-droiddeck-owned-headless-dropin' if owned_headless_policy is not None else 'pending-droiddeck-headless-dropin' if headless else 'pending-droiddeck-pid-dropin'
        for name in (target, pending):
            try:
                os.stat(name, dir_fd=destination, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ValueError('Existing container dropins must not be overwritten')
        os.mkdir(pending, 0o755, dir_fd=destination)
        out = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination)
        stack.callback(os.close, out)
        os.fchmod(out, 0o755)
        out_info = os.fstat(out)
        name = 'systemd/60-droiddeck-headless.conf' if headless else 'systemd/40-droiddeck-pid-compat.conf'
        leaf = name.split('/')[-1]
        output_fd, _ = copier.copy_image(stack, held[name][2], out, leaf, records[name])
        os.fchmod(output_fd, 0o644)
        os.fsync(output_fd)
        output_info = os.fstat(output_fd)
        def check(stage):
            guard()
            copier.check_chain(chain, root_owned=True)
            copier.check_chain(parent_chain, root_owned=True)
            copier.check_image(src, 'bundle-manifest.json', marker_fd, marker_info)
            for child, part, fd, info in held.values():
                copier.check_chain(child, root_owned=True)
                copier.check_image(child[-1][1], part, fd, info)
            now = os.stat(stage, dir_fd=destination, follow_symlinks=False)
            if not stat.S_ISDIR(now.st_mode) or (now.st_dev, now.st_ino) != (out_info.st_dev, out_info.st_ino) or os.listdir(out) != [leaf]:
                raise ValueError('PID dropin directory changed')
            copier.protected(now, 0o755)
            copier.check_image(out, leaf, output_fd, output_info)
            copier.protected(os.fstat(output_fd), 0o644)
            if copier.digest_fd(output_fd, records[name][0]) != records[name][1]:
                raise ValueError('PID dropin output changed')
        check(pending)
        os.fsync(out)
        check(pending)
        rename_exclusive(destination, pending, target)
        try:
            os.fsync(destination)
            check(target)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise PromotionUncertain('PID dropin published; inspect before service start or retry') from error
        return {'kind': 'protected-owned-headless-registration' if owned_headless_policy is not None else 'protected-headless-registration' if headless else 'protected-pid-startup-registration', 'registered': True, 'servicesActivated': False,
                'servicesEnabled': False, 'daemonReloaded': False, 'containerSessionHookRegistered': False,
                'installable': False, 'configurationDigest': configuration_digest}


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux promotion required')
    if args == ['--guest-payloads']:
        return _install_guest_payloads(load_copier())
    if args == ['--machine-guest-data-policy']:
        return _machine_guest_data_policy(load_copier())
    if args == ['--machine-offline-data-policy']:
        return _machine_offline_data_policy(load_copier())
    if args == ['--prepare-machine-gpu-transaction']:
        return _prepare_machine_gpu_transaction(load_copier())
    if args == ['--machine-guest-payloads']:
        return _install_machine_guest_payloads(load_copier())
    if len(args) == 2 and args[0] == '--pid-compat':
        copier = load_copier()
        _assert_codec_stopped()
        _assert_pid_metadata(copier)
        return _install_component(copier, args[1], Path('/opt/droiddeck'), manifest_pin=PID_MANIFEST_SHA256,
                                  base_pin=ARTIFACT_RECEIPT_SHA256, pid_compat=True)
    if args == ['--register-pid-compat']:
        return _register_pid_compat(load_copier(), Path('/opt/droiddeck/pid-compat'), Path('/etc/systemd/system'))
    if args == ['--machine-pid-policy']:
        return _machine_pid_policy(load_copier())
    if args == ['--machine-headless-policy']:
        return _machine_headless_policy(load_copier())
    if args == ['--machine-owned-headless-policy']:
        return _machine_owned_headless_policy(load_copier())
    if len(args) == 2 and args[0] == '--machine-owned-headless':
        return _install_machine_owned_headless(load_copier(), args[1], Path('/opt/droiddeck'), Path('/etc/droiddeck'), Path('/var/lib/droiddeck/waydroid-profile'))
    if args == ['--register-machine-owned-headless']:
        return _register_machine_owned_headless(load_copier(), Path('/opt/droiddeck/headless-owned-runtime'), Path('/etc/systemd/system'), Path('/etc/droiddeck'), Path('/var/lib/droiddeck/waydroid-profile'))
    if len(args) == 2 and args[0] == '--machine-headless':
        return _install_machine_headless(load_copier(), args[1], Path('/opt/droiddeck'), Path('/etc/droiddeck'), Path('/var/lib/waydroid'))
    if args == ['--register-machine-headless']:
        return _register_machine_headless(load_copier(), Path('/opt/droiddeck/headless-runtime'), Path('/etc/systemd/system'), Path('/etc/droiddeck'), Path('/var/lib/waydroid'))
    if len(args) == 2 and args[0] == '--machine-pid-compat':
        return _install_machine_pid(load_copier(), args[1], Path('/opt/droiddeck'), Path('/etc/droiddeck'))
    if args == ['--register-machine-pid-compat']:
        return _register_machine_pid(load_copier(), Path('/opt/droiddeck/pid-compat'), Path('/etc/systemd/system'), Path('/etc/droiddeck'))
    if args == ['--codecs']:
        return _install_codecs(load_copier(), Path('/var/lib/waydroid/overlay/vendor'), Path('/var/lib/waydroid/waydroid.cfg'))
    if args in (['--waydroid-baseline'], ['--restore-waydroid-baseline']):
        return _configure_waydroid(load_copier(), Path('/var/lib/waydroid/waydroid.cfg'), Path('/var/lib/droiddeck'),
                                   restore=args == ['--restore-waydroid-baseline'])
    if args == ['--waydroid-protocol-baseline']:
        return _prepare_profile_protocols(load_copier(), Path('/var/lib/waydroid/waydroid.cfg'),
                                          Path('/var/lib/droiddeck/waydroid-profile'))
    if args in (['--waydroid-gpu-profile'], ['--restore-waydroid-gpu-profile']):
        return _switch_gpu_profile(load_copier(), Path('/var/lib/waydroid/waydroid.cfg'),
                                   Path('/var/lib/droiddeck/waydroid-profile'),
                                   restore=args == ['--restore-waydroid-gpu-profile'])
    if args == ['--machine-gpu-profile-policy']:
        return _machine_gpu_policy(load_copier())
    if args in (['--machine-waydroid-gpu-profile'], ['--restore-machine-waydroid-gpu-profile']):
        return _switch_machine_gpu_profile(load_copier(), Path('/var/lib/waydroid/waydroid.cfg'),
                                            Path('/var/lib/droiddeck/waydroid-profile'), Path('/etc/droiddeck'),
                                            restore=args == ['--restore-machine-waydroid-gpu-profile'])
    if args in (['--saved-gpu-layer'], ['--restore-saved-gpu-layer']):
        return _configure_saved_layer(load_copier(), restore=args == ['--restore-saved-gpu-layer'])
    if args in (['--mesa-allocator'], ['--restore-mesa-allocator']):
        return _mesa_allocator(load_copier(), restore=args == ['--restore-mesa-allocator'])
    if len(args) == 2 and args[0] == '--configuration':
        return _install_configuration(load_copier(), args[1], Path('/etc'))
    if len(args) == 2 and args[0] == '--services':
        return _install_services(load_copier(), args[1], Path('/etc/droiddeck'))
    if len(args) == 2 and args[0] == '--machine-services':
        return _install_machine_services(load_copier(), args[1], Path('/etc/droiddeck'))
    if args == ['--machine-service-policy']:
        return _machine_service_policy(load_copier())
    if args == ['--register-services']:
        return _register_services(load_copier(), Path('/etc/droiddeck/services'), Path('/etc'))
    if args == ['--register-machine-services']:
        return _register_machine_services(load_copier(), Path('/etc'))
    if len(args) == 2 and args[0] == '--frontend':
        return _install_frontend(load_copier(), args[1], ARTIFACT_PARENT / ARTIFACT_TARGET)
    if len(args) == 2 and args[0] == '--runtime':
        return _install_runtime(load_copier(), args[1], ARTIFACT_PARENT / ARTIFACT_TARGET)
    if len(args) == 2 and args[0] in ('--gateway-dependencies', '--display-dependencies'):
        role = args[0][2:].removesuffix('-dependencies')
        return _install_dependencies(load_copier(), args[1], ARTIFACT_PARENT / ARTIFACT_TARGET / 'runtime' / DEPENDENCIES[role]['directory'], role)
    if len(args) == 2 and args[0] == '--artifacts':
        name = arguments(args[1:])
        copier = load_copier()
        return _promote_artifacts(copier, copier.ARTIFACT_PARENT, ARTIFACT_PARENT, name, copier.ARTIFACT_PINS)
    name = arguments(args)
    copier = load_copier()
    return _promote(copier, SOURCE_PARENT, PARENT, WORK, name, copier.PINS)


def run_cli(args):
    label = {'--artifacts': 'Runtime artifact', '--frontend': 'Frontend', '--runtime': 'Runtime source', '--configuration': 'Runtime configuration',
             '--services': 'Service plan', '--machine-services': 'Machine service plan',
             '--machine-service-policy': 'Machine service policy', '--register-services': 'Service registration',
             '--register-machine-services': 'Machine service registration', '--codecs': 'Codec overlay',
             '--waydroid-baseline': 'Waydroid baseline', '--restore-waydroid-baseline': 'Waydroid recovery',
             '--waydroid-protocol-baseline': 'Fresh session protocol preparation',
             '--waydroid-gpu-profile': 'GPU profile', '--restore-waydroid-gpu-profile': 'GPU profile recovery',
             '--machine-gpu-profile-policy': 'Machine GPU profile policy',
             '--machine-waydroid-gpu-profile': 'Machine GPU profile', '--restore-machine-waydroid-gpu-profile': 'Machine GPU profile recovery',
             '--saved-gpu-layer': 'Saved GPU layer', '--restore-saved-gpu-layer': 'Saved GPU layer recovery',
             '--mesa-allocator': 'Mesa allocator', '--restore-mesa-allocator': 'Mesa allocator recovery',
             '--guest-payloads': 'Guest payload',
             '--machine-offline-data-policy': 'Machine offline data policy',
             '--prepare-machine-gpu-transaction': 'Machine GPU preparation',
             '--machine-guest-data-policy': 'Machine guest data policy', '--machine-guest-payloads': 'Machine guest payload',
             '--pid-compat': 'PID startup', '--register-pid-compat': 'PID startup registration',
             '--machine-pid-policy': 'Machine PID policy', '--machine-pid-compat': 'Machine PID startup',
             '--register-machine-pid-compat': 'Machine PID startup registration',
             '--machine-headless-policy': 'Machine headless policy', '--machine-headless': 'Machine headless startup',
             '--register-machine-headless': 'Machine headless registration',
             '--machine-owned-headless-policy': 'Machine owned headless policy', '--machine-owned-headless': 'Machine owned headless startup',
             '--register-machine-owned-headless': 'Machine owned headless registration',
             '--gateway-dependencies': 'Gateway dependencies', '--display-dependencies': 'Display dependencies'}.get(args[0] if args else '', 'Image')
    try:
        receipt = main(args)
    except PromotionUncertain:
        diagnostic(label + ' promotion uncertain; inspect published layout before use')
        return 2
    except (OSError, ValueError, RuntimeError, AttributeError, ImportError, subprocess.SubprocessError):
        diagnostic(label + ' promotion did not complete; inspect existing or partial output')
        return 1
    try:
        print(json.dumps(receipt, sort_keys=True, indent=2), flush=True)
    except OSError:
        silence_failed_stream('stdout')
        diagnostic(label + ' promotion committed; receipt output failed; inspect published layout')
        return 2
    return 0


def diagnostic(message):
    try:
        print(message, file=sys.stderr, flush=True)
    except OSError:
        silence_failed_stream('stderr')


def silence_failed_stream(name):
    # Failed buffers must not override the phase exit code during Python shutdown.
    _FAILED_STREAMS.append(getattr(sys, name))
    setattr(sys, name, io.StringIO())


if __name__ == '__main__':
    sys.exit(run_cli(sys.argv[1:]))
