# SPDX-License-Identifier: GPL-3.0-only
"""Initialize browser-route Android music volume once; never reset user choices."""
from contextlib import ExitStack, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

STATE = '/var/lib/droiddeck/default-media'
VOLUME = ['/system/bin/cmd', 'media_session', 'volume', '--stream', '3']
SAVED = ['/system/bin/settings', '--user', '0', 'get', 'system', 'volume_music_remote_submix']


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def volume(output):
    matches = re.findall(r'^\[V\] volume is ([0-9]+) in range \[0\.\.([0-9]+)\]$', output, re.M)
    if len(matches) != 1 or '[E]' in output:
        raise ValueError('Unambiguous Android music volume required')
    current, maximum = map(int, matches[0])
    if not 0 <= current <= maximum <= 100 or maximum == 0:
        raise ValueError('Bounded nonzero Android music range required')
    return current, maximum


def browser_route(output):
    blocks = re.findall(r'^- STREAM_MUSIC:\n(.*?)(?=^- STREAM_|\Z)', output, re.M | re.S)
    if len(blocks) != 1 or re.findall(r'^\s+Devices: (.+)$', blocks[0], re.M) != ['remote_submix(8000)']:
        raise ValueError('Only active browser remote_submix music route admitted')


def context(guest):
    if guest.command(['/system/bin/am', 'get-current-user']) != '0':
        raise ValueError('Default Android user0 required')
    identity = guest.command(['/system/bin/stat', '-c', '%d:%i', '/data'])
    if not re.fullmatch('[0-9]+:[1-9][0-9]*', identity):
        raise ValueError('Original Android data identity required')
    browser_route(guest.command(['/system/bin/dumpsys', 'audio']))
    current, maximum = volume(guest.command(VOLUME + ['--get']))
    return {'version': 1, 'kind': 'browser-media-default', 'androidData': identity,
            'stream': 3, 'route': 'remote_submix', 'maximum': maximum}, current


def apply(guest, journal, mode, *, sleep=time.sleep):
    if mode not in ('--inspect', '--initialize', '--resume'):
        raise ValueError('Exact media default operation required')
    expected, current = context(guest)
    intent, complete = journal.read('intent.json'), journal.read('complete.json')
    if (intent is not None and encode(intent) != encode(expected)
            or complete is not None and encode(complete) != encode(expected)):
        raise ValueError('Original media default context required')
    if complete is not None and intent is None:
        raise ValueError('Completion without original intent refused')
    result = {**expected, 'current': current, 'percent': round(100 * current / expected['maximum']),
              'initialized': complete is not None, 'changedNow': False}
    if complete is not None or mode == '--inspect':
        # Completion means initialization happened, not that users must stay at 100%.
        return result
    if intent is None:
        if mode != '--initialize':
            raise ValueError('No interrupted media initialization to resume')
        journal.write('intent.json', expected)
        journal.check()
        if context(guest)[0] != expected:
            raise ValueError('Android data changed before volume initialization')
        volume(guest.command(VOLUME + ['--set', str(expected['maximum']), '--get']))
        result['changedNow'] = current != expected['maximum']
    elif mode != '--resume':
        raise ValueError('Interrupted initialization requires explicit resume; no volume replay')
    # Android persists each output route asynchronously. Only the browser capture
    # route is admitted; do not raise headphones, alarms, calls or browser gain.
    for attempt in range(11):
        actual, current = context(guest)
        saved = guest.command(SAVED)
        if actual != expected:
            raise ValueError('Android data changed during initialization')
        if current == expected['maximum'] and saved == str(expected['maximum']):
            journal.write('complete.json', expected)
            return {**result, 'current': current, 'percent': 100, 'initialized': True}
        if attempt != 10:
            sleep(0.5)
    raise ValueError('Full browser-route volume not persisted; retain intent and inspect')


class Journal:
    def __init__(self, directory, check):
        self.directory, self.check_directories, self.files = directory, check, {}

    def check(self):
        self.check_directories()
        for name, original in self.files.items():
            if generation(os.stat(name, dir_fd=self.directory, follow_symlinks=False)) != original:
                raise ValueError('Original media journal file changed')

    def read(self, name):
        self.check()
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink,
                    stat.S_IMODE(info.st_mode)) != (0, 0, 1, 0o600) or not 0 < info.st_size <= 4096):
                raise ValueError('Private bounded single-link media journal required')
            raw = os.read(fd, 4097)
            result = json.loads(raw)
            if encode(result) != raw or generation(os.fstat(fd)) != generation(info):
                raise ValueError('Canonical stable media journal required')
            self.files[name] = generation(info)
            self.check()
            return result
        finally:
            os.close(fd)

    def write(self, name, value):
        self.check()
        raw = encode(value)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.directory)
        try:
            os.fchmod(fd, 0o600)
            if os.write(fd, raw) != len(raw):
                raise ValueError('Incomplete media journal; preserve for inspection')
            os.fsync(fd)
            self.files[name] = generation(os.fstat(fd))
        finally:
            os.close(fd)
        os.fsync(self.directory)
        self.check()


def generation(info):
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode',
                 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


@contextmanager
def open_journal(path, *, create):
    import fcntl
    path = Path(path)
    if not path.is_absolute() or str(path) != str(path.resolve()) or '..' in path.parts:
        raise ValueError('Canonical protected media journal required')
    with ExitStack() as stack:
        parent, chain = None, []
        for index, name in enumerate(path.parts):
            leaf = index == len(path.parts) - 1
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                if not create or not leaf:
                    raise
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.fsync(fd)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
                    or info.st_mode & 0o022 or leaf and stat.S_IMODE(info.st_mode) != 0o700):
                raise ValueError('Root-protected media journal ancestors required')
            chain.append((parent, name, fd, info.st_dev, info.st_ino))
            parent = fd

        def check():
            for ancestor, name, fd, device, inode in chain:
                for info in (os.fstat(fd), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
                            or info.st_mode & 0o022 or (info.st_dev, info.st_ino) != (device, inode)):
                        raise ValueError('Original protected media journal path required')
            if stat.S_IMODE(os.fstat(parent).st_mode) != 0o700:
                raise ValueError('Private media journal required')
            if set(os.listdir(parent)) - {'intent.json', 'complete.json'}:
                raise ValueError('Unexpected media journal files; preserve them')

        # Lock the directory inode, so inspection creates no lock files.
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check()
        journal = Journal(parent, check)
        yield journal
        journal.check()


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or args not in (
            ['--inspect'], ['--initialize'], ['--resume']):
        raise ValueError('Root Linux media inspect/initialize/resume required')
    spec = importlib.util.spec_from_file_location('default_media_guest', Path(__file__).with_name('default-apps.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    guest = module.Guest()
    try:
        with open_journal(STATE, create=args == ['--initialize']) as journal:
            result = apply(guest, journal, args[0])
    except FileNotFoundError:
        if args != ['--inspect']:
            raise
        # Inspection of an uninitialized machine never creates state or changes volume.
        expected, current = context(guest)
        result = {**expected, 'current': current, 'initialized': False, 'changedNow': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['initialized'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        print('Media default incomplete; preserve journal and user volume, inspect before explicit resume', file=sys.stderr)
        sys.exit(1)
