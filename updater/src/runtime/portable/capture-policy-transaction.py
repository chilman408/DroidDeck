# SPDX-License-Identifier: GPL-3.0-only
"""Experimental policy-file transaction ONLY; no CLI, services or release admission.

A pending transaction recovers toward the exact supported prior bytes/mode/group
(or absence). Acceptance requires an explicit health proof; its retained receipt
still permits rollback until explicitly archived after terminal proof. Archived
receipts are retained history, never replay authority. Competing edits fail closed. Root writers
must share the directory flock. Interrupted scratch files are retained, never
interpreted or swept. Runtime configuration and Android are outside this API.
"""
from contextlib import contextmanager, ExitStack
import ctypes
import json
import os
import re
import secrets
import stat
import sys

POLICY = 'capture-policy.json'
JOURNAL = '.capture-policy-transaction.json'
LIMIT = 8192
UNSET = object()
PHASES = {'prepared', 'applying', 'accepted', 'rollback-requested', 'restoring', 'rolled-back'}


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def policy_bytes(mode):
    if type(mode) is not str or mode not in ('cpu', 'gpu'):
        raise ValueError('Closed preprocessing mode required')
    return encode({'version': 1, 'preprocessing': mode})


def parse_policy(raw):
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= 256:
            raise ValueError()
        text = raw.decode('utf-8', errors='strict')
        value = json.loads(text)
        if type(value) is not dict or set(value) != {'version', 'preprocessing'} or type(value['version']) is not int or value['version'] != 1:
            raise ValueError()
        mode = value['preprocessing']
        policy_bytes(mode)
        compact = re.sub(r'[ \t\r\n]', '', text)
        if compact not in ('{"version":1,"preprocessing":"' + mode + '"}', '{"preprocessing":"' + mode + '","version":1}'):
            raise ValueError()
        return mode
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('Invalid capture policy bytes') from None


def supported(info, maximum, private=False):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or info.st_mode & 0o7022 or not 1 <= info.st_size <= maximum
            or private and stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError('Unsafe transaction file metadata')


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


def snapshot_record(value):
    if value is None:
        return
    if type(value) is not dict or set(value) != {'hex', 'mode', 'gid', 'device', 'inode', 'mtime'}:
        raise ValueError('Invalid policy snapshot')
    if (any(type(value[k]) is not int or not 0 <= value[k] < 2**64 for k in ('gid', 'device', 'inode', 'mtime', 'mode'))
            or value['gid'] >= 2**32 or value['mode'] & ~0o755 or value['mode'] & 0o022
            or not value['inode'] or type(value['hex']) is not str or not re.fullmatch(r'(?:[0-9a-f]{2}){1,256}', value['hex'])):
        raise ValueError('Unsupported policy snapshot')
    parse_policy(bytes.fromhex(value['hex']))


def content(value):
    return None if value is None else {k: value[k] for k in ('hex', 'mode', 'gid')}


def journal_record(raw):
    try:
        value = json.loads(raw)
        if (type(value) is not dict or set(value) != {'version', 'kind', 'token', 'phase', 'mode', 'before', 'after', 'restored'}
                or type(value['version']) is not int or value['version'] != 1 or value['kind'] != 'capture-policy-transaction'
                or type(value['token']) is not str or not re.fullmatch('[0-9a-f]{32}', value['token'])
                or value['phase'] not in PHASES or encode(value) != raw):
            raise ValueError()
        target = policy_bytes(value['mode'])
        for k in ('before', 'after', 'restored'):
            snapshot_record(value[k])
        if value['after'] is not None and content(value['after']) != {'hex': target.hex(), 'mode': 0o644, 'gid': 0}:
            raise ValueError()
        if (value['phase'] == 'prepared' and (value['after'] is not None or value['restored'] is not None)
                or value['phase'] in ('applying', 'accepted', 'rollback-requested', 'restoring') and value['after'] is None
                or value['phase'] in ('applying', 'accepted', 'rollback-requested') and value['restored'] is not None
                or value['phase'] in ('restoring', 'rolled-back') and content(value['restored']) != content(value['before'])):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('Invalid capture transaction journal') from None


def rename_new(directory, source, target):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(directory, source.encode('ascii'), directory, target.encode('ascii'), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class PolicyTransaction:
    """Internal held-directory engine. Tests pass generated private directories.

    Production callers must use open_policy_store, not provide arbitrary paths.
    No release/binary-support or service-health claim follows from file success.
    """
    def __init__(self, directory, *, guard=lambda: None, checkpoint=lambda event: None):
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise ValueError('Root Linux policy transaction required')
        self.fd, self.guard, self.checkpoint = directory, guard, checkpoint
        self.busy = False
        self._check()

    def _check(self):
        self.guard()
        info = os.fstat(self.fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe transaction directory')

    @contextmanager
    def _locked(self):
        import fcntl
        if self.busy:
            raise ValueError('Capture transaction reentry rejected')
        self.busy = True
        locked = False
        try:
            self._check()
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        finally:
            if locked:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.busy = False

    def _read(self, name, maximum=256, private=False):
        self._check()
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(fd)
            supported(before, maximum, private)
            if os.listxattr(fd):
                raise ValueError('Extended file metadata requires separate review')
            raw = os.read(fd, maximum + 1)
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if identity(before) != identity(after) or identity(after) != identity(named) or len(raw) != before.st_size:
                raise ValueError('Transaction file changed during read')
            return {'hex': raw.hex(), 'mode': stat.S_IMODE(after.st_mode), 'gid': after.st_gid,
                    'device': after.st_dev, 'inode': after.st_ino, 'mtime': after.st_mtime_ns}
        finally:
            os.close(fd)

    def _policy(self, name=POLICY):
        value = self._read(name)
        snapshot_record(value)
        return value

    def _load(self):
        value = self._read(JOURNAL, LIMIT, True)
        return (None, None) if value is None else (journal_record(bytes.fromhex(value['hex'])), value)

    def _expect(self, name, expected, *, journal=False):
        actual = self._read(name, LIMIT if journal else 256, journal)
        if actual != expected:
            raise ValueError('Concurrent capture policy or journal change; preserve for inspection')

    def _scratch(self, raw, mode=0o600, gid=0):
        self._check()
        name = '.capture-policy-write-' + secrets.token_hex(16)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            data = memoryview(raw)
            while data:
                count = os.write(fd, data)
                if count <= 0:
                    raise OSError('Incomplete policy write')
                data = data[count:]
            os.fchown(fd, 0, gid)
            os.fchmod(fd, mode)
            os.fsync(fd)
            self.checkpoint('scratch-flushed')
        finally:
            os.close(fd)
        return name

    def _save(self, value, previous):
        raw = encode(value)
        journal_record(raw)
        temporary = self._scratch(raw)
        staged = self._read(temporary, LIMIT, True)
        if staged is None or staged['hex'] != raw.hex():
            raise ValueError('Staged capture journal changed')
        self._expect(JOURNAL, previous, journal=True)
        self._check()
        if previous is None:
            rename_new(self.fd, temporary, JOURNAL)
        else:
            os.rename(temporary, JOURNAL, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        self.checkpoint('journal-published-' + value['phase'])
        os.fsync(self.fd)
        self.checkpoint('journal-durable-' + value['phase'])
        self._expect(JOURNAL, staged, journal=True)
        return staged

    def _stage(self, name, data):
        value = self._policy(name)
        if value is None:
            temporary = self._scratch(bytes.fromhex(data['hex']), data['mode'], data['gid'])
            self._check()
            rename_new(self.fd, temporary, name)
            self.checkpoint('stage-published')
            os.fsync(self.fd)
            self.checkpoint('stage-durable')
            value = self._policy(name)
        if content(value) != data:
            raise ValueError('Foreign staged policy; preserve for inspection')
        return value

    def _unused_token(self, token):
        for name in ('.capture-policy-history-' + token + '.json',
                     '.capture-policy-next-' + token, '.capture-policy-restore-' + token):
            try:
                os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ValueError('Capture transaction token already has retained state')

    def begin(self, mode, *, token=None, expected_before=UNSET):
        desired = {'hex': policy_bytes(mode).hex(), 'mode': 0o644, 'gid': 0}
        with self._locked():
            old, previous = self._load()
            if old is not None:
                raise ValueError('Retained transaction must be inspected before another change')
            before = self._policy()
            if expected_before is not UNSET:
                snapshot_record(expected_before)
                if before != expected_before:
                    raise ValueError('Capture policy changed before transaction')
            token = secrets.token_hex(16) if token is None else token
            if type(token) is not str or not re.fullmatch('[0-9a-f]{32}', token):
                raise ValueError('Exact transaction token required')
            self._unused_token(token)
            value = {'version': 1, 'kind': 'capture-policy-transaction', 'token': token,
                     'phase': 'prepared', 'mode': mode, 'before': before, 'after': None, 'restored': None}
            previous = self._save(value, previous)
            stage = '.capture-policy-next-' + value['token']
            value.update(phase='applying', after=self._stage(stage, desired))
            previous = self._save(value, previous)
            self._expect(POLICY, before)
            self._expect(JOURNAL, previous, journal=True)
            self._expect(stage, value['after'])
            self._check()
            os.rename(stage, POLICY, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            self.checkpoint('policy-published')
            os.fsync(self.fd)
            self.checkpoint('policy-durable')
            self._expect(POLICY, value['after'])
            return value['token']

    def inspect(self):
        with self._locked():
            value, _ = self._load()
            return value

    def accept(self, token, health_proof):
        with self._locked():
            value, previous = self._load()
            if value is None or value['token'] != token or value['phase'] != 'applying' or not callable(health_proof):
                raise ValueError('Pending capture transaction and health proof required')
            self._expect(POLICY, value['after'])
            if health_proof() is not True:
                raise ValueError('Capture policy health proof rejected')
            self._expect(POLICY, value['after'])
            value['phase'] = 'accepted'
            self._save(value, previous)

    def _rollback(self, value, previous):
        current = self._policy()
        if value['phase'] == 'rolled-back':
            self._expect(POLICY, value['restored'])
            return 'rolled-back'
        if value['phase'] == 'prepared' or current == value['before']:
            self._expect(POLICY, value['before'])
            value.update(phase='rolled-back', restored=value['before'])
        else:
            if value['phase'] not in ('rollback-requested', 'restoring'):
                self._expect(POLICY, value['after'])
                value['phase'] = 'rollback-requested'
                previous = self._save(value, previous)
            if value['phase'] != 'restoring':
                self._expect(POLICY, value['after'])
                stage = '.capture-policy-restore-' + value['token']
                restored = None if value['before'] is None else self._stage(stage, content(value['before']))
                value.update(phase='restoring', restored=restored)
                previous = self._save(value, previous)
            if self._policy() != value['restored']:
                self._expect(POLICY, value['after'])
                self._expect(JOURNAL, previous, journal=True)
                self._check()
                if value['restored'] is None:
                    os.unlink(POLICY, dir_fd=self.fd)
                else:
                    stage = '.capture-policy-restore-' + value['token']
                    self._expect(stage, value['restored'])
                    os.rename(stage, POLICY, src_dir_fd=self.fd, dst_dir_fd=self.fd)
                self.checkpoint('restore-published')
                os.fsync(self.fd)
                self.checkpoint('restore-durable')
            self._expect(POLICY, value['restored'])
            value['phase'] = 'rolled-back'
        self._save(value, previous)
        return 'rolled-back'

    def rollback(self, token):
        with self._locked():
            value, previous = self._load()
            if value is None or value['token'] != token:
                raise ValueError('Exact capture transaction required')
            return self._rollback(value, previous)

    def retire(self, token, terminal_proof):
        # Explicit operator step only. Archive preserves the exact journal inode;
        # no scratch sweep and no terminal record deletion. Caller must hold the
        # independent serialized service/source/config admission and health proof.
        if type(token) is not str or not re.fullmatch('[0-9a-f]{32}', token) or not callable(terminal_proof):
            raise ValueError('Exact terminal transaction and proof required')
        with self._locked():
            name = '.capture-policy-history-' + token + '.json'
            value, previous = self._load()
            archived = value is None
            if archived:
                previous = self._read(name, LIMIT, True)
                if previous is None:
                    raise ValueError('Retained terminal transaction required')
                value = journal_record(bytes.fromhex(previous['hex']))
            if value['token'] != token or value['phase'] not in ('accepted', 'rolled-back'):
                raise ValueError('Exact accepted or restored terminal transaction required')
            expected = value['after'] if value['phase'] == 'accepted' else value['restored']
            self._expect(POLICY, expected)
            if terminal_proof() is not True:
                raise ValueError('Independent terminal health proof rejected')
            self._expect(POLICY, expected)
            self._expect(name if archived else JOURNAL, previous, journal=True)
            if not archived:
                self._expect(name, None, journal=True)
                self._check()
                rename_new(self.fd, JOURNAL, name)
                self.checkpoint('receipt-published')
            else:
                # An interrupted first retirement may be retried only if no new
                # journal exists and its exact terminal policy is still current.
                self._expect(JOURNAL, None, journal=True)
            os.fsync(self.fd)
            self.checkpoint('receipt-durable')
            self._expect(name, previous, journal=True)
            self._expect(JOURNAL, None, journal=True)
            self._expect(POLICY, expected)
            return {'phase': value['phase'], 'receipt': name, 'retired': True}

    def recover(self):
        with self._locked():
            value, previous = self._load()
            if value is None:
                return 'no-transaction'
            if value['phase'] == 'accepted':
                self._expect(POLICY, value['after'])
                return 'accepted'
            return self._rollback(value, previous)


@contextmanager
def open_policy_store():
    """Fixed protected production path. Currently not wired to any operator/API."""
    with ExitStack() as stack:
        chain = []
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        chain.append((None, '/', fd, os.fstat(fd)))
        for part in ('etc', 'droiddeck'):
            parent = fd
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            chain.append((parent, part, fd, os.fstat(fd)))
        def guard():
            for parent, name, held, original in chain:
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                actual = os.fstat(held)
                for info in (named, actual):
                    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                            or (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino)):
                        raise ValueError('Protected policy ancestry changed')
        yield PolicyTransaction(fd, guard=guard)
