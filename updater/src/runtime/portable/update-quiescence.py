# SPDX-License-Identifier: GPL-3.0-only
"""GPU mutation fence for a separately enrolled guard; no deployment/apply CLI."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sys

GUARD = 'droiddeck-update-recovery.service'
WATCH_PIN = '7de2b1af4bfbb29080d797e99e90666369225468d61255db6efbfec972becd0c'


def worker(digest):
    if type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Exact authenticated update digest required')
    return 'droiddeck-update-worker@' + digest + '.service'


def layout(gpu, name, fragment):
    raw = gpu.command(['/usr/bin/systemctl', 'show', name, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
    if len(raw) > 4096:
        raise ValueError('Bounded exact unit layout required')
    lines = raw.decode('ascii').splitlines()
    if len(lines) != 2 or dict(line.split('=', 1) for line in lines) != {'FragmentPath': fragment, 'DropInPaths': ''}:
        raise ValueError('Original unit without foreign overrides required')


class Fence:
    """Trusted guard caller supplies enrolled GPU actor and durable journal.

    Journal.intent/granted/return_intent/returned must exclusively fsync records;
    this class does not infer that authority from arbitrary caller JSON/flags.
    It intentionally exposes no live CLI, installer or filesystem destinations.
    """
    def __init__(self, gpu, active, journal, digest, guard_fragment_sha256):
        self.g, self.active, self.journal = gpu, active, journal
        self.worker, self.guard_pin = worker(digest), guard_fragment_sha256
        if type(self.guard_pin) is not str or not re.fullmatch('[a-f0-9]{64}', self.guard_pin):
            raise ValueError('Independently enrolled guard unit source required')
        self.stack, self.granted, self.stopped, self.closed = None, False, False, False
        self.before, self.state = None, None

    def phase(self, name, *args):
        if getattr(self.journal, name)(*args) is not None:
            raise ValueError('Exclusive durable raising journal required, not a success flag')

    def guard(self):
        state = self.g.unit(GUARD)
        if state['ActiveState'] != 'active' or state['MainPID'] != str(os.getpid()) or state['ControlPID'] != '0':
            raise ValueError('Independent enrolled guard main process required')
        fragment = '/etc/systemd/system/' + GUARD
        layout(self.g, GUARD, fragment)
        if hashlib.sha256(self.g.protected_read(Path(fragment), 0o644, 16384)).hexdigest() != self.guard_pin:
            raise ValueError('Enrolled guard fragment changed')

    def scope(self):
        token = self.active.admission.get('transaction')
        if (type(token) is not str or not re.fullmatch('[a-f0-9]{12}', token)
                or self.g.WATCHER != 'droiddeck-fresh-gpu-watch@' + token + '.service'
                or self.g.WORKER != 'droiddeck-fresh-gpu-worker@' + token + '.service'
                or self.g.STATE != Path('/var/lib/droiddeck/gpu-transactions') / token):
            raise ValueError('Original current admitted GPU mutation-lock scope required')
        fragment = '/etc/systemd/system/droiddeck-fresh-gpu-watch@.service'
        layout(self.g, self.g.WATCHER, fragment)
        if hashlib.sha256(self.g.protected_read(Path(fragment), 0o600, 16384)).hexdigest() != WATCH_PIN:
            raise ValueError('Original watcher template changed')
        self.g.require_quiet(self.g.unit(self.g.WORKER))
        self.active.metadata()
        self.g.sources()

    def probe(self):
        result = self.g.command(['/usr/bin/node', str(self.g.ROOT / 'runtime/portable/gpu-native.js')], timeout=35, check=False)
        if result.returncode != 0 or not 1 <= len(result.stdout) <= 8192:
            raise ValueError('Current independent native GPU evidence required')
        value = json.loads(result.stdout)
        if (value.get('kind') != 'android-gpu-native-health' or value.get('nativeGpuVerified') is not True
                or type(value.get('containerPid')) is not int or value['containerPid'] < 2
                or type(value.get('surfaceFlingerPid')) is not int or value['surfaceFlingerPid'] < 2
                or type(value.get('containerIdentity')) is not str
                or not re.fullmatch(r'\d+:\d+:[1-9]\d*', value['containerIdentity'])
                or type(value.get('androidIdentity')) is not str
                or not re.fullmatch(r'[1-9]\d*:[1-9]\d*/[1-9]\d*:[1-9]\d*', value['androidIdentity'])):
            raise ValueError('Stable original native GPU process generations required')
        return {name: value[name] for name in ('containerPid', 'surfaceFlingerPid', 'containerIdentity', 'androidIdentity')}

    def acquire(self):
        if self.stack is not None or self.closed:
            raise ValueError('GPU fence is single use')
        self.guard(); self.scope()
        self.g.require_quiet(self.g.unit(self.worker))
        stack = self.stack = ExitStack()
        try:
            # This is the SAME lock used by original GPU recovery. A recovery
            # already holding it wins; a prior decision cannot mutate behind it.
            stack.enter_context(self.active.store.lock())
            self.guard(); self.scope()
            self.state = self.active.store.status()
            if self.state['stage'] != 'ready' or self.state['boot'] != self.g.boot_id():
                raise ValueError('Current native-ready original GPU epoch required')
            old = self.g.unit(self.g.WATCHER)
            if old['ActiveState'] != 'active' or int(old['MainPID']) < 2 or old['ControlPID'] != '0':
                raise ValueError('Original independent watcher must initially be armed')
            self.before = self.probe()
            self.phase('intent', {'watcher': self.g.WATCHER, 'originalPid': old['MainPID'], 'guard': GUARD,
                                 'guardPid': str(os.getpid()), 'worker': self.worker,
                                 'boot': self.g.boot_id(), 'nativeGeneration': self.before})
            self.guard(); self.scope()
            if self.g.unit(self.g.WATCHER) != old or self.active.store.status() != self.state or self.probe() != self.before:
                raise ValueError('Original recovery epoch changed before quiescence')
            self.stopped = True  # A failing stop command may already have acted.
            self.g.command(['/usr/bin/systemctl', 'stop', self.g.WATCHER], timeout=70)
            self.g.require_quiet(self.g.unit(self.g.WATCHER))
            self.assert_stable()
            self.phase('granted')
            self.granted = True
            return self
        except Exception:
            stack.close(); self.stack = None; self.closed = True
            # No code authority was granted, so only unchanged original code can
            # be running. Keep the independent guard alive while rearming it.
            if self.stopped:
                self.guard(); self.scope()
                self.g.require_quiet(self.g.unit(self.g.WATCHER))
                self.g.command(['/usr/bin/systemctl', 'start', '--no-block', self.g.WATCHER], timeout=20)
            raise

    def assert_stable(self):
        if self.stack is None or self.closed:
            raise ValueError('Original held GPU fence required')
        self.guard(); self.scope()
        self.g.require_quiet(self.g.unit(self.g.WATCHER))
        if self.active.store.status() != self.state or self.probe() != self.before:
            raise ValueError('GPU generation changed; stop mutators and restore owned code')

    def return_owner(self, verify_code):
        if not self.granted or self.closed:
            raise ValueError('Original granted mutation fence required')
        self.guard(); self.scope()
        self.g.require_quiet(self.g.unit(self.worker))
        self.g.require_quiet(self.g.unit(self.g.WATCHER))
        # Trusted code verifier must raise on uncertainty, never return a boolean
        # that could accidentally be interpreted as filesystem ownership.
        if verify_code() is not None:
            raise ValueError('Raising original code-authority verifier required')
        self.phase('return_intent')
        self.stack.close(); self.stack = None; self.closed = True
        # Rearm only after releasing the original GPU lock; resume_boot uses it.
        self.g.command(['/usr/bin/systemctl', 'start', '--no-block', self.g.WATCHER], timeout=20)
        self.phase('returned')

    def stop_mutator(self):
        if not self.granted or self.closed:
            raise ValueError('Original granted fence required to stop its tracked worker')
        self.guard()
        self.g.command(['/usr/bin/systemctl', 'stop', self.worker], timeout=90)
        self.g.require_quiet(self.g.unit(self.worker))
        # Check source/metadata only after tracked writers have stopped.
        self.scope()


if __name__ == '__main__':
    print('No guard enrollment, live handoff or apply CLI is exposed', file=sys.stderr)
    sys.exit(1)
