# SPDX-License-Identifier: GPL-3.0-only
"""Experimental serialized policy + supervised-video controller. No CLI/activation.

Caller supplies independently verified module objects, source/config/release binding
and bounded native readiness. These seams are NOT a production release authority.
Cross-boot admission, independent recovery invocation and media proof remain outside.
"""
from contextlib import contextmanager
import json
import os
import re
import secrets
import stat
import sys

OPERATION = '.capture-policy-operation.json'
LOCK = '.capture-policy-controller.lock'
PHASES = {'prepared', 'changing', 'recycle-requested', 'accepted', 'restore-requested', 'restored', 'retirement-requested'}
KEYS = {'version', 'kind', 'token', 'phase', 'requestedMode', 'prior', 'binding', 'boot', 'peers', 'videoBefore', 'outcome'}


class RecoveryRequired(RuntimeError): pass
class ChangeRejected(RuntimeError): pass


def binding(value):
    if (type(value) is not dict or set(value) != {'sourceSha256', 'configSha256', 'releaseSha256'}
            or any(type(v) is not str or not re.fullmatch('[0-9a-f]{64}', v) for v in value.values())):
        raise ValueError('Closed independently verified runtime binding required')
    return dict(value)


class Controller:
    def __init__(self, policy, recycle, store, *, admit, ready, _backend=None, checkpoint=lambda event: None):
        if sys.platform != 'linux' or os.geteuid() != 0 or not callable(admit) or not callable(ready):
            raise ValueError('Root Linux independent binding and bounded readiness required')
        self.p, self.r, self.store, self.admit, self.ready = policy, recycle, store, admit, ready
        self.b = recycle.Backend() if _backend is None else _backend
        self.checkpoint, self.owner, self.lockfd = checkpoint, os.getpid(), None
        self.fixed_binding, self.operation, self.busy = None, None, False

    def _lock_identity(self):
        self.store._check()
        a, b = os.fstat(self.lockfd), os.stat(LOCK, dir_fd=self.store.fd, follow_symlinks=False)
        for value in (a, b):
            if (not stat.S_ISREG(value.st_mode) or value.st_uid != 0 or value.st_gid != 0
                    or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1 or value.st_size != 0
                    or (value.st_dev, value.st_ino) != self.lock_identity):
                raise ValueError('Controller operation lock changed')

    def _check(self):
        if self.owner != os.getpid() or self.lockfd is None:
            raise ValueError('Current-process controller lock required')
        self._lock_identity()
        current = binding(self.admit())
        if self.fixed_binding is None: self.fixed_binding = current
        elif current != self.fixed_binding: raise ValueError('Runtime source/config/release binding changed')
        if self.operation is not None:
            value = self.operation
            if value['binding'] != current or self.b.boot() != value['boot']:
                raise ValueError('Operation binding or boot changed; preserve obligation')
            if any(self.b.observe(unit) != record for unit, record in value['peers'].items()):
                raise ValueError('Original guarded peer generation changed')
        return True

    def acquire(self):
        import fcntl
        if self.lockfd is not None: raise ValueError('Controller lock already held')
        # Unsupported/unadmitted installed releases must fail before even creating
        # a controller lock file in their protected configuration namespace.
        self.fixed_binding = binding(self.admit())
        self.store._check()
        try:
            fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.store.fd)
        except FileExistsError:
            fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=self.store.fd)
        self.lockfd = fd
        info = os.fstat(fd); self.lock_identity = (info.st_dev, info.st_ino)
        try:
            self._lock_identity()
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(self.store.fd)
            self._check()
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        if self.lockfd is not None:
            fd, self.lockfd = self.lockfd, None
            # Closing our noninherited open-file description releases the flock.
            os.close(fd)

    @contextmanager
    def _action(self):
        if self.busy: raise ValueError('Controller action reentry rejected')
        self.busy = True
        try:
            self._check()
            yield
        finally: self.busy = False

    def _record(self, raw):
        try:
            value = json.loads(raw)
            if (type(value) is not dict or set(value) != KEYS or self.p.encode(value) != raw
                    or type(value['version']) is not int or value['version'] != 1
                    or value['kind'] != 'capture-policy-operation' or value['phase'] not in PHASES
                    or type(value['token']) is not str or not re.fullmatch('[0-9a-f]{32}', value['token'])):
                raise ValueError()
            self.p.policy_bytes(value['requestedMode']); self.p.snapshot_record(value['prior']); binding(value['binding'])
            if type(value['boot']) is not str or not re.fullmatch(self.r.BOOT, value['boot']): raise ValueError()
            if type(value['peers']) is not dict or set(value['peers']) != set(self.r.GUARDS): raise ValueError()
            for unit, record in value['peers'].items(): self.r.record(record, unit)
            self.r.record(value['videoBefore'], self.b.video_unit)
            if value['phase'] != 'retirement-requested' and value['outcome'] is not None: raise ValueError()
            if value['phase'] == 'retirement-requested' and value['outcome'] not in ('accepted', 'restored'): raise ValueError()
            return value
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError('Invalid capture operation record') from None

    def _load(self):
        self._check()
        raw = self.store._read(OPERATION, self.p.LIMIT, True)
        if raw is None: return None, None
        value = self._record(bytes.fromhex(raw['hex']))
        self.operation = value
        self._check()
        return value, raw

    def _save(self, value, previous):
        self._check()
        raw = self.p.encode(value); self._record(raw)
        if len(raw) > self.p.LIMIT: raise ValueError('Operation record exceeds bound')
        with self.store._locked():
            temporary = self.store._scratch(raw)
            staged = self.store._read(temporary, self.p.LIMIT, True)
            if staged is None or staged['hex'] != raw.hex(): raise ValueError('Operation staging changed')
            self.store._expect(OPERATION, previous, journal=True)
            self._check()
            if previous is None: self.p.rename_new(self.store.fd, temporary, OPERATION)
            else: os.rename(temporary, OPERATION, src_dir_fd=self.store.fd, dst_dir_fd=self.store.fd)
            self.checkpoint('operation-published-' + value['phase'])
            os.fsync(self.store.fd)
            self.checkpoint('operation-durable-' + value['phase'])
            self.store._expect(OPERATION, staged, journal=True)
        self.operation = value
        self._check()
        return staged

    def _policy_record(self, value, *, history=False):
        policy = self.store.inspect()
        if policy is None and history:
            raw = self.store._read('.capture-policy-history-' + value['token'] + '.json', self.p.LIMIT, True)
            if raw is not None: policy = self.p.journal_record(bytes.fromhex(raw['hex']))
        if policy is not None and (policy['token'] != value['token'] or policy['before'] != value['prior'] or policy['mode'] != value['requestedMode']):
            raise ValueError('Foreign policy transaction; preserve obligation')
        return policy

    def _prior_mode(self, value):
        return 'cpu' if value['prior'] is None else self.p.parse_policy(bytes.fromhex(value['prior']['hex']))

    def _probe(self, mode, current):
        self._check()
        result = self.ready(mode, dict(current)) is True
        self._check()
        if self.b.observe(self.b.video_unit) != current: raise ValueError('Readiness process changed')
        return result

    def _ensure(self, mode, *, force):
        self._check()
        # Never signal an activating/uncertain worker. Retained obligation permits
        # a later admitted retry once systemd is back in a stable running state.
        with self.r.hold_video_recycle(admit=self._check, _backend=self.b) as cycle:
            if not force and self._probe(mode, cycle.before):
                cycle._original()
                return {'recycled': False, 'after': dict(cycle.before)}
            result = cycle.run(lambda current: self._probe(mode, current))
            return {'recycled': True, 'after': result['after']}

    def _live(self, mode):
        with self.r.hold_video_recycle(admit=self._check, _backend=self.b) as cycle:
            result = self._probe(mode, cycle.before)
            cycle._original()
            return result

    def change(self, mode):
        self.p.policy_bytes(mode)
        with self._action():
            value, previous = self._load()
            if value is not None: raise ValueError('Existing operation must be recovered or retired')
            if self.store.inspect() is not None: raise ValueError('Unbound policy transaction requires inspection')
            self.operation = None
            token = secrets.token_hex(16)
            self.store._expect('.capture-policy-operation-history-' + token + '.json', None, journal=True)
            with self.r.hold_video_recycle(admit=self._check, _backend=self.b) as cycle:
                value = {'version': 1, 'kind': 'capture-policy-operation', 'token': token, 'phase': 'prepared',
                         'requestedMode': mode, 'prior': self.store._policy(), 'binding': dict(self.fixed_binding),
                         'boot': cycle.boot, 'peers': cycle.peers, 'videoBefore': cycle.before, 'outcome': None}
                previous = self._save(value, previous)
                try:
                    value['phase'] = 'changing'; previous = self._save(value, previous)
                    self._check()
                    self.store.begin(mode, token=token, expected_before=value['prior'])
                    value['phase'] = 'recycle-requested'; previous = self._save(value, previous)
                    receipt = cycle.run(lambda current: self._probe(mode, current))
                    self.store.accept(token, lambda: self._probe(mode, receipt['after']))
                    value['phase'] = 'accepted'; self._save(value, previous)
                    return {'token': token, 'phase': 'accepted', 'mode': mode, 'recycled': True, 'readyForRelease': False}
                except Exception:
                    try:
                        actual, stamp = self._load()
                        if actual is None or actual['token'] != token: raise ValueError('Operation changed')
                        self._restore(actual, stamp)
                    except Exception:
                        raise RecoveryRequired('Capture operation retained; admitted recovery required') from None
                    raise ChangeRejected('Capture change rejected; prior policy and worker verified') from None

    def _restore(self, value, previous):
        self._check()
        if value['phase'] == 'retirement-requested': raise ValueError('Retirement must finish before another action')
        value['phase'] = 'restore-requested'; previous = self._save(value, previous)
        policy = self._policy_record(value)
        if policy is None:
            self.store._expect(self.p.POLICY, value['prior'])
        else:
            self.store.rollback(value['token'])
        self.checkpoint('policy-restored-worker-obligation-pending')
        result = self._ensure(self._prior_mode(value), force=False)
        value['phase'] = 'restored'; self._save(value, previous)
        return {'phase': 'restored', 'mode': self._prior_mode(value), 'recycled': result['recycled'], 'token': value['token']}

    def rollback(self):
        with self._action():
            value, previous = self._load()
            if value is None: raise ValueError('Current operation required; archives cannot roll back')
            return self._restore(value, previous)

    def recover(self):
        with self._action():
            value, previous = self._load()
            if value is None:
                if self.store.inspect() is not None: raise ValueError('Unbound policy transaction requires inspection')
                return {'phase': 'no-operation'}
            if value['phase'] == 'retirement-requested': return self._finish_retirement(value, previous)
            if value['phase'] == 'accepted':
                policy = self._policy_record(value)
                if policy is None or policy['phase'] != 'accepted' or self.store.recover() != 'accepted': raise ValueError('Accepted policy changed')
                if not self._live(value['requestedMode']): raise RecoveryRequired('Accepted worker needs explicit attention; no expiry applied')
                return {'phase': 'accepted', 'token': value['token'], 'recycled': False}
            return self._restore(value, previous)

    def retire(self):
        with self._action():
            value, previous = self._load()
            if value is None or value['phase'] not in ('accepted', 'restored'): raise ValueError('Verified terminal operation required')
            value['outcome'], value['phase'] = value['phase'], 'retirement-requested'
            previous = self._save(value, previous)
            return self._finish_retirement(value, previous)

    def _finish_retirement(self, value, previous):
        self._check()
        mode = value['requestedMode'] if value['outcome'] == 'accepted' else self._prior_mode(value)
        policy = self._policy_record(value, history=True)
        if policy is None:
            if value['outcome'] != 'restored': raise ValueError('Terminal policy receipt missing')
            self.store._expect(self.p.POLICY, value['prior'])
            if not self._live(mode): raise RecoveryRequired('Restored worker not verified')
        else:
            expected = 'accepted' if value['outcome'] == 'accepted' else 'rolled-back'
            if policy['phase'] != expected: raise ValueError('Terminal policy outcome changed')
            self.store.retire(value['token'], lambda: self._live(mode))
        self.checkpoint('policy-receipt-retired-operation-pending')
        name = '.capture-policy-operation-history-' + value['token'] + '.json'
        with self.store._locked():
            self.store._expect(OPERATION, previous, journal=True)
            self.store._expect(name, None, journal=True)
            self._check()
            self.p.rename_new(self.store.fd, OPERATION, name)
            self.checkpoint('operation-receipt-published')
            os.fsync(self.store.fd)
            self.checkpoint('operation-receipt-durable')
            self.store._expect(name, previous, journal=True)
        self.operation = None
        return {'phase': 'retired', 'token': value['token'], 'outcome': value['outcome']}


@contextmanager
def hold_controller(policy, recycle, store, *, admit, ready, _backend=None, checkpoint=lambda event: None):
    controller = Controller(policy, recycle, store, admit=admit, ready=ready, _backend=_backend, checkpoint=checkpoint)
    try: yield controller.acquire()
    finally: controller.close()
