# SPDX-License-Identifier: GPL-3.0-only
"""Bind a held runtime plan to independently regenerated, protected GPU profiles."""
import configparser
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys

_spec = importlib.util.spec_from_file_location('owned_configured_plan', Path(__file__).with_name('runtime-configured-plan.py'))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)
A = C.A
PROFILE_ROOT = '/var/lib/droiddeck/waydroid-profile'
NAMES = ('baseline.cfg', 'gpu.cfg', 'original.cfg')


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


def _hold_profile_sources(stack, root):
    A._root_linux()
    path = A._canonical(root)
    chain, records = [], {}

    def directory(info, private):
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or (private and (info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o700))):
            raise ValueError('Protected profile directory required')

    def opened(name, parent=None, private=False):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        directory(info, private)
        identity = info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid
        chain.append((parent, name, fd, private, identity))
        return fd

    current = opened('/')
    for index, name in enumerate(path.parts[1:]):
        current = opened(name, current, private=index == len(path.parts) - 2)

    def file_check(name, fd, before):
        for info in (os.fstat(fd), os.stat(name, dir_fd=current, follow_symlinks=False)):
            if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or not 0 < info.st_size <= 65536 or A._identity(info) != A._identity(before)):
                raise ValueError('Original single-link private profile file required')

    def check():
        A._root_linux()
        for parent, name, fd, private, before in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                directory(info, private)
                if (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) != before:
                    raise ValueError('Original profile namespace required')
        if set(os.listdir(current)) != set(NAMES):
            raise ValueError('Closed profile inventory required')
        for name, (fd, before, raw) in records.items():
            file_check(name, fd, before)
            if A._read(fd, 65536) != raw:
                raise ValueError('Held profile generation changed')
            file_check(name, fd, before)

    check()
    for name in NAMES:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        file_check(name, fd, before)
        raw = A._read(fd, 65536)
        if len(raw) != before.st_size:
            raise ValueError('Bounded original profile required')
        file_check(name, fd, before)
        records[name] = fd, before, raw
    check()

    def read(name):
        if type(name) is not str or name not in records:
            raise ValueError('Closed profile read required')
        check()
        raw = records[name][2]
        check()
        return raw

    return {'check': check, 'read': read}


def hold_owned_profile_runtime_plan(stack, user, source, raw):
    A._root_linux()
    C._bounded(raw)
    held = C.hold_configured_runtime_plan(stack, user, source, raw)
    profiles = _hold_profile_sources(stack, PROFILE_ROOT)

    def check():
        held['check']()
        profiles['check']()
        held['check']()

    check()
    original = profiles['read']('original.cfg')
    software, gpu = profile_candidate(original), profile_candidate(original, gpu=True)
    if profiles['read']('baseline.cfg') != software or profiles['read']('gpu.cfg') != gpu:
        raise ValueError('Protected profiles differ from independent recipe')
    policy = {'version': 2, 'configurationDigest': held['policy']['configurationDigest'],
        'originalConfigurationSha256': hashlib.sha256(original).hexdigest(),
        'profiles': {'software': hashlib.sha256(profile_with_detected_protocols(software)).hexdigest(),
                     'gpu': hashlib.sha256(profile_with_detected_protocols(gpu)).hexdigest()}}
    if len({policy['originalConfigurationSha256'], *policy['profiles'].values()}) != 3:
        raise ValueError('Distinct owned profile generations required')
    base = held['read_plan']()
    value = json.loads(base)
    value.update({'kind': 'owned-profile-runtime-artifact-placement-plan', 'profilePolicyVerified': True,
        'ownedHeadlessPolicy': policy, 'configuredPlanSha256': hashlib.sha256(base).hexdigest()})
    plan = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    encoded_policy = (json.dumps(policy, indent=2) + '\n').encode('ascii')
    check()

    def checked(read, *args):
        check()
        result = read(*args)
        check()
        return result

    return {'check': check, 'read_plan': lambda: checked(lambda: plan),
        'read_owned_policy': lambda: checked(lambda: encoded_policy),
        'read_configuration': lambda: checked(held['read_configuration']),
        'read_source': lambda name: checked(held['read_source'], name), 'policy': {
            **held['policy'], 'kind': 'held-owned-profile-runtime-placement-plan',
            'profilePolicyVerified': True, 'gpuProfilePolicyRegenerated': True,
            'profileSourceNamespaceVerified': True, 'profileSourcesCopied': False,
            'gpuAccelerationVerified': False, 'codecOverlayVerified': False,
            'independentRecoveryArmed': False, 'ownedProfilePolicySha256': hashlib.sha256(encoded_policy).hexdigest(),
            'configuredPlanSha256': hashlib.sha256(base).hexdigest(), 'planSha256': hashlib.sha256(plan).hexdigest()}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4 or argv[0] != '--inspect-owned-profile-plan' or argv[3] != '-':
        raise ValueError('Only fixed read-only owned profile binding with bounded stdin is exposed')
    raw = sys.stdin.buffer.read(C.LIMIT + 1)
    with ExitStack() as stack:
        held = hold_owned_profile_runtime_plan(stack, argv[1], argv[2], raw)
        held['read_plan']()
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, configparser.Error):
        print('Owned profile runtime planning refused; no changes performed', file=sys.stderr)
        sys.exit(1)
