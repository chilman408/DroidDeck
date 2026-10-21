# SPDX-License-Identifier: GPL-3.0-only
"""Internal watcher engine; production bootstrap/worker CLI deliberately unavailable."""
import math
import re
import subprocess
import sys

BINDINGS = ('transactionManifestSha256', 'configurationDigest', 'metadataSha256', 'dataDevice', 'dataInode')
BOOT = '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'


class LifecycleUncertain(RuntimeError):
    """A stop/recovery/checkpoint may have happened; inspect durable state, do not retry."""


def binding(value):
    if (type(value) is not dict or set(value) != set(BINDINGS)
            or any(type(value[k]) is not str or not re.fullmatch('[a-f0-9]{64}', value[k]) for k in BINDINGS[:3])
            or any(type(value[k]) is not int or not 0 < value[k] < 2**64 for k in BINDINGS[3:])):
        raise ValueError('Independent closed machine transaction binding required')
    return dict(value)


def validate_record(value, expected, boot, now, stages):
    if (type(value) is not dict or set(value) != {'kind', 'version', 'stage', 'revision', 'boot', 'since', *BINDINGS}
            or value['kind'] != 'protected-machine-gpu-lifecycle' or type(value['version']) is not int or value['version'] != 1
            or type(value['stage']) is not str or value['stage'] not in stages
            or type(value['revision']) is not int or not 0 <= value['revision'] < 2**63
            or value['boot'] != boot or binding({key: value[key] for key in BINDINGS}) != expected
            or type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < 1e12
            or type(value['since']) not in (int, float) or not math.isfinite(value['since']) or not 0 <= value['since'] <= now):
        raise ValueError('Closed current-boot bound lifecycle observation required')
    return dict(value)


class Watcher:
    def __init__(self, sources, expected, *, admit, read_status, native, stop_worker, worker_stopped,
                 stop_guest, guest_stopped, recover, mark_restored, notify, current_boot, clock, sleep):
        callbacks = (admit, read_status, native, stop_worker, worker_stopped, stop_guest, guest_stopped,
                     recover, mark_restored, notify, current_boot, clock, sleep)
        if not all(callable(callback) for callback in callbacks):
            raise ValueError('All independently admitted lifecycle callbacks required')
        self.binding = binding(expected)
        self.sources, self.admit = sources, admit
        sources['check']()
        self.policy = sources['load']('health')
        sources['check']()
        self.read_status, self.native = read_status, native
        self.stop_worker, self.worker_stopped = stop_worker, worker_stopped
        self.stop_guest, self.guest_stopped = stop_guest, guest_stopped
        self.recover, self.mark_restored, self.notify = recover, mark_restored, notify
        self.current_boot, self.clock, self.sleep = current_boot, clock, sleep
        self.boot = current_boot()
        if type(self.boot) is not str or not re.fullmatch(BOOT, self.boot):
            raise ValueError('Current boot identity required')
        self.armed, self.finished, self.failures = False, False, 0
        self.last, self.failure_record, self.last_clock = None, None, None

    def _admit(self):
        self.sources['check']()
        self.admit()
        if self.current_boot() != self.boot:
            raise ValueError('Lifecycle boot changed; separate boot recovery required')

    def _observe(self):
        self._admit()
        value, now = self.read_status(), self.clock()
        stages = self.policy.STARTING | {'ready', 'restored', 'recovery-required'}
        value = validate_record(value, self.binding, self.boot, now, stages)
        if self.last is not None and (value['revision'] < self.last['revision'] or value['since'] < self.last['since']
                or (value['revision'] == self.last['revision'] and value != self.last)):
            raise ValueError('Lifecycle generation regressed or changed without a revision')
        if self.last_clock is not None and now < self.last_clock:
            raise ValueError('Lifecycle clock regressed')
        self._admit()
        self.last, self.last_clock = value, now
        return value, now

    def _same(self, expected):
        value, _now = self._observe()
        if value != expected:
            raise ValueError('Lifecycle generation changed before recovery')

    def arm(self):
        if self.armed or self.finished:
            raise ValueError('Watcher readiness is single-use')
        value, _now = self._observe()
        self._same(value)
        if value['stage'] == 'restored':
            self.finished = True
            return False
        self.notify()
        self.armed = True
        return True

    def _result(self, action, **fields):
        return {'kind': 'protected-machine-gpu-watch-cycle', 'action': action, 'failures': self.failures,
            **fields, 'independentRecoveryArmed': False, 'servicesActivated': False,
            'propertiesRegenerated': False, 'androidOperationsExecuted': False,
            'installable': False, 'readyForActivation': False}

    def _recover(self, expected):
        self._same(expected)
        if expected['revision'] + 1 >= 2**63:
            raise ValueError('Insufficient lifecycle checkpoint revision capacity')
        self.finished = True  # Never automatically retry an attempted stop or uncertain file action.
        try:
            self.stop_worker()
            if self.worker_stopped() is not True:
                raise ValueError('Entire worker must be quiescent before guest stop')
            self._same(expected)
            self.stop_guest()

            def guard():
                self._same(expected)
                if self.worker_stopped() is not True or self.guest_stopped() is not True:
                    raise ValueError('Worker and guest must remain independently stopped')
            guard()
            result = self.recover(guard)
            if (type(result) is not dict or result.get('ownedDataRestored') is not True or result.get('stage') != 'settled'
                    or result.get('nextAction') is not None or type(result.get('sequence')) is not int or not 0 <= result['sequence'] < 2**63
                    or any(type(result.get(k)) is not type(self.binding[k]) or result[k] != self.binding[k] for k in BINDINGS)
                    or type(result.get('states')) is not dict or set(result['states']) != {'allocator', 'savedLayer', 'configuration'}
                    or any(state not in ('baseline', 'restored') for state in result['states'].values())
                    or any(result.get(key) is not False for key in ('servicesActivated', 'propertiesRegenerated',
                        'androidOperationsExecuted', 'independentRecoveryArmed', 'installable', 'readyForActivation'))):
                raise ValueError('Bound owned recovery did not durably settle stopped data')
            guard()
            self.mark_restored(dict(expected), result['sequence'])
            final, _now = self._observe()
            if (final['stage'] != 'restored' or final['revision'] != expected['revision'] + 1
                    or self.worker_stopped() is not True or self.guest_stopped() is not True):
                raise ValueError('Recovery completion checkpoint did not verify')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise LifecycleUncertain('Lifecycle recovery may have advanced; inspect durable state before resuming') from error
        return self._result('recovered', ownedDataRestored=True, recoverySequence=result['sequence'], servicesLeftStopped=True)

    def tick(self):
        if not self.armed or self.finished:
            raise ValueError('Current armed unfinished watcher required')
        before, _now = self._observe()
        if before != self.failure_record:
            self.failures = 0
        healthy = False
        if before['stage'] == 'ready':
            try:
                healthy = self.native() is True
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                healthy = False
        after, now = self._observe()
        if before != after:
            self.failures, self.failure_record = 0, None
            return self._result('retry', ownedDataRestored=False)
        action, self.failures = self.policy.decision(before['stage'], healthy, self.failures, now - before['since'])
        self.failure_record = before
        if action == 'recover':
            return self._recover(before)
        if action == 'stop':
            self.finished = True
        return self._result(action, ownedDataRestored=False)

    def run(self):
        if not self.arm():
            return self._result('stop', ownedDataRestored=False)
        while True:
            result = self.tick()
            if result['action'] in ('stop', 'recovered'):
                return result
            self.sleep(15)


def main(_args):
    # No production callbacks or source-anchored bootstrap have been installed yet.
    raise ValueError('Machine GPU lifecycle production entry is unavailable')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Machine GPU lifecycle entry unavailable', file=sys.stderr)
        sys.exit(1)
