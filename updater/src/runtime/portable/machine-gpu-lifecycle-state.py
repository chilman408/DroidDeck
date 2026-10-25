# SPDX-License-Identifier: GPL-3.0-only
"""Internal append-only lifecycle metadata; no service or Android file operations."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

NAME = 'machine-gpu-lifecycle'
PENDING = 'pending-machine-gpu-lifecycle'
ATTEMPT_PREFIX = 'pending-machine-gpu-lifecycle-attempt-'
RECORD = 'pending-record.json'
MAX_RECORDS = 16
EDGES = {
    'prepared': {'stopping', 'recovery-required'}, 'stopping': {'installing', 'recovery-required'},
    'installing': {'starting', 'recovery-required'}, 'starting': {'waiting-h264', 'verifying-native', 'recovery-required'},
    'waiting-h264': {'verifying-native', 'recovery-required'}, 'verifying-native': {'ready', 'recovery-required'},
    'ready': {'recovery-required'}, 'recovery-required': {'recovering'}, 'recovering': set(), 'restored': set(),
}


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def filename(revision):
    if type(revision) is not int or not 0 <= revision < MAX_RECORDS:
        raise ValueError('Bounded lifecycle history index required')
    return f'{revision:019d}.json'


def sample_clock(clock, previous=None):
    now = clock()
    if (type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < 1e12
            or (previous is not None and now < previous)):
        raise ValueError('Finite nonregressing lifecycle storage clock required')
    return now


def checkpoint(raw, lifecycle, expected, boot, now, previous=None):
    if type(raw) is not bytes or not 0 < len(raw) <= 16384:
        raise ValueError('Bounded lifecycle checkpoint required')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'kind', 'version', 'status', 'previousSha256', 'recoverySequence'}
            or value['kind'] != 'protected-machine-gpu-lifecycle-checkpoint' or type(value['version']) is not int or value['version'] != 1
            or encode(value) != raw):
        raise ValueError('Closed canonical lifecycle checkpoint required')
    status = lifecycle.validate_record(value['status'], expected, boot, now, set(EDGES))
    if previous is None:
        if status['revision'] != 0 or status['stage'] != 'prepared' or value['previousSha256'] is not None or value['recoverySequence'] is not None:
            raise ValueError('Exact initial prepared lifecycle checkpoint required')
    else:
        older, digest = previous
        if (status['revision'] != older['revision'] + 1 or status['since'] < older['since'] or value['previousSha256'] != digest
                or type(value['previousSha256']) is not str):
            raise ValueError('Lifecycle checkpoint chain changed')
        if status['stage'] == 'restored':
            if older['stage'] == 'restored' or type(value['recoverySequence']) is not int or not 0 <= value['recoverySequence'] < 2**63:
                raise ValueError('Bounded recovery completion sequence required')
        elif status['stage'] not in EDGES[older['stage']] or value['recoverySequence'] is not None:
            raise ValueError('Closed forward lifecycle transition required')
    return status, hashlib.sha256(raw).hexdigest()


class Store:
    def __init__(self, copier, stack, sources, lifecycle, expected, parent=Path('/var/lib/droiddeck'), *,
                 admit=None, current_boot=None, clock=None, verify_recovery=None):
        if sys.platform != 'linux' or os.geteuid() != 0 or not all(callable(v) for v in (admit, current_boot, clock)):
            raise ValueError('Root Linux admitted lifecycle storage required')
        self.c, self.stack, self.sources, self.l = copier, stack, sources, lifecycle
        self.expected, self.admit, self.current_boot, self.clock = lifecycle.binding(expected), admit, current_boot, clock
        self.verify_recovery, self.known, self.busy, self.last_clock = verify_recovery, {}, False, None
        self.boot = current_boot()
        if type(self.boot) is not str or not re.fullmatch(lifecycle.BOOT, self.boot):
            raise ValueError('Canonical lifecycle storage boot required')
        self._admit()
        self.p = sources['load']('recovery')
        self.parent = Path(copier.canonical(str(parent)))
        self.chain = copier.open_chain(stack, self.parent / NAME, root_owned=True)
        self.directory = self.chain[-1][1]
        self.c.protected(os.fstat(self.directory), 0o700)
        self.read()

    def _admit(self):
        self.sources['check']()
        self.admit()
        if self.current_boot() != self.boot:
            raise ValueError('Lifecycle storage boot changed')

    def _check(self, entries):
        self._admit()
        self._now()
        self.c.check_chain(self.chain, root_owned=True)
        self.c.protected(os.fstat(self.directory), 0o700)
        if set(os.listdir(self.directory)) != entries:
            raise ValueError('Partial or foreign lifecycle history must be preserved')
        if any(name.startswith(ATTEMPT_PREFIX) for name in os.listdir(self.chain[-2][1])):
            raise ValueError('Interrupted lifecycle preparation attempt must be preserved')
        try:
            os.stat(PENDING, dir_fd=self.chain[-2][1], follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('Pending lifecycle initialization must be inspected')
        for name, (fd, info, raw) in self.known.items():
            self.c.check_image(self.directory, name, fd, info, private=True)
            if self.c.digest_fd(fd, info.st_size) != hashlib.sha256(raw).hexdigest():
                raise ValueError('Held immutable lifecycle checkpoint changed')

    @contextmanager
    def _locked(self):
        if self.busy:
            raise ValueError('Lifecycle storage cannot reenter its own transaction')
        import fcntl
        self.busy = True
        locked = False
        try:
            self._admit()
            fcntl.flock(self.directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        finally:
            if locked:
                fcntl.flock(self.directory, fcntl.LOCK_UN)
            self.busy = False

    def _history(self):
        entries = set(os.listdir(self.directory))
        if not 1 <= len(entries) <= MAX_RECORDS or entries != {filename(i) for i in range(len(entries))}:
            raise ValueError('Exact contiguous bounded lifecycle history required')
        self._check(entries)
        previous = None
        now = self._now()
        for i in range(len(entries)):
            name = filename(i)
            if name not in self.known:
                self.known[name] = self.p._read_profile_file(self.c, self.stack, self.directory, name, maximum=16384)
            previous = checkpoint(self.known[name][2], self.l, self.expected, self.boot, now, previous)
        self._check(entries)
        return previous, entries

    def _now(self):
        now = sample_clock(self.clock, self.last_clock)
        self.last_clock = now
        return now

    def read(self):
        with self._locked():
            (status, _digest), _entries = self._history()
            return dict(status)

    def _append(self, expected, stage, sequence, proof):
        with self._locked(), ExitStack() as temporary:
            (old, digest), entries = self._history()
            if encode(old) != encode(expected) or len(entries) >= MAX_RECORDS:
                raise ValueError('Stale lifecycle compare-and-swap or exhausted history')
            if stage != 'restored' and stage not in EDGES[old['stage']]:
                raise ValueError('Illegal lifecycle phase advance')
            proof()
            now = self._now()
            status = {**old, 'revision': old['revision'] + 1, 'stage': stage, 'since': now}
            value = {'kind': 'protected-machine-gpu-lifecycle-checkpoint', 'version': 1,
                     'status': status, 'previousSha256': digest, 'recoverySequence': sequence}
            raw = encode(value)
            checkpoint(raw, self.l, self.expected, self.boot, now, (old, digest))
            fd = self.c.create_private(temporary, self.directory, RECORD)
            self.c.write_all(fd, raw)
            os.fsync(fd)
            info = os.fstat(fd)
            os.fsync(self.directory)
            proof()
            self._check(entries | {RECORD})
            self.c.check_image(self.directory, RECORD, fd, info, private=True)
            if self.c.digest_fd(fd, info.st_size) != hashlib.sha256(raw).hexdigest():
                raise ValueError('Pending lifecycle checkpoint changed')
            target = filename(status['revision'])
            held_fd = os.dup(fd)
            self.stack.callback(os.close, held_fd)
            self.p.rename_exclusive(self.directory, RECORD, target)
            try:
                # Keep the original inode anchored even when publication durability is uncertain.
                self.known[target] = held_fd, info, raw
                self.known[target] = held_fd, os.fstat(held_fd), raw
                os.fsync(self.directory)
                proof()
                self._check(entries | {target})
                current = os.fstat(fd)
                a, b = self.c.identity(info), self.c.identity(current)
                if a[:4] + a[5:] != b[:4] + b[5:]:
                    raise ValueError('Published lifecycle record generation changed')
                self.c.check_image(self.directory, target, fd, current, private=True)
                if self.c.digest_fd(fd, current.st_size) != hashlib.sha256(raw).hexdigest():
                    raise ValueError('Published lifecycle checkpoint bytes changed')
                self.known[target] = held_fd, current, raw
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                raise self.l.LifecycleUncertain('Lifecycle checkpoint published; inspect immutable history before retry') from error
            return dict(status)

    def advance(self, expected, stage):
        if type(stage) is not str or stage not in EDGES or stage == 'restored':
            raise ValueError('Closed nonterminal lifecycle phase required')
        return self._append(expected, stage, None, lambda: None)

    def mark_restored(self, expected, sequence):
        if not callable(self.verify_recovery) or type(sequence) is not int or not 0 <= sequence < 2**63:
            raise ValueError('Independent durable recovery proof and exact sequence required')
        def proof():
            value = self.verify_recovery()
            if (type(value) is not dict or value.get('kind') != 'protected-machine-gpu-recovery-status'
                    or type(value.get('sequence')) is not int or value['sequence'] != sequence
                    or value.get('stage') != 'settled' or value.get('checkpointRequired') is not False or value.get('nextAction') is not None
                    or any(type(value.get(k)) is not type(self.expected[k]) or value[k] != self.expected[k] for k in self.l.BINDINGS)
                    or type(value.get('states')) is not dict or set(value['states']) != {'allocator', 'savedLayer', 'configuration'}
                    or value.get('observedStates') != value['states']
                    or any(v not in ('baseline', 'restored') for v in value['states'].values())):
                raise ValueError('Recovery journal is not independently settled at the requested sequence')
        return self._append(expected, 'restored', sequence, proof)


def prepare(copier, stack, sources, lifecycle, expected, parent=Path('/var/lib/droiddeck'), *, admit=None, current_boot=None, clock=None):
    if sys.platform != 'linux' or os.geteuid() != 0 or not all(callable(v) for v in (admit, current_boot, clock)):
        raise ValueError('Root Linux admitted new lifecycle preparation required')
    expected = lifecycle.binding(expected)
    sources['check']()
    admit()
    p = sources['load']('recovery')
    parent = Path(copier.canonical(str(parent)))
    chain = copier.open_chain(stack, parent, root_owned=True)
    directory = chain[-1][1]
    def absent(name):
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError('Existing lifecycle namespace must be preserved')
    absent(NAME)
    absent(PENDING)
    if any(name.startswith(ATTEMPT_PREFIX) for name in os.listdir(directory)):
        raise ValueError('Interrupted lifecycle preparation attempt must be preserved')
    boot, now = current_boot(), sample_clock(clock)
    if type(boot) is not str or not re.fullmatch(lifecycle.BOOT, boot):
        raise ValueError('Canonical lifecycle preparation boot required')
    status = {'kind': 'protected-machine-gpu-lifecycle', 'version': 1, 'stage': 'prepared',
              'revision': 0, 'boot': boot, 'since': now, **expected}
    raw = encode({'kind': 'protected-machine-gpu-lifecycle-checkpoint', 'version': 1,
                  'status': status, 'previousSha256': None, 'recoverySequence': None})
    checkpoint(raw, lifecycle, expected, boot, now)
    attempt = ATTEMPT_PREFIX + secrets.token_hex(16)
    os.mkdir(attempt, 0o700, dir_fd=directory)
    created = os.stat(attempt, dir_fd=directory, follow_symlinks=False)
    copier.protected(created, 0o700)
    staged = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    stack.callback(os.close, staged)
    staged_info = os.fstat(staged)
    def initial_guard(name):
        nonlocal now
        sources['check']()
        admit()
        now = sample_clock(clock, now)
        copier.check_chain(chain, root_owned=True)
        if current_boot() != boot:
            raise ValueError('Lifecycle preparation boot changed')
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if copier.identity(created) != copier.identity(staged_info) or (named.st_dev, named.st_ino) != (created.st_dev, created.st_ino):
            raise ValueError('New lifecycle preparation directory changed')
        copier.protected(named, 0o700)
        if os.listdir(staged):
            raise ValueError('New lifecycle preparation directory must be empty')
        attempts = {entry for entry in os.listdir(directory) if entry.startswith(ATTEMPT_PREFIX)}
        if attempts != ({attempt} if name == attempt else set()):
            raise ValueError('Concurrent lifecycle preparation must be preserved')
        absent(NAME)
        absent(PENDING if name == attempt else attempt)
    initial_guard(attempt)
    p.rename_exclusive(directory, attempt, PENDING)
    os.fsync(directory)
    initial_guard(PENDING)
    fd = copier.create_private(stack, staged, filename(0))
    copier.write_all(fd, raw)
    os.fsync(fd)
    info = os.fstat(fd)
    def guard(published=False):
        nonlocal now
        sources['check']()
        admit()
        now = sample_clock(clock, now)
        copier.check_chain(chain, root_owned=True)
        if current_boot() != boot:
            raise ValueError('Lifecycle preparation boot changed')
        entry = NAME if published else PENDING
        named = os.stat(entry, dir_fd=directory, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (staged_info.st_dev, staged_info.st_ino):
            raise ValueError('Lifecycle preparation directory changed')
        copier.protected(named, 0o700)
        if any(name.startswith(ATTEMPT_PREFIX) for name in os.listdir(directory)):
            raise ValueError('Interrupted lifecycle preparation attempt must be preserved')
        if set(os.listdir(staged)) != {filename(0)}:
            raise ValueError('Lifecycle preparation inventory changed')
        copier.check_image(staged, filename(0), fd, info, private=True)
        if copier.digest_fd(fd, info.st_size) != hashlib.sha256(raw).hexdigest():
            raise ValueError('Lifecycle preparation record changed')
        absent(PENDING if published else NAME)
    os.fsync(staged)
    os.fsync(directory)
    guard()
    p.rename_exclusive(directory, PENDING, NAME)
    try:
        os.fsync(directory)
        guard(True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise lifecycle.LifecycleUncertain('Lifecycle initialization published; inspect before retry') from error
    return dict(status)
